"""Word 文本提取（python-docx，仅 .docx）。"""
import docx


def extract_docx(path: str) -> str:
    """提取 .docx 段落文本。"""
    doc = docx.Document(path)
    return "\n".join(p.text for p in doc.paragraphs)
