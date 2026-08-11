"""生成扫描型测试 PDF（无文本层）：中文合同 → 渲染为位图 → 重建纯图 PDF（T4.1 E2E 验收用）。"""
import sys
from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parents[1] / "data" / "scanned_test.pdf"

CONTRACT = """设备采购与服务合同

甲方：北京某科技有限公司
乙方：上海某信息技术有限公司

第一条 合同标的
甲方向乙方采购智能巡检设备 10 套，单价人民币 5 万元，总价人民币 50 万元。

第二条 付款方式
合同签订后 7 个工作日内，甲方支付合同总价的 30% 作为预付款；
设备验收合格后 30 个工作日内，甲方支付剩余 70% 尾款。

第三条 交货与验收
乙方应于合同签订后 45 日内完成交付；双方在交付现场共同验收，验收合格后签署验收单。

第四条 违约责任
任何一方违约，应向守约方支付违约金，违约金金额为合同总价的 5%。

第五条 争议解决
因本合同引起的争议，双方应友好协商解决；协商不成的，提交甲方所在地人民法院诉讼解决。

甲方（盖章）：____________        乙方（盖章）：____________
日期：2026 年 8 月 11 日
"""


def make_scanned_pdf(out: Path, text: str, dpi: int = 200) -> None:
    """两阶段：文本 PDF → 每页渲染位图 → 重建纯图 PDF（无文本层）。"""
    src = pymupdf.open()
    page = src.new_page(width=595, height=842)  # A4
    rect = page.rect
    # 内置简体中文字体，避免依赖系统字体
    page.insert_textbox(rect + (40, 40, -40, -40), text, fontname="china-s", fontsize=11, lineheight=1.5)

    out.parent.mkdir(parents=True, exist_ok=True)
    dst = pymupdf.open()
    try:
        for p in src:
            pix = p.get_pixmap(dpi=dpi)
            npage = dst.new_page(width=p.rect.width, height=p.rect.height)
            npage.insert_image(npage.rect, pixmap=pix)
    finally:
        src.close()
    dst.save(str(out))
    dst.close()
    print(f"已生成扫描 PDF: {out} ({out.stat().st_size} 字节)")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    make_scanned_pdf(out, CONTRACT)
