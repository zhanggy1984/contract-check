"""FastAPI 应用入口。启动时建表，挂业务路由。"""
import logging
import os
import time
import uuid
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import auth, contracts, files, rules, tasks, violations
from app.common.security import require_auth
from app.common.trace import install, trace_id_var
from app.config import settings
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
# 受保护路由挂 require_auth；豁免：health（健康探针）、contracts（B.4 平台自动发现）、auth/login（登录入口）
app.include_router(files.router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(tasks.router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(violations.router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(rules.router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(auth.router, prefix="/api")
app.include_router(contracts.router, prefix="/api")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """未捕获异常兜底：防 FastAPI 默认 500 纯文本，统一结构化错误 + trace 日志。

    仅记录不向客户端泄露内部信息；HTTPException（files.py 的 400 等）走各自 handler，
    {"detail": ...} 格式不受影响。
    """
    logger.error("未捕获异常 %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(status_code=500,
                        content={"error": {"code": "INTERNAL_ERROR", "message": "服务器内部错误"}})


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


def _warn_auth_and_llm() -> None:
    """启动告警：认证配置缺失（fail-closed 提醒）+ LLM 端点外发提醒（数据出域告知）。

    LLM 判定为外部端点（非 localhost/回环/内网段）时告警，提醒合同内容将外发——
    系统定位"本地处理"仅指解析，抽取/语义校验依赖外部 LLM 必须让使用方知情。
    """
    if settings.auth_enabled:
        if not settings.jwt_secret or not settings.auth_password:
            logger.warning("鉴权已开启但未配置 AUTH_PASSWORD / JWT_SECRET——登录与受保护接口将 503 拒绝，请补齐 env")
    else:
        logger.warning("鉴权已关闭（AUTH_ENABLED=false）——仅评测/本地开发用，生产务必开启")
    if _is_external_llm(settings.deepseek_base_url):
        logger.warning("LLM 端点 %s 为外部服务：合同抽取/语义校验内容将外发，请确认数据出域合规",
                       settings.deepseek_base_url)


def _is_external_llm(url: str) -> bool:
    """LLM 端点是否外部：非回环/非内网段视为外部（DeepSeek 官方域名即外部）。"""
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1") or host.startswith("127."):
        return False
    if host.startswith("10.") or host.startswith("192.168."):
        return False
    if host.startswith("172."):
        try:
            return not (16 <= int(host.split(".")[1]) <= 31)
        except (IndexError, ValueError):
            return True
    return True


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


def _ensure_unique_index(engine, table: str, column: str) -> None:
    """幂等补唯一索引（T4.3-6 并发竞态兜底，如 ontology_version.md5）。

    create_all 不给已存在的表加约束；存量已有重复值时建索引会失败，
    先查唯一索引是否存在、数据是否唯一：重复则跳过并告警（需人工清理后再建），
    避免启动即崩——评测重建库走 schema.sql 无此问题。
    """
    with engine.begin() as conn:
        exists = conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = :t "
            "AND column_name = :c AND non_unique = 0"
        ), {"t": table, "c": column}).scalar()
        if exists:
            return
        dups = conn.execute(text(
            f"SELECT COUNT(*) FROM (SELECT {column} FROM {table} "
            f"GROUP BY {column} HAVING COUNT(*) > 1) d"
        )).scalar()
        if dups:
            logger.warning("表 %s 列 %s 存在 %s 组重复值，跳过建唯一索引（需人工清理）",
                           table, column, dups)
            return
        conn.execute(text(
            f"ALTER TABLE {table} ADD UNIQUE KEY uk_{table}_{column} ({column})"))


@app.on_event("startup")
def startup() -> None:
    # 鉴权 fail-closed：开了鉴权却没配密钥 → 显式告警（require_auth/login 会 503 拒绝，
    # 不静默放行）。LLM 端点外部化也在此告警（见下方 _warn_auth_and_llm）。
    _warn_auth_and_llm()
    models.Base.metadata.create_all(bind=engine)
    _ensure_column(engine, "check_task", "token_usage_json", "LONGTEXT")
    _ensure_column(engine, "check_task", "decision_json", "LONGTEXT")
    _ensure_column(engine, "check_task", "extraction_usage_json", "LONGTEXT")
    _ensure_column(engine, "check_task", "sem_outcomes_json", "LONGTEXT")
    _ensure_column(engine, "check_task", "sem_usage_json", "LONGTEXT")
    _ensure_column(engine, "contract_file", "page_texts_json", "LONGTEXT")
    _ensure_unique_index(engine, "ontology_version", "md5")
    ensure_loaded()          # 加载本体 + 版本落库（T1.1）
    svc.cleanup_terminal_checkpoints()  # 启动兜底：清理终态任务 checkpoint（T4.3-2）
    files.cleanup_orphan_files()        # 启动兜底：清理孤儿文件（T4.3-3）
    svc.recover_pending()    # 启动恢复未完成任务


@app.get("/api/health")
def health():
    # auth_required 供前端决定是否展示登录页（鉴权关闭的评测/开发模式跳过登录）
    return {"status": "ok", "auth_required": settings.auth_enabled}
