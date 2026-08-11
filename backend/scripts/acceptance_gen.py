"""端到端验收测试件生成（task.md 场景全覆盖）：各类合同 PDF + docx。
生成到 data/acceptance/。
"""
import sys
from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parents[1] / "data" / "acceptance"
OUT.mkdir(parents=True, exist_ok=True)


def pdf(name: str, text: str) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(page.rect + (40, 40, -40, -40), text, fontname="china-s", fontsize=10, lineheight=1.4)
    path = OUT / name
    doc.save(str(path))
    doc.close()
    return path


def docx(name: str, text: str) -> Path:
    from docx import Document
    doc = Document()
    for para in text.split("\n"):
        doc.add_paragraph(para)
    path = OUT / name
    doc.save(str(path))
    return path


# ---- A/B 系列合同 ----
def contract(title, parties, clauses, extra="") -> str:
    s = f"{title}\n\n"
    for p in parties:
        s += f"{p}\n"
    s += "\n"
    for c in clauses:
        s += f"{c}\n"
    s += extra
    return s


GOOD = contract(
    "设备采购与服务合同",
    ["甲方：北京华讯科技有限公司", "乙方：上海安信信息科技有限公司"],
    [
        "第一条 合同标的：甲方向乙方采购智能巡检设备 10 套，单价人民币 5 万元，总价人民币 50 万元。",
        "第二条 合同生效：本合同自双方签署之日起生效，生效日期为 2026 年 1 月 1 日。",
        "第三条 付款方式：合同签订后 7 个工作日内支付 30%，验收合格后支付 70%。",
        "第四条 违约责任：任何一方违约，应向守约方支付违约金，违约金为合同总价的 10%。",
        "第五条 争议解决：协商不成的，提交甲方所在地人民法院诉讼解决。",
    ],
    "甲方（盖章）：____________\n乙方（盖章）：____________\n",
)

B1_MISSING_DATE = contract(
    "服务合同", ["甲方：北京华讯科技有限公司", "乙方：上海安信信息科技有限公司"],
    ["第一条 服务内容：乙方提供保洁服务。", "第二条 违约责任：任何一方违约赔偿对方损失。"],
)
B1_MISSING_DATE += "甲方（盖章）：________\n"

B2_NEGATIVE = contract(
    "采购合同", ["甲方：甲公司", "乙方：乙公司"],
    [
        "第一条 采购内容：设备 A 单价人民币 -5000 元，共 2 套；设备 B 单价人民币 -3000 元，共 1 套。",
        "第二条 合同生效：生效日期为 2026 年 1 月 1 日。",
        "第三条 违约责任：任何一方违约应支付违约金。",
    ],
)

B3_BAD_TYPE = contract(
    "合作合同", ["甲方：甲公司", "乙方：乙公司"],
    [
        "第一条 合同类型：本合同为战略合作协议（类型：战略合作）。",
        "第二条 合同生效：生效日期为 2026 年 1 月 1 日。",
        "第三条 违约责任：任何一方违约应支付违约金。",
        "第四条 合同总金额：人民币 10000 元。",
    ],
)

B4_NO_PARTY_B = contract(
    "服务合同", ["甲方：甲公司"],
    [
        "第一条 服务内容：乙方提供服务。",
        "第二条 合同生效：生效日期为 2026 年 1 月 1 日。",
        "第三条 违约责任：任何一方违约应支付违约金。",
    ],
)

B5_TERMINATION = contract(
    "租赁合同", ["甲方：甲公司", "乙方：乙公司"],
    [
        "第一条 合同期限：租赁期自 2026 年 3 月 1 日起至 2026 年 1 月 15 日止。",
        "第二条 合同生效：生效日期为 2026 年 2 月 1 日。",
        "第三条 违约责任：任何一方违约应支付违约金。",
    ],
)

SHORT = "设备维修合同\n甲方：甲公司\n乙方：乙公司\n第一条 服务内容：设备维修。\n第二条 违约责任：违约赔偿。\n"

LONG_BODY = "甲方向乙方采购设备 10 台，单价 1000 元，总计 10000 元。设备须在 30 日内交付，验收合格后付款。任何一方违约应支付违约金。"
LONG = ("采购合同\n甲方：甲公司\n乙方：乙公司\n第一条 合同标的：" + LONG_BODY * 200 +
        "\n第二条 合同生效：生效日期为 2026 年 1 月 1 日。\n第三条 违约责任：任何一方违约应支付违约金。\n")


def main():
    out = []
    out.append(pdf("good.pdf", GOOD))
    out.append(docx("good.docx", GOOD))
    out.append(pdf("b1_missing_date.pdf", B1_MISSING_DATE))
    out.append(pdf("b2_negative.pdf", B2_NEGATIVE))
    out.append(pdf("b3_bad_type.pdf", B3_BAD_TYPE))
    out.append(pdf("b4_no_party_b.pdf", B4_NO_PARTY_B))
    out.append(pdf("b5_termination.pdf", B5_TERMINATION))
    out.append(pdf("short.pdf", SHORT))
    out.append(pdf("long.pdf", LONG))
    print(f"生成 {len(out)} 个测试件:", [p.name for p in out])
    print("长合同字符数:", len(LONG))


if __name__ == "__main__":
    main()
