"""check_task_service.update_status 单测：error 截断与 no-op（T232）。

error_message 是 varchar(1000)，超长异常堆栈必须截断否则 1406 使状态更新失败，
任务永久卡旧状态导致平台 300s 轮询超时判错。保证状态一定落库。
"""
import unittest
from unittest.mock import MagicMock, patch

from app.service import check_task_service as svc


class TestUpdateStatus(unittest.TestCase):
    def _mock_db(self, task):
        db = MagicMock()
        db.get.return_value = task
        # MagicMock 默认 __enter__ 返回新对象，必须让它返回自身，
        # 否则 `with SessionLocal() as db:` 里拿到的是另一个 mock
        db.__enter__.return_value = db
        return db

    def test_error_truncated_to_1000(self):
        task = MagicMock()
        db = self._mock_db(task)
        with patch.object(svc, "SessionLocal", return_value=db):
            svc.update_status(1, "FAILED", error="E" * 5000)
        self.assertEqual(task.error_message, "E" * 1000)
        self.assertEqual(task.status, "FAILED")
        db.commit.assert_called_once()

    def test_short_error_kept_as_is(self):
        task = MagicMock()
        db = self._mock_db(task)
        with patch.object(svc, "SessionLocal", return_value=db):
            svc.update_status(1, "FAILED", error="err")
        self.assertEqual(task.error_message, "err")

    def test_no_error_keeps_existing_message(self):
        task = MagicMock(error_message="旧消息")
        db = self._mock_db(task)
        with patch.object(svc, "SessionLocal", return_value=db):
            svc.update_status(1, "SUCCESS", progress=100)
        self.assertEqual(task.error_message, "旧消息")
        self.assertEqual(task.progress, 100)
        db.commit.assert_called_once()

    def test_missing_task_is_noop(self):
        db = MagicMock()
        db.get.return_value = None
        with patch.object(svc, "SessionLocal", return_value=db):
            svc.update_status(999, "FAILED", error="x")  # 不抛异常
        db.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
