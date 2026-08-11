"""异常（violation）接口：查询列表 + 人工确认/误报状态更新。"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.common.constants import ViolationStatus
from app.db.models import Violation
from app.db.session import get_db

router = APIRouter(prefix="/violations", tags=["violations"])


@router.get("")
def list_violations(
    task_id: int | None = None,
    status: str | None = None,
    rule_type: str | None = None,
    severity: str | None = None,
    page: int = 1,
    size: int = 20,
    db: Session = Depends(get_db),
):
    """按任务/状态/规则类型/严重级别筛选，分页。"""
    q = db.query(Violation)
    if task_id:
        q = q.filter(Violation.task_id == task_id)
    if status:
        q = q.filter(Violation.status == status)
    if rule_type:
        q = q.filter(Violation.rule_type == rule_type)
    if severity:
        q = q.filter(Violation.severity == severity)
    total = q.count()
    items = q.order_by(Violation.id).offset((page - 1) * size).limit(size).all()
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [_to_dict(v) for v in items],
    }


class StatusBody(BaseModel):
    status: str                 # CONFIRMED / FALSE_POSITIVE
    confirm_user: str | None = None


@router.patch("/{violation_id}/status")
def update_status(violation_id: int, body: StatusBody, db: Session = Depends(get_db)):
    """直接确认/误报单条异常（前端也可走 resume 批量提交）。"""
    if body.status not in (ViolationStatus.CONFIRMED.value, ViolationStatus.FALSE_POSITIVE.value):
        raise HTTPException(400, "status 必须是 CONFIRMED 或 FALSE_POSITIVE")
    v = db.get(Violation, violation_id)
    if v is None:
        raise HTTPException(404, "violation 不存在")
    v.status = body.status
    v.confirm_user = body.confirm_user
    v.confirm_time = datetime.now()
    db.commit()
    return _to_dict(v)


def _to_dict(v: Violation) -> dict:
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
