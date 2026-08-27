"""OcrService 直接单测：页级隔离 / pages 范围 / 低置信行丢弃 / 全页失败与空页边界。

mock 掉 PaddleOCR 模型与 _run，用真实 numpy 构造位图像（pix.samples bytes → frombuffer/reshape），
不触真实 OCR 模型，验证 ocr_pdf 的页级编排逻辑。
"""
import unittest
from unittest import mock

from app.ocr.ocr_service import OcrService


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


class TestOcrService(unittest.TestCase):
    def test_pages_range_only_requested(self):
        # pages 指定时只处理这些页（混合扫描 PDF 只 OCR 扫描页）
        doc = _fake_doc(4)
        calls = []
        model = "model"

        def fake_run(model_arg, img):
            calls.append(len(img.tobytes()))
            return [("扫描内容", 0.9)]

        with mock.patch("app.ocr.ocr_service.OcrService.available", return_value=True), \
             mock.patch("app.ocr.ocr_service.OcrService._model", return_value=model), \
             mock.patch("app.ocr.ocr_service.OcrService._run", side_effect=fake_run), \
             mock.patch("app.ocr.ocr_service.pymupdf.open", return_value=doc):
            results = OcrService.ocr_pdf("/tmp/x.pdf", pages=[0, 3])
        self.assertEqual(set(results), {0, 3})
        self.assertEqual(len(calls), 2, "只渲染/识别请求的页")

    def test_pages_none_returns_all(self):
        doc = _fake_doc(2)
        with mock.patch("app.ocr.ocr_service.OcrService.available", return_value=True), \
             mock.patch("app.ocr.ocr_service.OcrService._model", return_value="model"), \
             mock.patch("app.ocr.ocr_service.OcrService._run", return_value=[("内容", 0.9)]), \
             mock.patch("app.ocr.ocr_service.pymupdf.open", return_value=doc):
            results = OcrService.ocr_pdf("/tmp/x.pdf")
        self.assertEqual(set(results), {0, 1})

    def test_low_confidence_line_dropped(self):
        # 低置信行（<0.6）丢弃：该页无可用文本 → 页缺席
        doc = _fake_doc(1)
        with mock.patch("app.ocr.ocr_service.OcrService.available", return_value=True), \
             mock.patch("app.ocr.ocr_service.OcrService._model", return_value="model"), \
             mock.patch("app.ocr.ocr_service.OcrService._run",
                        return_value=[("模糊行", 0.3), ("清晰行", 0.95)]), \
             mock.patch("app.ocr.ocr_service.pymupdf.open", return_value=doc):
            results = OcrService.ocr_pdf("/tmp/x.pdf")
        self.assertEqual(results, {0: "清晰行"}, "低置信行丢弃，仅保留阈值以上")

    def test_single_page_failure_isolated(self):
        # 逐页隔离：单页渲染/识别失败不中止整篇，该页缺席
        doc = _fake_doc(2)
        calls = [0]

        def fake_run(model_arg, img):
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("第 0 页识别崩溃")
            return [("第二页内容", 0.9)]

        with mock.patch("app.ocr.ocr_service.OcrService.available", return_value=True), \
             mock.patch("app.ocr.ocr_service.OcrService._model", return_value="model"), \
             mock.patch("app.ocr.ocr_service.OcrService._run", side_effect=fake_run), \
             mock.patch("app.ocr.ocr_service.pymupdf.open", return_value=doc):
            results = OcrService.ocr_pdf("/tmp/x.pdf")
        self.assertEqual(results, {1: "第二页内容"}, "失败页缺席，其余页正常返回")

    def test_all_pages_fail_raises(self):
        # 全部页失败 → RuntimeError（图节点置任务 FAILED，不做空文本进抽取）
        doc = _fake_doc(2)
        with mock.patch("app.ocr.ocr_service.OcrService.available", return_value=True), \
             mock.patch("app.ocr.ocr_service.OcrService._model", return_value="model"), \
             mock.patch("app.ocr.ocr_service.OcrService._run",
                        side_effect=RuntimeError("模型崩溃")), \
             mock.patch("app.ocr.ocr_service.pymupdf.open", return_value=doc):
            with self.assertRaises(RuntimeError):
                OcrService.ocr_pdf("/tmp/x.pdf")

    def test_empty_pages_returns_empty(self):
        # pages=[] 边界：空页请求返回 {}，不抛 "0/0 页"
        doc = _fake_doc(3)
        with mock.patch("app.ocr.ocr_service.OcrService.available", return_value=True), \
             mock.patch("app.ocr.ocr_service.OcrService._model", return_value="model"), \
             mock.patch("app.ocr.ocr_service.OcrService._run",
                        side_effect=RuntimeError("不应被调用")), \
             mock.patch("app.ocr.ocr_service.pymupdf.open", return_value=doc):
            results = OcrService.ocr_pdf("/tmp/x.pdf", pages=[])
        self.assertEqual(results, {})

    def test_unavailable_raises(self):
        with mock.patch("app.ocr.ocr_service.OcrService.available", return_value=False):
            with self.assertRaises(RuntimeError):
                OcrService.ocr_pdf("/tmp/x.pdf")


if __name__ == "__main__":
    unittest.main()
