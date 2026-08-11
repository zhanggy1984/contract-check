# -*- coding: utf-8 -*-
"""T2.2 验收：SparqlExecutor ASK 判反例 + SELECT 定位 + 多反例合并。

构造一份"故意违约"的 std_json（绕过 LLM 直接喂转换器，校验逻辑本就在 RDF 层）：
  - 缺 effectiveDate（必填 FAIL）
  - totalAmount=-100（数值 FAIL）
  - 只有甲方、缺乙方（人工规则 FAIL）
  - 两个标的物都缺 itemName（单规则多反例 → 合并一条，message 列出全部 ?s）
其余字段合规 → 相应规则 PASS。
"""
from types import SimpleNamespace

from app.ontology.loader import load_ontology
from app.ontology.rdf_converter import JsonToRdfConverter
from app.ontology.rule_generator import generate_rules, load_manual_rules
from app.ontology.schema_mapper import build_extraction_schema
from app.validation.sparql_executor import SparqlExecutor, build_graph

BAD_STD = {
    "contractTitle": "违约测试合同",          # 合规
    "contractType": "采购",                   # 合规
    # 缺 effectiveDate（必填）
    "totalAmount": -100,                      # 违反 minInclusive 0
    "currency": "CNY",                        # 合规
    "hasParty": [
        {"partyRole": "甲方", "partyName": "北京智达科技有限公司"},   # 缺乙方
    ],
    "hasItem": [                              # 两项都缺 itemName
        {"quantity": 10, "unitPrice": 80000},
        {"quantity": 5, "unitPrice": 20000},
    ],
    "hasClause": [{"clauseType": "违约责任", "clauseText": "如违约需承担责任。"}],
}

schema = build_extraction_schema(load_ontology())
nt = JsonToRdfConverter(schema).convert(BAD_STD, task_id=1)
graph = build_graph(nt)
assert graph is not None
executor = SparqlExecutor()

all_rules = generate_rules(load_ontology()) + load_manual_rules()
results = {}
for r in all_rules:
    results[r["rule_iri"]] = executor.run(graph, SimpleNamespace(**r))

def hit(iri):
    res = results[iri]
    assert res.passed is False, f"{iri} 应 FAIL"
    return res.subjects

# ---- 1. 必填 FAIL（缺生效日） ----
subj = hit("urn:rule:required:Contract.effectiveDate")
assert len(subj) == 1 and "contract_1" in subj[0], subj
print("[OK] 缺生效日 → required FAIL, 反例=%s" % subj[0])

# ---- 2. 数值 FAIL（金额为负） ----
subj = hit("urn:rule:min:Contract.totalAmount")
assert len(subj) == 1 and "contract_1" in subj[0], subj
print("[OK] 金额为负 → min FAIL, 反例=%s" % subj[0])

# ---- 3. 人工规则 FAIL（缺乙方）；甲方规则 PASS ----
subj = hit("urn:rule:manual:missing_b_party")
assert len(subj) == 1, subj
assert results["urn:rule:manual:missing_a_party"].passed is True, "甲方存在应 PASS"
print("[OK] 缺乙方 → 人工规则 FAIL；缺甲方 → PASS")

# ---- 4. 单规则多反例合并（两个标的物都缺 itemName） ----
subj = hit("urn:rule:required:ContractItem.itemName")
assert len(subj) == 2, f"两个反例都应被定位，实际={subj}"
assert any("_hasItem_0" in s for s in subj) and any("_hasItem_1" in s for s in subj), subj
print("[OK] 多反例合并：%d 个标的物反例全部列出 -> %s" % (len(subj), subj))

# ---- 5. 合规规则 PASS ----
for iri in (
    "urn:rule:required:Contract.contractTitle",
    "urn:rule:enum:Contract.contractType",
    "urn:rule:enum:Contract.currency",
    "urn:rule:required:Party.partyName",
):
    assert results[iri].passed is True, f"{iri} 应 PASS"
print("[OK] 合规字段规则全部 PASS")

# ---- 6. 空图 → passed=False + 空反例（上层判 SKIPPED） ----
empty = executor.run(None, SimpleNamespace(**all_rules[0]))
assert empty.passed is False and empty.subjects == []
print("[OK] 空图 → passed=False/空反例（由上层映射为 SKIPPED）")

# ---- 7. 枚举/格式越界 FAIL（值语义防御） ----
BAD_ENUM = {
    "contractTitle": "t", "contractType": "采购协议",  # 枚举越界
    "effectiveDate": "2024-03-15", "totalAmount": 1, "currency": "RMB",  # 枚举越界
    "hasParty": [{"partyRole": "甲方", "partyName": "P",
                  "unifiedSocialCreditCode": "BADCODE123"}],  # 非 18 位
}
g2 = build_graph(JsonToRdfConverter(schema).convert(BAD_ENUM, task_id=2))
res2 = {r["rule_iri"]: executor.run(g2, SimpleNamespace(**r)) for r in all_rules}
for iri in ("urn:rule:enum:Contract.contractType",
            "urn:rule:enum:Contract.currency",
            "urn:rule:pattern:Party.unifiedSocialCreditCode"):
    assert res2[iri].passed is False, f"{iri} 应 FAIL"
print("[OK] 枚举越界/currency 越界/信用代码格式 → FAIL")

print("\nT2.2 验收通过 ✅")
