"""输入清洗单测：逐规则验证 clean_text，重点保护中文标点与零信息损失。"""
import unittest

from app.parser.text_cleaner import clean_text


def _s(*cps: int) -> str:
    """用 codepoint 构造不可见字符，避免测试源码内嵌不可见字符。"""
    return "".join(chr(cp) for cp in cps)


class TestCleanText(unittest.TestCase):
    def test_empty_text(self):
        self.assertEqual(clean_text(""), "")

    def test_strip_bom(self):
        self.assertEqual(clean_text(_s(0xFEFF) + "合同"), "合同")

    def test_unify_newlines(self):
        self.assertEqual(clean_text("甲方\r\n乙方\r丙方"), "甲方\n乙方\n丙方")

    def test_fullwidth_to_halfwidth(self):
        # 全角字母/数字/符号 → 半角（NFKC）
        self.assertEqual(clean_text("ＡＢＣ１２３＠"), "ABC123@")

    def test_chinese_punctuation_preserved(self):
        # 中文标点不被 NFKC 破坏（排版保护）
        for ch in "，。！？；：、（）《》「」『』【】“”‘’—…":
            self.assertIn(ch, clean_text(f"合同{ch}条款"))

    def test_control_chars_removed(self):
        self.assertEqual(clean_text("甲方" + _s(0, 1, 2) + "乙方"), "甲方乙方")

    def test_newline_tab_preserved(self):
        self.assertEqual(clean_text("甲方\n\t乙方"), "甲方\n\t乙方")

    def test_zero_width_removed(self):
        self.assertEqual(clean_text("工资" + _s(0x200B) + "发放"), "工资发放")
        self.assertEqual(clean_text(_s(0x200B, 0x200C, 0x200D)), "")

    def test_nbsp_to_space(self):
        self.assertEqual(clean_text("甲方" + _s(0xA0) + "乙方"), "甲方 乙方")

    def test_fullwidth_space_to_space(self):
        self.assertEqual(clean_text("甲方" + _s(0x3000) + "乙方"), "甲方 乙方")

    def test_collapse_blank_lines(self):
        self.assertEqual(clean_text("甲方\n\n\n\n\n乙方"), "甲方\n\n乙方")

    def test_trailing_whitespace_removed(self):
        self.assertEqual(clean_text("甲方  \n乙方\t\n"), "甲方\n乙方")

    def test_strip_outer(self):
        self.assertEqual(clean_text("  甲方\n乙方\n\n"), "甲方\n乙方")

    def test_idempotent(self):
        t = _s(0xFEFF) + "　甲方Ａ" + _s(0x200B) + "\n\n\n乙方！\t\r\n"
        self.assertEqual(clean_text(clean_text(t)), clean_text(t), "清洗应幂等（不重复处理引入新变化）")

    def test_nfkc_boundary_locked(self):
        """锁定 NFKC 兼容字符的转换行为（清洗语义边界）：
        全角￥→¥、全角～→~、带圈①→1、罗马Ⅳ→IV 会被规范化，间隔号·保持原样。
        evidence 匹配的 normalize 也做 NFKC，原文与 LLM 抽取引用两边同步，故转换可接受；
        若未来调整保护集/NFKC 逻辑导致转换变化，此测试会暴露。"""
        for src, exp in {
            chr(0xFFE5): "¥",   # 全角人民币 → 半角
            chr(0xFF5E): "~",   # 全角波浪号 → 半角
            chr(0x2460): "1",   # 带圈数字 ① → 半角 1
            chr(0x2163): "IV",  # 罗马数字 Ⅳ → 拉丁 IV
        }.items():
            self.assertEqual(clean_text(src), exp, f"U+{ord(src):04X} NFKC 转换被破坏")
        self.assertEqual(clean_text(chr(0x00B7)), chr(0x00B7), "间隔号·应保持原样")


if __name__ == "__main__":
    unittest.main()
