# -*- coding: utf-8 -*-
"""生成 T2.6 浏览器验收用"违约合同" PDF：缺生效日期、缺乙方、金额为负。"""
import pymupdf

TEXT = """违约测试合同
合同编号：HT-BAD-001

甲方（采购方）：北京智达科技有限公司
统一社会信用代码：91110108MA01ABCDE2

第一条 合同标的
服务器主机，数量10台，单价-8000元，金额-80000元

第二条 合同金额
合同总金额为人民币-80000元（含税），币种CNY，税率13%。

第三条 违约责任
如乙方未能按期交货，每逾期一日按合同总金额的0.5%支付违约金。
"""

doc = pymupdf.open()
page = doc.new_page()
page.insert_textbox(pymupdf.Rect(50, 50, 545, 800), TEXT, fontsize=10, fontname="china-s")
doc.save("scripts/bad_contract.pdf")
doc.close()
print("已生成 scripts/bad_contract.pdf")
