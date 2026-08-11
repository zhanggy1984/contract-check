# -*- coding: utf-8 -*-
"""T1.4 验收：RDF 转换（N-Triples 正确）+ segments 恒产出（短合同至少一段）。"""
import json

from app.ontology.loader import load_ontology
from app.ontology.rdf_converter import JsonToRdfConverter
from app.ontology.schema_mapper import build_extraction_schema
from app.parser.segment_splitter import split_segments

# ---- 1. RDF 转换 ----
STD_JSON = {
    "contractTitle": "设备采购合同", "contractType": "采购",
    "effectiveDate": "2024-03-15", "totalAmount": 900000, "currency": "CNY",
    "hasParty": [
        {"partyRole": "甲方", "partyName": "北京智达科技有限公司",
         "unifiedSocialCreditCode": "91110108MA01ABCDE2"},
        {"partyRole": "乙方", "partyName": "深圳蓝海电子有限公司"},
    ],
    "hasItem": [{"itemName": "服务器主机", "quantity": 10, "unitPrice": 80000}],
    "hasClause": [{"clauseType": "违约责任",
                   "clauseText": "如乙方未能按期交货，每逾期一日按合同总金额的0.5%支付违约金。"}],
}

schema = build_extraction_schema(load_ontology())
nt = JsonToRdfConverter(schema).convert(STD_JSON, task_id=999)

# 关键三元组断言
NS = "http://example.org/contract#"
assert f"<{NS}contract_999> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <{NS}Contract>" in nt
assert f"<{NS}contract_999> <{NS}contractTitle> \"设备采购合同\"" in nt
assert f"<{NS}contract_999> <{NS}effectiveDate> \"2024-03-15\"" in nt
assert f"<{NS}contract_999> <{NS}totalAmount> \"9e+05\"" in nt or f"<{NS}contract_999> <{NS}totalAmount> \"900000" in nt
assert f"<{NS}contract_999> <{NS}hasParty> <{NS}contract_999_hasParty_0>" in nt
assert f"<{NS}contract_999> <{NS}hasItem> <{NS}contract_999_hasItem_0>" in nt
assert f"<{NS}contract_999> <{NS}hasClause> <{NS}contract_999_hasClause_0>" in nt
assert f"<{NS}contract_999_hasParty_0> <{NS}partyRole> \"甲方\"" in nt
assert f"<{NS}contract_999_hasItem_0> <{NS}itemName> \"服务器主机\"" in nt
# 类型正确：unifiedSocialCreditCode 字符串 / 数量为数字
assert f"<{NS}contract_999_hasParty_0> <{NS}unifiedSocialCreditCode> \"91110108MA01ABCDE2\"" in nt
print("[OK] RDF N-Triples 包含类型/数据属性/对象属性链接")

# ---- 2. SegmentSplitter：多章节 / 短合同单段 ----
seg_text = """设备采购合同
第一条 合同标的
服务器主机 10 台。
第二条 违约责任
乙方逾期交货按 0.5%/日 支付违约金。
第三条 保密
双方保密。
"""
segs = split_segments(seg_text)
assert len(segs) == 4, segs   # preamble + 3 个章节
assert segs[0]["title"] == "" and segs[0]["content"] == "设备采购合同"   # 文首未匹配章节标记 → preamble
assert segs[1]["title"] == "合同标的" and "服务器主机" in segs[1]["content"]
assert segs[2]["title"] == "违约责任" and "违约金" in segs[2]["content"]
assert segs[3]["title"] == "保密"
print("[OK] 章节分段: %d 段，标题=%s" % (len(segs), [s["title"] for s in segs]))

short_segs = split_segments("这是一份短合同，只有一句。")
assert len(short_segs) == 1 and short_segs[0]["content"] == "这是一份短合同，只有一句。"
print("[OK] 短合同至少一段（整文）")

empty_segs = split_segments("")
assert empty_segs == []
print("[OK] 空文本 → 空分段")

print("\nT1.4 单元部分通过 ✅")
