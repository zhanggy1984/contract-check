"""校验结果落库（T2.3）：rule_check_result 全量 + violation 生成，同一事务幂等。

- 每条规则一行 rule_check_result（PASS/FAIL/SKIPPED，成功也存），冗余 rule_snapshot
- FAIL 行生成 violation 并回填 violation_id
- 幂等：(task_id, rule_id) 唯一键，先删后插，崩溃后 resume 重跑不重复写库
"""
from dataclasses import dataclass

from app.common.constants import RuleResult
from app.db.models import CheckRule, RuleCheckResult, Violation


@dataclass
class RuleOutcome:
    rule: CheckRule
    result: str            # PASS / FAIL / SKIPPED
    subjects: list[str]    # FAIL 反例个体 IRI（多反例合并进 message）
    message: str | None = None         # 语义规则直接携带 LLM reason；确定性为空时由 subjects 生成
    segment_ref: str | None = None
    evidence_text: str | None = None
    confidence: str = "HIGH"


MAX_MESSAGE_LEN = 1000     # 对应 message VARCHAR(1000)，超长截断避免整事务失败
MAX_EVIDENCE_LEN = 15000   # 对应 evidence_text TEXT（utf8mb4 下 65535 字节的安全上界）


def _clip(s: str | None, limit: int) -> str | None:
    return s if s is None or len(s) <= limit else s[:limit]


def _message_of(rule: CheckRule, subjects: list[str], message: str | None = None) -> str | None:
    """FAIL 行 message：语义规则用 LLM reason；确定性规则用规则描述（人话）+ 命中数。

    不用"命中 N 个反例：<IRI 列表>"——IRI 对业务用户不可读（用户反馈看不懂）。
    规则 description 为生成时的中文说明（如"Contract 缺少必填属性 effectiveDate"）。
    """
    if message:
        return message
    if not subjects:
        return None
    base = (rule.description or "").strip() or rule.rule_name or "校验未通过"
    return f"{base}（命中 {len(subjects)} 处）"


def persist_results(db, task_id: int, outcomes: list[RuleOutcome]) -> dict:
    """同一事务写入全量明细 + violations。返回统计。"""
    # 幂等：先删旧。rule_check_result 持有 violation_id FK，须先删明细再删 violation
    db.query(RuleCheckResult).filter_by(task_id=task_id).delete(synchronize_session=False)
    db.query(Violation).filter_by(task_id=task_id).delete(synchronize_session=False)

    n_fail = 0
    for o in outcomes:
        message = _message_of(o.rule, o.subjects, o.message) if o.result == RuleResult.FAIL.value else None
        message = _clip(message, MAX_MESSAGE_LEN)
        evidence = _clip(o.evidence_text, MAX_EVIDENCE_LEN)
        rcr = RuleCheckResult(
            task_id=task_id,
            rule_id=o.rule.id,
            rule_snapshot=o.rule.expression,
            result=o.result,
            rule_type=o.rule.rule_type,
            severity=o.rule.severity,
            concept_iri=o.rule.concept_iri,
            property_iri=o.rule.property_iri,
            segment_ref=o.segment_ref,
            evidence_text=evidence,
            confidence=o.confidence,
            message=message,
        )
        db.add(rcr)
        db.flush()
        if o.result == RuleResult.FAIL.value:
            v = Violation(
                task_id=task_id,
                rule_id=o.rule.id,
                rule_snapshot=o.rule.expression,
                rule_type=o.rule.rule_type,
                severity=o.rule.severity,
                concept_iri=o.rule.concept_iri,
                property_iri=o.rule.property_iri,
                segment_ref=o.segment_ref,
                evidence_text=evidence,
                confidence=o.confidence,
                message=message,
            )
            db.add(v)
            db.flush()
            rcr.violation_id = v.id   # 回填
            n_fail += 1
    db.commit()
    return {"rows": len(outcomes), "violations": n_fail}
