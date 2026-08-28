"""任务并发闸单测：同时运行的图流水线不超过 max_concurrent_tasks，超限排队不拒绝；
resume（人工确认）与首次运行共享同一额度（旁路已封）。

mock 隔离：_run_flow 用假函数（线程内持有信号量槽位并统计并发峰值），
_cleanup_if_terminal 置空避免触真实 DB（对齐 test_task_timeout 模式）。
"""
import asyncio
import threading
import time
import unittest
from unittest.mock import patch

import app.service.check_task_service as svc
from app.config import settings


class TestConcurrencyLimit(unittest.TestCase):
    def test_max_concurrent_not_exceeded_and_all_run(self):
        svc._sem = None
        lock = threading.Lock()
        active = 0
        peak = 0
        completed = []

        def fake_run_flow(task_id, reviews=None):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.15)  # 持有信号量槽位，模拟真实流水线耗时
            with lock:
                active -= 1
                completed.append(task_id)

        async def _runner():
            for tid in range(1, 6):   # 5 个任务 > max_concurrent_tasks=2
                svc.run_task_async(tid)
            for _ in range(200):      # 轮询等全部跑完（上限 4s，远超任务总耗时）
                with lock:
                    if len(completed) == 5:
                        return
                await asyncio.sleep(0.02)

        with patch.object(svc, "_run_flow", side_effect=fake_run_flow), \
             patch.object(svc, "_cleanup_if_terminal"), \
             patch.object(settings, "max_concurrent_tasks", 2):
            asyncio.run(_runner())

        self.assertLessEqual(peak, 2, "同时运行的流水线不得超过 max_concurrent_tasks")
        self.assertEqual(sorted(completed), [1, 2, 3, 4, 5],
                         "超限任务应排队后全部执行，而非拒绝")

    def test_peak_reaches_limit_with_multiple_tasks(self):
        """并发闸不误伤：任务数足够时确实跑到上限（证明信号量生效而非序列化）。"""
        svc._sem = None
        lock = threading.Lock()
        active = 0
        peak = 0
        completed = []

        def fake_run_flow(task_id, reviews=None):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.12)
            with lock:
                active -= 1
                completed.append(task_id)

        async def _runner():
            for tid in range(1, 4):
                svc.run_task_async(tid)
            for _ in range(200):
                with lock:
                    if len(completed) == 3:
                        return
                await asyncio.sleep(0.02)

        with patch.object(svc, "_run_flow", side_effect=fake_run_flow), \
             patch.object(svc, "_cleanup_if_terminal"), \
             patch.object(settings, "max_concurrent_tasks", 2):
            asyncio.run(_runner())

        self.assertEqual(peak, 2, "3 个任务跑 2 并发，峰值应正好到 2（信号量生效）")

    def test_resume_task_gated_by_same_semaphore(self):
        """P2 收口：resume（人工确认）与首次运行共享同一并发额度。

        主线程直接持有唯一槽位（max_concurrent_tasks=1），resume_task 在后台线程里必须
        排队等待（不得旁路并行执行 _run_flow）；槽位释放后才轮到。resume 的 DB 用假对象
        隔离，只验证并发闸行为。（不用 asyncio.run：其结束会 drain to_thread 执行器，
        与"长期持有槽位"的语义相冲突。）
        """
        svc._sem = None
        resume_entered = threading.Event()
        resume_result = {}

        def fake_run_flow(task_id, reviews=None):
            resume_entered.set()
            time.sleep(0.05)

        def run_resume():
            try:
                resume_result["ok"] = svc.resume_task(
                    2, [{"violation_id": 1, "action": "CONFIRMED"}])
            except Exception as e:  # pragma: no cover —— 失败现场保留供断言定位
                resume_result["exc"] = e

        # 假 DB：resume_task 的覆盖校验/CAS 全走假对象，仅 _run_flow 真调度
        fake_db = unittest.mock.MagicMock()
        fake_db.__enter__.return_value = fake_db
        fake_db.query.return_value.filter_by.return_value.all.return_value = []  # 无 UNCONFIRMED
        fake_db.execute.return_value.rowcount = 1                                 # CAS 抢占成功

        with patch.object(svc, "_run_flow", side_effect=fake_run_flow), \
             patch.object(svc, "_cleanup_if_terminal"), \
             patch.object(svc, "SessionLocal", return_value=fake_db), \
             patch.object(settings, "max_concurrent_tasks", 1):
            sem = svc._get_sem()
            self.assertIsInstance(sem, threading.Semaphore)
            sem.acquire()                     # 主线程持有唯一槽位，模拟长跑流水线占闸

            t = threading.Thread(target=run_resume)
            t.start()
            time.sleep(0.3)                   # 给 resume 线程充足时间走到闸前
            self.assertTrue(t.is_alive(), "槽位被占时 resume 应排队，不得立即跑完")
            self.assertFalse(resume_entered.is_set(),
                             "槽位被占期间，resume 的 _run_flow 不得并行进入")

            sem.release()                     # 释放槽位 → resume 应接着执行
            t.join(timeout=5)
            self.assertTrue(resume_entered.is_set(), "槽位释放后 resume 的 _run_flow 应执行")
            self.assertTrue(resume_result.get("ok"), "resume 应返回 True")
            self.assertNotIn("exc", resume_result, f"resume 不应抛异常: {resume_result}")


if __name__ == "__main__":
    unittest.main()
