"""OCR 识别质量指标统计单测（T4.3-9）：coverage / avg_confidence / low_conf_line_ratio。

ocr_pdf_with_stats 在页级编排的同时收集识别质量指标（应识别页/成功页/行/字符/置信度），
供日志观测与低质量扫描件标记。mock _run 返回带置信度行，不触真实 OCR 模型；
ocr_pdf 契约不变（返回 {页索引: 文本}），exec_ocr_pdf 透传 stats。
unittest 风格，pytest 作 runner。
"""
import contextlib
import unittest
from unittest import mock

from app.ocr.ocr_service import OcrService
from app.tools.executors import exec_ocr_pdf


def _fake_pix(width: int = 4, height: int = 4) -> mock.MagicMock:
    pix = mock.MagicMock()
    pix.samples = b"\x00" * (width * height * 3)
    pix.width = width
    pix.height = height
    pix.n = 3
    return pix


def _fake_doc(n_pages: int) -> mock.MagicMock:
    pages = [mock.MagicMock(get_pixmap=mock.MagicMock(return_value=_fake_pix()))
             for _ in range(n_pages)]
    doc = mock.MagicMock()
    doc.__len__.return_value = n_pages
    doc.__getitem__.side_effect = lambda i: pages[i]
    return doc


def _patched(doc, run):
    """mock 掉 OCR 模型/渲染：返回 ExitStack，with 块内生效。"""
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch("app.ocr.ocr_service.OcrService.available", return_value=True))
    stack.enter_context(mock.patch("app.ocr.ocr_service.OcrService._model", return_value="model"))
    stack.enter_context(mock.patch("app.ocr.ocr_service.OcrService._run", side_effect=run))
    stack.enter_context(mock.patch("app.ocr.ocr_service.pymupdf.open", return_value=doc))
    return stack


class TestOcrQualityStats(unittest.TestCase):
    def test_mixed_pages_stats(self):
        """第 0 页高分+低分混合、第 1 页空：验证覆盖率/均值/低占比。"""
        doc = _fake_doc(2)
        calls = {"n": 0}

        def run(model, img):
            if calls["n"] == 0:
                calls["n"] += 1
                return [("清晰条款", 0.95), ("模糊噪声", 0.3)]
            calls["n"] += 1
            return []

        with _patched(doc, run):
            results, stats = OcrService.ocr_pdf_with_stats("/tmp/x.pdf")
        self.assertEqual(set(results), {0})
        self.assertEqual(stats["pages_total"], 2)
        self.assertEqual(stats["pages_ok"], 1)
        self.assertAlmostEqual(stats["coverage"], 0.5)
        self.assertEqual(stats["lines_total"], 1, "低分行 0.3 被阈值丢弃不计入")
        self.assertEqual(stats["chars_total"], len("清晰条款"))
        self.assertAlmostEqual(stats["avg_confidence"], 0.95)
        self.assertEqual(stats["low_conf_line_ratio"], 0.0, "唯一保留行 0.95 ≥ 0.8")

    def test_all_high_confidence_full_coverage(self):
        doc = _fake_doc(2)
        with _patched(doc, lambda m, i: [("第一页", 0.9)]):
            results, stats = OcrService.ocr_pdf_with_stats("/tmp/x.pdf")
        self.assertEqual(stats["coverage"], 1.0)
        self.assertEqual(stats["pages_ok"], 2)
        self.assertEqual(stats["low_conf_line_ratio"], 0.0)

    def test_low_conf_lines_counted(self):
        """0.6-0.8 之间的行保留但计入低质量占比。"""
        doc = _fake_doc(1)
        with _patched(doc, lambda m, i: [("中等质量", 0.7), ("高质量", 0.95)]):
            _, stats = OcrService.ocr_pdf_with_stats("/tmp/x.pdf")
        self.assertEqual(stats["lines_total"], 2)
        self.assertAlmostEqual(stats["avg_confidence"], 0.825)
        self.assertAlmostEqual(stats["low_conf_line_ratio"], 0.5, "0.7<0.8 计入低质量 1/2")

    def test_pages_subset_stats_only_requested(self):
        doc = _fake_doc(4)
        with _patched(doc, lambda m, i: [("内容", 0.9)]):
            _, stats = OcrService.ocr_pdf_with_stats("/tmp/x.pdf", pages=[0, 3])
        self.assertEqual(stats["pages_total"], 2, "只统计请求的页")
        self.assertEqual(stats["pages_ok"], 2)

    def test_empty_pages_no_divide_by_zero(self):
        doc = _fake_doc(3)
        with _patched(doc, lambda m, i: []):
            results, stats = OcrService.ocr_pdf_with_stats("/tmp/x.pdf", pages=[])
        self.assertEqual(results, {})
        self.assertEqual(stats["coverage"], 0.0, "空页请求覆盖率 0 不除零")

    def test_ocr_pdf_contract_unchanged(self):
        """ocr_pdf 契约不变：仍返回 {页索引: 文本}（现有调用不受影响）。"""
        doc = _fake_doc(1)
        with _patched(doc, lambda m, i: [("扫描文本", 0.9)]):
            results = OcrService.ocr_pdf("/tmp/x.pdf")
        self.assertEqual(results, {0: "扫描文本"})

    def test_exec_ocr_pdf_passthrough_stats(self):
        """executor 契约：{"pages": ..., "stats": ...}，nodes 取 ["pages"] 不受影响。"""
        doc = _fake_doc(1)
        with _patched(doc, lambda m, i: [("扫描文本", 0.9)]), \
             mock.patch("app.tools.executors.clean_text", side_effect=lambda t: t):
            out = exec_ocr_pdf("/tmp/x.pdf")
        self.assertEqual(out["pages"], {0: "扫描文本"})
        self.assertEqual(out["stats"]["pages_total"], 1)
        self.assertEqual(out["stats"]["coverage"], 1.0)


if __name__ == "__main__":
    unittest.main()
