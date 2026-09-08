"""obs_sdk 观测接线单测（§11.3 cc：request 中间件收口 + 后台合成 task span + LLM chokepoint）。

直通路径（obs 门关闭 → obs() 返 None → 零打点零开销）由既有 LLM/任务单测天然覆盖
（mock HTTP/图下业务语义不变即证明观测不掺入主链路）；本文件只验证**启用观测**分支：
- app.obs 门/LLM 出口：关闭返 None；record_llm ok/error 把 usage/error_type 透传给 sdk
- request 中间件收口映射：HTTP_{code} / ok（_obs_finish，仿 cs _obs_end）
- chokepoint（llm_client.call_json / tool_client.call_with_tools）：成功带 usage、失败先记再抛
- 后台合成 task span（check_task_service）：run_task_async 正常 → ok；TaskCancelledError → CANCELLED；
  取消/异常路径 span 必配对收口

观测 seam：单点替换 app.obs.obs（真实打点全经它取模块）；不触真实 Kafka/sdk 安装/真实 LLM/DB。
"""
import asyncio
import unittest
from unittest import mock

import app.obs
import app.service.check_task_service as svc
from app.common.errors import TaskCancelledError
from app.config import settings
from app.llm import llm_client, tool_client
from app.llm.llm_client import LLMError


class _FakeObs:
    """假 obs_sdk：记录 begin/end/record_llm 调用（无 init 状态校验）。"""

    def __init__(self):
        self.begins = []
        self.ends = []
        self.llm_calls = []

    def begin_request(self, *, method="GET", path="/", trace_id=None):
        self.begins.append({"method": method, "path": path, "trace_id": trace_id})

    def end_request(self, status, *, error_type=None, error_msg=None,
                    duration_ms=None, output=None, extra=None):
        self.ends.append({"status": status, "error_type": error_type,
                          "error_msg": error_msg})

    def record_llm(self, model, status, *, duration_ms, error_type=None,
                   error_msg=None, usage=None):
        self.llm_calls.append({
            "model": model, "status": status, "duration_ms": duration_ms,
            "error_type": error_type, "error_msg": error_msg, "usage": usage,
        })


# ---------- app.obs 门 / LLM 出口 ----------


class TestObsGate(unittest.TestCase):
    def test_disabled_returns_none(self):
        """三要素缺一（默认 env 无 OBS_*）→ obs() None → 全链路空转。"""
        with mock.patch.object(settings, "obs_enabled", False):
            self.assertIsNone(app.obs.obs())

    def test_error_type_mapping(self):
        """HTTP 状态优先，其余按类名归并（口径对齐 cs HTTP_xxx）。"""
        class _E500(Exception):
            status_code = 503

        class _E(Exception):
            pass

        self.assertEqual(app.obs.llm_error_type(_E500()), "HTTP_503")
        self.assertEqual(app.obs.llm_error_type(TimeoutError()), "TIMEOUT")
        self.assertEqual(app.obs.llm_error_type(ConnectionError()), "CONNECTION_ERROR")
        self.assertEqual(app.obs.llm_error_type(RuntimeError("x")), "LLM_ERROR")

    def test_record_ok_error_passthrough(self):
        """ok 透传 usage；error 透传 error_type + error_msg（先记再抛由 chokepoint 保证）。"""
        fake = _FakeObs()
        started = app.obs.llm_start()
        with mock.patch("app.obs.obs", return_value=fake):
            app.obs.record_llm_ok(started, {"prompt_tokens": 3, "total_tokens": 5})
            app.obs.record_llm_error(started, RuntimeError("boom"))
        self.assertEqual(len(fake.llm_calls), 2)
        ok_c, err_c = fake.llm_calls
        self.assertEqual(ok_c["status"], "ok")
        self.assertEqual(ok_c["usage"]["total_tokens"], 5)
        self.assertEqual(ok_c["model"], settings.deepseek_model)
        self.assertIsNotNone(ok_c["duration_ms"]) and ok_c["duration_ms"] >= 0
        self.assertEqual(err_c["status"], "error")
        self.assertEqual(err_c["error_type"], "LLM_ERROR")
        self.assertEqual(err_c["error_msg"], "boom")
        self.assertIsNone(err_c["usage"])

    def test_record_fault_swallowed(self):
        """观测 sdk 自身故障（record 抛异常）不炸业务。"""
        class _BoomObs:
            def record_llm(self, *a, **k):
                raise RuntimeError("sdk 故障")

        with mock.patch("app.obs.obs", return_value=_BoomObs()):
            app.obs.record_llm_ok(app.obs.llm_start(), None)  # 不应 raise


# ---------- request 中间件收口映射（_obs_finish，仿 cs _obs_end） ----------


class TestRequestFinishMapping(unittest.TestCase):
    """HTTP>=400 → error HTTP_{code}；其余 ok；断连标记优先。"""

    @staticmethod
    def _finish(fake, code=200, aborted=False):
        import app.main as main

        resp = type("_R", (), {"status_code": code})()
        req = type("_Q", (), {"url": type("_U", (), {"path": "/api/tasks/1"})()})()
        with mock.patch("app.obs.obs", return_value=fake):
            main._obs_finish(resp, req, aborted=aborted)
        return fake

    def test_ok(self):
        fake = self._finish(_FakeObs())
        self.assertEqual(fake.ends[-1]["status"], "ok")

    def test_http_500(self):
        fake = self._finish(_FakeObs(), code=500)
        self.assertEqual(fake.ends[-1]["status"], "error")
        self.assertEqual(fake.ends[-1]["error_type"], "HTTP_500")

    def test_http_400(self):
        fake = self._finish(_FakeObs(), code=400)
        self.assertEqual(fake.ends[-1]["error_type"], "HTTP_400")

    def test_aborted_beats_status(self):
        fake = self._finish(_FakeObs(), code=200, aborted=True)
        self.assertEqual(fake.ends[-1]["status"], "error")
        self.assertEqual(fake.ends[-1]["error_type"], "CLIENT_DISCONNECT")


# ---------- chokepoint：llm_client.call_json ----------


def _resp_ns(content="{}", usage=None, finish="stop"):
    return type("_R", (), {
        "content": content,
        "response_metadata": {"finish_reason": finish,
                              "token_usage": usage or {"prompt_tokens": 1,
                                                       "completion_tokens": 2,
                                                       "total_tokens": 3}},
    })()


class _Llms(list):
    """可调用的假 llm：invoke 依次执行注册脚本。"""

    def __init__(self, steps=None):
        super().__init__(steps or [])
        self.calls = []

    def invoke(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        step = self.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


class TestCallJsonObs(unittest.TestCase):
    def _run(self, llm):
        with mock.patch("app.obs.obs", return_value=self.fake), \
             mock.patch.object(llm_client, "get_chat_model", return_value=llm):
            return llm_client.call_json("sys", "usr")

    def setUp(self):
        self.fake = _FakeObs()

    def test_ok_with_usage(self):
        """成功 → record_llm ok + usage 透传，返回值不被观测改动。"""
        out = self._run(_Llms([_resp_ns(content="{\"a\":1}")]))
        self.assertEqual(out[0], "{\"a\":1}", "观测不得改返回值")
        self.assertEqual(len(self.fake.llm_calls), 1)
        call = self.fake.llm_calls[0]
        self.assertEqual(call["status"], "ok")
        self.assertEqual(call["usage"]["total_tokens"], 3)
        self.assertIsNone(call["error_type"])
        self.assertIsNotNone(call["duration_ms"])

    def test_error_5xx_records_then_raises(self):
        """OpenAIError 折叠 5xx（status_code=503）→ 先记 error HTTP_503 再抛 LLMError（§2.4）。"""
        class _Err503(Exception):
            status_code = 503

        with mock.patch("app.llm.llm_client.OpenAIError", _Err503), \
             mock.patch("app.obs.obs", return_value=self.fake), \
             mock.patch.object(llm_client, "get_chat_model",
                               return_value=_Llms([_Err503("upstream down")])):
            with self.assertRaises(LLMError):
                llm_client.call_json("sys", "usr")
        self.assertEqual(self.fake.llm_calls[-1]["status"], "error")
        self.assertEqual(self.fake.llm_calls[-1]["error_type"], "HTTP_503")
        self.assertIsNone(self.fake.llm_calls[-1]["usage"])

    def test_disabled_no_record(self):
        """观测未启用（obs() None）→ 成功调用零打点。"""
        with mock.patch("app.obs.obs", return_value=None), \
             mock.patch.object(llm_client, "get_chat_model",
                               return_value=_Llms([_resp_ns()])):
            content, _, _ = llm_client.call_json("sys", "usr")
        self.assertEqual(content, "{}")


# ---------- chokepoint：tool_client.call_with_tools ----------


class TestCallWithToolsObs(unittest.TestCase):
    def setUp(self):
        self.fake = _FakeObs()
        self.tools = [{"type": "function", "function": {"name": "decide", "parameters": {}}}]

    def test_ok_records_usage(self):
        """成功 → record_llm ok + 决策 usage 透传。"""
        resp = tool_client.ToolResponse(content="x", finish_reason="stop",
                                        usage={"total_tokens": 4})
        with mock.patch("app.obs.obs", return_value=self.fake), \
             mock.patch.object(tool_client, "_decision_model",
                               return_value=_Llms([object()])), \
             mock.patch.object(tool_client, "_parse_aimessage", return_value=resp):
            out = tool_client.call_with_tools("sys", "usr", self.tools)
        self.assertEqual(out.usage["total_tokens"], 4)
        self.assertEqual(self.fake.llm_calls[-1]["status"], "ok")
        self.assertEqual(self.fake.llm_calls[-1]["usage"]["total_tokens"], 4)

    def test_error_records_then_raises(self):
        """异常（无 status_code → LLM_ERROR）→ 先记 error 再原样上抛。"""
        with mock.patch("app.obs.obs", return_value=self.fake), \
             mock.patch.object(tool_client, "_decision_model",
                               return_value=_Llms([RuntimeError("boom")])):
            with self.assertRaises(RuntimeError):
                tool_client.call_with_tools("sys", "usr", self.tools)
        self.assertEqual(self.fake.llm_calls[-1]["status"], "error")
        self.assertEqual(self.fake.llm_calls[-1]["error_type"], "LLM_ERROR")
        self.assertEqual(self.fake.llm_calls[-1]["error_msg"], "boom")


# ---------- 合成 task span（check_task_service.run_task_async） ----------


class TestTaskSpan(unittest.TestCase):
    """run_task_async 在观测启用时对每个后台任务合成 request span（trace_id=task-{id}）。"""

    def _runner(self, flow):
        async def runner():
            svc.run_task_async(7)
            await asyncio.sleep(0.2)

        with mock.patch("app.obs.obs", return_value=self.fake), \
             mock.patch.object(svc, "_run_flow", side_effect=flow), \
             mock.patch.object(svc, "update_status"), \
             mock.patch.object(svc, "_cleanup_if_terminal"):
            asyncio.run(runner())

    def setUp(self):
        self.fake = _FakeObs()

    def test_success_begin_end_ok(self):
        """图执行正常返回 → span begin(POST /internal/check-tasks/7/run) + end ok。"""
        self._runner(lambda task_id, reviews=None: None)
        self.assertEqual(len(self.fake.begins), 1)
        b = self.fake.begins[0]
        self.assertEqual(b["trace_id"], "task-7")
        self.assertIn("/internal/check-tasks/", b["path"])
        self.assertIn("7", b["path"])
        self.assertEqual(self.fake.ends[-1]["status"], "ok")

    def test_cancelled_span_error_cancelled(self):
        """图入口 CANCELLED 短路（TaskCancelledError）→ end error + CANCELLED（span 必配对）。"""
        def _cancelled(task_id, reviews=None):
            raise TaskCancelledError("任务已取消")

        self._runner(_cancelled)
        self.assertEqual(len(self.fake.begins), 1, "取消也须先建 span（LLM 若打过点有锚）")
        end = self.fake.ends[-1]
        self.assertEqual(end["status"], "error")
        self.assertEqual(end["error_type"], "CANCELLED")

    def test_exception_span_error_internal(self):
        """图抛一般异常 → end error + INTERNAL_ERROR。"""
        def _boom(task_id, reviews=None):
            raise RuntimeError("graph blew")

        self._runner(_boom)
        self.assertEqual(self.fake.ends[-1]["status"], "error")
        self.assertEqual(self.fake.ends[-1]["error_type"], "INTERNAL_ERROR")

    def test_disabled_no_span(self):
        """观测未启用 → 零 begin/end（直通路径与既有任务测试一致）。"""
        async def runner():
            svc.run_task_async(7)
            await asyncio.sleep(0.2)

        with mock.patch("app.obs.obs", return_value=None), \
             mock.patch.object(svc, "_run_flow", lambda task_id, reviews=None: None), \
             mock.patch.object(svc, "update_status"), \
             mock.patch.object(svc, "_cleanup_if_terminal"):
            asyncio.run(runner())
        self.assertEqual(self.fake.begins, [])


if __name__ == "__main__":
    unittest.main()
