"""规则/本体版本注册并发竞态单测（T4.3-6 修复）。

- register_version：并发首插靠 md5 唯一约束 + begin_nested（savepoint）捕获 IntegrityError
  回退复用，不破坏外层 session 的 pending 改动
- sync_rules：同版本记忆化只同步一次；唯一键冲突（uk_rule_iri_version）回退复用；
  死锁（1213/40001）顶层重试；版本变化重同步
- main._ensure_unique_index：幂等补唯一索引三分支（已存在/无重复新建/有重复跳过）

SQLite 内存库 + mock 竞态控制流；unittest 风格（与 test_rule_service.py 一致），pytest 作 runner。
"""
import unittest
from unittest import mock

from sqlalchemy import BigInteger, Integer, create_engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, CheckRule, OntologyVersion
from app.ontology import loader
from app.service import rule_service as svc

# SQLite 只对 `INTEGER PRIMARY KEY` 自增，BIGINT 主键会 NOT NULL 报错（同 test_rule_service）
for _table in Base.metadata.tables.values():
    for _col in _table.columns:
        if _col.primary_key and isinstance(_col.type, BigInteger):
            _col.type = Integer()


class _Orig(Exception):
    """构造 DBAPI 底层异常，e.orig.args[0] 即为 MySQL 错误码。"""

    def __init__(self, code):
        super().__init__(code)


class _RuleDB(unittest.TestCase):
    """基类：每个用例独立内存 SQLite + 会话；清 sync_rules 进程级缓存。"""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        svc._synced_ids.clear()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _rules(self, severity="HIGH"):
        return [{
            "rule_iri": "urn:r:1", "rule_name": "规则1", "rule_type": "DETERMINISTIC",
            "severity": severity, "source": "ONTOLOGY_GENERATED", "expression": "ASK {}",
            "description": None, "concept_iri": None, "property_iri": None, "aggregation": None,
        }]


class TestRegisterVersion(_RuleDB):
    def test_normal_first_insert(self):
        with mock.patch.object(loader, "md5_of_file", return_value="abc"):
            rid = loader.register_version(self.db)
        row = self.db.query(OntologyVersion).first()
        self.assertEqual(rid, row.id)
        self.assertEqual(self.db.query(OntologyVersion).count(), 1)

    def test_idempotent_reuse(self):
        with mock.patch.object(loader, "md5_of_file", return_value="abc"):
            rid1 = loader.register_version(self.db)
            rid2 = loader.register_version(self.db)   # 同 md5 → 复用
        self.assertEqual(rid1, rid2)
        self.assertEqual(self.db.query(OntologyVersion).count(), 1)

    def test_duplicate_flush_falls_back(self):
        """并发首插：入口 query 无 → begin_nested flush 撞唯一键 → 回退复用已有版本。"""
        db = _FakeDb(existing_id=42)
        with mock.patch.object(loader, "md5_of_file", return_value="abc"):
            rid = loader.register_version(db)
        self.assertEqual(rid, 42, "撞唯一键应回退复用已有版本")
        self.assertIsNotNone(db.expunged, "应 expunge pending 行防重复 flush")


class TestSyncRules(_RuleDB):
    def test_memoized_same_version_runs_once(self):
        with mock.patch.object(svc, "load_ontology", return_value=None), \
             mock.patch.object(svc, "generate_rules", return_value=self._rules()), \
             mock.patch.object(svc, "load_manual_rules", return_value=[]):
            ids1 = svc.sync_rules(self.db, 100)
            n = self.db.query(CheckRule).count()
            ids2 = svc.sync_rules(self.db, 100)   # 缓存命中，不重跑
        self.assertEqual(self.db.query(CheckRule).count(), n, "同版本第二次调用零 DB 写")
        self.assertEqual(ids1, ids2)

    def test_new_version_resyncs(self):
        with mock.patch.object(svc, "load_ontology", return_value=None), \
             mock.patch.object(svc, "generate_rules", return_value=self._rules()), \
             mock.patch.object(svc, "load_manual_rules", return_value=[]):
            svc.sync_rules(self.db, 100)
            svc.sync_rules(self.db, 101)   # 新版本 → 重同步
        self.assertEqual(self.db.query(CheckRule).count(), 2, "新版本各生成一套规则")

    def test_sync_once_refreshes_existing(self):
        """幂等 upsert：同 (rule_iri, version) 已存在 → update 刷新不重复插入。"""
        svc._sync_once(self.db, 100, self._rules())
        svc._sync_once(self.db, 100, self._rules(severity="LOW"))
        row = self.db.query(CheckRule).first()
        self.assertEqual(row.severity, "LOW", "已存在规则应刷新 severity")
        self.assertEqual(self.db.query(CheckRule).count(), 1)

    def test_deadlock_retries_then_succeeds(self):
        """死锁（1213）：顶层重试，重试成功返回结果。"""
        op = OperationalError("INSERT", {}, _Orig(1213))
        fake_ids = {"urn:r:1": 1}
        with mock.patch.object(svc, "load_ontology", return_value=None), \
             mock.patch.object(svc, "generate_rules", return_value=self._rules()), \
             mock.patch.object(svc, "load_manual_rules", return_value=[]), \
             mock.patch.object(svc, "_sync_once", side_effect=[op, fake_ids]) as m_once:
            ids = svc.sync_rules(self.db, 100)
        self.assertEqual(m_once.call_count, 2, "死锁应重试一次")
        self.assertEqual(ids, fake_ids)

    def test_deadlock_exhausted_raises_and_no_cache(self):
        """重试耗尽仍死锁 → 上抛，且不写缓存（下次任务重试）。"""
        op = OperationalError("INSERT", {}, _Orig(1213))
        with mock.patch.object(svc, "load_ontology", return_value=None), \
             mock.patch.object(svc, "generate_rules", return_value=self._rules()), \
             mock.patch.object(svc, "load_manual_rules", return_value=[]), \
             mock.patch.object(svc, "_sync_once", side_effect=op):
            with self.assertRaises(OperationalError):
                svc.sync_rules(self.db, 100)
        self.assertNotIn(100, svc._synced_ids, "失败不得写缓存")


class _FakeDb:
    """模拟并发首插控制流：入口 query 无 → begin_nested flush 抛 IntegrityError → 回退复用。"""

    def __init__(self, existing_id):
        self.existing_id = existing_id
        self.expunged = None
        self._q = 0

    def query(self, model):
        return self

    def filter(self, *a, **k):
        return self

    def first(self):
        self._q += 1
        return None if self._q == 1 else type("V", (), {"id": self.existing_id})()

    def add(self, row):
        pass

    def begin_nested(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def flush(self):
        raise IntegrityError("INSERT", {}, _Orig(1062))

    def expunge(self, row):
        self.expunged = row

    def commit(self):
        pass

    def refresh(self, row):
        pass


class TestEnsureUniqueIndex(unittest.TestCase):
    """main._ensure_unique_index 三分支：已存在/无重复新建/有重复跳过。"""

    def _engine(self, scalars):
        """execute 依次返回带 .scalar() 的 mock（None 表示返回值不用，如 ALTER）。"""
        conn = mock.MagicMock()
        results = []
        for v in scalars:
            r = mock.MagicMock()
            if v is not None:
                r.scalar.return_value = v
            results.append(r)
        conn.execute.side_effect = results
        eng = mock.MagicMock()
        eng.begin.return_value.__enter__.return_value = conn
        return eng, conn

    def test_index_exists_noop(self):
        from app import main
        eng, conn = self._engine([1])
        main._ensure_unique_index(eng, "t", "c")
        self.assertEqual(conn.execute.call_count, 1, "唯一索引已存在则不再查询/ALTER")

    def test_no_dup_adds_index(self):
        from app import main
        eng, conn = self._engine([0, 0, None])
        main._ensure_unique_index(eng, "t", "c")
        self.assertEqual(conn.execute.call_count, 3)
        alter = conn.execute.call_args_list[2]
        self.assertIn("ADD UNIQUE KEY", str(alter.args[0]))

    def test_dup_skips_with_warning(self):
        from app import main
        eng, conn = self._engine([0, 3])
        main._ensure_unique_index(eng, "t", "c")
        self.assertEqual(conn.execute.call_count, 2, "存量有重复则跳过建索引（告警待人工清理）")
        self.assertNotIn("ADD UNIQUE KEY", [str(c.args[0]) for c in conn.execute.call_args_list])


if __name__ == "__main__":
    unittest.main()
