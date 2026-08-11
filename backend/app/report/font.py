"""中文字体解析（T4.4）：REPORT_FONT 环境变量优先，否则系统字体路径兜底。

容器部署（T4.5）bundle 开源字体后设 REPORT_FONT；本机 Windows 直接用系统字体。
reportlab 的 TTFont 支持 .ttf 与 .ttc（取集合内第一个字体面）。
"""
import os
import threading
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# 依次探测：env 指定 → Windows 系统字体 → Linux 常见容器字体路径
CANDIDATES = [
    os.environ.get("REPORT_FONT"),
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simsun.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/wqy/wqy-zenhei.ttc",
]

FONT_NAME = "CJK"
_lock = threading.Lock()
_registered = False


def _first_existing() -> str | None:
    for c in CANDIDATES:
        if c and Path(c).exists():
            return c
    return None


def get_font_path() -> str:
    """返回可用中文字体路径；找不到抛异常——报告渲染不出中文宁可明确失败。"""
    path = _first_existing()
    if path is None:
        raise RuntimeError("未找到中文字体，请设置 REPORT_FONT 环境变量指向 TTF/TTC 文件")
    return path


def ensure_registered() -> str:
    """注册中文字体到 reportlab，返回字体名（幂等，线程安全）。"""
    global _registered
    if not _registered:
        with _lock:
            if not _registered:
                pdfmetrics.registerFont(TTFont(FONT_NAME, get_font_path()))
                _registered = True
    return FONT_NAME
