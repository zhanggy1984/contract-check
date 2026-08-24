"""任务执行：后台跑图、resume（CAS 抢占+失败回退）、cancel、删除、启动恢复。"""
import asyncio
import logging
from pathlib import Path

from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver
from langgraph.types import Command
from sqlalchemy import update

from app.common.constants import TaskStatus, ViolationStatus
from app.common.errors import TaskCancelledError
from app.config import settings
from app.graph.build import DATABASE_URL, build_graph
from app.db.models import CheckTask, ContractFile, RuleCheckResult, Violation
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

# 取消短路由各节点入口检查 CANCELLED 实现；此处 asyncio.to_thread 不阻塞事件循环
_ACTIVE = set()  # 运行中 task_id，供 cancel 判断（Phase 0 简化）

PARSED_DIR = Path("data/parsed")  # 物理解析文本目录（与 files.py 保持一致）

# 可取消状态（P0 review）：抽取中/校验中/待审核可取消——运行中任务靠节点入口
# CANCELLED 短路（persist/mark_waiting 检查后抛 TaskCancelledError）；
# REVIEWING 不可取消（resume 同步跑图，取消会让图线程写终态覆盖 CANCELLED，T4.3 review P1）
_CANCELLABLE_STATUSES = [
    TaskStatus.PENDING.value, TaskStatus.PARSING.value,
    TaskStatus.EXTRACTING.value, TaskStatus.VALIDATING.value,
    TaskStatus.WAITING_REVIEW.value,
]


def _run_flow(task_id: int, reviews: dict | None = None) -> None:
    """同步执行图：首次运行至 interrupt，或 resume 续跑。"""
    with PyMySQLSaver.from_conn_string(DATABASE_URL) as saver:
        saver.setup()
        graph = build_graph().compile(checkpointer=saver)
        cfg = {"configurable": {"thread_id": f"task-{task_id}"}}
        if reviews is None:
            graph.invoke({"task_id": task_id}, cfg)
        else:
            graph.invoke(Command(resume=reviews), cfg)


def _cleanup_if_terminal(task_id: int) -> None:
    """终态任务清理 langgraph checkpoint（T4.3-2）。

    终态（SUCCESS/FAILED/CANCELLED）不再 resume，checkpoint 是死数据。线程安全无害：
    即使超时后后台线程仍在写，任务已 FAILED 不会 resume，轻微残留不影响。
    """
    with SessionLocal() as db:
        t = db.get(CheckTask, task_id)
        if t is None or t.status not in (
                TaskStatus.SUCCESS.value, TaskStatus.FAILED.value,
                TaskStatus.CANCELLED.value):
            return
    try:
        with PyMySQLSaver.from_conn_string(DATABASE_URL) as saver:
            saver.delete_thread(f"task-{task_id}")
    except Exception:
        logger.warning("清理 checkpoint 失败 task_id=%s", task_id)


def cleanup_terminal_checkpoints(limit: int = 500) -> int:
    """启动兜底：清理终态任务 + 孤儿（无对应任务）checkpoint，返回清理数。"""
    from sqlalchemy import text

    with SessionLocal() as db:
        ids = [
            t.id for t in db.query(CheckTask).filter(
                CheckTask.status.in_([
                    TaskStatus.SUCCESS.value, TaskStatus.FAILED.value,
                    TaskStatus.CANCELLED.value,
                ])
            ).order_by(CheckTask.id.desc()).limit(limit).all()
        ]
        known_ids = {t.id for t in db.query(CheckTask).all()}
        threads = [r[0] for r in db.execute(text("SELECT DISTINCT thread_id FROM checkpoints")).all()]

    def _is_orphan(thread: str) -> bool:
        if not thread.startswith("task-"):
            return True  # 非 task-N 命名（如历史 spike-1）
        try:
            return int(thread.removeprefix("task-")) not in known_ids
        except ValueError:
            return True

    targets = {f"task-{tid}" for tid in ids} | {t for t in threads if _is_orphan(t)}
    cleaned = 0
    try:
        with PyMySQLSaver.from_conn_string(DATABASE_URL) as saver:
            for thread in targets:
                try:
                    saver.delete_thread(thread)
                    cleaned += 1
                except Exception:
                    logger.warning("清理 checkpoint 失败 thread=%s", thread)
    except Exception:
        logger.warning("checkpoint 清理连接异常")
    return cleaned


def update_status(task_id: int, status: str, progress: int | None = None, error: str | None = None) -> None:
    with SessionLocal() as db:
        task = db.get(CheckTask, task_id)
        if task is None:
            return
        task.status = status
        if progress is not None:
            task.progress = progress
        if error is not None:
            # T232：error_message 是 varchar(1000)，超长异常堆栈（langgraph/pymysql 层层嵌套）
            # 直接赋值会 1406 导致状态更新失败——任务永久卡在旧状态（如 VALIDATING），
            # 平台侧 300s 轮询超时判错。截断保证状态一定落库。
            task.error_message = error[:1000]
        db.commit()


def run_task_async(task_id: int) -> None:
    """后台异步执行任务流程；异常/超时置 FAILED。

    软超时（T4.3）：to_thread 的图线程无法强制中断，wait_for 超时先标记 FAILED
    让用户尽快看到结果；图线程跑完若真实成功，后续节点写 SUCCESS 覆盖（真实结果优先）。
    """
    _ACTIVE.add(task_id)
    timeout = settings.task_timeout_seconds

    async def _go():
        try:
            await asyncio.wait_for(
                asyncio.to_thread(_run_flow, task_id), timeout=timeout)
        except asyncio.TimeoutError:
            update_status(task_id, TaskStatus.FAILED.value,
                          error=f"任务执行超时（超过 {timeout} 秒）")
        except TaskCancelledError:  # 节点入口 CANCELLED 短路 → 置 CANCELLED 而非 FAILED
            update_status(task_id, TaskStatus.CANCELLED.value, error="任务已取消")
        except Exception as e:
            update_status(task_id, TaskStatus.FAILED.value, error=str(e))
        finally:
            _ACTIVE.discard(task_id)
            _cleanup_if_terminal(task_id)  # 终态即清 checkpoint（T4.3-2）

    asyncio.create_task(_go())


def resume_task(task_id: int, reviews: list) -> bool:
    """resume：reviews 覆盖校验 + CAS 抢占；invoke 失败幂等回退。

    - 覆盖校验（C4）：提交的 reviews 必须覆盖全部 UNCONFIRMED violation，否则拒绝
    - CAS 抢占（C5）：WAITING_REVIEW→REVIEWING，并发/已处理返回 False（409）
    - 回退（C6）：invoke 异常仅 REVIEWING 才回退 WAITING_REVIEW，防与 cancel 竞态
    """
    # 防御（与 apply_reviews 一致）：载荷须为 dict、action 合法、violation_id 可转 int，
    # 否则拒绝——避免非法载荷 500 或"action 非法被跳过→UNCONFIRMED 残留却 SUCCESS"
    submitted_ids: set[int] = set()
    for r in reviews:
        if not isinstance(r, dict) or r.get("action") not in (
                ViolationStatus.CONFIRMED.value, ViolationStatus.FALSE_POSITIVE.value):
            return False
        try:
            submitted_ids.add(int(r.get("violation_id")))
        except (TypeError, ValueError):
            return False
    with SessionLocal() as db:
        unconfirmed_ids = {
            v.id for v in db.query(Violation).filter_by(
                task_id=task_id, status=ViolationStatus.UNCONFIRMED.value).all()
        }
        if not unconfirmed_ids.issubset(submitted_ids):
            return False  # 部分提交或含无关 violation → 拒绝
        res = db.execute(
            update(CheckTask)
            .where(CheckTask.id == task_id, CheckTask.status == TaskStatus.WAITING_REVIEW.value)
            .values(status=TaskStatus.REVIEWING.value)
        )
        db.commit()
        if res.rowcount == 0:
            return False
    try:
        _run_flow(task_id, reviews={"reviews": reviews})
        _cleanup_if_terminal(task_id)  # resume 到 SUCCESS 后清理（T4.3-2）
        return True
    except Exception:
        # 幂等回退：仅 REVIEWING 才回退，防与 cancel 竞态
        with SessionLocal() as db:
            res = db.execute(
                update(CheckTask)
                .where(CheckTask.id == task_id, CheckTask.status == TaskStatus.REVIEWING.value)
                .values(status=TaskStatus.WAITING_REVIEW.value)
            )
            db.commit()
        return False


def cancel_task(task_id: int) -> bool:
    with SessionLocal() as db:
        res = db.execute(
            update(CheckTask)
            .where(
                CheckTask.id == task_id,
                CheckTask.status.in_(_CANCELLABLE_STATUSES),
            )
            .values(status=TaskStatus.CANCELLED.value)
        )
        db.commit()
        if res.rowcount == 0:
            return False
        _cleanup_if_terminal(task_id)  # 取消后清理 checkpoint（T4.3-2）
        return True


def delete_task(task_id: int) -> tuple[bool, str]:
    """删除任务及级联数据；运行中（PENDING/PARSING/EXTRACTING/VALIDATING/REVIEWING）拒绝。

    删除内容：violation → rule_check_result → check_task → checkpoint thread；
    contract_file 若删除后无其他任务引用（sha256 幂等复用场景保留），则连同物理文件
    （data/uploads/{sha}.ext + data/parsed/{sha}.txt）一起删。
    """
    with SessionLocal() as db:
        task = db.get(CheckTask, task_id)
        if task is None:
            return False, "任务不存在"
        if task.status in (
            TaskStatus.PENDING.value, TaskStatus.PARSING.value,
            TaskStatus.EXTRACTING.value, TaskStatus.VALIDATING.value,
            TaskStatus.REVIEWING.value,
        ):
            return False, "任务运行中，不可删除"
        cf = task.contract_file
        # 删除顺序关键：rule_check_result.violation_id 引用 violation.id（persist 回填），
        # 必须先删 rcr 再删 violation，否则 FK 1451
        db.query(RuleCheckResult).filter_by(task_id=task_id).delete(synchronize_session=False)
        db.query(Violation).filter_by(task_id=task_id).delete(synchronize_session=False)
        db.delete(task)
        db.flush()  # 先落删除，再查剩余引用，避免把当前 task 计入
        refs = db.query(CheckTask).filter(CheckTask.contract_file_id == cf.id).count()
        file_paths = []
        if refs == 0:
            file_paths = [cf.storage_path, str(PARSED_DIR / f"{cf.sha256}.txt")]
            db.delete(cf)
        db.commit()
    try:
        with PyMySQLSaver.from_conn_string(DATABASE_URL) as saver:
            saver.delete_thread(f"task-{task_id}")
    except Exception:
        logger.warning("删除 checkpoint 失败 task_id=%s", task_id)
    for p in file_paths:
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:
            logger.warning("删除物理文件失败: %s", p)
    return True, "已删除"


def recover_pending() -> None:
    """启动时恢复未完成任务（PENDING/PARSING/EXTRACTING/VALIDATING）。"""
    with SessionLocal() as db:
        ids = [
            t.id for t in db.query(CheckTask).filter(
                CheckTask.status.in_([
                    TaskStatus.PENDING.value,
                    TaskStatus.PARSING.value,
                    TaskStatus.EXTRACTING.value,
                    TaskStatus.VALIDATING.value,
                ])
            ).all()
        ]
    for tid in ids:
        run_task_async(tid)
