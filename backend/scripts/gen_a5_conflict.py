"""临时生成 A5 金额冲突长合同测试件（验收后删除）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf

body = "甲方向乙方采购设备 10 台，单价 1000 元，总计 10000 元。设备须在 30 日内交付，验收合格后付款。任何一方违约应支付违约金。"
TEXT = (
    "采购合同\n\n甲方：甲公司\n乙方：乙公司\n\n"
    "第一条 合同标的：" + body * 400 + "\n"
    "第二条 合同总价：本合同总金额为人民币 20000 元整。\n"
    "第三条 合同生效：生效日期为 2026 年 1 月 1 日。\n"
)

import textwrap

doc = pymupdf.open()
# 大文本手动分行写入（insert_text 不自动换行、insert_textbox 超框丢弃文本 → 均致文本层缺失）
page = doc.new_page(width=595, height=842)
y = 50
for raw in TEXT.split("\n"):
    for seg in textwrap.wrap(raw, width=55) or [""]:
        page.insert_text((40, y), seg, fontname="china-s", fontsize=9)
        y += 14
        if y > 800:
            page = doc.new_page(width=595, height=842)
            y = 50
path = Path("data/acceptance/a5_conflict.pdf")
doc.save(str(path))
doc.close()
print("generated", path, "chars:", len(TEXT))
