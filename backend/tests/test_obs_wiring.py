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
                    duration_ms=None, input=None, output=None, extra=None):
        self.ends.append({"status": status, "error_type": error_type,
                          "error_msg": error_msg, "input": input})

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
        """仅 429 单列，其余状态码/类名统一归 llm_other（平台白名单值域，口径对齐 cs）。"""
        class _E500(Exception):
            status_code = 503

        class _E(Exception):
            pass

        self.assertEqual(app.obs.llm_error_type(_E500()), "llm_other")
        self.assertEqual(app.obs.llm_error_type(TimeoutError()), "llm_timeout")
        self.assertEqual(app.obs.llm_error_type(ConnectionError()), "llm_connection")
        self.assertEqual(app.obs.llm_error_type(RuntimeError("x")), "llm_other")

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
        self.assertEqual(err_c["error_type"], "llm_other")
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
    def _finish(fake, code=200, aborted=False, obs_input=None):
        import app.main as main

        resp = type("_R", (), {"status_code": code})()
        state = type("_S", (), {})()
        if obs_input is not None:
            state.obs_input = obs_input
        req = type("_Q", (), {"url": type("_U", (), {"path": "/api/tasks/1"})(),
                              "state": state})()
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

    def test_ok_carries_route_input(self):
        """路由置了 request.state.obs_input → ok 出口也带出。

        input 是平台侧 root 去重键与 case 现场的唯一来源，缺则失败 trace 建不出簇。
        """
        fake = self._finish(_FakeObs(), obs_input={"filename": "a.pdf"})
        self.assertEqual(fake.ends[-1]["input"], {"filename": "a.pdf"})

    def test_error_carries_route_input(self):
        """error 路径必须同样带现场（HTTP_400 这类最该有现场的就是失败路径）。"""
        fake = self._finish(_FakeObs(), code=400, obs_input={"filename": "a.pdf"})
        self.assertEqual(fake.ends[-1]["error_type"], "HTTP_400")
        self.assertEqual(fake.ends[-1]["input"], {"filename": "a.pdf"})

    def test_no_route_input_stays_none(self):
        """未置位路由（其余 16 个）input 为 None，与接入前行为一致——不误造现场。"""
        fake = self._finish(_FakeObs())
        self.assertIsNone(fake.ends[-1]["input"])


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
        """OpenAIError 折叠 5xx（status_code=503）→ 先记 error（折叠 llm_other）再抛 LLMError（§2.4）。"""
        class _Err503(Exception):
            status_code = 503

        with mock.patch("app.llm.llm_client.OpenAIError", _Err503), \
             mock.patch("app.obs.obs", return_value=self.fake), \
             mock.patch.object(llm_client, "get_chat_model",
                               return_value=_Llms([_Err503("upstream down")])):
            with self.assertRaises(LLMError):
                llm_client.call_json("sys", "usr")
        self.assertEqual(self.fake.llm_calls[-1]["status"], "error")
        self.assertEqual(self.fake.llm_calls[-1]["error_type"], "llm_other")
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
        """异常（无 status_code → llm_other）→ 先记 error 再原样上抛。"""
        with mock.patch("app.obs.obs", return_value=self.fake), \
             mock.patch.object(tool_client, "_decision_model",
                               return_value=_Llms([RuntimeError("boom")])):
            with self.assertRaises(RuntimeError):
                tool_client.call_with_tools("sys", "usr", self.tools)
        self.assertEqual(self.fake.llm_calls[-1]["status"], "error")
        self.assertEqual(self.fake.llm_calls[-1]["error_type"], "llm_other")
        self.assertEqual(self.fake.llm_calls[-1]["error_msg"], "boom")


# ---------- 合成 task span（check_task_service.run_task_async） ----------


class TestTaskSpan(unittest.TestCase):
    """run_task_async 在观测启用时对每个后台任务合成 request span（trace_id=task-{id}）。"""

    _TASK_INPUT = {"task_id": 7, "file_path": "/app/uploads/cc_b1_missing_date.pdf"}

    def _runner(self, flow):
        async def runner():
            svc.run_task_async(7)
            await asyncio.sleep(0.2)

        with mock.patch("app.obs.obs", return_value=self.fake), \
             mock.patch.object(svc, "_run_flow", side_effect=flow), \
             mock.patch.object(svc, "update_status"), \
             mock.patch.object(svc, "_cleanup_if_terminal"), \
             mock.patch.object(svc, "_obs_task_input",
                               return_value=dict(self._TASK_INPUT)):
            asyncio.run(runner())

    def setUp(self):
        self.fake = _FakeObs()

    def test_success_begin_end_ok(self):
        """图执行正常返回 → span begin(GET /api/tasks/7/result) + end ok。"""
        self._runner(lambda task_id, reviews=None: None)
        self.assertEqual(len(self.fake.begins), 1)
        b = self.fake.begins[0]
        self.assertEqual(b["trace_id"], "task-7")
        # interface 必须是契约业务接口：平台侧 interface 是可重放的业务入口，离线回流按
        # (agent, method, path) 逐段匹配登记表；internal 执行路径未登记 → offline_cap_gap 驳回
        self.assertEqual(b["method"], "GET")
        self.assertEqual(b["path"], "/api/tasks/7/result")
        self.assertEqual(self.fake.ends[-1]["status"], "ok")
        # 现场必须原样带出：平台侧 root 去重键与 case 现场只认它（口径见 _obs_task_input）
        self.assertEqual(self.fake.ends[-1]["input"], self._TASK_INPUT)

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


# ---------- 现场口径：_obs_task_input ----------


class TestObsTaskInput(unittest.TestCase):
    """file_path 必须落在**离线容器**的 uploads 命名空间（/app/uploads/{原名}）。

    离线契约 prepare 以 {case.input.file_path} 走 multipart，出站前白名单要求 realpath
    在离线 /app/uploads 内；写 cc 自己的存储路径会被拒，只写 task_id 会被 content_gap 驳。
    """

    def _run(self, task_id=7, name="cc_b1_missing_date.pdf", db_raises=False):
        cm = mock.MagicMock()
        if db_raises:
            cm.__enter__.side_effect = RuntimeError("db down")
        else:
            q = cm.__enter__.return_value.query.return_value
            q.join.return_value.filter.return_value.first.return_value = (
                (name,) if name is not None else None)
        with mock.patch.object(svc, "SessionLocal", return_value=cm):
            return svc._obs_task_input(task_id)

    def test_resolves_uploads_path_from_original_name(self):
        """task→contract_file 反查原名 → /app/uploads/{原名}（离线侧同一份文件）。"""
        out = self._run()
        self.assertEqual(out["file_path"], "/app/uploads/cc_b1_missing_date.pdf")
        self.assertEqual(out["task_id"], 7, "现场标识一并保留，便于对账")

    def test_no_record_falls_back_to_task_id(self):
        """查不到记录（任务与文件已解绑）→ 只留 task_id，不编造路径。"""
        out = self._run(name=None)
        self.assertEqual(out, {"task_id": 7})
        self.assertNotIn("file_path", out)

    def test_db_failure_never_breaks_task(self):
        """观测取材失败不得影响任务收口：吞异常、退化为 task_id。"""
        out = self._run(db_raises=True)
        self.assertEqual(out, {"task_id": 7})

    def test_none_task_id_returns_none(self):
        out = self._run(task_id=None)
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
