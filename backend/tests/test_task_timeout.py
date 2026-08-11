"""T4.3-1 任务超时兜底：run_task_async 对慢任务超时置 FAILED。

mock 隔离：_run_flow 用假慢函数，update_status 捕获调用，不触真实 DB。
用 unittest 风格（与既有测试一致），pytest 仅作 runner。
"""
import asyncio
import time
import unittest
from unittest.mock import patch

import app.service.check_task_service as svc
from app.config import settings


def _slow_run(task_id, reviews=None):
    time.sleep(0.5)  # 模拟慢执行：比 timeout 长，触发超时


def _fast_run(task_id, reviews=None):
    pass


class TestTaskTimeout(unittest.TestCase):
    def test_slow_task_marks_failed_with_timeout_msg(self):
        captured = {}

        def _fake_update(task_id, status, error=None):
            captured["status"] = status
            captured["error"] = error

        async def _runner():
            svc.run_task_async(1)
            await asyncio.sleep(1.0)  # 等后台超时分支跑完

        with patch.object(svc, "_run_flow", side_effect=_slow_run), \
             patch.object(svc, "update_status", side_effect=_fake_update), \
             patch.object(settings, "task_timeout_seconds", 0.2):
            asyncio.run(_runner())

        self.assertEqual(captured.get("status"), "FAILED")
        self.assertIn("超时", captured.get("error", ""))

    def test_fast_task_not_marked_failed(self):
        captured = {}

        def _fake_update(task_id, status, error=None):
            captured["status"] = status

        async def _runner():
            svc.run_task_async(2)
            await asyncio.sleep(0.3)

        with patch.object(svc, "_run_flow", side_effect=_fast_run), \
             patch.object(svc, "update_status", side_effect=_fake_update), \
             patch.object(settings, "task_timeout_seconds", 1):
            asyncio.run(_runner())

        self.assertNotIn("status", captured)  # 正常任务不触发 FAILED

    def test_exception_still_marks_failed(self):
        captured = {}

        def _boom_run(task_id, reviews=None):
            raise RuntimeError("图执行异常")

        def _fake_update(task_id, status, error=None):
            captured["status"] = status
            captured["error"] = error

        async def _runner():
            svc.run_task_async(3)
            await asyncio.sleep(0.3)

        with patch.object(svc, "_run_flow", side_effect=_boom_run), \
             patch.object(svc, "update_status", side_effect=_fake_update), \
             patch.object(settings, "task_timeout_seconds", 10):
            asyncio.run(_runner())

        self.assertEqual(captured.get("status"), "FAILED")
        self.assertIn("图执行异常", captured.get("error", ""))


if __name__ == "__main__":
    unittest.main()
