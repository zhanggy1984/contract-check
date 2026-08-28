"""上传接口：接收文件 → 委托 service 保存解析并建任务。

四层分层收口：本文件只做 HTTP 契约（类型/大小校验与状态码），
解析、去重落库、建任务全部委托 check_task_service.save_uploaded_file（控制层）。
"""
from fastapi import APIRouter, File, HTTPException, UploadFile

from app.config import settings
from app.service import check_task_service as svc

router = APIRouter(prefix="/files", tags=["files"])

EXT_TYPE = {"pdf": "PDF", "docx": "DOCX"}   # 只列真正受理的类型；.doc 走 EXT_TYPE 未命中统一 400
MAX_BYTES = settings.max_upload_mb * 1024 * 1024


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    # 类型与大小校验（交互层解析请求）
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    ftype = EXT_TYPE.get(ext)
    if ftype is None:
        raise HTTPException(400, "仅支持 PDF / DOCX 文件")

    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f"文件超过 {settings.max_upload_mb}MB 上限")

    try:
        return svc.save_uploaded_file(original_name=file.filename, ext=ext,
                                      file_type=ftype, data=data)
    except ValueError:
        # F4：损坏/非法文件解析失败 → 明确 400，不留 500（残留文件由孤儿清理兜底）。
        # 回通用文案不泄露内部路径/库错误细节，细节已进 service 日志
        raise HTTPException(400, "文件解析失败，请检查文件格式或内容")
