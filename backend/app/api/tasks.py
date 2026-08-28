"""任务接口：轮询、结果、历史列表、resume、cancel、报告导出。

四层分层收口：本文件只做 HTTP 契约（路径/参数校验/HTTP 状态码/响应组装），
查询与渲染全部委托 check_task_service（控制层）。
"""
import asyncio
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.service import check_task_service as svc

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}")
def get_task(task_id: int):
    """前端轮询任务状态。"""
    result = svc.get_task(task_id)
    if result is None:
        raise HTTPException(404, "任务不存在")
    return result


@router.get("/{task_id}/report")
def download_report(task_id: int, format: str = "pdf"):
    """校验报告导出：format=pdf|xlsx。文件名 RFC 5987 编码支持中文。"""
    if format not in ("pdf", "xlsx"):
        raise HTTPException(400, "format 必须是 pdf 或 xlsx")
    try:
        buf, media_type, ext, file_name = svc.render_report(task_id, format)
    except ValueError:
        raise HTTPException(404, "任务不存在")

    filename = f"合同校验报告_{file_name}_{task_id}.{ext}"
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return StreamingResponse(buf, media_type=media_type,
                             headers={"Content-Disposition": disposition})


@router.get("/{task_id}/result")
def get_task_result(task_id: int):
    """结果页：标准文本 JSON + 校验明细全量 + violations + 评测契约字段（B.4）。"""
    try:
        return svc.get_task_result(task_id)
    except ValueError:
        raise HTTPException(404, "任务不存在")


@router.get("")
def list_tasks(status: str | None = None, file_name: str | None = None,
               page: int = Query(1, ge=1), size: int = Query(10, ge=0, le=100)):
    """历史记录：分页 + 状态/文件名筛选。"""
    return svc.list_tasks(status=status, file_name=file_name, page=page, size=size)


class ResumeBody(BaseModel):
    reviews: list  # 覆盖全部 UNCONFIRMED 的 [{violation_id, action}]


@router.post("/{task_id}/resume")
async def resume(task_id: int, body: ResumeBody):
    """CAS 抢占后 resume；并发/失败返回 409。"""
    ok = await asyncio.to_thread(svc.resume_task, task_id, body.reviews)
    if not ok:
        raise HTTPException(409, "任务不在可审核状态（或已被处理）")
    return {"status": "resumed"}


@router.post("/{task_id}/cancel")
async def cancel(task_id: int):
    ok = svc.cancel_task(task_id)
    if not ok:
        raise HTTPException(409, "任务当前不可取消")
    return {"status": "cancelled"}


@router.delete("/{task_id}")
async def delete_task(task_id: int):
    """删除任务（级联 violations/校验明细/checkpoint，独占文件一并删除）。运行中拒绝。"""
    ok, msg = svc.delete_task(task_id)
    if not ok:
        raise HTTPException(409, msg)
    return {"status": "deleted"}
