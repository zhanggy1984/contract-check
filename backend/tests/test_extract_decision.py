"""抽取失败处置决策单测：短路 fail、LLM 建议记录、异常兜底、trace 完整。"""
import unittest
from unittest import mock

from app.graph.decisions import decide_extract_retry
from app.llm.tool_client import ToolCall, ToolResponse

_DECIDE = "app.graph.decisions.call_with_tools"


def _resp(action: str, reason: str = "r") -> ToolResponse:
    return ToolResponse(
        content=None,
        tool_calls=[ToolCall(name="decide_extract_retry", arguments={"action": action, "reason": reason})],
        finish_reason="stop",
        usage={"total_tokens": 4},
    )


class TestExtractShortCircuit(unittest.TestCase):
    def test_engine_disabled_fail_no_llm(self):
        with mock.patch("app.graph.decisions.settings.tool_decision_enabled", False), \
             mock.patch(_DECIDE) as m:
            action, trace = decide_extract_retry(text="长文本" * 10, result_status="FAILED",
                                                 error="e", std_json=None)
            self.assertEqual(action, "fail")
            self.assertEqual(trace["status"], "disabled")
            m.assert_not_called()

    def test_not_failed_status_short_circuit(self):
        with mock.patch(_DECIDE) as m:
            action, trace = decide_extract_retry(text="长文本" * 10, result_status="COMPLETE",
                                                 error=None, std_json={})
            self.assertEqual(action, "fail")
            self.assertEqual(trace["status"], "short_circuit")
            m.assert_not_called()

    def test_text_too_short_short_circuit(self):
        with mock.patch(_DECIDE) as m:
            action, trace = decide_extract_retry(text="   ", result_status="FAILED",
                                                 error=None, std_json=None)
            self.assertEqual(action, "fail")
            self.assertEqual(trace["status"], "short_circuit")
            self.assertIn("过短", trace["reason"])
            m.assert_not_called()


class TestExtractLlmDecision(unittest.TestCase):
    def test_llm_retry_recorded(self):
        with mock.patch(_DECIDE, return_value=_resp("retry", "文本较长，可重试")):
            action, trace = decide_extract_retry(text="长文本" * 10, result_status="FAILED",
                                                 error="JSON 解析失败", std_json=None)
            self.assertEqual(action, "retry")
            self.assertEqual(trace["status"], "llm")
            self.assertEqual(trace["decision"], "retry")
            self.assertEqual(trace["reason"], "文本较长，可重试")

    def test_llm_fail_recorded(self):
        with mock.patch(_DECIDE, return_value=_resp("fail", "原因不可恢复")):
            action, trace = decide_extract_retry(text="长文本" * 10, result_status="FAILED",
                                                 error=None, std_json={})
            self.assertEqual(action, "fail")
            self.assertEqual(trace["status"], "llm")
            self.assertEqual(trace["decision"], "fail")

    def test_trace_complete(self):
        with mock.patch(_DECIDE, return_value=_resp("retry")):
            _, trace = decide_extract_retry(text="长文本" * 10, result_status="FAILED",
                                            error="截断", std_json=None)
            for key in ("node", "tool", "decision", "status", "reason", "signals", "usage", "ts"):
                self.assertIn(key, trace)
            self.assertEqual(trace["node"], "extract")
            self.assertEqual(trace["tool"], "decide_extract_retry")
            self.assertEqual(trace["signals"]["failure_reason"], "truncated")
            self.assertEqual(trace["usage"]["total_tokens"], 4)


class TestExtractFallback(unittest.TestCase):
    def test_exception_falls_back_fail(self):
        with mock.patch(_DECIDE, side_effect=RuntimeError("限流")):
            action, trace = decide_extract_retry(text="长文本" * 10, result_status="FAILED",
                                                 error=None, std_json=None)
            self.assertEqual(action, "fail")
            self.assertEqual(trace["status"], "fallback_error")
            self.assertIn("限流", trace["reason"])

    def test_no_tool_call_falls_back(self):
        resp = ToolResponse(content="放弃", tool_calls=[], finish_reason="stop", usage=None)
        with mock.patch(_DECIDE, return_value=resp):
            action, trace = decide_extract_retry(text="长文本" * 10, result_status="FAILED",
                                                 error=None, std_json=None)
            self.assertEqual(action, "fail")
            self.assertEqual(trace["status"], "fallback_error")
            self.assertIn("未返回工具调用", trace["reason"])


if __name__ == "__main__":
    unittest.main()
