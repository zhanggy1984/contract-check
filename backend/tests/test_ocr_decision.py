"""OCR 决策引擎单测：确定性短路不调 LLM、LLM 决策记录、保守执行权、异常兜底、_decide 多轮循环。"""
import unittest
from unittest import mock

from app.graph.decisions import _decide, decide_ocr_required
from app.llm.tool_client import ToolCall, ToolResponse

_DECIDE_OCR = "app.graph.decisions.call_with_tools"


def _resp(action: str, reason: str = "r", usage: dict | None = None) -> ToolResponse:
    return ToolResponse(
        content=None,
        tool_calls=[ToolCall(name="decide_ocr", arguments={"action": action, "reason": reason})],
        finish_reason="stop",
        usage=usage or {"total_tokens": 5},
    )


class TestOcrShortCircuit(unittest.TestCase):
    def test_engine_disabled_returns_legacy_no_llm(self):
        with mock.patch("app.graph.decisions.settings.tool_decision_enabled", False), \
             mock.patch(_DECIDE_OCR) as m:
            need, trace = decide_ocr_required(has_scanned=True, ocr_applied=False, existing_text="")
            self.assertTrue(need)
            self.assertEqual(trace["status"], "disabled")
            m.assert_not_called()

    def test_ocr_point_disabled_no_llm(self):
        with mock.patch("app.graph.decisions.settings.ocr_decision_enabled", False), \
             mock.patch(_DECIDE_OCR) as m:
            need, trace = decide_ocr_required(has_scanned=True, ocr_applied=False, existing_text="")
            self.assertTrue(need)
            self.assertEqual(trace["status"], "disabled")
            m.assert_not_called()

    def test_not_scanned_short_circuit(self):
        with mock.patch(_DECIDE_OCR) as m:
            need, trace = decide_ocr_required(has_scanned=False, ocr_applied=False, existing_text="")
            self.assertFalse(need)
            self.assertEqual(trace["status"], "short_circuit")
            m.assert_not_called()

    def test_ocr_applied_short_circuit(self):
        with mock.patch(_DECIDE_OCR) as m:
            need, trace = decide_ocr_required(has_scanned=True, ocr_applied=True, existing_text="")
            self.assertFalse(need)
            self.assertEqual(trace["status"], "short_circuit")
            m.assert_not_called()

    def test_text_layer_readable_short_circuit(self):
        # has_scanned 误报：文本层已有可读内容
        with mock.patch(_DECIDE_OCR) as m:
            need, trace = decide_ocr_required(
                has_scanned=True, ocr_applied=False, existing_text="合同正文" * 10)
            self.assertFalse(need)
            self.assertEqual(trace["status"], "short_circuit")
            self.assertIn("误报", trace["reason"])
            m.assert_not_called()

    def test_empty_pdf_short_circuit(self):
        with mock.patch(_DECIDE_OCR) as m, mock.patch("app.graph.decisions._pdf_meta", return_value=(0, 0)):
            need, trace = decide_ocr_required(
                has_scanned=True, ocr_applied=False, existing_text="", pdf_path="/tmp/x.pdf")
            self.assertFalse(need)
            self.assertEqual(trace["status"], "short_circuit")
            m.assert_not_called()

    def test_no_images_short_circuit(self):
        with mock.patch(_DECIDE_OCR) as m, mock.patch("app.graph.decisions._pdf_meta", return_value=(3, 0)):
            need, trace = decide_ocr_required(
                has_scanned=True, ocr_applied=False, existing_text="", pdf_path="/tmp/x.pdf")
            self.assertFalse(need)
            self.assertEqual(trace["status"], "short_circuit")
            m.assert_not_called()

    def test_page_level_no_scanned_pages_skip(self):
        # 页级契约：无扫描页 → 短路 skip，不调 LLM
        with mock.patch(_DECIDE_OCR) as m:
            need, trace = decide_ocr_required(scanned_pages=[], ocr_applied=False, existing_text="")
            self.assertFalse(need)
            self.assertEqual(trace["status"], "short_circuit")
            m.assert_not_called()

    def test_page_level_ocr_applied_skip(self):
        with mock.patch(_DECIDE_OCR) as m:
            need, trace = decide_ocr_required(scanned_pages=[0, 2], ocr_applied=True, existing_text="")
            self.assertFalse(need)
            self.assertEqual(trace["status"], "short_circuit")
            m.assert_not_called()

    def test_page_level_text_layer_not_skip(self):
        # 混合扫描 PDF：文本层可读不构成跳过理由（有文本页也有扫描页，扫描页内容缺失）
        with mock.patch(_DECIDE_OCR, return_value=_resp("ocr")) as m, \
             mock.patch("app.graph.decisions._pdf_meta", return_value=(3, 2)):
            need, trace = decide_ocr_required(
                scanned_pages=[1], ocr_applied=False, existing_text="第一条 标的：服务器。",
                pdf_path="/tmp/x.pdf")
            self.assertTrue(need, "有真实扫描页应需要 OCR，文本层可读不短路")
            self.assertEqual(trace["status"], "llm")
            self.assertIn("scanned_pages", trace["signals"])
            self.assertEqual(trace["signals"]["scanned_pages"], [1])

    def test_page_level_no_images_short_circuit(self):
        with mock.patch(_DECIDE_OCR) as m, mock.patch("app.graph.decisions._pdf_meta", return_value=(2, 0)):
            need, trace = decide_ocr_required(scanned_pages=[0], ocr_applied=False, existing_text="")
            self.assertFalse(need)
            self.assertEqual(trace["status"], "short_circuit")
            m.assert_not_called()


class TestOcrLlmDecision(unittest.TestCase):
    def test_llm_ocr_need_ocr(self):
        with mock.patch(_DECIDE_OCR, return_value=_resp("ocr")):
            need, trace = decide_ocr_required(
                has_scanned=True, ocr_applied=False, existing_text="", pdf_path="/tmp/x.pdf",
                file_name="扫描合同.pdf", file_size=1024)
            self.assertTrue(need)
            self.assertEqual(trace["status"], "llm")
            self.assertEqual(trace["decision"], "ocr")
            self.assertEqual(trace["usage"]["total_tokens"], 5)
            self.assertEqual(trace["signals"]["file_name"], "扫描合同.pdf")

    def test_llm_skip_allowed_when_switch_on(self):
        with mock.patch(_DECIDE_OCR, return_value=_resp("skip", "文件无扫描内容")), \
             mock.patch("app.graph.decisions.settings.ocr_decision_allow_llm_skip", True):
            need, trace = decide_ocr_required(
                has_scanned=True, ocr_applied=False, existing_text="", pdf_path="/tmp/x.pdf")
            self.assertFalse(need)
            self.assertEqual(trace["status"], "llm")
            self.assertEqual(trace["decision"], "skip")
            self.assertEqual(trace["reason"], "文件无扫描内容")

    def test_llm_skip_ignored_when_conservative(self):
        # 保守默认：LLM skip 不生效，执行仍强制 OCR
        with mock.patch(_DECIDE_OCR, return_value=_resp("skip")):
            need, trace = decide_ocr_required(
                has_scanned=True, ocr_applied=False, existing_text="", pdf_path="/tmp/x.pdf")
            self.assertTrue(need)
            self.assertEqual(trace["status"], "llm")
            self.assertEqual(trace["decision"], "skip")

    def test_llm_usage_only_in_trace(self):
        with mock.patch(_DECIDE_OCR, return_value=_resp("ocr", usage={"total_tokens": 9})):
            _, trace = decide_ocr_required(
                has_scanned=True, ocr_applied=False, existing_text="", pdf_path="/tmp/x.pdf")
            self.assertEqual(trace["usage"]["total_tokens"], 9)


class TestOcrFallback(unittest.TestCase):
    def test_exception_falls_back_keeps_legacy_execution(self):
        with mock.patch(_DECIDE_OCR, side_effect=RuntimeError("网络超时")):
            need, trace = decide_ocr_required(
                has_scanned=True, ocr_applied=False, existing_text="", pdf_path="/tmp/x.pdf")
            self.assertTrue(need)  # 执行不受影响，仍强制 OCR
            self.assertEqual(trace["status"], "fallback_error")
            self.assertIn("网络超时", trace["reason"])

    def test_no_tool_call_falls_back(self):
        resp = ToolResponse(content="无需调用", tool_calls=[], finish_reason="stop", usage=None)
        with mock.patch(_DECIDE_OCR, return_value=resp):
            need, trace = decide_ocr_required(
                has_scanned=True, ocr_applied=False, existing_text="", pdf_path="/tmp/x.pdf")
            self.assertTrue(need)
            self.assertEqual(trace["status"], "fallback_error")
            self.assertIn("未返回工具调用", trace["reason"])


class TestDecideLoop(unittest.TestCase):
    """_decide 多轮循环：首轮命中 / 补轮 / 乱调工具忽略 / 截断继续 / 耗尽兜底。"""

    def _resp_no_tool(self, content="", finish="stop", usage=None):
        return ToolResponse(content=content, tool_calls=[], finish_reason=finish, usage=usage)

    def _resp_tool(self, action="ocr", tool="decide_ocr", usage=None):
        return ToolResponse(
            content=None,
            tool_calls=[ToolCall(name=tool, arguments={"action": action, "reason": "r"})],
            finish_reason="stop",
            usage=usage or {"total_tokens": 5},
        )

    def test_first_round_match(self):
        m_reg = mock.MagicMock()
        m_reg.execute.return_value = {"action": "ocr", "reason": "r"}
        with mock.patch("app.graph.decisions.registry", m_reg), \
             mock.patch("app.graph.decisions.call_with_tools", return_value=self._resp_tool()) as m_cwt:
            d = _decide("sys", "user", "decide_ocr")
        self.assertEqual(d.action, "ocr")
        self.assertEqual(d.reason, "r")
        m_reg.schemas.assert_called_once_with(["decide_ocr"])
        m_reg.execute.assert_called_once_with("decide_ocr", action="ocr", reason="r")
        m_cwt.assert_called_once()

    def test_second_round_after_stop_without_tool(self):
        m_reg = mock.MagicMock()
        m_reg.execute.return_value = {"action": "skip", "reason": "r"}
        with mock.patch("app.graph.decisions.registry", m_reg), \
             mock.patch("app.graph.decisions.call_with_tools",
                        side_effect=[self._resp_no_tool(content="无需调用"), self._resp_tool("skip")]) as m_cwt:
            d = _decide("sys", "user", "decide_ocr")
        self.assertEqual(d.action, "skip")
        self.assertEqual(m_cwt.call_count, 2, "首轮正常结束未调工具应补一轮提示")

    def test_wrong_tool_ignored(self):
        m_reg = mock.MagicMock()
        m_reg.execute.return_value = {"action": "ocr", "reason": "r"}
        with mock.patch("app.graph.decisions.registry", m_reg), \
             mock.patch("app.graph.decisions.call_with_tools",
                        side_effect=[self._resp_tool(tool="decide_extract_retry"), self._resp_tool()]) as m_cwt:
            d = _decide("sys", "user", "decide_ocr")
        self.assertEqual(d.action, "ocr")
        self.assertEqual(m_cwt.call_count, 2, "LLM 乱调其它工具应忽略并重试，不执行任意工具")

    def test_length_continues_next_round(self):
        m_reg = mock.MagicMock()
        m_reg.execute.return_value = {"action": "ocr", "reason": "r"}
        with mock.patch("app.graph.decisions.registry", m_reg), \
             mock.patch("app.graph.decisions.call_with_tools",
                        side_effect=[self._resp_no_tool(finish="length"), self._resp_tool()]) as m_cwt:
            d = _decide("sys", "user", "decide_ocr")
        self.assertEqual(d.action, "ocr")
        self.assertEqual(m_cwt.call_count, 2, "截断应继续下一轮")

    def test_exhausted_returns_none_with_last_usage(self):
        m_reg = mock.MagicMock()
        with mock.patch("app.graph.decisions.registry", m_reg), \
             mock.patch("app.graph.decisions.call_with_tools",
                        return_value=self._resp_no_tool(usage={"total_tokens": 4})) as m_cwt:
            d = _decide("sys", "user", "decide_ocr")
        self.assertIsNone(d.action)
        self.assertEqual(d.last_usage["total_tokens"], 4, "耗尽时带出最后轮 usage 供兜底审计")
        self.assertEqual(m_cwt.call_count, 2, "max_rounds=2 两轮未决策应耗尽")


if __name__ == "__main__":
    unittest.main()
