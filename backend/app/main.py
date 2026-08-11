"""FastAPI 应用入口。启动时建表，挂业务路由。"""
import logging
import os
import time

from fastapi import FastAPI, Request

from app.api import files, rules, tasks, violations
from app.db import models
from app.db.session import engine
from app.ontology.loader import ensure_loaded
from app.service import check_task_service as svc

# 日志：LOG_LEVEL env 控制（默认 INFO），DEBUG 时中间件打印每个接口入参/出参（T4.3-4）
logger = logging.getLogger("app.api")
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(title="合同校验系统", version="0.1.0")
app.include_router(files.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
app.include_router(violations.router, prefix="/api")
app.include_router(rules.router, prefix="/api")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """统一打印接口入参（method/path/query/body）与出参（status/耗时），debug 级别。

    body 仅对非 multipart（上传文件）接口打印，截断防刷屏；starlette 会缓存
    request._body，下游 handler 读 body 不受影响。
    """
    qs = request.url.query
    logger.debug("REQ %s %s?%s", request.method, request.url.path, qs)
    if "multipart" not in request.headers.get("content-type", ""):
        body = await request.body()
        if body:
            logger.debug("REQ BODY %s: %.500s", request.url.path,
                         body.decode("utf-8", "replace"))
    t0 = time.time()
    response = await call_next(request)
    dt = (time.time() - t0) * 1000
    logger.debug("RESP %s %s -> %d (%dms)", request.method, request.url.path,
                 response.status_code, dt)
    return response


@app.on_event("startup")
def startup() -> None:
    models.Base.metadata.create_all(bind=engine)
    ensure_loaded()          # 加载本体 + 版本落库（T1.1）
    svc.cleanup_terminal_checkpoints()  # 启动兜底：清理终态任务 checkpoint（T4.3-2）
    files.cleanup_orphan_files()        # 启动兜底：清理孤儿文件（T4.3-3）
    svc.recover_pending()    # 启动恢复未完成任务


@app.get("/api/health")
def health():
    return {"status": "ok"}
