"""上传接口：保存文件、提取文本、幂等去重、创建校验任务。"""
import hashlib
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.common.constants import TaskStatus
from app.config import settings
from app.db.models import CheckTask, ContractFile
from app.db.session import SessionLocal, get_db
from app.parser.docx_parser import extract_docx
from app.parser.pdf_parser import extract_pdf
from app.service import check_task_service as svc

router = APIRouter(prefix="/files", tags=["files"])

logger = logging.getLogger(__name__)

EXT_TYPE = {"pdf": "PDF", "docx": "DOCX", "doc": "DOC"}
MAX_BYTES = settings.max_upload_mb * 1024 * 1024
UPLOAD_DIR = Path(settings.upload_dir)
PARSED_DIR = Path("data/parsed")

# 孤儿文件须"稳定"一段时间才删（T4.3 review P2）：上传是"先写文件后 commit"，
# 太新的文件可能是正在写入、DB 尚未登记，删了会丢用户原件
STALE_ORPHAN_MINUTES = 60


def _extract(ext: str, path: str) -> tuple[str, bool]:
    if ext == "pdf":
        return extract_pdf(path)
    return extract_docx(path), False


def cleanup_orphan_files() -> int:
    """T4.3-3：清理孤儿文件——磁盘上 sha 不在 contract_file 记录里的残留。

    只清无 DB 记录、且修改时间早于 STALE_ORPHAN_MINUTES 的残留（上传中断/历史遗留）；
    DB 有记录或太新的文件一律不动（原件可能被审核引用，parsed txt 可能被同 sha 任务复用，
    太新的文件可能是上传写入中尚未 commit）。
    """
    with SessionLocal() as db:
        known = {cf.sha256 for cf in db.query(ContractFile).all()}
    cutoff = time.time() - STALE_ORPHAN_MINUTES * 60
    removed = 0
    for d in (UPLOAD_DIR, PARSED_DIR):
        if not d.exists():
            continue
        for p in d.glob("*"):
            if p.suffix.lower() not in (".txt", ".pdf", ".docx"):
                continue
            if p.stem in known:
                continue
            if p.stat().st_mtime > cutoff:
                continue  # 太新，可能是正在写入/未登记的上传
            p.unlink()
            logger.info("清理孤儿文件: %s", p)
            removed += 1
    return removed


@router.post("/upload")
async def upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    # 类型与大小校验
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    ftype = EXT_TYPE.get(ext)
    if ftype is None:
        raise HTTPException(400, "仅支持 PDF / DOCX 文件")
    if ext == "doc":
        raise HTTPException(400, "不支持旧版 .doc，请转存为 .docx 或 PDF")

    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f"文件超过 {settings.max_upload_mb}MB 上限")

    # sha256 幂等去重
    sha = hashlib.sha256(data).hexdigest()
    cf = db.query(ContractFile).filter(ContractFile.sha256 == sha).first()

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    PARSED_DIR.mkdir(parents=True, exist_ok=True)

    if cf is None:
        storage_path = str(UPLOAD_DIR / f"{sha}.{ext}")
        with open(storage_path, "wb") as f:
            f.write(data)
        try:
            text, has_scanned = _extract(ext, storage_path)
        except Exception as e:
            # F4：损坏/非法文件解析失败 → 明确 400，不留 500（残留文件由孤儿清理兜底）
            logger.warning("文件解析失败 %s: %s", storage_path, e)
            raise HTTPException(400, f"文件解析失败：{str(e)[:100]}")
        (PARSED_DIR / f"{sha}.txt").write_text(text or "", encoding="utf-8")
        cf = ContractFile(
            file_name=file.filename, file_type=ftype, storage_path=storage_path,
            file_size=len(data), sha256=sha, has_scanned=has_scanned,
        )
        db.add(cf)
        db.commit()
        db.refresh(cf)

    # 创建校验任务并后台启动图执行
    task = CheckTask(contract_file_id=cf.id, status=TaskStatus.PENDING.value, progress=0)
    db.add(task)
    db.commit()
    db.refresh(task)
    svc.run_task_async(task.id)

    char_count = 0
    if not cf.has_scanned:
        txt = PARSED_DIR / f"{sha}.txt"
        if not txt.exists():
            # 复用分支但 txt 缺失（换环境/volume 变更）：尝试从存储件重建，避免 500
            if Path(cf.storage_path).exists():
                text, _ = _extract(Path(cf.storage_path).suffix.lstrip(".").lower(),
                                   cf.storage_path)
                txt.write_text(text or "", encoding="utf-8")
        if txt.exists():
            char_count = len(txt.read_text(encoding="utf-8"))
    return {"task_id": task.id, "file_id": cf.id, "has_scanned": cf.has_scanned, "char_count": char_count}
