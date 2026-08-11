# -*- coding: utf-8 -*-
"""T2.1 验收：规则生成 + 人工 .rq 加载 + check_rule 版本化落库。

- 自动规则覆盖：必填(contractTitle/hasParty)、枚举(contractType/currency/partyRole)、
  数值(totalAmount minInclusive)、格式(unifiedSocialCreditCode pattern)
- 全部 ASK 经 rdflib SPARQL 语法校验
- 人工规则 2 条（缺甲方/缺乙方），ontology_version_id=NULL
- sync_rules 幂等：连跑两次规则数不变、rule_id 不变
"""
from rdflib.plugins.sparql import prepareQuery

from app.db.session import SessionLocal
from app.ontology.loader import load_ontology, register_version
from app.ontology.rule_generator import generate_rules, load_manual_rules
from app.service.rule_service import sync_rules
from app.db.models import CheckRule

onto = load_ontology()
rules = generate_rules(onto)
by_iri = {r["rule_iri"]: r for r in rules}

# ---- 1. 规则覆盖断言 ----
assert "urn:rule:required:Contract.contractTitle" in by_iri, list(by_iri)
assert "urn:rule:required:Contract.hasParty" in by_iri, "hasParty 必填规则缺失"
assert "urn:rule:enum:Contract.contractType" in by_iri
assert "urn:rule:enum:Contract.currency" in by_iri
assert "urn:rule:enum:Party.partyRole" in by_iri
assert "urn:rule:min:Contract.totalAmount" in by_iri
assert "urn:rule:pattern:Party.unifiedSocialCreditCode" in by_iri
assert "urn:rule:required:Party.partyRole" in by_iri
assert "urn:rule:required:Clause.clauseText" in by_iri
print("[OK] 自动规则共 %d 条，必填/枚举/数值/格式/对象属性引用全覆盖" % len(rules))

# 规则内容抽查
r = by_iri["urn:rule:enum:Contract.contractType"]["expression"]
assert "NOT IN" in r and "采购" in r and "销售" in r, r
r = by_iri["urn:rule:min:Contract.totalAmount"]["expression"]
assert "FILTER (?v < 0.0)" in r, r
r = by_iri["urn:rule:required:Contract.hasParty"]["expression"]
assert "FILTER NOT EXISTS" in r and "hasParty" in r, r
print("[OK] SPARQL 内容抽查通过")

# ---- 2. 全部 ASK 语法校验（rdflib） ----
for r in rules:
    prepareQuery(r["expression"])  # 语法错误直接抛异常
print("[OK] 自动规则 %d 条全部通过 SPARQL 语法校验" % len(rules))

# ---- 3. 人工规则 ----
manual = load_manual_rules()
assert len(manual) == 2, manual
m_by_iri = {m["rule_iri"]: m for m in manual}
assert "urn:rule:manual:missing_a_party" in m_by_iri
assert "urn:rule:manual:missing_b_party" in m_by_iri
assert m_by_iri["urn:rule:manual:missing_b_party"]["severity"] == "HIGH"
for m in manual:
    prepareQuery(m["expression"])
print("[OK] 人工规则 2 条加载，语法通过")

# ---- 4. sync_rules 幂等落库 ----
with SessionLocal() as db:
    ovid = register_version(db)
    ids1 = sync_rules(db, ovid)
    total1 = db.query(CheckRule).count()
    ids2 = sync_rules(db, ovid)          # 重跑
    total2 = db.query(CheckRule).count()
    assert total1 == total2, (total1, total2)
    assert ids1 == ids2, "重跑 rule_id 必须稳定（幂等）"
    # 人工规则版本为空
    for iri in ("urn:rule:manual:missing_a_party", "urn:rule:manual:missing_b_party"):
        row = db.query(CheckRule).filter_by(rule_iri=iri, ontology_version_id=None).one()
        assert row.ontology_version_id is None and row.source == "MANUAL"
    # 自动规则绑定版本且 (rule_iri, version) 唯一
    dup = (
        db.query(CheckRule.rule_iri)
        .filter(CheckRule.source == "ONTOLOGY_GENERATED")
        .group_by(CheckRule.rule_iri, CheckRule.ontology_version_id)
        .having(__import__("sqlalchemy").func.count() > 1)
        .count()
    )
    assert dup == 0, "自动规则 (rule_iri, ontology_version_id) 唯一性被破坏"
    print("[OK] sync_rules 幂等：共 %d 条，重跑零重复；人工规则版本=NULL" % total2)

print("\nT2.1 验收通过 ✅")
