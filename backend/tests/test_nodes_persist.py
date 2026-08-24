"""nodes.persist_node 死锁重试 + _is_deadlock + _merge_usage 单元测试（B.4 持久化稳定性）。

InnoDB 死锁（1213）/ 序列化失败（40001）时整事务重试 _PERSIST_RETRY_MAX 次；
usage 聚合对全字段求和（含 7.4 cache 字段）。
"""
import unittest
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

from app.graph import nodes
from app.graph.nodes import _is_deadlock, _merge_usage, persist_node


def _deadlock_err(code: int = 1213) -> OperationalError:
    """构造 mysqlclient/pymysql 风格死锁错误：orig.args[0] 为错误码。"""
    return OperationalError("INSERT INTO ...", [], Exception(code, f"deadlock code {code}"))


def _other_err() -> OperationalError:
    return OperationalError("SELECT 1", [], Exception(1064, "syntax error"))


class TestIsDeadlock(unittest.TestCase):
    def test_1213_true(self):
        self.assertTrue(_is_deadlock(_deadlock_err(1213)))

    def test_40001_true(self):
        self.assertTrue(_is_deadlock(_deadlock_err(40001)))

    def test_other_code_false(self):
        self.assertFalse(_is_deadlock(_deadlock_err(1062)))

    def test_other_error_false(self):
        self.assertFalse(_is_deadlock(_other_err()))

    def test_no_orig_false(self):
        self.assertFalse(_is_deadlock(OperationalError("stmt", [], None)))


class TestMergeUsage(unittest.TestCase):
    """nodes._merge_usage：评测契约 usage 全字段求和，双空返回 None（不落库）。"""

    FULL = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
            "prompt_cache_hit_tokens": 30, "prompt_cache_miss_tokens": 70}

    def test_both_none(self):
        self.assertIsNone(_merge_usage(None, None))

    def test_none_b_returns_a(self):
        self.assertEqual(_merge_usage(dict(self.FULL), None), self.FULL)

    def test_none_a_copies_b(self):
        self.assertEqual(_merge_usage(None, dict(self.FULL)), self.FULL)

    def test_sums_all_fields(self):
        b = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
             "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0}
        self.assertEqual(_merge_usage(dict(self.FULL), b),
                         {"prompt_tokens": 110, "completion_tokens": 55, "total_tokens": 165,
                          "prompt_cache_hit_tokens": 30, "prompt_cache_miss_tokens": 70})


class TestPersistNodeRetry(unittest.TestCase):
    """persist_node：死锁整事务重试，非死锁错误立即上抛。"""

    def test_deadlock_then_success_retries(self):
        state = {"task_id": 1}
        calls = {"n": 0}

        def flaky(state, tid, det, sem):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _deadlock_err()
            return {"persisted": True}

        with patch.object(nodes, "_persist_once", side_effect=flaky):
            out = persist_node(state)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(out, {"persisted": True})

    def test_persistent_deadlock_raises_after_max(self):
        state = {"task_id": 1}
        with patch.object(nodes, "_persist_once", side_effect=_deadlock_err()):
            with self.assertRaises(OperationalError):
                persist_node(state)

    def test_non_deadlock_error_raises_immediately(self):
        state = {"task_id": 1}
        with patch.object(nodes, "_persist_once", side_effect=_other_err()):
            with self.assertRaises(OperationalError):
                persist_node(state)


if __name__ == "__main__":
    unittest.main()
