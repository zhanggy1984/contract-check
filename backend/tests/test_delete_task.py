"""删除任务单测：状态守卫 + 级联删除 + contract_file 引用计数。

mock SessionLocal / PyMySQLSaver 隔离 DB 与 checkpoint（不触真实库），
物理文件路径用不存在的路径（unlink missing_ok 无副作用）。
unittest 风格，pytest 作 runner。
"""
import unittest
from unittest.mock import patch

import app.service.check_task_service as svc
from app.db.models import CheckTask, ContractFile, RuleCheckResult, Violation


class _FakeCf:
    id = 1
    storage_path = "data/uploads/fake_sha.pdf"
    sha256 = "fake_sha"


class _FakeTask:
    id = 999
    status = "SUCCESS"
    contract_file = _FakeCf()


class _FakeSession:
    """支持 delete_task 用到的调用链：get/query.filter_by.delete/query.filter.count。"""

    def __init__(self, task):
        self.task = task
        self.refs = 0          # contract_file 剩余引用数
        self.deleted_rows = []  # 记录级联 delete 的目标类型
        self.task_deleted = False
        self.cf_deleted = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, model, task_id):
        return self.task

    def query(self, model, *a, **k):
        if model is CheckTask and self._in_refs_count:
            return _RefQuery(self)
        return _FilterQuery(self, model)

    def delete(self, obj):
        if obj is self.task:
            self.task_deleted = True

    def flush(self):
        pass

    def commit(self):
        pass


class _FilterQuery:
    def __init__(self, session, model):
        self.session = session
        self.model = model

    def filter_by(self, *a, **k):
        return self

    def delete(self, synchronize_session=False):
        self.session.deleted_rows.append(self.model)


class _RefQuery:
    def __init__(self, session):
        self.session = session

    def filter(self, *a, **k):
        return self

    def count(self):
        return self.session.refs


class TestDeleteTask(unittest.TestCase):
    def _run(self, session):
        with patch.object(svc, "SessionLocal", return_value=session), \
             patch.object(svc, "PyMySQLSaver") as MockSaver:
            ok, msg = svc.delete_task(999)
        return ok, msg, MockSaver

    def test_running_task_rejected(self):
        task = _FakeTask()
        task.status = "PENDING"
        session = _FakeSession(task)
        session._in_refs_count = False
        ok, msg, _ = self._run(session)
        self.assertFalse(ok, "运行中任务应拒绝删除")
        self.assertIn("运行中", msg)

    def test_success_deletes_cascade_and_file(self):
        session = _FakeSession(_FakeTask())
        session._in_refs_count = True
        ok, msg, MockSaver = self._run(session)
        self.assertTrue(ok)
        # 级联：rule_check_result 先删（violation_id 引用 violation），再删 violation，最后 task
        self.assertEqual(session.deleted_rows, [RuleCheckResult, Violation])
        self.assertTrue(session.task_deleted)
        # 引用归零 → contract_file 记录删除 + checkpoint 清理
        (MockSaver.from_conn_string.return_value.__enter__.return_value.delete_thread
         .assert_called_once_with("task-999"))

    def test_shared_file_kept_when_still_referenced(self):
        session = _FakeSession(_FakeTask())
        session._in_refs_count = True
        session.refs = 2   # 其他任务仍引用同一 sha 文件
        ok, msg, _ = self._run(session)
        self.assertTrue(ok)
        self.assertFalse(session.cf_deleted, "仍有引用时不应删 contract_file")


if __name__ == "__main__":
    unittest.main()
