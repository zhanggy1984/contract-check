# -*- coding: utf-8 -*-
"""生成面试演示用各场景合同 PDF 到 data/test-contracts/（对应 solution.md 验收场景）。"""
import os
import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "test-contracts")
os.makedirs(OUT, exist_ok=True)

# 中文字体候选（跨平台）：默认 Helvetica 不支持中文，文本层会变成 ??????
# （曾导致上传后抽取输入乱码）；找不到时给出清晰报错而非神秘崩溃。
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Debian/Ubuntu
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",       # Fedora/Arch
    "/System/Library/Fonts/PingFang.ttc",                      # macOS
    "/System/Library/Fonts/STHeiti Light.ttc",                 # macOS 旧版
]


def _find_font():
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    raise RuntimeError(
        "未找到中文字体，请安装 Noto Sans CJK / 黑体等中文字体后重试，"
        "或修改脚本 FONT_CANDIDATES 列表加入你的字体路径"
    )

BASE = """购销合同

甲方：北京华云科技有限公司
统一社会信用代码：91110108MA01XXXXXX
乙方：上海中远贸易有限公司
统一社会信用代码：91310000MA1XXXXXXX

第一条 合同标的
乙方供应甲方服务器设备一批，总金额人民币 100000 元（含税）。

第二条 合同类型
本合同为采购合同，适用《中华人民共和国民法典》相关规定。

第三条 生效日期
本合同自 2026 年 1 月 1 日起生效，终止日期为 2026 年 12 月 31 日。

第四条 付款方式
货到验收合格后 30 日内，甲方向乙方支付全部合同价款。

第五条 交货期限
乙方应于本合同生效后 45 日内完成交货。

第六条 违约责任
任何一方违约应赔偿对方由此遭受的全部损失，违约金为合同总价的 10%。

第七条 争议解决
因本合同引起的争议，双方协商解决；协商不成的，提交甲方所在地人民法院诉讼解决。

第八条 保密条款
双方应对本合同内容及履行过程中知悉的对方商业秘密严格保密。

第九条 不可抗力
因不可抗力导致本合同无法履行的，受影响方应在不可抗力发生之日起 7 日内书面通知对方。

第十条 合同解除
经双方协商一致，可解除本合同；一方违约导致合同目的无法实现的，守约方有权解除合同。

第十一条 通知条款
双方往来通知应以书面形式送达对方注册地址。

甲方（盖章）：北京华云科技有限公司    乙方（盖章）：上海中远贸易有限公司
签订日期：2026 年 1 月 1 日
"""


def _drop_section(text, title):
    """删除含指定标题的条款段（含标题行）。"""
    lines = text.split("\n")
    out, skip = [], False
    for ln in lines:
        if title in ln:
            skip = True
            continue
        if skip and ln.strip().startswith(("第", "甲方（盖章", "签订")):
            skip = False
        if skip:
            continue
        out.append(ln)
    return "\n".join(out)


# 合规合同专用文本：T1 模板（实测 DeepSeek 稳定抽对 title/type/amount/date/party）
GOOD_TEXT = """设备采购合同

甲方：北京华云科技有限公司
乙方：上海中远贸易有限公司

一、合同标的
乙方向甲方供应台式电脑 100 台，单价 1000 元，总金额 100000 元。

二、合同类型
采购。

三、生效日期
2026 年 1 月 1 日起生效。

四、违约责任
任何一方违约应赔偿对方全部损失，违约金为合同总价的 10%。

五、争议解决
双方协商解决。

甲方（盖章）：北京华云科技有限公司
乙方（盖章）：上海中远贸易有限公司
签订日期：2026 年 1 月 1 日
"""


# 各场景：文件名 → 合同文本
SCENARIOS = {
    # A1 合规合同（零/少量 violation）
    "good": GOOD_TEXT,
    # B1 缺生效日期 → required FAIL
    "b1_missing_date": _drop_section(BASE, "第三条"),
    # B2 金额为负 → min FAIL
    "b2_negative_amount": BASE.replace("100000", "-50000"),
    # B3 合同类型越界 → enum FAIL
    "b3_bad_type": BASE.replace("采购合同", "合作共赢合同"),
    # B4 缺乙方主体 → 人工规则 FAIL
    "b4_missing_party_b": _drop_section(BASE, "乙方：") + "\n乙方（盖章）：__________",
    # B5 终止日期早于生效日期 → 人工规则 FAIL
    "b5_termination_before_effective": BASE.replace("2026 年 12 月 31 日", "2025 年 12 月 31 日"),
    # B6 缺违约条款（语义 aggregation=all）→ FAIL
    "b6_missing_breach_clause": _drop_section(BASE, "第六条"),
    # B7 权利义务不对等（语义，low-confidence）→ FAIL
    "b7_unbalanced_obligations": BASE.replace(
        "第六条 违约责任\n任何一方违约应赔偿对方由此遭受的全部损失，违约金为合同总价的 10%。",
        "第六条 义务条款\n甲方应全额支付货款、承担全部运输与安装费用、无条件接受乙方提出的任何变更要求；乙方不承担任何义务，仅在甲方违约时有权解除合同。"),
    # B8 规则不适用（纯服务合同，无技术标准引用，语义 applicable=false → SKIPPED）
    "b8_service_contract": "技术服务合同\n\n甲方：北京华云科技有限公司\n乙方：上海中远贸易有限公司\n\n第一条 服务内容\n乙方向甲方提供为期 12 个月的设备运维咨询服务。\n\n第二条 服务费用\n服务费共计人民币 50000 元，甲方应于服务完成后 30 日内支付。\n\n第三条 生效日期\n本合同自 2026 年 1 月 1 日起生效。\n\n第四条 争议解决\n因本合同引起的争议，双方协商解决。\n\n甲方（盖章）：北京华云科技有限公司    乙方（盖章）：上海中远贸易有限公司\n签订日期：2026 年 1 月 1 日",
    # 长合同（>20k 字符，触发分段抽取）
    "long_contract": BASE + "\n\n" + "\n".join(
        f"第{i}条 附加条款\n乙方额外提供设备型号 MODEL-{i:03d} 一批，单价人民币 {1000 + i * 37} 元，数量 {i} 台，质保期自验收合格之日起 {12 + i % 24} 个月，交货地点为甲方指定仓库，运费由乙方承担。"
        for i in range(12, 250)
    ),
}


def _render_pdf(path, text, fontsize=10):
    """逐行渲染 + 自动分页：长合同（long_contract 24k 字符）放不下会换页继续，
    保证文本层完整。不能用 insert_textbox（固定矩形，放不下会静默裁剪文本，
    曾导致 long_contract 文本层不足 20k，A5 分段抽取场景不触发）。"""
    doc = pymupdf.open()
    page = doc.new_page()
    fontfile = _find_font()
    page.insert_font(fontname="cn", fontfile=fontfile)
    font = pymupdf.Font(fontfile=fontfile)

    margin = 40
    maxw = page.rect.width - 2 * margin
    line_h = fontsize * 1.5
    bottom = page.rect.height - margin
    y = margin

    def new_page_if_needed():
        nonlocal page, y
        if y + line_h > bottom:
            page = doc.new_page()
            page.insert_font(fontname="cn", fontfile=fontfile)
            y = margin

    for raw in text.split("\n"):
        if not raw:
            new_page_if_needed()
            y += line_h
            continue
        # 折行：逐字累积到超宽才换行，避免长句溢出页面
        buf = ""
        for ch in raw:
            if font.text_length(buf + ch, fontsize=fontsize) <= maxw:
                buf += ch
            else:
                new_page_if_needed()
                page.insert_text((margin, y), buf, fontsize=fontsize, fontname="cn")
                y += line_h
                buf = ch
        if buf:
            new_page_if_needed()
            page.insert_text((margin, y), buf, fontsize=fontsize, fontname="cn")
            y += line_h

    doc.save(path)
    doc.close()


for name, text in SCENARIOS.items():
    _render_pdf(os.path.join(OUT, f"{name}.pdf"), text)
    print(f"{name}.pdf  len={len(text)}")

# 扫描件：无文本层的图片型 PDF（触发 OCR）
scanned_text = "购销合同\n\n甲方：北京华云科技有限公司\n乙方：上海中远贸易有限公司\n\n第一条 合同标的\n乙方供应甲方服务器设备，总金额人民币 100000 元。\n\n第二条 生效日期\n本合同自 2026 年 1 月 1 日起生效。\n\n甲方（盖章）：北京华云科技有限公司    乙方（盖章）：上海中远贸易有限公司\n签订日期：2026 年 1 月 1 日"
from PIL import Image, ImageDraw, ImageFont
import tempfile
img = Image.new("RGB", (900, 1400), "white")
d = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype(_find_font(), 24)
except Exception:
    font = ImageFont.load_default()
d.text((60, 60), scanned_text, font=font, fill="black")
tmp = os.path.join(tempfile.gettempdir(), "scan.png")
img.save(tmp)
doc = pymupdf.open()
page = doc.new_page()
page.insert_image(page.rect, filename=tmp)
doc.save(os.path.join(OUT, "scanned.pdf"))
doc.close()
print("scanned.pdf  (图片型，无文本层)")

# README：场景说明
README = """# 面试演示合同库

各场景对应 solution.md §13 验收点，供演示时上传触发对应校验结果：

| 文件 | 场景 | 预期校验结果 |
|---|---|---|
| good.pdf | 合规合同 | 零/少量 violation → SUCCESS |
| b1_missing_date.pdf | 缺生效日期 | 必填 FAIL（required） |
| b2_negative_amount.pdf | 合同金额为负 | 数值下限 FAIL（min） |
| b3_bad_type.pdf | 合同类型越界 | 枚举 FAIL（⚠️ LLM 可能把"合作共赢"映射到枚举值"合作"，此时不报） |
| b4_missing_party_b.pdf | 缺乙方主体 | 人工规则 FAIL（⚠️ LLM 可能从"乙方（盖章）"推断乙方存在，此时不报） |
| b5_termination_before_effective.pdf | 终止早于生效 | 人工规则 FAIL |
| b6_missing_breach_clause.pdf | 缺违约责任条款 | 语义 FAIL（aggregation=all） |
| b7_unbalanced_obligations.pdf | 权利义务不对等 | 语义 FAIL（evidence 命中原文 → 高置信） |
| b8_service_contract.pdf | 纯服务合同无标准引用 | 技术标准规则 SKIPPED；但无违约条款 → 语义 FAIL |
| long_contract.pdf | 超 20k 字符 | 分段抽取合并（A5） |
| scanned.pdf | 扫描件（无文本层） | 触发 OCR（A3） |

演示建议：先传 good.pdf 展示全流程走通；再传 b1/b2/b6/b7 展示异常进入人工审核闭环与置信度标注；
scanned.pdf 展示 OCR 能力；long_contract.pdf 展示分段抽取与冲突低置信。
"""
with open(os.path.join(OUT, "README.md"), "w", encoding="utf-8") as f:
    f.write(README)
print("README.md 已生成")
