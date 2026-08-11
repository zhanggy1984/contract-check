"""规则管理接口（T2.5）：列表/创建/编辑/失效 + dry-run。"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.service import rule_service as svc

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("")
def list_rules(rule_type: str | None = None, source: str | None = None,
               enabled: bool | None = None, page: int = 1, size: int = 20,
               db: Session = Depends(get_db)):
    return svc.list_rules(db, rule_type, source, enabled, page, size)


class CreateBody(BaseModel):
    rule_iri: str | None = None   # 可选：缺省由规则名自动生成（用户无需填技术标识）
    name: str
    type: Literal["SEMANTIC"] = "SEMANTIC"   # 新建仅支持语义 LLM（确定性规则由本体生成）
    severity: str          # HIGH / MEDIUM / LOW
    expression: str        # LLM 判定 prompt
    aggregation: Literal["any", "all"] | None = None   # 缺失性检查用 all
    description: str | None = None


@router.post("")
def create_rule(body: CreateBody, db: Session = Depends(get_db)):
    try:
        rule = svc.create_rule(db, body.model_dump())
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"id": rule.id, "status": "created", "enabled": rule.enabled}


class UpdateBody(BaseModel):
    enabled: bool | None = None
    severity: str | None = None
    expression: str | None = None
    aggregation: Literal["any", "all"] | None = None
    description: str | None = None
    name: str | None = None


@router.put("/{rule_id}")
def update_rule(rule_id: int, body: UpdateBody, db: Session = Depends(get_db)):
    rule = svc.update_rule(db, rule_id, body.model_dump(exclude_none=True))
    if rule is None:
        raise HTTPException(404, "规则不存在")
    return {"id": rule.id, "enabled": rule.enabled, "status": "updated"}


@router.delete("/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    try:
        ok = svc.delete_rule(db, rule_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, "规则不存在")
    return {"status": "deleted"}


class DryRunBody(BaseModel):
    task_id: int


@router.post("/{rule_id}/dry-run")
def dry_run(rule_id: int, body: DryRunBody, db: Session = Depends(get_db)):
    result = svc.dry_run(db, rule_id, body.task_id)
    if result is None:
        raise HTTPException(404, "规则或任务不存在")
    return result
