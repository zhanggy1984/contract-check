# -*- coding: utf-8 -*-
"""T2.3 验收：校验结果全量落库 + FAIL→violation 一致 + 幂等重跑 + INCOMPLETE 全 SKIPPED。

通过 validate_deterministic 节点驱动（含 sync_rules + SparqlExecutor + persist_results）。
"""
from app.common.constants import ExtractionStatus, TaskStatus
from app.db.models import CheckRule, CheckTask, ContractFile, RuleCheckResult, Violation
from app.db.session import SessionLocal
from app.graph.nodes import validate_deterministic
from app.ontology.loader import load_ontology, register_version
from app.ontology.rdf_converter import JsonToRdfConverter
from app.ontology.schema_mapper import build_extraction_schema
from app.service.rule_service import get_enabled_rules

BAD_STD = {
    "contractTitle": "违约测试合同",
    "contractType": "采购",
    # 缺 effectiveDate
    "totalAmount": -100,                        # 负金额
    "currency": "CNY",
    "hasParty": [{"partyRole": "甲方", "partyName": "北京智达科技有限公司"}],  # 缺乙方
    "hasItem": [                                # 两标的物都缺 itemName → 多反例合并 1 条
        {"quantity": 10, "unitPrice": 80000},
        {"quantity": 5, "unitPrice": 20000},
    ],
    "hasClause": [{"clauseType": "违约责任", "clauseText": "如违约需承担责任。"}],
}

schema = build_extraction_schema(load_ontology())


import time

def mk_task(ext_status: str, tag: str) -> int:
    sha = f"sha-{tag}-{int(time.time() * 1000)}"   # 重跑唯一
    with SessionLocal() as db:
        cf = ContractFile(file_name=f"{sha}.pdf", file_type="PDF", storage_path="x",
                          file_size=1, sha256=sha)
        db.add(cf)
        db.flush()
        ovid = register_version(db)
        t = CheckTask(contract_file_id=cf.id, status=TaskStatus.PENDING.value,
                      ontology_version_id=ovid, extraction_status=ext_status,
                      extraction_rdf=JsonToRdfConverter(schema).convert(BAD_STD, task_id=1))
        db.add(t)
        db.commit()
        return t.id


# ---- 1. COMPLETE 违约合同 → 全量落库 + violation 一致 ----
tid = mk_task(ExtractionStatus.COMPLETE.value, "sha-t23-a")
out = validate_deterministic({"task_id": tid})
assert out["violations_count"] == 4, out   # 缺生效日 / 金额负 / 缺乙方 / 两标的物缺itemName(合并1条)

with SessionLocal() as db:
    rows = db.query(RuleCheckResult).filter_by(task_id=tid).all()
    vios = db.query(Violation).filter_by(task_id=tid).all()
    n_rules = len(get_enabled_rules(db, db.get(CheckTask, tid).ontology_version_id))
    assert len(rows) == n_rules, f"全量落库 {len(rows)} != 规则数 {n_rules}"
    assert len(vios) == 4
    # FAIL 行 violation_id 回填且与 violation 一一对应
    fail_rows = [r for r in rows if r.result == "FAIL"]
    assert len(fail_rows) == len(vios) == 4
    for r in fail_rows:
        assert r.violation_id is not None
        assert db.get(Violation, r.violation_id).rule_id == r.rule_id
    # 单规则多反例 → 合并一条，message 列出全部 ?s
    merged = [v for v in vios if "hasItem" in (v.message or "")][0]
    assert "2 个反例" in merged.message
    assert "_hasItem_0" in merged.message and "_hasItem_1" in merged.message
    # 规则明细冗余 rule_snapshot
    assert all(r.rule_snapshot == db.get(CheckRule, r.rule_id).expression for r in rows)
    print("[OK] COMPLETE 违约合同：%d 条明细、%d 条 violation 一致落库，多反例合并" % (len(rows), len(vios)))

    # ---- 2. 幂等：重跑不产生重复 ----
    out2 = validate_deterministic({"task_id": tid})
    assert out2["violations_count"] == 4
    assert db.query(RuleCheckResult).filter_by(task_id=tid).count() == len(rows)
    assert db.query(Violation).filter_by(task_id=tid).count() == len(vios)
    print("[OK] 重跑幂等：明细 %d 条 / violation %d 条零重复" % (len(rows), len(vios)))

# ---- 3. INCOMPLETE → 全部 SKIPPED、零 violation ----
tid2 = mk_task(ExtractionStatus.INCOMPLETE.value, "sha-t23-b")
validate_deterministic({"task_id": tid2})
with SessionLocal() as db:
    assert db.query(Violation).filter_by(task_id=tid2).count() == 0
    rows2 = db.query(RuleCheckResult).filter_by(task_id=tid2).all()
    assert rows2 and all(r.result == "SKIPPED" for r in rows2)
    print("[OK] INCOMPLETE → 全部 %d 条规则 SKIPPED，零 violation" % len(rows2))

print("\nT2.3 验收通过 ✅")
