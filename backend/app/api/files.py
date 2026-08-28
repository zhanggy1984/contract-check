"""上传接口：保存文件、提取文本、幂等去重、创建校验任务。"""
import hashlib
import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError
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

EXT_TYPE = {"pdf": "PDF", "docx": "DOCX"}   # 只列真正受理的类型；.doc 走 EXT_TYPE 未命中统一 400
MAX_BYTES = settings.max_upload_mb * 1024 * 1024
UPLOAD_DIR = Path(settings.upload_dir)
PARSED_DIR = Path("data/parsed")
# DB file_name 为 VARCHAR(255)；前端无长度提示，超长截断自愈（存储路径用 sha，截断只影响展示名）
FILE_NAME_MAX = 255

# 孤儿文件须"稳定"一段时间才删（T4.3 review P2）：上传是"先写文件后 commit"，
# 太新的文件可能是正在写入、DB 尚未登记，删了会丢用户原件
STALE_ORPHAN_MINUTES = 60


def _sanitize_filename(name: str | None) -> str:
    """上传文件名清洗（T4.3-8）：None→""、超 255 时保留扩展名截断主干
    （DB VARCHAR(255)，防 DataError 1406；存储路径用 sha，截断只影响展示名）。

    截断保留最后一个扩展名（a.tar.gz → a.tar 截主干 + .gz），展示一致性优先；
    无扩展名（或扩展名自身超长）时退化裸截断。后端自愈而非 422 拒绝——前端无长度提示。
    """
    if not name:
        return ""
    if len(name) <= FILE_NAME_MAX:
        return name
    dot = name.rfind(".")
    if dot > 0 and len(name) - dot <= FILE_NAME_MAX:   # 扩展名（含点）自身不超限才保留
        keep = FILE_NAME_MAX - (len(name) - dot)
        return name[:keep] + name[dot:]
    return name[:FILE_NAME_MAX]


def _extract(ext: str, path: str) -> tuple[str, list[str] | None]:
    """提取文本 → (合并文本, 页级文本列表)。PDF 页级提取；DOCX 无页概念返回 None。"""
    if ext == "pdf":
        return extract_pdf(path)   # (text, page_texts)
    return extract_docx(path), None


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
            text, page_texts = _extract(ext, storage_path)
        except Exception as e:
            # F4：损坏/非法文件解析失败 → 明确 400，不留 500（残留文件由孤儿清理兜底）。
            # 回通用文案不泄露内部路径/库错误细节，细节已进日志
            logger.warning("文件解析失败 %s: %s", storage_path, e)
            raise HTTPException(400, "文件解析失败，请检查文件格式或内容")
        (PARSED_DIR / f"{sha}.txt").write_text(text or "", encoding="utf-8")
        # 混合扫描 PDF：页级文本落库为单一事实来源（parse_node 逐页 OCR 用），
        # has_scanned 由页级派生（存在清洗后为空的页），比整篇判空精确
        has_scanned = bool(page_texts) and any(not t.strip() for t in page_texts)
        cf = ContractFile(
            file_name=_sanitize_filename(file.filename), file_type=ftype, storage_path=storage_path,
            file_size=len(data), sha256=sha, has_scanned=has_scanned,
            page_texts_json=json.dumps(page_texts, ensure_ascii=False) if page_texts is not None else None,
        )
        db.add(cf)
        try:
            db.commit()
        except IntegrityError:
            # 并发上传同 sha（T4.3-7）：另一线程已提交相同 sha，回退复用已有记录。
            # 同 sha 同路径，本线程写盘文件即成功线程引用的文件，无孤儿残留；
            # rollback 只回滚本 session 的 cf（upload 无调用方 pending，安全）
            db.rollback()
            cf = db.query(ContractFile).filter(ContractFile.sha256 == sha).first()
            if cf is None:
                raise
        else:
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
