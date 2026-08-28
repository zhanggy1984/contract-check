"""语义降级强制人工（c1 收口）单测。

- _should_wait：第三条件 sem_degraded=True → "await"（零 violation + COMPLETE 也不自动 SUCCESS）
- validate_semantic：整体降级（全 SKIPPED/LOW）返回 sem_degraded=True；正常/无规则不误伤；
  崩溃重放（sem_outcomes_json 快照）路径补算标记，防"快照后→checkpoint 前"窗口崩溃丢标记静默通过
"""
import json
import unittest
from unittest import mock

from app.graph.build import _should_wait
from app.graph import nodes


class FakeSession:
    """复用 test_nodes_decisions 的假 session：get 返回固定 task，commit 空转。"""

    def __init__(self, task):
        self._task = task

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, model, task_id):
        return self._task

    def commit(self):
        pass


class FakeRule:
    def __init__(self):
        self.id = 10
        self.rule_iri = "urn:rule:manual:1"
        self.rule_name = "测试规则"
        self.rule_type = "SEMANTIC"
        self.expression = ""
        self.aggregation = "any"


class FakeTask:
    id = 1
    status = "PENDING"
    progress = 0
    ontology_version_id = 1
    segments_json = json.dumps([{"index": 0, "title": "段", "content": "正文内容"}])
    sem_outcomes_json = None
    sem_usage_json = None
    decision_json = "[]"


class TestShouldWait(unittest.TestCase):
    """_should_wait 三分支分流：violation / INCOMPLETE / sem_degraded 都进人工。"""

    def test_normal_zero_violation_done(self):
        self.assertEqual(_should_wait({"violations_count": 0, "extraction_status": "COMPLETE"}), "done")

    def test_has_violation_await(self):
        self.assertEqual(_should_wait({"violations_count": 1, "extraction_status": "COMPLETE"}), "await")

    def test_incomplete_await(self):
        self.assertEqual(_should_wait({"violations_count": 0, "extraction_status": "INCOMPLETE"}), "await")

    def test_sem_degraded_await(self):
        """语义降级 + 零 violation + COMPLETE：不自动 SUCCESS，强制人工确认放行。"""
        self.assertEqual(
            _should_wait({"violations_count": 0, "extraction_status": "COMPLETE", "sem_degraded": True}),
            "await")

    def test_sem_degraded_false_done(self):
        self.assertEqual(
            _should_wait({"violations_count": 0, "extraction_status": "COMPLETE", "sem_degraded": False}),
            "done")

    def test_empty_outcomes_no_rules_done(self):
        """零语义规则（空 outcomes，sem_degraded 未设置）不误伤，正常自动通过。"""
        self.assertEqual(_should_wait({"violations_count": 0, "extraction_status": "COMPLETE"}), "done")


class TestValidateSemanticDegraded(unittest.TestCase):
    """validate_semantic 返回 sem_degraded 标记：降级 True / 正常 False / 重放补算。"""

    def _run(self, task, outcomes=None, segments_json=None):
        if segments_json is not None:
            task.segments_json = segments_json
        with mock.patch.object(nodes, "SessionLocal", return_value=FakeSession(task)), \
             mock.patch.object(nodes, "get_enabled_rules", return_value=[FakeRule()]), \
             mock.patch.object(nodes, "registry") as m_reg:
            if outcomes is not None:
                m_reg.execute.return_value = {"outcomes": outcomes, "usage": {"total_tokens": 9}}
            return nodes.validate_semantic({"task_id": 1})

    def test_degraded_returns_true(self):
        task = FakeTask()
        out = self._run(task, outcomes=[
            {"rule_id": 10, "result": "SKIPPED", "confidence": "LOW"},
            {"rule_id": 10, "result": "SKIPPED", "confidence": "LOW"},
        ])
        self.assertIs(out["sem_degraded"], True)

    def test_normal_returns_false(self):
        task = FakeTask()
        out = self._run(task, outcomes=[
            {"rule_id": 10, "result": "PASS", "confidence": "HIGH"},
        ])
        self.assertIs(out["sem_degraded"], False)

    def test_no_segments_degraded(self):
        """段原文缺失 = 评估失败 → 全 SKIPPED/LOW → 降级（不需 registry）。"""
        task = FakeTask()
        out = self._run(task, segments_json=json.dumps([]))
        self.assertIs(out["sem_degraded"], True)
        self.assertTrue(all(o["confidence"] == "LOW" for o in out["sem_outcomes"]))

    def test_replay_recomputes_degraded_flag(self):
        """崩溃重放：快照是降级数据 → 早退路径也补算 sem_degraded=True，防丢标记静默通过。"""
        task = FakeTask()
        task.sem_outcomes_json = json.dumps([
            {"rule_id": 10, "result": "SKIPPED", "confidence": "LOW"},
        ])
        out = self._run(task)
        self.assertIs(out["sem_degraded"], True)
        self.assertIsNone(out["sem_usage"])

    def test_replay_normal_no_flag(self):
        task = FakeTask()
        task.sem_outcomes_json = json.dumps([
            {"rule_id": 10, "result": "PASS", "confidence": "HIGH"},
        ])
        out = self._run(task)
        self.assertIs(out["sem_degraded"], False)


if __name__ == "__main__":
    unittest.main()
