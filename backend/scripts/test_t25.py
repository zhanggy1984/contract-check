# -*- coding: utf-8 -*-
"""T2.5 验收：规则管理 API（CRUD + dry-run）通过 HTTP 层全链路。

- 建人工规则（默认 disabled）/ 重复 iri 拒绝
- 编辑人工规则全字段；本体生成规则仅启停/severity（表达式只读）
- 失效人工规则；本体规则不可失效
- dry-run 复用历史任务 RDF，预览命中，不落库，token_cost=0
"""
import time
from fastapi.testclient import TestClient

from app.db.models import CheckRule, CheckTask, ContractFile
from app.db.session import SessionLocal
from app.main import app
from app.ontology.loader import load_ontology
from app.ontology.rdf_converter import JsonToRdfConverter
from app.ontology.schema_mapper import build_extraction_schema

BAD_STD = {"contractTitle": "t", "contractType": "采购", "totalAmount": 1, "currency": "CNY",
           "hasParty": [{"partyRole": "甲方", "partyName": "P"}, {"partyRole": "乙方", "partyName": "Q"}]}
# 缺 effectiveDate（必填 FAIL）

client = TestClient(app)

# ---- 1. 建人工规则（默认 disabled）+ 重复拒绝 ----
iri = f"urn:rule:manual:test-{int(time.time()*1000)}"
r = client.post("/api/rules", json={
    "rule_iri": iri, "name": "测试规则", "type": "DETERMINISTIC", "severity": "HIGH",
    "expression": "ASK { ?s a <http://example.org/contract#Contract> FILTER NOT EXISTS { ?s <http://example.org/contract#effectiveDate> ?v } }",
    "description": "缺生效日期（人工测试规则）",
})
assert r.status_code == 200 and r.json()["enabled"] is False, r.text
rule_id = r.json()["id"]
dup = client.post("/api/rules", json={
    "rule_iri": iri, "name": "重复", "type": "DETERMINISTIC", "severity": "LOW", "expression": "ASK {}",
})
assert dup.status_code == 409, dup.text
print("[OK] 建人工规则默认禁用；重复 rule_iri → 409")

# ---- 2. 编辑人工规则全字段 ----
r = client.put(f"/api/rules/{rule_id}", json={"enabled": True, "severity": "MEDIUM"})
assert r.status_code == 200 and r.json()["enabled"] is True
print("[OK] 编辑人工规则启停/severity")

# ---- 3. 本体生成规则：仅启停/severity，表达式只读 ----
with SessionLocal() as db:
    auto = db.query(CheckRule).filter_by(rule_iri="urn:rule:required:Contract.effectiveDate").one()
    auto_id, orig_expr = auto.id, auto.expression
r = client.put(f"/api/rules/{auto_id}", json={"enabled": False, "severity": "LOW", "expression": "ASK { }"})
assert r.status_code == 200
with SessionLocal() as db:
    row = db.get(CheckRule, auto_id)
    assert row.enabled is False and row.severity == "LOW" and row.expression == orig_expr
client.put(f"/api/rules/{auto_id}", json={"enabled": True})  # 恢复
print("[OK] 本体生成规则表达式只读（仅启停/severity 生效）")

# ---- 4. 失效人工规则；本体规则不可失效 ----
assert client.delete(f"/api/rules/{rule_id}").status_code == 200
assert client.delete(f"/api/rules/{auto_id}").status_code == 400
print("[OK] 人工规则失效；本体生成规则拒绝失效")

# ---- 5. dry-run：复用任务 RDF，预览命中，不落库 ----
schema = build_extraction_schema(load_ontology())
with SessionLocal() as db:
    sha = f"sha-t25-{int(time.time()*1000)}"
    cf = ContractFile(file_name=f"{sha}.pdf", file_type="PDF", storage_path="x", file_size=1, sha256=sha)
    db.add(cf)
    db.commit()
    t = CheckTask(contract_file_id=cf.id, status="PENDING", extraction_status="COMPLETE",
                  extraction_rdf=JsonToRdfConverter(schema).convert(BAD_STD, task_id=1))
    db.add(t)
    db.commit()
    task_id = t.id
    n_before = db.query(CheckTask).count()

r = client.post(f"/api/rules/{auto_id}/dry-run", json={"task_id": task_id})
assert r.status_code == 200
body = r.json()
assert body["result"] == "FAIL" and body["subjects"] and body["token_cost"] == 0, body
# dry-run 不落库：没有产生 rule_check_result/violation
with SessionLocal() as db:
    from app.db.models import Violation
    assert db.query(Violation).filter_by(task_id=task_id).count() == 0
print("[OK] dry-run 复用 RDF 预览命中（FAIL + subjects），token_cost=0，不落库")

# ---- 6. 列表筛选 ----
r = client.get("/api/rules", params={"source": "ONTOLOGY_GENERATED", "size": 5})
assert r.status_code == 200 and r.json()["total"] >= 22 and len(r.json()["items"]) == 5
print("[OK] 规则列表筛选（本体来源，分页正常）")

print("\nT2.5 验收通过 ✅")
