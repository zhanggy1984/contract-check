"""parser 清洗接入单测：extract_pdf/extract_docx 输出已清洗，零宽 PDF 不误判文本层。"""
import unittest
from unittest import mock

from app.parser.docx_parser import extract_docx
from app.parser.pdf_parser import extract_pdf


def _s(*cps: int) -> str:
    return "".join(chr(cp) for cp in cps)


class TestExtractPdfClean(unittest.TestCase):
    def _fake_doc(self, pages_text):
        """构造可迭代的伪 pymupdf doc（每页 get_text 返回固定文本）。"""
        pages = [mock.MagicMock(get_text=lambda t=t: t) for t in pages_text]
        doc = mock.MagicMock()
        doc.__iter__.return_value = iter(pages)
        return doc

    @mock.patch("app.parser.pdf_parser.pymupdf.open")
    def test_zero_width_only_pdf_flagged_scanned(self, m_open):
        # 只有零宽字符的 PDF：清洗后为空 → has_scanned=True（修复：不再误判有文本层）
        m_open.return_value = self._fake_doc([_s(0x200B, 0x200B), _s(0x200C)])
        text, has_scanned = extract_pdf("/tmp/x.pdf")
        self.assertEqual(text, "")
        self.assertTrue(has_scanned)

    @mock.patch("app.parser.pdf_parser.pymupdf.open")
    def test_cleans_extracted_text(self, m_open):
        # 全角/零宽/多余空行在提取时统一清洗
        m_open.return_value = self._fake_doc(["　甲方Ａ" + _s(0x200B) + "条款\r\n", "\n\n\n乙方"])
        text, has_scanned = extract_pdf("/tmp/x.pdf")
        self.assertEqual(text, "甲方A条款\n\n乙方")
        self.assertFalse(has_scanned)


class TestExtractDocxClean(unittest.TestCase):
    @mock.patch("app.parser.docx_parser.docx.Document")
    def test_cleans_extracted_text(self, m_doc):
        m_doc.return_value.paragraphs = [
            mock.MagicMock(text=_s(0xFEFF) + "甲方"),
            mock.MagicMock(text=_s(0x200B) + "乙方"),
            mock.MagicMock(text=""),
        ]
        self.assertEqual(extract_docx("/tmp/x.docx"), "甲方\n乙方")


if __name__ == "__main__":
    unittest.main()
