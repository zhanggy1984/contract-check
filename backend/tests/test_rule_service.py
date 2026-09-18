"""规则管理核心逻辑单测：删除保护、rule_iri 自动生成、创建限制、当前版本过滤、规则名大白话。

用 SQLite 内存库 + 真实 ORM 模型跑，避免连 MySQL；list_rules 的版本注册
monkeypatch 成固定版本号，聚焦过滤/排序本身。
"""
import unittest
from unittest.mock import patch

from sqlalchemy import BigInteger, Integer, create_engine
from sqlalchemy.orm import sessionmaker

from app.common.constants import RuleResult, RuleSource, RuleType, Severity
from app.db.models import Base, CheckRule, RuleCheckResult
from app.ontology.loader import load_ontology
from app.ontology import rule_generator as rg
from app.service import rule_service as svc

# SQLite 只对 `INTEGER PRIMARY KEY` 自增，BIGINT 主键会 NOT NULL 报错；
# 测试仅用内存库，把主键 BigInteger 换成 Integer（业务侧仍走 MySQL 的 BIGINT AUTO_INCREMENT）。
for _table in Base.metadata.tables.values():
    for _col in _table.columns:
        if _col.primary_key and isinstance(_col.type, BigInteger):
            _col.type = Integer()


class _RuleDB(unittest.TestCase):
    """基类：每个用例独立内存 SQLite + 会话。"""

    def setUp(self):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        self.db = self.Session()
        self.engine = engine

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _rule(self, **kw):
        r = CheckRule(
            rule_iri=kw.get("rule_iri", "urn:rule:test"),
            rule_name=kw.get("rule_name", "测试规则"),
            rule_type=kw.get("rule_type", RuleType.SEMANTIC.value),
            severity=kw.get("severity", Severity.HIGH.value),
            source=kw.get("source", RuleSource.MANUAL.value),
            expression=kw.get("expression", "prompt"),
            aggregation=kw.get("aggregation", "any"),
            enabled=kw.get("enabled", True),
            ontology_version_id=kw.get("ontology_version_id"),
        )
        self.db.add(r)
        self.db.commit()
        self.db.refresh(r)
        return r


class TestDeleteRule(_RuleDB):
    def test_ontology_rule_not_deletable(self):
        r = self._rule(source=RuleSource.ONTOLOGY_GENERATED.value, ontology_version_id=1)
        with self.assertRaises(ValueError):
            svc.delete_rule(self.db, r.id)
        self.assertIsNotNone(self.db.get(CheckRule, r.id), "本体规则删除应被拒绝且保留")

    def test_manual_rule_referenced_rejected(self):
        r = self._rule()
        self.db.add(RuleCheckResult(
            task_id=999, rule_id=r.id, rule_snapshot="snap",
            result=RuleResult.PASS.value, rule_type=r.rule_type, severity=r.severity))
        self.db.commit()
        with self.assertRaises(ValueError):
            svc.delete_rule(self.db, r.id)
        self.assertIsNotNone(self.db.get(CheckRule, r.id), "被引用规则删除应被拒绝且保留")

    def test_manual_rule_unreferenced_deleted(self):
        r = self._rule()
        self.assertTrue(svc.delete_rule(self.db, r.id))
        self.assertIsNone(self.db.get(CheckRule, r.id), "无引用人工规则应彻底删除")

    def test_not_found_returns_false(self):
        self.assertFalse(svc.delete_rule(self.db, 9999))


class TestGenRuleIri(_RuleDB):
    def test_basic(self):
        self.assertEqual(svc._gen_rule_iri(self.db, "违约条款"), "urn:rule:manual:违约条款")

    def test_empty_name_fallback(self):
        self.assertEqual(svc._gen_rule_iri(self.db, "  "), "urn:rule:manual:untitled")

    def test_conflict_appends_suffix(self):
        svc.create_rule(self.db, {"type": RuleType.SEMANTIC.value, "name": "同名",
                                  "severity": Severity.HIGH.value, "expression": "p"})
        self.assertEqual(svc._gen_rule_iri(self.db, "同名"), "urn:rule:manual:同名-2")
        # 连续冲突递增：同名-2 已存在 → 同名-3
        svc.create_rule(self.db, {"type": RuleType.SEMANTIC.value, "name": "同名-2",
                                  "severity": Severity.HIGH.value, "expression": "p"})
        self.assertEqual(svc._gen_rule_iri(self.db, "同名"), "urn:rule:manual:同名-3")


class TestCreateRule(_RuleDB):
    def test_reject_deterministic(self):
        """确定性规则只由本体生成，人工创建必须语义 LLM（前端已取消入口，后端兜底）。"""
        with self.assertRaises(ValueError):
            svc.create_rule(self.db, {"type": RuleType.DETERMINISTIC.value, "name": "x",
                                      "severity": Severity.HIGH.value, "expression": "ASK {}"})

    def test_auto_iri_and_default_disabled(self):
        r = svc.create_rule(self.db, {"type": RuleType.SEMANTIC.value, "name": "违约条款",
                                      "severity": Severity.HIGH.value, "expression": "prompt"})
        self.assertEqual(r.rule_iri, "urn:rule:manual:违约条款")
        self.assertFalse(r.enabled, "新建规则默认 disabled，需人工启用")

    def test_duplicate_iri_rejected(self):
        svc.create_rule(self.db, {"type": RuleType.SEMANTIC.value, "name": "重复",
                                  "severity": Severity.HIGH.value, "expression": "p"})
        # 同名自动生成会加 -2 后缀避让，不报错；只有显式传已存在的 rule_iri 才撞唯一
        with self.assertRaises(ValueError):
            svc.create_rule(self.db, {"type": RuleType.SEMANTIC.value, "name": "重复2",
                                      "rule_iri": "urn:rule:manual:重复",
                                      "severity": Severity.HIGH.value, "expression": "p"})


class TestListRules(_RuleDB):
    @patch("app.service.rule_service.register_version", return_value=100)
    def test_only_current_version_plus_manual(self, _):
        cur = self._rule(rule_iri="urn:r:cur", source=RuleSource.ONTOLOGY_GENERATED.value, ontology_version_id=100)
        old = self._rule(rule_iri="urn:r:old", source=RuleSource.ONTOLOGY_GENERATED.value, ontology_version_id=99)
        manual = self._rule(rule_iri="urn:r:manual")
        ids = {i["id"] for i in svc.list_rules(self.db, size=50)["items"]}
        self.assertIn(cur.id, ids)
        self.assertIn(manual.id, ids)
        self.assertNotIn(old.id, ids, "历史版本规则不应出现在列表")

    @patch("app.service.rule_service.register_version", return_value=100)
    def test_order_by_id_desc(self, _):
        created = [self._rule(rule_iri=f"urn:r:{i}", ontology_version_id=100) for i in range(3)]
        ids = [i["id"] for i in svc.list_rules(self.db, size=50)["items"]]
        self.assertEqual(ids, [c.id for c in reversed(created)], "列表应按 ID 倒序")


class TestUpdateRule(_RuleDB):
    """F6 回归：请求体键是 name（与 CreateBody 同口径），ORM 属性是 rule_name。

    修前 `update_rule` 把 "rule_name" 混在遍历元组里，而唯一调用方 api/rules.py
    发的是 UpdateBody.model_dump() ⇒ 键恒为 name ⇒ 改名是条死分支。
    真机实证：同一个 PUT 里 severity 落库、name 不落库（见 task.md §3.12）。
    """

    def test_rename_takes_effect(self):
        r = self._rule(rule_name="旧名", rule_iri="urn:rule:manual:旧名")
        svc.update_rule(self.db, r.id, {"name": "新名"})
        self.assertEqual(self.db.get(CheckRule, r.id).rule_name, "新名")

    def test_rename_absent_keeps_old_name(self):
        """只改 severity 的 PUT（exclude_none 后 name 缺席）不能把名字弄丢。"""
        r = self._rule(rule_name="旧名")
        svc.update_rule(self.db, r.id, {"severity": Severity.MEDIUM.value})
        updated = self.db.get(CheckRule, r.id)
        self.assertEqual(updated.rule_name, "旧名")
        self.assertEqual(updated.severity, Severity.MEDIUM.value)

    def test_rename_ignored_for_ontology_rule(self):
        """本体规则只读：name 必须被忽略，别因为修 F6 把改名权限漏给它。"""
        r = self._rule(rule_name="本体名", source=RuleSource.ONTOLOGY_GENERATED.value,
                       ontology_version_id=1)
        svc.update_rule(self.db, r.id, {"name": "改它", "severity": Severity.LOW.value})
        updated = self.db.get(CheckRule, r.id)
        self.assertEqual(updated.rule_name, "本体名")
        self.assertEqual(updated.severity, Severity.LOW.value, "severity 是本体规则允许改的字段")

    def test_not_found_returns_none(self):
        self.assertIsNone(svc.update_rule(self.db, 9999, {"name": "x"}))


class TestRuleGeneratorLabels(unittest.TestCase):
    def test_cls_label(self):
        self.assertEqual(rg._cls_label("Contract"), "合同")
        self.assertEqual(rg._cls_label("Party"), "当事人")
        self.assertEqual(rg._cls_label("Unknown"), "Unknown", "未收录类名回退短名")

    def test_prop_label(self):
        self.assertEqual(rg._prop_label("totalAmount"), "合同总金额")
        self.assertEqual(rg._prop_label("unifiedSocialCreditCode"), "统一社会信用代码")
        self.assertEqual(rg._prop_label("nope"), "nope", "未收录属性回退短名")

    def test_fmt_num(self):
        self.assertEqual(rg._fmt_num(0.0), "0", "整数下限不显示小数位")
        self.assertEqual(rg._fmt_num(1.5), "1.5")

    def test_generate_rule_names_are_business_terms(self):
        """端到端：真实本体生成的规则名应是大白话法律术语，不暴露 IRI 短名。"""
        onto = load_ontology()
        rules = rg.generate_rules(onto)
        self.assertTrue(rules)
        leaked = [r for r in rules if "Contract" in r["rule_name"] and "合同" not in r["rule_name"]]
        self.assertEqual(leaked, [], "规则名不应残留类短名 Contract")
        names = "".join(r["rule_name"] for r in rules)
        self.assertIn("合同", names)


if __name__ == "__main__":
    unittest.main()
