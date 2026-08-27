"""僵尸线程终态防覆盖单测（T4.3 review P1 修复）。

软超时（run_task_async）置 FAILED 后，to_thread 图线程无法中断，若节点不拦会继续执行，
把外部判定（超时 FAILED / 取消 CANCELLED）覆盖成中间态、WAITING_REVIEW 或 SUCCESS，
掩埋超时事实。验证：
- 全部状态节点入口对 FAILED 任务抛 RuntimeError（图终止），且不改状态（不覆盖外部判定）
- mark_waiting/finalize 条件更新（CAS）在外部终态已落库时 rowcount=0 → 抛异常，终态保持
- 正常路径回归：非终态任务照常走 CAS 写状态

mock 隔离 SessionLocal，不触真实 DB；unittest 风格（与 test_finalize.py 一致），pytest 作 runner。
"""
import unittest
from unittest.mock import patch

import app.graph.nodes as nodes


class _Task:
    id = 1
    status = "PENDING"


class _FakeViolationQuery:
    """支持 finalize 的 query(Violation.id).filter_by(...).first() 调用链。"""

    def __init__(self, has_confirmed):
        self.has_confirmed = has_confirmed

    def filter_by(self, *a, **k):
        return self

    def first(self):
        return object() if self.has_confirmed else None


class _FakeResult:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self, task, rowcount=1, has_confirmed=False):
        self.task = task
        self.rowcount = rowcount
        self.has_confirmed = has_confirmed

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, model, task_id):
        return self.task

    def execute(self, stmt, *a, **k):
        # mock 条件更新：CAS 命中时应用 values；rowcount=0（外部终态已落库）不应用
        if self.rowcount > 0:
            for key, bv in (getattr(stmt, "_values", None) or {}).items():
                col = getattr(key, "name", key)   # ORM Update._values 键是 Column 对象
                if col in ("status", "progress"):
                    setattr(self.task, col, getattr(bv, "value", bv))
        return _FakeResult(self.rowcount)

    def query(self, expr, *a, **k):
        return _FakeViolationQuery(self.has_confirmed)

    def commit(self):
        pass


class TestZombieGuard(unittest.TestCase):
    """FAILED 终态下各节点必须入口短路抛 RuntimeError，防僵尸线程覆盖超时判错。"""

    def _assert_failed_short_circuits(self, node, state=None):
        task = _Task()
        task.status = "FAILED"
        with patch.object(nodes, "SessionLocal", return_value=_FakeSession(task)):
            with self.assertRaises(RuntimeError) as cm:
                node(state or {"task_id": 1})
        self.assertIn("外部标记", str(cm.exception))
        self.assertEqual(task.status, "FAILED", "节点不得覆盖外部 FAILED（不写中间态/终态）")

    def test_parse_node_short_circuits_failed(self):
        self._assert_failed_short_circuits(nodes.parse_node)

    def test_extract_node_short_circuits_failed(self):
        self._assert_failed_short_circuits(nodes.extract_node)

    def test_validate_deterministic_short_circuits_failed(self):
        self._assert_failed_short_circuits(nodes.validate_deterministic)

    def test_validate_semantic_short_circuits_failed(self):
        self._assert_failed_short_circuits(nodes.validate_semantic)

    def test_persist_node_short_circuits_failed(self):
        self._assert_failed_short_circuits(nodes.persist_node)

    def test_mark_waiting_short_circuits_failed(self):
        self._assert_failed_short_circuits(nodes.mark_waiting)

    def test_apply_reviews_short_circuits_failed(self):
        self._assert_failed_short_circuits(nodes.apply_reviews)

    def test_finalize_short_circuits_failed(self):
        self._assert_failed_short_circuits(nodes.finalize)


class TestTerminalCas(unittest.TestCase):
    """mark_waiting/finalize 条件更新（CAS）：外部终态落库时 rowcount=0 → 不覆盖并终止图。"""

    def test_mark_waiting_cas_rowcount_zero_raises(self):
        # 入口通过（非终态）但窗口内外部 FAILED 已落库 → CAS 未命中，不写 WAITING_REVIEW
        task = _Task()
        with patch.object(nodes, "SessionLocal", return_value=_FakeSession(task, rowcount=0)):
            with self.assertRaises(RuntimeError) as cm:
                nodes.mark_waiting({"task_id": 1})
        self.assertIn("跳过待审核", str(cm.exception))
        self.assertEqual(task.status, "PENDING", "CAS 未命中不得写 WAITING_REVIEW")

    def test_finalize_cas_rowcount_zero_raises(self):
        # 零 violation 首次运行 finalize：窗口内超时 FAILED 已落库 → 不覆盖成 SUCCESS
        task = _Task()
        task.status = "REVIEWING"
        with patch.object(nodes, "SessionLocal", return_value=_FakeSession(task, rowcount=0)):
            with self.assertRaises(RuntimeError) as cm:
                nodes.finalize({"task_id": 1})
        self.assertIn("跳过定稿", str(cm.exception))
        self.assertEqual(task.status, "REVIEWING", "CAS 未命中不得覆盖外部终态")


class TestTerminalCasNormal(unittest.TestCase):
    """正常路径回归：非终态任务照常走 CAS 写状态。"""

    def test_mark_waiting_normal_sets_waiting_review(self):
        task = _Task()
        with patch.object(nodes, "SessionLocal", return_value=_FakeSession(task, rowcount=1)):
            nodes.mark_waiting({"task_id": 1})
        self.assertEqual(task.status, "WAITING_REVIEW")

    def test_finalize_normal_confirmed_leads_failed(self):
        task = _Task()
        task.status = "REVIEWING"
        with patch.object(nodes, "SessionLocal",
                          return_value=_FakeSession(task, rowcount=1, has_confirmed=True)):
            nodes.finalize({"task_id": 1})
        self.assertEqual(task.status, "FAILED", "存在 CONFIRMED violation → 定稿 FAILED")

    def test_finalize_normal_no_confirmed_leads_success(self):
        task = _Task()
        task.status = "REVIEWING"
        with patch.object(nodes, "SessionLocal", return_value=_FakeSession(task, rowcount=1)):
            nodes.finalize({"task_id": 1})
        self.assertEqual(task.status, "SUCCESS", "零 CONFIRMED → 定稿 SUCCESS")


if __name__ == "__main__":
    unittest.main()
