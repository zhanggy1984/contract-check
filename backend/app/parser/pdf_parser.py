"""PDF 文本提取（PyMuPDF）。"""
import pymupdf

from app.parser.text_cleaner import clean_text


def extract_pdf(path: str) -> tuple[str, bool]:
    """提取 PDF 文本层。

    返回 (文本, has_scanned)；has_scanned=True 表示无文本层，后续需走 OCR。
    """
    doc = pymupdf.open(path)
    try:
        text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()
    # 输入侧统一清洗（零信息损失）：真实 PDF 常有零宽字符/BOM/全角混用等噪声，
    # 清洗后再判 has_scanned 更准——只有零宽字符的 PDF 不会误判为有文本层
    text = clean_text(text)
    return text, not text.strip()
