"""任务接口：轮询、结果、历史列表、resume、cancel、报告导出。"""
import asyncio
import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from app.api.violations import _to_dict as violation_to_dict
from app.db.models import CheckRule, CheckTask, ContractFile, RuleCheckResult, Violation
from app.db.session import get_db
from app.report import excel_generator, pdf_generator
from app.report.report_data import build_report_data
from app.service import check_task_service as svc

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}")
def get_task(task_id: int, db: Session = Depends(get_db)):
    """前端轮询任务状态。"""
    task = db.get(CheckTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    conflicts = json.loads(task.extraction_conflicts) if task.extraction_conflicts else []
    return {"id": task.id, "status": task.status, "progress": task.progress,
            "message": task.error_message, "extraction_status": task.extraction_status,
            "conflicts": conflicts}


@router.get("/{task_id}/report")
def download_report(task_id: int, format: str = "pdf", db: Session = Depends(get_db)):
    """校验报告导出：format=pdf|xlsx。文件名 RFC 5987 编码支持中文。"""
    try:
        data = build_report_data(db, task_id)
    except ValueError:
        raise HTTPException(404, "任务不存在")
    if format == "pdf":
        buf, media_type, ext = pdf_generator.render(data), "application/pdf", "pdf"
    elif format == "xlsx":
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        buf, ext = excel_generator.render(data), "xlsx"
    else:
        raise HTTPException(400, "format 必须是 pdf 或 xlsx")

    filename = f"合同校验报告_{data.file_name}_{task_id}.{ext}"
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return StreamingResponse(buf, media_type=media_type,
                             headers={"Content-Disposition": disposition})


@router.get("/{task_id}/result")
def get_task_result(task_id: int, db: Session = Depends(get_db)):
    """结果页：标准文本 JSON + 校验明细全量 + violations。"""
    task = db.get(CheckTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    violations = db.query(Violation).filter_by(task_id=task_id).order_by(Violation.id).all()
    results = db.query(RuleCheckResult).filter_by(task_id=task_id).order_by(RuleCheckResult.id).all()
    # 规则名称映射：结果明细带 rule_name 供前端展示（rule_id 用户看不懂）
    rule_names = {r.id: r.rule_name for r in db.query(CheckRule).all()}
    return {
        "id": task.id,
        "status": task.status,
        "extraction_status": task.extraction_status,
        "standard_json": json.loads(task.standard_json) if task.standard_json else None,
        "violations": [violation_to_dict(v) for v in violations],
        "rule_results": [_rule_result_dict(r, rule_names.get(r.rule_id)) for r in results],
    }


def _rule_result_dict(r: RuleCheckResult, rule_name: str | None = None) -> dict:
    return {
        "id": r.id, "rule_id": r.rule_id, "rule_name": rule_name,
        "result": r.result, "rule_type": r.rule_type,
        "severity": r.severity, "concept_iri": r.concept_iri, "property_iri": r.property_iri,
        "segment_ref": r.segment_ref, "evidence_text": r.evidence_text,
        "message": r.message, "confidence": r.confidence, "violation_id": r.violation_id,
    }


@router.get("")
def list_tasks(status: str | None = None, file_name: str | None = None,
               page: int = 1, size: int = 10, db: Session = Depends(get_db)):
    """历史记录：分页 + 状态/文件名筛选。joinedload 防 N+1（t.contract_file 每行懒加载）。"""
    q = db.query(CheckTask).options(joinedload(CheckTask.contract_file))
    if status:
        q = q.filter(CheckTask.status == status)
    if file_name:
        q = q.join(ContractFile).filter(ContractFile.file_name.like(f"%{file_name}%"))
    total = q.count()
    items = q.order_by(CheckTask.id.desc()).offset((page - 1) * size).limit(size).all()
    return {
        "total": total, "page": page, "size": size,
        "items": [{
            "id": t.id, "status": t.status, "extraction_status": t.extraction_status,
            "file_name": t.contract_file.file_name,
            "create_time": t.create_time.isoformat() if t.create_time else None,
        } for t in items],
    }


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
