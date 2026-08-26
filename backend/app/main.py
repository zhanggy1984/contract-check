"""FastAPI 应用入口。启动时建表，挂业务路由。"""
import logging
import os
import time
import uuid

from fastapi import FastAPI, Request
from sqlalchemy import text

from app.api import contracts, files, rules, tasks, violations
from app.common.trace import install, trace_id_var
from app.db import models
from app.db.session import engine
from app.ontology.loader import ensure_loaded
from app.service import check_task_service as svc

# 日志：LOG_LEVEL env 控制（默认 INFO），DEBUG 时中间件打印每个接口入参/出参（T4.3-4）
# [%(trace_id)s]：链路追踪，值来自中间件注入的 X-Request-ID（网关生成）或本地 uuid
logger = logging.getLogger("app.api")
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: [%(trace_id)s] %(message)s",
)
# 挂 trace_id filter（install 内 root logger + handler 双挂）：见 trace.install 说明，
# 关键在 handler filter 于 emit 前应用，子 logger 传播的记录也会带 trace_id
install()

app = FastAPI(title="合同校验系统", version="0.1.0")
app.include_router(files.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
app.include_router(violations.router, prefix="/api")
app.include_router(rules.router, prefix="/api")
app.include_router(contracts.router, prefix="/api")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """统一打印接口入参（method/path/query/body）与出参（status/耗时），debug 级别。

    body 仅对非 multipart（上传文件）接口打印，截断防刷屏；starlette 会缓存
    request._body，下游 handler 读 body 不受影响。

    链路追踪：取网关透传的 X-Request-ID（无则生成 uuid），写入 contextvar 供
    日志 filter 使用，并在响应头回传（经网关时网关会隐藏后端重复头，无副作用）。
    """
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    trace_id_var.set(rid)

    qs = request.url.query
    logger.debug("REQ %s %s?%s", request.method, request.url.path, qs)
    if "multipart" not in request.headers.get("content-type", ""):
        body = await request.body()
        if body:
            logger.debug("REQ BODY %s: %.500s", request.url.path,
                         body.decode("utf-8", "replace"))
    t0 = time.time()
    response = await call_next(request)
    response.headers.setdefault("X-Request-ID", rid)
    dt = (time.time() - t0) * 1000
    logger.debug("RESP %s %s -> %d (%dms)", request.method, request.url.path,
                 response.status_code, dt)
    return response


def _ensure_column(engine, table: str, column: str, ddl_type: str) -> None:
    """幂等补列：create_all 不修改已存在的表，旧库升级需手动 ALTER（B.4 token_usage_json）。

    MySQL 无 ADD COLUMN IF NOT EXISTS，先查 information_schema 缺列再 ALTER。
    """
    with engine.begin() as conn:
        exists = conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
        ), {"t": table, "c": column}).scalar()
        if not exists:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))


@app.on_event("startup")
def startup() -> None:
    models.Base.metadata.create_all(bind=engine)
    _ensure_column(engine, "check_task", "token_usage_json", "LONGTEXT")
    ensure_loaded()          # 加载本体 + 版本落库（T1.1）
    svc.cleanup_terminal_checkpoints()  # 启动兜底：清理终态任务 checkpoint（T4.3-2）
    files.cleanup_orphan_files()        # 启动兜底：清理孤儿文件（T4.3-3）
    svc.recover_pending()    # 启动恢复未完成任务


@app.get("/api/health")
def health():
    return {"status": "ok"}
