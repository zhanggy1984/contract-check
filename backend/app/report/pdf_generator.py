"""PDF 校验报告生成（T4.4）：reportlab + 中文字体，长文本用 Paragraph 换行。"""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from app.report.font import FONT_NAME, ensure_registered
from app.report.report_data import ReportData

PRIMARY = colors.HexColor("#1F4E79")
WARN_BG = colors.HexColor("#FDE9E9")
SKIP_BG = colors.HexColor("#F4F4F4")
GREY = colors.grey

PAGE_W, PAGE_H = A4
MARGIN = 40
USABLE = PAGE_W - 2 * MARGIN


def _styles() -> dict:
    return {
        "title": ParagraphStyle("title", fontName=FONT_NAME, fontSize=18, leading=24,
                                alignment=1, textColor=PRIMARY, spaceAfter=4),
        "subtitle": ParagraphStyle("subtitle", fontName=FONT_NAME, fontSize=9, leading=13,
                                   alignment=1, textColor=GREY, spaceAfter=12),
        "h2": ParagraphStyle("h2", fontName=FONT_NAME, fontSize=12, leading=16,
                             textColor=PRIMARY, spaceBefore=14, spaceAfter=6),
        "body": ParagraphStyle("body", fontName=FONT_NAME, fontSize=9, leading=13,
                               wordWrap="CJK"),
        "label": ParagraphStyle("label", fontName=FONT_NAME, fontSize=9, leading=13,
                                wordWrap="CJK", textColor=GREY),
        "cell": ParagraphStyle("cell", fontName=FONT_NAME, fontSize=8.5, leading=12,
                               wordWrap="CJK"),
        "evidence": ParagraphStyle("evidence", fontName=FONT_NAME, fontSize=8.5, leading=12,
                                   wordWrap="CJK", backColor=colors.white),
    }


def _kv_table(rows: list[tuple[str, str]], st: dict) -> Table:
    """两列键值表：左侧灰标签，右侧值。"""
    data = [[Paragraph(k, st["label"]), Paragraph(v, st["cell"])] for k, v in rows]
    t = Table(data, colWidths=[USABLE * 0.3, USABLE * 0.7])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def _entity_block(title: str, rows: list[list[tuple[str, str]]], st: dict) -> list:
    """当事人/标的物区块：每项一个浅底小表格。"""
    elems = []
    for i, fields in enumerate(rows, start=1):
        head = Paragraph(f"{title} {i}", st["h2"])
        t = _kv_table(fields, st)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FAFAFA")),
                               ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD"))]))
        elems.extend([head, t])
    return elems


def render(data: ReportData) -> BytesIO:
    """渲染 PDF 到 BytesIO。"""
    ensure_registered()
    st = _styles()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=MARGIN, bottomMargin=MARGIN,
                            title=f"合同校验报告-{data.task_id}",
                            author="合同校验系统")
    story: list = []

    # 标题
    story.append(Paragraph("合同校验报告", st["title"]))
    story.append(Paragraph(
        f"任务号 {data.task_id} ｜ 生成时间 {data.create_time or ''}", st["subtitle"]))

    # 基本信息
    story.append(Paragraph("一、基本信息", st["h2"]))
    base_rows = [
        ("合同文件", data.file_name),
        ("文件类型", data.file_type),
        ("文件大小", data.file_size),
        ("是否扫描件", "是（已 OCR）" if data.has_scanned and data.ocr_applied
         else "是（未 OCR）" if data.has_scanned else "否（含文本层）"),
        ("任务状态", data.task_status),
        ("抽取状态", data.extraction_status or ""),
        ("LLM 模型", data.llm_model or ""),
    ]
    story.append(_kv_table(base_rows, st))

    # 抽取摘要
    story.append(Paragraph("二、抽取摘要", st["h2"]))
    if data.summary:
        story.append(_kv_table(data.summary, st))
    if not data.summary and not data.parties and not data.items:
        story.append(Paragraph("（无抽取数据）", st["body"]))
    story.extend(_entity_block("当事人", data.parties, st))
    story.extend(_entity_block("标的明细", data.items, st))

    # 校验明细
    story.append(Paragraph("三、校验明细", st["h2"]))
    if data.rule_results:
        story.append(_rule_table(data, st))
    else:
        story.append(Paragraph("（无校验明细）", st["body"]))

    # 异常明细
    story.append(Paragraph("四、异常明细", st["h2"]))
    if data.violations:
        for v in data.violations:
            story.extend(_violation_block(v, st))
    else:
        story.append(Paragraph("未发现异常。", st["body"]))

    doc.build(story)
    buf.seek(0)
    return buf


def _rule_table(data: ReportData, st: dict) -> Table:
    header = ["规则", "类型", "结果", "严重级别", "置信度", "段落", "说明"]
    rows = [[Paragraph(h, st["label"]) for h in header]]
    for r in data.rule_results:
        rows.append([
            Paragraph(f"{r['rule_name']}", st["cell"]),
            Paragraph(r["rule_type"], st["cell"]),
            Paragraph(r["result"], st["cell"]),
            Paragraph(r["severity"], st["cell"]),
            Paragraph(r["confidence"], st["cell"]),
            Paragraph(r["segment_ref"], st["cell"]),
            Paragraph(r["message"] or "", st["cell"]),
        ])
    t = Table(rows, colWidths=[USABLE * 0.22, USABLE * 0.08, USABLE * 0.08,
                               USABLE * 0.09, USABLE * 0.08, USABLE * 0.09, USABLE * 0.36],
              repeatRows=1)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, 0), (-1, 0), FONT_NAME),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F9FC")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    # 结果列按值着色（行内第 2 列是"结果"）
    for i, r in enumerate(data.rule_results, start=1):
        bg = WARN_BG if r["result"] == "异常" else SKIP_BG if r["result"] == "跳过" else colors.white
        style.append(("BACKGROUND", (2, i), (2, i), bg))
    t.setStyle(TableStyle(style))
    return t


def _violation_block(v: dict, st: dict) -> list:
    rows = [
        ("规则", f"{v['rule_name']}（{v['rule_id']}）"),
        ("类型 / 严重级别", f"{v['rule_type']} / {v['severity']}"),
        ("置信度", v["confidence"]),
        ("段落", v["segment_ref"]),
        ("审核状态", f"{v['status']}  {v['confirm_user']} {v['confirm_time']}".strip()),
    ]
    rows = [(k, val.strip()) for k, val in rows if val.strip()]

    block = [_kv_table(rows, st)]
    if v["message"]:
        block.append(Spacer(1, 4))
        block.append(Paragraph(f"问题说明：{v['message']}", st["cell"]))
    if v["evidence_text"]:
        block.append(Spacer(1, 4))
        ev = Paragraph(f"原文证据：{v['evidence_text']}", st["evidence"])
        evt = Table([[ev]], colWidths=[USABLE])
        evt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF7E6")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E6D9B8")),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        block.append(evt)
    block.append(Spacer(1, 8))
    return block
