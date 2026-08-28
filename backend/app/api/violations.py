"""异常（violation）接口：查询列表 + 人工确认/误报状态更新。

四层分层收口：本文件只做 HTTP 契约（参数校验/状态码），查询与状态变更委托
check_task_service（控制层）；violation 序列化经资源层 db/serializers 复用。
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.common.constants import ViolationStatus
from app.service import check_task_service as svc

router = APIRouter(prefix="/violations", tags=["violations"])


@router.get("")
def list_violations(task_id: int | None = None, status: str | None = None,
                    rule_type: str | None = None, severity: str | None = None,
                    page: int = Query(1, ge=1), size: int = Query(20, ge=0, le=100)):
    """按任务/状态/规则类型/严重级别筛选，分页。"""
    return svc.list_violations(task_id=task_id, status=status, rule_type=rule_type,
                               severity=severity, page=page, size=size)


class StatusBody(BaseModel):
    status: str                 # CONFIRMED / FALSE_POSITIVE
    confirm_user: str | None = Field(None, max_length=50)   # 对齐 DB VARCHAR(50)，超长 422 防 DataError


@router.patch("/{violation_id}/status")
def update_status(violation_id: int, body: StatusBody):
    """直接确认/误报单条异常（前端也可走 resume 批量提交）。"""
    if body.status not in (ViolationStatus.CONFIRMED.value, ViolationStatus.FALSE_POSITIVE.value):
        raise HTTPException(400, "status 必须是 CONFIRMED 或 FALSE_POSITIVE")
    try:
        return svc.update_violation_status(violation_id, body.status, body.confirm_user)
    except ValueError:
        raise HTTPException(404, "violation 不存在")
