"""校验结果落库单测：双表同事务、FAIL→violation 回填、幂等先删后插（T2.3 核心逻辑）。"""
import unittest

from app.common.constants import RuleResult
from app.db.models import CheckRule, RuleCheckResult, Violation
from app.validation.persist import RuleOutcome, persist_results


def _rule(rule_id=1):
    r = CheckRule(
        rule_iri="urn:rule:test", rule_name="测试规则", rule_type="DETERMINISTIC",
        severity="HIGH", source="MANUAL", expression="ASK {}",
        description="测试规则说明", enabled=True,
    )
    r.id = rule_id
    return r


class _FakeQ:
    def __init__(self, db, model):
        self.db = db
        self.model = model

    def filter_by(self, *a, **k):
        return self

    def delete(self, synchronize_session=False):
        self.db.deleted.append(self.model)


class _FakeDB:
    def __init__(self):
        self.added = []
        self.deleted = []
        self._next = 1

    def query(self, model, *a, **k):
        return _FakeQ(self, model)

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        # 模拟 DB flush：为未分配 id 的对象分配递增 id（violation_id 回填依赖）
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = self._next
                self._next += 1

    def commit(self):
        pass


class TestPersistResults(unittest.TestCase):
    def test_pass_row_written_no_violation(self):
        db = _FakeDB()
        info = persist_results(db, 1, [RuleOutcome(_rule(), RuleResult.PASS.value, [])])
        self.assertEqual(info, {"rows": 1, "violations": 0})
        self.assertEqual(len(db.added), 1)
        self.assertIsInstance(db.added[0], RuleCheckResult)
        self.assertIsNone(db.added[0].violation_id)

    def test_fail_writes_violation_and_backfill(self):
        db = _FakeDB()
        info = persist_results(db, 1, [RuleOutcome(_rule(), RuleResult.FAIL.value, ["http://s"])])
        self.assertEqual(info, {"rows": 1, "violations": 1})
        self.assertEqual(len(db.added), 2)  # rcr + violation
        rcr, v = db.added
        self.assertIsInstance(v, Violation)
        self.assertEqual(rcr.violation_id, v.id, "FAIL 行回填 violation_id")
        # 注：status 默认 UNCONFIRMED 由 DB INSERT 层 default 触发，fake DB 不执行 INSERT，故不断言

    def test_semantic_message_and_evidence_persisted(self):
        db = _FakeDB()
        persist_results(db, 1, [RuleOutcome(_rule(), RuleResult.FAIL.value, [],
                                            message="缺少违约条款", segment_ref="seg-0",
                                            evidence_text="第六条", confidence="HIGH")])
        rcr, v = db.added
        self.assertEqual(rcr.message, "缺少违约条款")
        self.assertEqual(v.segment_ref, "seg-0")
        self.assertEqual(v.evidence_text, "第六条")

    def test_fail_message_uses_rule_description(self):
        """确定性 FAIL 的 message 用规则描述（人话），不再暴露"命中 N 个反例：<IRI>"。"""
        db = _FakeDB()
        persist_results(db, 1, [RuleOutcome(_rule(), RuleResult.FAIL.value, ["http://s"])])
        rcr, _ = db.added
        self.assertIn("测试规则说明", rcr.message)
        self.assertNotIn("http://", rcr.message, "不应暴露反例 IRI")

    def test_idempotent_delete_before_insert(self):
        db = _FakeDB()
        persist_results(db, 1, [RuleOutcome(_rule(), RuleResult.PASS.value, [])])
        self.assertEqual(db.deleted, [RuleCheckResult, Violation],
                         "先删 rcr 再删 violation（violation_id FK 依赖）")


if __name__ == "__main__":
    unittest.main()
