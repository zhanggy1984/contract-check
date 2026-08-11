"""防御路径单测：D3 截断降级 / D4 空 content、非法 JSON 重试 / C6 resume 失败回退。

背景：端到端验收中这些分支依赖真实 LLM 异常，难以自然触发（验收缺口）。
此处 mock call_json / _run_flow 隔离 LLM 与图执行，直接覆盖分支逻辑。
unittest 风格（与 test_task_timeout.py 一致），pytest 作 runner，不触真实 DB/LLM。
"""
import unittest
from unittest.mock import patch

from app.llm import extractor
from app.llm.extractor import extract_contract

# schema required = [contractTitle, currency, hasParty]，hasParty 允许空数组
VALID_JSON = '{"contractTitle": "测试合同", "currency": "CNY", "hasParty": []}'
TEXT = (
    "购销合同\n甲方：甲公司\n乙方：乙公司\n"
    "第一条 合同标的：服务器，金额 10000 元。\n"
    "第二条 违约责任：任何一方违约应赔偿对方损失。\n"
)


class TestExtractRetry(unittest.TestCase):
    """D4a 空 content → feedback 重试后成功；D4b 非法 JSON → feedback 重试后成功。"""

    def test_empty_content_retries_then_succeeds(self):
        calls = []

        def fake_call_json(system, user):
            calls.append(user)
            if len(calls) == 1:
                return (None, "stop", None)   # 空 content
            return (VALID_JSON, "stop", None)

        with patch.object(extractor, "call_json", side_effect=fake_call_json):
            r = extract_contract(TEXT)
        self.assertEqual(len(calls), 2, "空 content 应触发一次重试")
        self.assertEqual(r.status, "COMPLETE")
        self.assertIsNotNone(r.std_json)

    def test_invalid_json_retries_then_succeeds(self):
        calls = []

        def fake_call_json(system, user):
            calls.append(user)
            if len(calls) == 1:
                return ("{not-json", "stop", None)   # 非法 JSON
            return (VALID_JSON, "stop", None)

        with patch.object(extractor, "call_json", side_effect=fake_call_json):
            r = extract_contract(TEXT)
        self.assertEqual(len(calls), 2, "非法 JSON 应触发一次重试")
        self.assertEqual(r.status, "COMPLETE")


class TestTruncateFallback(unittest.TestCase):
    """D3 输出截断（finish_reason=length）→ 降级分段重抽。"""

    def test_truncated_falls_back_to_segmented(self):
        calls = []

        def fake_call_json(system, user):
            calls.append(user)
            if len(calls) == 1:
                return ("", "length", None)   # 单段截断
            return (VALID_JSON, "stop", None)

        with patch.object(extractor, "call_json", side_effect=fake_call_json):
            r = extract_contract(TEXT)
        self.assertEqual(len(calls), 2, "截断后应走分段重抽")
        self.assertEqual(r.status, "COMPLETE", "降级分段重抽应成功而非 FAILED")
        # 注：r.truncated 不保证为 True——单段入口截断转 segmented 后，
        # any_truncated 按分段实际截断重算且 truncated 无下游消费方，故不断言。


class TestResumeRollback(unittest.TestCase):
    """C6 resume 图执行异常 → REVIEWING 幂等回退 WAITING_REVIEW，返回 False。"""

    def test_resume_failure_rolls_back(self):
        import app.service.check_task_service as svc

        class _FakeResult:
            def __init__(self, rowcount):
                self.rowcount = rowcount

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def query(self, *a, **k):
                return self

            def filter_by(self, *a, **k):
                return self

            def all(self):
                return []                       # 无 UNCONFIRMED violation

            def commit(self):
                pass

            def execute(self, *a, **k):
                self.execute_count = getattr(self, "execute_count", 0) + 1
                return _FakeResult(1)           # CAS / 回退 UPDATE 均成功

        fake = FakeSession()
        with patch.object(svc, "SessionLocal", return_value=fake), \
             patch.object(svc, "_run_flow", side_effect=RuntimeError("图执行异常")):
            ok = svc.resume_task(999, [])
        self.assertFalse(ok, "resume 失败应返回 False")
        self.assertEqual(fake.execute_count, 2,
                         "CAS 抢占 + 回退各 1 次 UPDATE，证明失败后回退发生")


class TestBlankRequiredField(unittest.TestCase):
    """方案 A：必填字段空串视为缺失 → 触发重试，而非被 Pydantic 当"有值"放行。"""

    def test_blank_required_triggers_retry(self):
        calls = []

        def fake_call_json(system, user):
            calls.append(user)
            if len(calls) == 1:
                return ('{"contractTitle": "", "currency": "CNY", "hasParty": []}', "stop", None)
            return (VALID_JSON, "stop", None)

        with patch.object(extractor, "call_json", side_effect=fake_call_json):
            r = extract_contract(TEXT)
        self.assertEqual(len(calls), 2, "必填空串应触发一次重试")
        self.assertEqual(r.status, "COMPLETE")

    def test_blank_required_still_blank_leads_incomplete(self):
        calls = []

        def fake_call_json(system, user):
            calls.append(user)
            return ('{"contractTitle": "", "currency": "CNY", "hasParty": []}', "stop", None)

        with patch.object(extractor, "call_json", side_effect=fake_call_json):
            r = extract_contract(TEXT)
        self.assertEqual(len(calls), 3, "重试仍空串 → 重试耗尽（MAX_ATTEMPTS=3）")
        self.assertEqual(r.status, "INCOMPLETE", "必填空串重试仍空 → INCOMPLETE 而非 COMPLETE")


class TestSplitTextNoLoss(unittest.TestCase):
    """P0 review 修复：_split_text 遇超长段落时，先前累积的短段不得被丢弃。"""

    def test_long_paragraph_keeps_accumulated_text(self):
        from app.llm.extractor import SEGMENT_CHAR_LIMIT, _split_text

        text = "短段落A\n" + "长" * (SEGMENT_CHAR_LIMIT + 100) + "\n短段落B"
        chunks = _split_text(text)
        joined = "".join(chunks)
        self.assertIn("短段落A", joined, "超长段前累积的段落不应丢失")
        self.assertIn("短段落B", joined)
        # 字符级无丢失：全部字符都保留在 chunks 拼接结果中
        expected_len = len("短段落A") + (SEGMENT_CHAR_LIMIT + 100) + len("短段落B")
        self.assertEqual(sum(len(c) for c in chunks), expected_len)


if __name__ == "__main__":
    unittest.main()
