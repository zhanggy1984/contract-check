"""PDF 文本提取（PyMuPDF）。"""
import pymupdf


def extract_pdf(path: str) -> tuple[str, bool]:
    """提取 PDF 文本层。

    返回 (文本, has_scanned)；has_scanned=True 表示无文本层，后续需走 OCR。
    """
    doc = pymupdf.open(path)
    try:
        text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()
    return text, not text.strip()
