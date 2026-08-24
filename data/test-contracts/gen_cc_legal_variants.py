# -*- coding: utf-8 -*-
"""7.8 薄弱点①延伸：single_party 规则对「合法签署形态」的误报验证。

薄弱点①最可能失败模式 = good 只覆盖「双方盖章+签字」标准形态，电子签章/仅签名等合法
格式可能被误判为单方签署。生成 7 个 PDF（正文统一、仅签署区形态不同）：

合法形态（期望 0 违规，SUCCESS）：
  cc_gen_l1_esign        双方电子签章
  cc_gen_l2_sign_only    双方仅签字、无盖章
  cc_gen_l3_seal_sign    双方盖章 + 签字双栏
  cc_gen_l4_no_colon     （盖章）后直接主体、无冒号
  cc_gen_l5_gongzhang    双方（公章）
  cc_gen_l6_mixed_legal  甲方盖章、乙方签字（双方均实签）
检出对照（期望检出 single_party）：
  cc_gen_l7_esign_missing  甲方电子签章、乙方签署区整体缺失
"""
import os

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

OUT = os.path.dirname(os.path.abspath(__file__))
pdfmetrics.registerFont(TTFont("SimHei", r"C:\Windows\Fonts\SimHei.ttf"))

BODY = [
    "技术服务与设备采购合同",
    "",
    "甲方：北京云途科技有限公司",
    "统一社会信用代码：91110108MA01XXXXXX",
    "",
    "乙方：上海品控贸易有限公司",
    "统一社会信用代码：91310115MA1KYYYYYY",
    "",
    "第一条 合同标的",
    "乙方供应甲方所需设备一批，合同总金额 100000 元（含税）。",
    "第二条 合同依据",
    "本合同为采购合同，适用《中华人民共和国民法典》相关规定。",
    "第三条 有效期间",
    "本合同自 2026 年 1 月 1 日起生效，终止日期为 2026 年 12 月 31 日。",
    "第四条 付款方式",
    "甲方验收合格后 30 日内，甲方向乙方支付全部合同价款。",
    "第五条 交付方式",
    "乙方应在本合同生效后 45 日内完成交付。",
    "第六条 违约责任",
    "任何一方违约应赔偿对方由此受到的全部损失，违约金为合同总价的 10%。",
    "第七条 争议解决",
    "因本合同产生纠纷，双方协商解决；协商不成的，提交甲方所在地人民法院诉讼管辖。",
    "第八条 保密义务",
    "双方应对本合同内容及获悉的对方商业秘密严格保密。",
    "第九条 不可抗力",
    "因不可抗力导致本合同无法履行的，受影响方应在不可抗力发生之日起 7 日内书面通知对方。",
    "第十条 合同解除",
    "经双方协商一致，可解除本合同；一方违约致合同目的无法实现的，守约方有权解除合同。",
    "第十一条 通知送达",
    "双方往来通知应以书面形式送达对方注册地址。",
    "",
    "（以下无正文，为本合同签署区）",
]

SIGNATURES = {
    "cc_gen_l1_esign": [
        "甲方（电子签章）：北京云途科技有限公司",
        "乙方（电子签章）：上海品控贸易有限公司",
        "签订日期：2026 年 6 月 1 日",
    ],
    "cc_gen_l2_sign_only": [
        "甲方（签字）：王强",
        "乙方（签字）：李敏",
        "签订日期：2026 年 6 月 1 日",
    ],
    "cc_gen_l3_seal_sign": [
        "甲方（盖章）：北京云途科技有限公司",
        "甲方（签字）：王强",
        "乙方（盖章）：上海品控贸易有限公司",
        "乙方（签字）：李敏",
        "签订日期：2026 年 6 月 1 日",
    ],
    "cc_gen_l4_no_colon": [
        "甲方（盖章）北京云途科技有限公司",
        "乙方（盖章）上海品控贸易有限公司",
        "签订日期：2026 年 6 月 1 日",
    ],
    "cc_gen_l5_gongzhang": [
        "甲方（公章）：北京云途科技有限公司",
        "乙方（公章）：上海品控贸易有限公司",
        "签订日期：2026 年 6 月 1 日",
    ],
    "cc_gen_l6_mixed_legal": [
        "甲方（盖章）：北京云途科技有限公司",
        "乙方（签字）：李敏",
        "签订日期：2026 年 6 月 1 日",
    ],
    "cc_gen_l7_esign_missing": [
        "甲方（电子签章）：北京云途科技有限公司",
        "签订日期：2026 年 6 月 1 日",
    ],
}

STYLE = ParagraphStyle("cn", fontName="SimHei", fontSize=11, leading=18)
STYLE_TITLE = ParagraphStyle("title", fontName="SimHei", fontSize=15,
                             leading=24, alignment=1, spaceAfter=10)


def build(fname: str, sig_lines: list[str]) -> None:
    doc = SimpleDocTemplate(os.path.join(OUT, f"{fname}.pdf"), pagesize=A4,
                            topMargin=20 * mm, bottomMargin=20 * mm,
                            leftMargin=25 * mm, rightMargin=25 * mm)
    story = [Paragraph(BODY[0], STYLE_TITLE)]
    for line in BODY[1:]:
        story.append(Paragraph(line if line else "&nbsp;", STYLE))
    story.append(Spacer(1, 24))
    story.append(Paragraph("签署区", STYLE))
    for line in sig_lines:
        story.append(Paragraph(line if line else "&nbsp;", STYLE))
    doc.build(story)
    print(f"生成 {fname}.pdf（签署区 {len(sig_lines)} 行）")


if __name__ == "__main__":
    for name, lines in SIGNATURES.items():
        build(name, lines)
