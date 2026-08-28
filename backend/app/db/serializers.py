"""ORM → 交互 JSON 序列化（资源层）：供控制层/交互层复用，避免跨层直连模型。

四层分层收口后，violation 序列化从 api/violations.py 下沉至此：
- 控制层（service/check_task_service 组装任务结果）与交互层（api/violations 列表/更新）
  都经此复用，交互层不再持有 db.models 的序列化逻辑；
- 依赖方向：本模块只依赖 db.models（资源层内部），被控制层/交互层上层调用。
"""
from app.db.models import Violation


def violation_to_dict(v: Violation) -> dict:
    """Violation → 交互 JSON（confirm_time 转 ISO 字符串）。"""
    return {
        "id": v.id,
        "task_id": v.task_id,
        "rule_id": v.rule_id,
        "rule_type": v.rule_type,
        "severity": v.severity,
        "concept_iri": v.concept_iri,
        "property_iri": v.property_iri,
        "segment_ref": v.segment_ref,
        "evidence_text": v.evidence_text,
        "confidence": v.confidence,
        "message": v.message,
        "expected_value": v.expected_value,
        "actual_value": v.actual_value,
        "status": v.status,
        "confirm_user": v.confirm_user,
        "confirm_time": v.confirm_time.isoformat() if v.confirm_time else None,
    }
