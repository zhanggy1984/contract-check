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
    def test_text_returned(self, m):
        m.ocr_pdf.return_value = "扫描识别文本"
        self.assertEqual(executors.exec_ocr_pdf("/tmp/x.pdf"), {"text": "扫描识别文本"})

    @mock.patch("app.tools.executors.OcrService")
    def test_exception_propagates(self, m):
        m.ocr_pdf.side_effect = RuntimeError("PaddleOCR 未安装")
        with self.assertRaises(RuntimeError):
            executors.exec_ocr_pdf("/tmp/x.pdf")


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
        self.assertEqual(executors.exec_decide_ocr("ocr", "文本层可读"), {"action": "ocr", "reason": "文本层可读"})

    def test_exec_decide_ocr_reason_empty_default(self):
        self.assertEqual(executors.exec_decide_ocr("skip", None), {"action": "skip", "reason": ""})

    def test_exec_decide_extract_retry_normalizes(self):
        self.assertEqual(
            executors.exec_decide_extract_retry("fail", "文本过短"),
            {"action": "fail", "reason": "文本过短"},
        )


if __name__ == "__main__":
    unittest.main()
