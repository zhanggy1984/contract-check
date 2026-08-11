"""finalize 终态计算单测（修复：人工确认的异常不应 SUCCESS）。

mock SessionLocal 隔离 DB；按「是否存在 CONFIRMED violation」断言终态：
- 有 CONFIRMED → FAILED（验证失败）
- 全 FALSE_POSITIVE → SUCCESS（验证通过）
- 零 violation（自动 SUCCESS 路径）→ SUCCESS
"""
import unittest
from unittest.mock import patch

import app.graph.nodes as nodes
from app.db.models import CheckTask


class _FakeTask:
    id = 999
    status = "WAITING_REVIEW"
    progress = 0


class _FakeViolationQuery:
    """支持 query(Violation.id).filter_by(...).first() 调用链。"""

    def __init__(self, has_confirmed):
        self.has_confirmed = has_confirmed

    def filter_by(self, *a, **k):
        return self

    def first(self):
        return object() if self.has_confirmed else None


class _FakeSession:
    def __init__(self, task, has_confirmed):
        self.task = task
        self.has_confirmed = has_confirmed

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, model, task_id):
        return self.task

    def query(self, expr, *a, **k):
        return _FakeViolationQuery(self.has_confirmed)

    def commit(self):
        pass


class TestFinalize(unittest.TestCase):
    def _run(self, has_confirmed):
        task = _FakeTask()
        with patch.object(nodes, "SessionLocal", return_value=_FakeSession(task, has_confirmed)):
            nodes.finalize({"task_id": 999})
        return task

    def test_confirmed_violation_leads_to_failed(self):
        task = self._run(has_confirmed=True)
        self.assertEqual(task.status, "FAILED", "存在人工确认异常 → 验证失败")

    def test_all_false_positive_leads_to_success(self):
        task = self._run(has_confirmed=False)
        self.assertEqual(task.status, "SUCCESS", "全部误报 → 验证通过")

    def test_zero_violation_leads_to_success(self):
        task = self._run(has_confirmed=False)
        self.assertEqual(task.status, "SUCCESS", "零异常 → 验证通过")

    def test_progress_set_to_100(self):
        task = self._run(has_confirmed=True)
        self.assertEqual(task.progress, 100)


if __name__ == "__main__":
    unittest.main()
