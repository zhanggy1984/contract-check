# -*- coding: utf-8 -*-
"""T1.2 验收：schema 正确反映必填/枚举/minInclusive/pattern/嵌套对象属性。"""
import json

from app.ontology.loader import load_ontology
from app.ontology.schema_mapper import build_extraction_schema

onto = load_ontology()
schema = build_extraction_schema(onto)
props = schema["properties"]

# 1. 必填（数据属性 + hasParty 对象属性）
assert sorted(schema["required"]) == sorted(
    ["contractTitle", "contractType", "effectiveDate", "totalAmount", "currency", "hasParty"]), schema["required"]

# 2. 枚举
assert props["contractType"]["enum"] == ["采购", "销售", "服务", "劳务", "租赁", "合作", "其他"]
assert props["currency"]["enum"] == ["CNY", "USD", "EUR", "JPY", "HKD"]

# 3. 数值范围
assert props["totalAmount"]["type"] == "number"
assert props["totalAmount"]["minimum"] == 0.0
assert props["taxRate"]["minimum"] == 0.0

# 4. 日期格式
assert props["effectiveDate"] == {"type": "string", "format": "date"}

# 5. pattern（Party 的信用代码）
party_items = props["hasParty"]["items"]
assert party_items["type"] == "object"
assert sorted(party_items["required"]) == ["partyName", "partyRole"]
assert party_items["properties"]["unifiedSocialCreditCode"]["pattern"] == "[0-9A-Z]{18}"

# 6. 嵌套对象属性
assert props["hasParty"]["type"] == "array"
assert props["hasItem"]["type"] == "array"
assert props["hasClause"]["type"] == "array"
assert sorted(props["hasItem"]["items"]["required"]) == ["itemName"]
assert sorted(props["hasClause"]["items"]["required"]) == ["clauseText", "clauseType"]

# 7. 类型正确
assert props["taxInclusive"]["type"] == "boolean"
assert props["contractNo"]["type"] == "string"

print(json.dumps(schema, ensure_ascii=False, indent=2))
print("\nT1.2 全部通过 ✅")
