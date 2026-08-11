# -*- coding: utf-8 -*-
"""T2.4 验收：人工审核闭环（C 类场景全覆盖）。

C1 全部确认 / C2 全部误报 / C3 混合 / C4 部分提交拒绝 / C5 并发 409 /
C6 resume 失败回退 / C7 cancel 三态 + 拒绝后续 resume。

构造违约抽取结果（缺生效日 + 缺乙方 → 2 条 UNCONFIRMED），
mock extract_contract 避开真实 LLM，跑图至 WAITING_REVIEW 后逐场景断言。
"""
import time
from pathlib import Path
from unittest.mock import patch

from app.db.models import CheckTask, ContractFile, Violation
from app.db.session import SessionLocal
from app.llm.extractor import ExtractionResult
from app.service.check_task_service import _run_flow, cancel_task, resume_task

BAD_STD = {
    "contractTitle": "审核测试合同", "contractType": "采购",
    # 缺 effectiveDate → 必填 FAIL
    "totalAmount": 100, "currency": "CNY",
    "hasParty": [{"partyRole": "甲方", "partyName": "P"}],  # 缺乙方 → 人工规则 FAIL
    "hasItem": [{"itemName": "服务器", "quantity": 1, "unitPrice": 100}],
    "hasClause": [{"clauseType": "违约责任", "clauseText": "如违约承担责任。"}],
}


def run_to_waiting(tag: str) -> int:
    sha = f"sha-t24-{tag}-{int(time.time() * 1000)}"
    Path("data/parsed").mkdir(exist_ok=True)
    Path(f"data/parsed/{sha}.txt").write_text("审核测试合同文本", encoding="utf-8")
    with SessionLocal() as db:
        cf = ContractFile(file_name=f"{sha}.pdf", file_type="PDF", storage_path="x",
                          file_size=1, sha256=sha)
        db.add(cf)
        db.commit()
        t = CheckTask(contract_file_id=cf.id, status="PENDING")
        db.add(t)
        db.commit()
        task_id = t.id
    with patch("app.graph.nodes.extract_contract",
               return_value=ExtractionResult(BAD_STD, "COMPLETE", ["审核测试合同文本"])):
        _run_flow(task_id)
    return task_id


def get_violations(tid: int) -> list[Violation]:
    with SessionLocal() as db:
        return db.query(Violation).filter_by(task_id=tid).all()


def get_status(tid: int) -> str:
    with SessionLocal() as db:
        return db.get(CheckTask, tid).status


def reviews_of(vs: list[Violation], action: str, user: str = "tester") -> list[dict]:
    return [{"violation_id": v.id, "action": action, "confirm_user": user} for v in vs]


# ---- C4 + C1：部分提交拒绝 → 全部确认 SUCCESS ----
tid = run_to_waiting("a")
vs = get_violations(tid)
assert len(vs) == 2, len(vs)
assert resume_task(tid, [{"violation_id": vs[0].id, "action": "CONFIRMED"}]) is False, "部分提交应拒绝"
assert get_status(tid) == "WAITING_REVIEW"
assert resume_task(tid, reviews_of(vs, "CONFIRMED")) is True
assert get_status(tid) == "SUCCESS"
assert all(v.status == "CONFIRMED" and v.confirm_user == "tester" for v in get_violations(tid))
print("[OK] C4 部分提交拒绝；C1 全部确认 → SUCCESS")

# ---- C2：全部误报 ----
tid = run_to_waiting("b")
vs = get_violations(tid)
assert resume_task(tid, reviews_of(vs, "FALSE_POSITIVE")) is True
assert get_status(tid) == "SUCCESS"
assert all(v.status == "FALSE_POSITIVE" for v in get_violations(tid))
print("[OK] C2 全部误报 → SUCCESS")

# ---- C3：混合决策 ----
tid = run_to_waiting("c")
vs = get_violations(tid)
actions = [{"violation_id": vs[0].id, "action": "CONFIRMED"},
           {"violation_id": vs[1].id, "action": "FALSE_POSITIVE"}]
assert resume_task(tid, actions) is True
assert get_status(tid) == "SUCCESS"
assert {v.status for v in get_violations(tid)} == {"CONFIRMED", "FALSE_POSITIVE"}
print("[OK] C3 混合决策 → SUCCESS，两态并存")

# ---- C5：并发/已处理 → 第二次 409 ----
tid = run_to_waiting("e")
vs = get_violations(tid)
assert resume_task(tid, reviews_of(vs, "CONFIRMED")) is True
assert resume_task(tid, reviews_of(vs, "CONFIRMED")) is False, "已处理任务应 409"
print("[OK] C5 重复提交 → 第二次拒绝(409)")

# ---- C6：resume 失败幂等回退可重试 ----
tid = run_to_waiting("f")
vs = get_violations(tid)
with patch("app.service.check_task_service._run_flow", side_effect=RuntimeError("boom")):
    assert resume_task(tid, reviews_of(vs, "CONFIRMED")) is False
assert get_status(tid) == "WAITING_REVIEW", "失败应回退待审核"
assert resume_task(tid, reviews_of(vs, "CONFIRMED")) is True, "回退后可重试"
assert get_status(tid) == "SUCCESS"
print("[OK] C6 resume 失败 → 幂等回退 WAITING_REVIEW → 重试成功")

# ---- C7：cancel 生效且拒绝后续 resume ----
tid = run_to_waiting("d")
vs = get_violations(tid)
assert cancel_task(tid) is True
assert get_status(tid) == "CANCELLED"
assert resume_task(tid, reviews_of(vs, "CONFIRMED")) is False, "取消后拒绝 resume"
print("[OK] C7 cancel → CANCELLED，拒绝后续 resume")

print("\nT2.4 验收通过 ✅")
