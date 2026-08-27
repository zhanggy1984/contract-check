"""executor 包装单测：patch 底层能力，断言出参 dict 键完整、异常上抛、决策薄壳归一化。"""
import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest import mock

from app.tools import executors


@dataclass
class _SemOutcome:
    rule_id: int
    result: str
    message: str | None = None
    evidence_text: str | None = None
    segment_ref: str | None = None
    confidence: str = "HIGH"


@dataclass
class _DetResult:
    passed: bool
    subjects: list = field(default_factory=list)
    rule_snapshot: str = ""


class TestExecExtractContract(unittest.TestCase):
    @mock.patch("app.tools.executors.extract_contract")
    def test_dict_keys_complete(self, m):
        m.return_value = SimpleNamespace(
            std_json={"name": "采购合同"}, status="COMPLETE", segments=["s1"], truncated=False,
            error=None, conflicts=[], token_usage={"total_tokens": 10},
        )
        r = executors.exec_extract_contract("正文", {"type": "object"})
        self.assertEqual(r["status"], "COMPLETE")
        self.assertEqual(r["std_json"]["name"], "采购合同")
        self.assertEqual(r["segments"], ["s1"])
        self.assertFalse(r["truncated"])
        self.assertIsNone(r["error"])
        self.assertEqual(r["token_usage"]["total_tokens"], 10)
        m.assert_called_once_with("正文", {"type": "object"})

    @mock.patch("app.tools.executors.extract_contract")
    def test_exception_propagates(self, m):
        m.side_effect = RuntimeError("抽取异常")
        with self.assertRaises(RuntimeError):
            executors.exec_extract_contract("正文")


class TestExecEvaluateSemantic(unittest.TestCase):
    @mock.patch("app.tools.executors.SemanticEvaluator")
    def test_outcomes_asdict_and_usage(self, m):
        ev = m.return_value
        ev.evaluate.return_value = [
            _SemOutcome(1, "FAIL", message="缺违约条款", evidence_text="原文", segment_ref="seg-0"),
            _SemOutcome(2, "PASS", confidence="LOW"),
        ]
        ev.usage = {"total_tokens": 99}
        r = executors.exec_evaluate_semantic([{"index": 0}], [{"rule_iri": "r1"}])
        self.assertEqual(len(r["outcomes"]), 2)
        self.assertEqual(r["outcomes"][0], {
            "rule_id": 1, "result": "FAIL", "message": "缺违约条款",
            "evidence_text": "原文", "segment_ref": "seg-0", "confidence": "HIGH",
        })
        self.assertEqual(r["usage"]["total_tokens"], 99)

    @mock.patch("app.tools.executors.SemanticEvaluator")
    def test_exception_propagates(self, m):
        m.return_value.evaluate.side_effect = RuntimeError("判定异常")
        with self.assertRaises(RuntimeError):
            executors.exec_evaluate_semantic([], [])


class TestExecOcrPdf(unittest.TestCase):
    @mock.patch("app.tools.executors.OcrService")
    def test_pages_returned(self, m):
        m.ocr_pdf_with_stats.return_value = ({0: "扫描识别文本", 2: "第三页文本"}, {})
        self.assertEqual(executors.exec_ocr_pdf("/tmp/x.pdf"),
                         {"pages": {0: "扫描识别文本", 2: "第三页文本"}, "stats": {}})

    @mock.patch("app.tools.executors.OcrService")
    def test_pages_param_passed(self, m):
        # 混合扫描 PDF：页级 OCR 透传 pages（只识别扫描页）
        m.ocr_pdf_with_stats.return_value = ({1: "扫描识别文本"}, {})
        executors.exec_ocr_pdf("/tmp/x.pdf", pages=[1])
        m.ocr_pdf_with_stats.assert_called_once_with("/tmp/x.pdf", pages=[1])

    @mock.patch("app.tools.executors.OcrService")
    def test_exception_propagates(self, m):
        m.ocr_pdf_with_stats.side_effect = RuntimeError("PaddleOCR 未安装")
        with self.assertRaises(RuntimeError):
            executors.exec_ocr_pdf("/tmp/x.pdf")

    @mock.patch("app.tools.executors.OcrService")
    def test_output_cleaned(self, m):
        # OCR 文本噪声更大，统一过输入清洗（全角/零宽/全角空格）
        m.ocr_pdf_with_stats.return_value = ({0: "扫描ＡＢＣ" + chr(0x200B) + "识别" + chr(0x3000) + "文本"}, {})
        self.assertEqual(executors.exec_ocr_pdf("/tmp/x.pdf"),
                         {"pages": {0: "扫描ABC识别 文本"}, "stats": {}})


class TestExecRunSparql(unittest.TestCase):
    @mock.patch("app.tools.executors.SparqlExecutor")
    def test_result_dict(self, m):
        m.return_value.run.return_value = _DetResult(True, ["#party1"], "snapshot")
        r = executors.exec_run_sparql(graph=object(), rule=object())
        self.assertEqual(r["passed"], True)
        self.assertEqual(r["subjects"], ["#party1"])
        self.assertEqual(r["rule_snapshot"], "snapshot")

    @mock.patch("app.tools.executors.SparqlExecutor")
    def test_exception_propagates(self, m):
        m.return_value.run.side_effect = RuntimeError("SPARQL 异常")
        with self.assertRaises(RuntimeError):
            executors.exec_run_sparql(graph=object(), rule=object())


class TestDecisionThinShells(unittest.TestCase):
    def test_exec_decide_ocr_normalizes(self):
        # 干净输入：action 原样返回，invalid_action 恒为 None
        self.assertEqual(executors.exec_decide_ocr("ocr", "文本层可读"),
                         {"action": "ocr", "reason": "文本层可读", "invalid_action": None})

    def test_exec_decide_ocr_reason_empty_default(self):
        self.assertEqual(executors.exec_decide_ocr("skip", None),
                         {"action": "skip", "reason": "", "invalid_action": None})

    def test_exec_decide_extract_retry_normalizes(self):
        self.assertEqual(
            executors.exec_decide_extract_retry("fail", "文本过短"),
            {"action": "fail", "reason": "文本过短", "invalid_action": None},
        )

    def test_action_noise_normalized(self):
        # LLM 返回带噪声 action：结尾标点/大小写/空白/全角 → 归一化命中 enum
        # （修复点：原样透传时 "skip。"/"Skip" 会让 decisions.py 判断失效走保守兜底）
        cases = [
            (executors.exec_decide_ocr, {"skip。": "skip", "Skip": "skip", "ocr ": "ocr", "ｓｋｉｐ": "skip"}),
            (executors.exec_decide_extract_retry, {" retry\n": "retry", "FAIL！": "fail"}),
        ]
        for fn, mapping in cases:
            for noisy, expected in mapping.items():
                with self.subTest(fn=fn.__name__, noisy=noisy):
                    r = fn(noisy, "理由")
                    self.assertEqual(r["action"], expected)
                    self.assertIsNone(r["invalid_action"])

    def test_action_invalid_falls_back_none(self):
        # 非法值 → action=None 上游保守兜底，invalid_action 保留原始值供审计
        for bad in ("maybe", "123", "skip？skip", None, 123, ""):
            with self.subTest(bad=bad):
                r = executors.exec_decide_ocr(bad, "理由")
                self.assertIsNone(r["action"])
                self.assertEqual(r["invalid_action"], bad)

    def test_reason_cleaned(self):
        # reason 清洗：去首尾空白 + 超长截断 200（防 LLM 废话污染 traces/DB）
        self.assertEqual(executors.exec_decide_ocr("skip", "理" * 250)["reason"], "理" * 200)
        self.assertEqual(executors.exec_decide_ocr("skip", "  理由  ")["reason"], "理由")
        self.assertEqual(executors.exec_decide_ocr("skip", None)["reason"], "")


if __name__ == "__main__":
    unittest.main()
