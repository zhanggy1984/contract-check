"""取消语义单测（P0 review 修复）：可取消状态白名单 + CANCELLED 短路置 CANCELLED。

- cancel_task 对运行中状态（PARSING/EXTRACTING/VALIDATING）可取消（此前 409 的根因）
- _go 捕获 TaskCancelledError → 置 CANCELLED（而非 FAILED）
- persist_node / mark_waiting 对 CANCELLED 任务短路抛 TaskCancelledError（取消失效根因）

mock 隔离：SessionLocal / _run_flow / update_status / _cleanup_if_terminal，不触真实 DB。
unittest 风格（与既有测试一致），pytest 作 runner。
"""
import asyncio
import unittest
from unittest.mock import patch

import sqlalchemy.dialects.mysql as my

import app.service.check_task_service as svc
from app.common.errors import TaskCancelledError
from app.config import settings


class TestCancellableStatuses(unittest.TestCase):
    def test_running_and_waiting_states_cancellable(self):
        """抽取中/校验中/待审核可取消；REVIEWING 因竞态不可取消。"""
        statuses = set(svc._CANCELLABLE_STATUSES)
        self.assertIn("PENDING", statuses)
        self.assertIn("PARSING", statuses)
        self.assertIn("EXTRACTING", statuses)
        self.assertIn("VALIDATING", statuses)
        self.assertIn("WAITING_REVIEW", statuses)
        self.assertNotIn("REVIEWING", statuses, "resume 同步跑图期间不可取消")
        self.assertNotIn("SUCCESS", statuses)
        self.assertNotIn("FAILED", statuses)


class _FakeResult:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _FakeCancelSession:
    """支持 cancel_task 的 execute/commit 调用链。"""

    def __init__(self, rowcount):
        self.rowcount = rowcount

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        return _FakeResult(self.rowcount)

    def commit(self):
        pass


class TestCancelTask(unittest.TestCase):
    def test_cancel_success_returns_true_and_cleans_checkpoint(self):
        with patch.object(svc, "SessionLocal", return_value=_FakeCancelSession(1)), \
             patch.object(svc, "_cleanup_if_terminal") as mock_cleanup:
            ok = svc.cancel_task(1)
        self.assertTrue(ok)
        mock_cleanup.assert_called_once_with(1)

    def test_cancel_rejected_when_no_matching_row(self):
        with patch.object(svc, "SessionLocal", return_value=_FakeCancelSession(0)), \
             patch.object(svc, "_cleanup_if_terminal"):
            ok = svc.cancel_task(1)
        self.assertFalse(ok)


class _FakeRecoverSession:
    """支持 recover_pending 的 query().filter().all() + execute/commit 调用链。"""

    def __init__(self, runnable_ids, rowcount=1):
        self.runnable_ids = runnable_ids
        self.rowcount = rowcount
        self.executed = None
        self.last_result = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def query(self, model):
        return self

    def filter(self, *a, **k):
        return self

    def all(self):
        return [type("_T", (), {"id": i})() for i in self.runnable_ids]

    def execute(self, stmt, *a, **k):
        self.executed = stmt
        self.last_result = _FakeResult(self.rowcount)
        return self.last_result

    def commit(self):
        pass


class TestRecoverPending(unittest.TestCase):
    """启动恢复自愈：REVIEWING（resume 崩溃残留）回退 WAITING_REVIEW，PENDING 等照常重跑。"""

    def _run(self, runnable_ids, rowcount=1):
        captured = []
        sess = _FakeRecoverSession(runnable_ids, rowcount)
        with patch.object(svc, "SessionLocal", return_value=sess), \
             patch.object(svc, "run_task_async",
                          side_effect=lambda tid: captured.append(tid)):
            svc.recover_pending()
        return sess, captured

    def test_reviewing_rolls_back_to_waiting(self):
        # DB 有 PENDING(1) 与 REVIEWING(2)：REVIEWING 走回退 UPDATE，PENDING 照常重跑
        sess, captured = self._run(runnable_ids=[1])
        self.assertIsNotNone(sess.executed, "必须发出 REVIEWING→WAITING_REVIEW 回退 UPDATE")
        sql = str(sess.executed.compile(dialect=my.dialect(),
                                        compile_kwargs={"literal_binds": True}))
        self.assertIn("REVIEWING", sql, "回退条件限定 REVIEWING（CAS，不误伤活线程）")
        self.assertIn("WAITING_REVIEW", sql, "回退目标为 WAITING_REVIEW")
        self.assertEqual(captured, [1], "仅 PENDING/PARSING/EXTRACTING/VALIDATING 重跑")

    def test_no_pending_still_emits_rollback(self):
        # 无待重跑任务时：回退 UPDATE 仍执行（rowcount 可能 0，无害），不调 run_task_async
        sess, captured = self._run(runnable_ids=[])
        self.assertIsNotNone(sess.executed)
        self.assertEqual(captured, [])

    def test_rowcount_zero_rollback_is_harmless(self):
        # 无 REVIEWING 任务（UPDATE rowcount=0）时不报错，PENDING 照常重跑
        sess, captured = self._run(runnable_ids=[7], rowcount=0)
        self.assertEqual(sess.last_result.rowcount, 0)
        self.assertEqual(captured, [7])


class TestCancelledShortCircuit(unittest.TestCase):
    """_go 捕获 TaskCancelledError → CANCELLED（沿用 test_task_timeout 的 update_status 捕获手法）。"""

    def test_cancelled_error_marks_cancelled(self):
        captured = {}

        def _cancel_run(task_id, reviews=None):
            raise TaskCancelledError("任务已取消")

        def _fake_update(task_id, status, error=None):
            captured["status"] = status
            captured["error"] = error

        async def _runner():
            svc.run_task_async(1)
            await asyncio.sleep(0.3)

        with patch.object(svc, "_run_flow", side_effect=_cancel_run), \
             patch.object(svc, "update_status", side_effect=_fake_update), \
             patch.object(svc, "_cleanup_if_terminal"), \
             patch.object(settings, "task_timeout_seconds", 10):
            asyncio.run(_runner())

        self.assertEqual(captured.get("status"), "CANCELLED")
        self.assertIn("取消", captured.get("error", ""))


class _CancelledTask:
    id = 888
    status = "CANCELLED"


class _FakeNodeSession:
    """支持 nodes 节点入口的 get/commit（仿 test_finalize.py）。"""

    def __init__(self, task):
        self.task = task

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, model, task_id):
        return self.task

    def commit(self):
        pass


class TestNodeShortCircuit(unittest.TestCase):
    """persist/mark_waiting 对 CANCELLED 任务必须短路，否则取消失效（覆盖成 WAITING_REVIEW/落库）。"""

    def _patch(self, node_module, task):
        return patch.object(node_module, "SessionLocal", return_value=_FakeNodeSession(task))

    def test_persist_node_short_circuits_cancelled(self):
        import app.graph.nodes as nodes
        with self._patch(nodes, _CancelledTask()):
            with self.assertRaises(nodes.TaskCancelledError):
                nodes.persist_node({"task_id": 888})

    def test_mark_waiting_short_circuits_cancelled(self):
        import app.graph.nodes as nodes
        with self._patch(nodes, _CancelledTask()):
            with self.assertRaises(nodes.TaskCancelledError):
                nodes.mark_waiting({"task_id": 888})

    def test_validate_deterministic_short_circuits_cancelled(self):
        import app.graph.nodes as nodes
        with self._patch(nodes, _CancelledTask()):
            with self.assertRaises(nodes.TaskCancelledError):
                nodes.validate_deterministic({"task_id": 888})


if __name__ == "__main__":
    unittest.main()
