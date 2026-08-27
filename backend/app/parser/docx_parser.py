"""Word 文本提取（python-docx，仅 .docx）。"""
import docx
from docx.oxml.ns import qn
from docx.table import Table

from app.parser.text_cleaner import clean_text


def _extract_textboxes(doc) -> list[str]:
    """正文文本框（w:txbxContent）内容。文本框可能承载"备注/附件"类条款。

    页眉页脚刻意不提取：公司名/页码等重复装饰信息进正文会污染 contractTitle/条款抽取。
    """
    parts = []
    for txbx in doc.element.body.iter(qn("w:txbxContent")):
        text = "".join(t.text or "" for t in txbx.iter(qn("w:t")))
        if text.strip():
            parts.append(text.strip())
    return parts


def extract_docx(path: str) -> str:
    """提取 .docx 段落 + 表格 + 文本框文本。

    doc.paragraphs 不含表格内段落——合同的价格表/设备清单/付款计划等关键内容载体
    会整块丢失；iter_inner_content 按文档顺序取段落/表格交替块保证阅读顺序，
    表格逐 cell 提取。页眉页脚不提取（重复装饰信息进正文会污染抽取）。
    """
    doc = docx.Document(path)
    parts: list[str] = []
    for block in doc.iter_inner_content():
        if isinstance(block, Table):
            for row in block.rows:
                for cell in row.cells:
                    t = cell.text.strip()
                    if t:
                        parts.append(t)
        else:  # Paragraph
            t = block.text.strip()
            if t:
                parts.append(t)
    parts.extend(_extract_textboxes(doc))
    return clean_text("\n".join(parts))
