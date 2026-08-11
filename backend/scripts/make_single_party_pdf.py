# -*- coding: utf-8 -*-
"""生成 T2.6 浏览器验收用"单方合同" PDF：字段齐全可 COMPLETE，但缺乙方 → 人工规则 FAIL。"""
import pymupdf

TEXT = """单方设备采购合同
合同编号：HT-ONE-001

甲方（采购方）：北京智达科技有限公司
统一社会信用代码：91110108MA01ABCDE2
法定代表人：王强
地址：北京市海淀区中关村大街1号

第一条 合同标的
服务器主机，数量10台，单价80000元，金额800000元

第二条 合同金额
合同总金额为人民币800000元（含税），币种CNY，税率13%。
开具增值税专用发票。

第三条 违约责任
如乙方未能按期交货，每逾期一日按合同总金额的0.5%支付违约金。

本合同自双方签字盖章之日起生效，生效日期2024年3月15日。
"""

doc = pymupdf.open()
page = doc.new_page()
page.insert_textbox(pymupdf.Rect(50, 50, 545, 800), TEXT, fontsize=10, fontname="china-s")
doc.save("scripts/single_party_contract.pdf")
doc.close()
print("已生成 scripts/single_party_contract.pdf")
