"""main._ensure_column 幂等补列单测（B.4 token_usage_json 旧库升级）。

create_all 不修改已存在表，旧库需查 information_schema 缺列再 ALTER；
已存在时必须跳过，避免重复 ALTER 报 1060。
"""
import unittest
from unittest.mock import MagicMock

from app.main import _ensure_column


def _engine_with_conn(conn):
    engine = MagicMock()
    cm = engine.begin.return_value
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    return engine


class TestEnsureColumn(unittest.TestCase):
    def test_adds_column_when_missing(self):
        conn = MagicMock()
        conn.execute.side_effect = [
            MagicMock(scalar=lambda: 0),   # 查询：列不存在
            MagicMock(),                   # ALTER
        ]
        _ensure_column(_engine_with_conn(conn), "check_task", "token_usage_json", "LONGTEXT")
        alters = [c for c in conn.execute.call_args_list if "ALTER TABLE" in str(c.args[0])]
        self.assertEqual(len(alters), 1)
        self.assertIn("token_usage_json", str(alters[0].args[0]))

    def test_skips_alter_when_exists(self):
        conn = MagicMock()
        conn.execute.return_value = MagicMock(scalar=lambda: 1)   # 查询：列已存在
        _ensure_column(_engine_with_conn(conn), "check_task", "token_usage_json", "LONGTEXT")
        # 只执行了查询，没有 ALTER
        self.assertEqual(conn.execute.call_count, 1)
        self.assertNotIn("ALTER TABLE", str(conn.execute.call_args.args[0]))


if __name__ == "__main__":
    unittest.main()
