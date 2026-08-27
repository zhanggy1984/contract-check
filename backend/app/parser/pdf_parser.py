"""PDF 文本提取（PyMuPDF）。"""
import pymupdf

from app.parser.text_cleaner import clean_text


def extract_pdf(path: str) -> tuple[str, list[str]]:
    """提取 PDF 文本层（页级）。

    返回 (合并文本, 每页文本列表)。扫描页（清洗后为空）由调用方按页判定——
    page_texts 是存储的单一事实来源（files.py 落 page_texts_json），
    parse_node 直接读页级数据逐页 OCR，不再全文档重新判定（混合扫描 PDF 只 OCR 空页）。

    get_text(sort=True)：按阅读顺序排序。PyMuPDF 默认按块在页面中的位置排序，
    两栏/多栏 PDF 会错序（左栏下半 + 右栏上半交替），sort=True 恢复阅读顺序。
    """
    doc = pymupdf.open(path)
    try:
        page_texts = [page.get_text(sort=True) for page in doc]
    finally:
        doc.close()
    # 输入侧统一清洗（零信息损失）：页级清洗，扫描页按页判定更准——
    # 只有零宽字符的页清洗后为空，不会误判为有文本层
    page_texts = [clean_text(t) for t in page_texts]
    # 合并文本再整体清洗（strip 首尾/压缩页间多余空行），与整篇提取语义一致
    return clean_text("\n".join(page_texts)), page_texts
