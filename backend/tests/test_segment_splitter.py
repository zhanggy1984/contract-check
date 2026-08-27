"""segment_splitter 单测：章节切片 + 编号子项甄别（方案 3，P0 数据完整性）。

核心逻辑：第X条无条件标题；编号行（一、/1、）仅在行内正文 ≤20 字符时视为标题，
长正文编号行是条款内枚举子项，并入当前段防上下文割裂（语义评估按段批跑，
拆碎后条款上下文丢失会误判）。
"""
import unittest

from app.parser.segment_splitter import split_segments


class TestSplitSegments(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(split_segments(""), [])
        self.assertEqual(split_segments(None), [])

    def test_no_header_single_segment(self):
        segs = split_segments("甲方：甲公司\n乙方：乙公司\n标的：服务器")
        self.assertEqual(len(segs), 1)
        self.assertIn("服务器", segs[0]["content"])

    def test_clause_header_with_inline_body(self):
        # T4.6：标题与正文同在一行，行内正文归属该标题段
        segs = split_segments("第一条 合同标的：服务器壹台。\n第二条 违约责任：违约方赔偿损失。")
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0]["title"], "第一条")
        self.assertEqual(segs[0]["content"], "合同标的：服务器壹台。")

    def test_blank_clause_line(self):
        # "第一条"单独一行，下一行正文归属该段
        segs = split_segments("第一条\n交付服务器壹台。\n第二条\n违约方赔偿损失。")
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0]["content"], "交付服务器壹台。")
        self.assertEqual(segs[1]["title"], "第二条")

    def test_short_numbered_header(self):
        # 短中文编号行（"一、总则"）仍视为标题，行内正文归属该段（T4.6）
        segs = split_segments("一、总则\n本规则适用于全体。\n二、分则\n细则如下。")
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0]["title"], "一、")
        self.assertEqual(segs[0]["content"], "总则\n本规则适用于全体。")

    def test_arabic_numbered_never_split(self):
        # 阿拉伯数字编号一律视为子项不拆段：即使 rest 很短（"1、定义"），
        # 合同正文中阿拉伯编号几乎总是条款内枚举，不冒误拆风险（最稳妥判定）
        segs = split_segments("第一条 术语\n1、定义\n本协议所称甲方指甲公司。\n第二条 标的\n1、服务器")
        self.assertEqual(len(segs), 2, "阿拉伯数字编号不得拆段，归属第一条")
        self.assertIn("1、定义", segs[0]["content"])
        self.assertIn("1、服务器", segs[1]["content"])
        self.assertEqual(segs[1]["title"], "第二条")

    def test_long_numbered_subitem_not_split(self):
        # 编号行长正文 = 条款内枚举子项，不得拆成独立段（方案 3 核心修复）
        text = ("第一条 交付清单\n1、交付服务器壹台、交换机两台、路由器三台，共六台设备。\n"
                "2、交付日期为合同生效后三十日内。\n第二条 违约责任\n违约方赔偿全部损失。")
        segs = split_segments(text)
        self.assertEqual(len(segs), 2, "枚举子项不得拆段，仍归属第一条")
        self.assertIn("1、交付服务器壹台", segs[0]["content"])
        self.assertIn("2、交付日期为合同生效后三十日内", segs[0]["content"])
        self.assertEqual(segs[1]["title"], "第二条")

    def test_mixed_short_and_long_numbered(self):
        # 同一段落内既有短编号标题（章）也有长子项（条内枚举）混排不串段
        text = ("一、总则\n1、本规则适用于全部合同主体。\n"
                "二、分则\n1、违约方应赔偿守约方全部直接损失和可得利益损失。")
        segs = split_segments(text)
        self.assertEqual(len(segs), 2, "短编号标题拆段、长编号子项并入对应段")
        self.assertIn("本规则适用于全部合同主体", segs[0]["content"])
        self.assertIn("违约方应赔偿守约方全部直接损失", segs[1]["content"])


if __name__ == "__main__":
    unittest.main()
