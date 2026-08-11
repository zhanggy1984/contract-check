# -*- coding: utf-8 -*-
"""T1.1 验收：本体加载无错、类/属性/约束可遍历、版本落库。"""
import owlready2 as owl

from app.db.models import OntologyVersion
from app.db.session import SessionLocal
from app.ontology.constraints import MIN_CARDINALITY
from app.ontology.loader import ensure_loaded, load_ontology, md5_of_file, register_version

onto = load_ontology()

# 1. 类可遍历
class_names = {c.name for c in onto.classes()}
expect_classes = {"Contract", "Party", "ContractItem", "Clause"}
assert expect_classes <= class_names, f"缺类: {expect_classes - class_names}"
print(f"[OK] 类齐全: {sorted(class_names)}")

# 2. 必填约束可遍历（Contract/Party/Clause/ContractItem 的 minCardinality）
def required_of(cls):
    req = set()
    for sup in cls.is_a:
        if isinstance(sup, owl.Restriction) and sup.type == MIN_CARDINALITY:
            req.add(sup.property.name)
    return req

req_contract = required_of(onto.Contract)
assert {"contractTitle", "contractType", "effectiveDate", "totalAmount", "currency"} <= req_contract
assert "hasParty" in req_contract
req_party = required_of(onto.Party)
assert {"partyRole", "partyName"} <= req_party
req_item = required_of(onto.ContractItem)
assert "itemName" in req_item
req_clause = required_of(onto.Clause)
assert {"clauseType", "clauseText"} <= req_clause
print(f"[OK] 必填约束: Contract={sorted(req_contract)} Party={sorted(req_party)} "
      f"Item={sorted(req_item)} Clause={sorted(req_clause)}")

# 3. 枚举约束（内联 owl:oneOf）
def enum_of(prop_name):
    rng = getattr(onto, prop_name).range[0]
    if isinstance(rng, owl.OneOf):
        return [str(v) for v in rng.instances]
    return None

assert enum_of("contractType") == ["采购", "销售", "服务", "劳务", "租赁", "合作", "其他"]
assert enum_of("partyRole") == ["甲方", "乙方", "丙方"]
assert enum_of("currency") == ["CNY", "USD", "EUR", "JPY", "HKD"]
print(f"[OK] 枚举: contractType={enum_of('contractType')}")
print(f"[OK] 枚举: partyRole={enum_of('partyRole')}, currency={enum_of('currency')}")

# 4. 数值约束（minInclusive）与 pattern
def facet_of(prop_name):
    rng = getattr(onto, prop_name).range[0]
    return getattr(rng, "min_inclusive", None)

assert facet_of("totalAmount") == 0.0
assert facet_of("depositAmount") == 0.0
assert facet_of("taxRate") == 0.0
uscc = getattr(onto, "unifiedSocialCreditCode").range[0]
assert getattr(uscc, "pattern", None) == "[0-9A-Z]{18}"
print(f"[OK] 数值约束: totalAmount.minInclusive={facet_of('totalAmount')}, pattern={uscc.pattern}")

# 5. 对象属性
obj_props = {p.name for p in onto.object_properties()}
assert {"hasParty", "hasClause", "hasItem"} <= obj_props
assert onto.hasParty.range[0].name == "Party"
print(f"[OK] 对象属性: {sorted(obj_props)}")

# 6. 版本落库（幂等）
with SessionLocal() as db:
    vid = register_version(db)
    md5 = md5_of_file(__import__("app.ontology.loader", fromlist=["ONTOLOGY_PATH"]).ONTOLOGY_PATH)
    rows = db.query(OntologyVersion).filter(OntologyVersion.md5 == md5).all()
    assert len(rows) == 1, f"版本应幂等唯一，实际 {len(rows)} 条"
    assert rows[0].id == vid
    print(f"[OK] 版本落库幂等: id={vid} md5={md5[:12]} version={rows[0].version}")

vid2 = ensure_loaded()
assert vid2 == vid
print("[OK] ensure_loaded 幂等，返回同一版本 id")

print("\nT1.1 全部通过 ✅")
