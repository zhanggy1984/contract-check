"""输入文本清洗：零信息损失的规范化，保证进入 LLM / 落盘的文本干净一致。

定位：防御性优化。真实世界 PDF（旧软件导出、两栏排版、OCR 文本）常携带噪声——
BOM、零宽字符（U+200B/C/D）、NBSP、全角/半角混用、控制字符、连续空行——
这些会打断 evidence 精确子串匹配（零宽字符破坏"逐字一致"，全角数字/字母与
LLM 抽取的半角无法对齐）、污染抽取输入。此处只做 Unicode 规范化 + 空白整理，
零信息损失，与 semantic_evaluator.normalize（NFKC+去空白）协同。

刻意不做页眉/页脚/页码重复短行去重：那是启发式有损清洗，long_contract.pdf 实证
"交付地点为甲方指定仓库…"这类重复短行是合法正文条款（150 次），误删会破坏抽取
（good-question solution 13.3 的教训：拍脑袋优化会倒退）。
"""
import re
import unicodedata

# NFKC 会把中文标点（，。！？等全角形式）一并转半角，破坏中文排版，故跳过
_CHINESE_PUNCT = set("，。！？；：、（）《》「」『』【】“”‘’—…")

# 不可见噪声字符用 chr() 构造，避免源码内嵌不可见字符导致编辑/复制出错
_BOM = chr(0xFEFF)                  # 零宽不换行（文件头 BOM / 行中残留）
_ZERO_WIDTH = (0x200B, 0x200C, 0x200D)  # 零宽空格/不连字/连接符
_NBSP = chr(0xA0)                   # 不换行空格
_FULLWIDTH_SPACE = chr(0x3000)      # 全角空格


def clean_text(text: str) -> str:
    """规范化抽取文本，保持段落结构（\n）。空文本返回空串。"""
    if not text:
        return ""

    # 1. 去 BOM（行首）
    text = text.lstrip(_BOM)

    # 2. 统一换行符（\r\n / \r → \n），否则跨 \r 的段落在 split/匹配时会错位
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 3. 全角转半角（NFKC：ＡＢＣ→ABC、１２３→123、＠→@），跳过中文标点保护排版
    text = "".join(
        unicodedata.normalize("NFKC", ch) if ch not in _CHINESE_PUNCT else ch
        for ch in text
    )

    # 4. 移除控制字符（保留换行和制表符）
    text = "".join(ch for ch in text if ch >= " " or ch in "\n\t")

    # 5. 不可见噪声：零宽字符删除（不可见但会打断 evidence 逐字匹配与中文分词）；
    #    行中 BOM 删除（行首的已在第 1 步处理）
    for cp in _ZERO_WIDTH:
        text = text.replace(chr(cp), "")
    text = text.replace(_BOM, "")

    # 6. NBSP/全角空格 转普通空格，避免"看似连续"的词被空格打断
    text = text.replace(_NBSP, " ")
    text = text.replace(_FULLWIDTH_SPACE, " ")

    # 7. 压缩连续空行为最多两个换行（保留段落分隔）
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 8. 行尾多余空白
    text = re.sub(r"[ \t]+$", "", text, flags=re.M)

    return text.strip()
