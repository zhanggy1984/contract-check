"""崩溃重放 LLM 节点复用守卫单测（T4.3-5 防重复计费）。

extract_node / validate_semantic 先落库/快照后返回；崩溃在「落库后→checkpoint 前」窗口，
recover 重放若重新执行 registry.execute 会重复计费。已落库结果直接复用跳过 LLM。验证：
- extract_node 已落库（COMPLETE/INCOMPLETE）→ 不调 registry.execute / register_version，读回快照
- validate_semantic 已落库快照 → 不调 evaluate_semantic，读回快照
- 正常路径回归：LLM 照常调用，结果落库快照（供下次重放复用）

mock 隔离 SessionLocal，不触真实 DB；unittest 风格（与 test_nodes_decisions.py 一致），pytest 作 runner。
"""
import json
import unittest
from unittest import mock

from app.graph import nodes


class _FakeSession:
    """可复用假 session：get 返回固定 task，commit 空转（快照落库的二次 SessionLocal 也兼容）。"""

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


class _FakeRule:
    def __init__(self, rule_type="SEMANTIC"):
        self.id = 10
        self.rule_iri = "urn:rule:manual:1"
        self.rule_name = "测试规则"
        self.rule_type = rule_type
        self.expression = "prompt"
        self.aggregation = "any"


class _Task:
    """带已落库抽取/语义快照字段的任务（复用守卫读取的快照来源）。"""

    id = 1
    status = "VALIDATING"
    progress = 0
    ontology_version_id = 1
    extraction_status = None
    extraction_rdf = "rdf"
    segments_json = json.dumps([{"index": 0, "title": "段", "content": "正文"}])
    standard_json = json.dumps({"contractTitle": "T"}, ensure_ascii=False)
    extraction_conflicts = None
    extraction_usage_json = json.dumps({"total_tokens": 7})
    sem_outcomes_json = None
    sem_usage_json = None


class TestExtractReuse(unittest.TestCase):
    def _run(self, status):
        task = _Task()
        task.extraction_status = status
        m_reg = mock.MagicMock()
        with mock.patch.object(nodes, "SessionLocal", return_value=_FakeSession(task)), \
             mock.patch.object(nodes, "registry", m_reg), \
             mock.patch.object(nodes, "register_version") as m_regver:
            out = nodes.extract_node({"task_id": 1})
        return out, m_reg, m_regver

    def test_complete_reuses_snapshot_skips_llm(self):
        """COMPLETE 已落库 → 复用快照，跳过 LLM 与 register_version（防重复计费）。"""
        out, m_reg, m_regver = self._run("COMPLETE")
        m_reg.execute.assert_not_called()
        m_regver.assert_not_called()
        self.assertEqual(out["extraction_json"], {"contractTitle": "T"})
        self.assertEqual(out["extraction_status"], "COMPLETE")
        self.assertEqual(out["segments"], [{"index": 0, "title": "段", "content": "正文"}])
        self.assertEqual(out["extraction_rdf"], "rdf")
        self.assertEqual(out["extraction_usage"], {"total_tokens": 7})

    def test_incomplete_reuses_snapshot_skips_llm(self):
        """INCOMPLETE 已落库同样复用（守卫只防重复计费，不改变部分结果语义）。"""
        out, m_reg, m_regver = self._run("INCOMPLETE")
        m_reg.execute.assert_not_called()
        m_regver.assert_not_called()
        self.assertEqual(out["extraction_status"], "INCOMPLETE")

    def test_non_terminal_status_still_calls_llm(self):
        """未落库（None/FAILED）→ 守卫不触发，走正常 LLM 抽取路径。"""
        task = _Task()
        task.extraction_status = None
        m_reg = mock.MagicMock()
        m_reg.execute.return_value = {"std_json": {"contractTitle": "T"}, "status": "COMPLETE",
                                      "segments": [], "truncated": False, "error": None,
                                      "conflicts": [], "token_usage": {"total_tokens": 7}}
        with mock.patch.object(nodes, "SessionLocal", return_value=_FakeSession(task)), \
             mock.patch.object(nodes, "registry", m_reg), \
             mock.patch.object(nodes, "register_version", return_value=1), \
             mock.patch.object(nodes, "build_extraction_schema", return_value={}), \
             mock.patch.object(nodes, "load_ontology", return_value=None), \
             mock.patch.object(nodes, "split_segments", return_value=[]), \
             mock.patch.object(nodes, "JsonToRdfConverter") as m_conv:
            m_conv.return_value.convert.return_value = "rdf"
            out = nodes.extract_node({"task_id": 1, "parsed_text": "正文"})
        m_reg.execute.assert_called_once()
        self.assertEqual(out["extraction_status"], "COMPLETE")


class TestSemanticReuse(unittest.TestCase):
    def test_sem_outcomes_snapshot_reused_skips_llm(self):
        """语义快照已落库 → 复用跳过 evaluate_semantic（防重复计费）。"""
        task = _Task()
        task.sem_outcomes_json = json.dumps(
            [{"rule_id": 10, "result": "PASS", "confidence": "HIGH"}], ensure_ascii=False)
        task.sem_usage_json = json.dumps({"total_tokens": 9})
        m_reg = mock.MagicMock()
        with mock.patch.object(nodes, "SessionLocal", return_value=_FakeSession(task)), \
             mock.patch.object(nodes, "get_enabled_rules", return_value=[_FakeRule()]), \
             mock.patch.object(nodes, "registry", m_reg):
            out = nodes.validate_semantic({"task_id": 1})
        m_reg.execute.assert_not_called()
        self.assertEqual(out["sem_outcomes"],
                         [{"rule_id": 10, "result": "PASS", "confidence": "HIGH"}])
        self.assertEqual(out["sem_usage"], {"total_tokens": 9})

    def test_normal_path_snapshots_after_llm(self):
        """正常路径：LLM 照常调用，返回后结果落库快照（供崩溃重放复用）。"""
        task = _Task()
        outcomes = [{"rule_id": 10, "result": "PASS", "confidence": "HIGH"}]
        m_reg = mock.MagicMock()
        m_reg.execute.return_value = {"outcomes": outcomes, "usage": {"total_tokens": 9}}
        with mock.patch.object(nodes, "SessionLocal", return_value=_FakeSession(task)), \
             mock.patch.object(nodes, "get_enabled_rules", return_value=[_FakeRule()]), \
             mock.patch.object(nodes, "registry", m_reg), \
             mock.patch.object(nodes, "_persist_decisions"):
            out = nodes.validate_semantic({"task_id": 1})
        m_reg.execute.assert_called_once()
        self.assertEqual(out["sem_outcomes"], outcomes)
        # 快照已落库（task 实例属性被赋值）→ 若崩溃重放可直接复用
        self.assertEqual(json.loads(task.sem_outcomes_json), outcomes)
        self.assertEqual(json.loads(task.sem_usage_json), {"total_tokens": 9})

    def test_no_segments_no_snapshot(self):
        """无段分支不调 LLM 不落库快照（sem_outcomes_json 保持 None，重放结果一致无计费风险）。"""
        task = _Task()
        task.segments_json = None
        with mock.patch.object(nodes, "SessionLocal", return_value=_FakeSession(task)), \
             mock.patch.object(nodes, "get_enabled_rules", return_value=[_FakeRule()]), \
             mock.patch.object(nodes, "registry") as m_reg, \
             mock.patch.object(nodes, "_persist_decisions"):
            out = nodes.validate_semantic({"task_id": 1})
        m_reg.execute.assert_not_called()
        self.assertEqual(out["sem_outcomes"],
                         [{"rule_id": 10, "result": "SKIPPED", "confidence": "LOW"}])
        self.assertIsNone(task.sem_outcomes_json)


if __name__ == "__main__":
    unittest.main()
