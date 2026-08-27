"""节点决策接入单测：parse/extract/validate 经 registry + 决策引擎，决策痕迹落库。"""
import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from app.graph import nodes


class FakeSession:
    """可复用假 session：get 返回固定 task，commit 空转。"""

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
    def __init__(self, rule_type="DETERMINISTIC", iri="urn:rule:required:x",
                 expression="ASK { ?s <urn:p> ?o . }"):
        self.id = 10
        self.rule_iri = iri
        self.rule_name = "测试规则"
        self.rule_type = rule_type
        self.expression = expression
        self.aggregation = "any"


class FakeTask:
    id = 1
    status = "PENDING"
    progress = 0
    ontology_version_id = 1
    extraction_status = "COMPLETE"
    extraction_rdf = None
    segments_json = json.dumps([{"index": 0, "title": "段", "content": "正文内容"}])
    decision_json = None
    standard_json = None
    extraction_conflicts = None
    extraction_rdf_attr = None
    llm_model = None
    contract_file = None


def _state(**kw):
    s = {"task_id": 1}
    s.update(kw)
    return s


class TestParseNode(unittest.TestCase):
    def test_returns_parsed_text_and_decisions(self):
        class Cf:
            sha256 = "abc"
            storage_path = "/tmp/x.pdf"
            file_name = "合同.pdf"
            file_size = 1024
            has_scanned = False
            ocr_applied = False

        task = FakeTask()
        task.contract_file = Cf()
        trace = {"node": "parse", "tool": "decide_ocr", "decision": "skip",
                 "status": "short_circuit", "reason": "无需", "signals": {}, "usage": None, "ts": "t"}
        with TemporaryDirectory() as td, \
             mock.patch.object(nodes, "SessionLocal", return_value=FakeSession(task)), \
             mock.patch.object(nodes, "PARSED_DIR", Path(td)), \
             mock.patch.object(nodes, "decide_ocr_required", return_value=(False, trace)) as m_decide, \
             mock.patch.object(nodes, "_persist_decisions") as m_persist:
            out = nodes.parse_node(_state())
        self.assertEqual(out["parsed_text"], "")
        self.assertEqual(set(out), {"parsed_text"}, "决策痕迹不进 state（即时落库为唯一落库点）")
        m_decide.assert_called_once()
        m_persist.assert_called_once_with(1, [trace])

    def test_need_ocr_calls_registry(self):
        class Cf:
            sha256 = "abc"
            storage_path = "/tmp/x.pdf"
            file_name = "合同.pdf"
            file_size = 1024
            has_scanned = True
            ocr_applied = False

        task = FakeTask()
        task.contract_file = Cf()
        trace = {"node": "parse", "tool": "decide_ocr", "decision": "ocr", "status": "llm",
                 "reason": "", "signals": {}, "usage": None, "ts": "t"}
        with TemporaryDirectory() as td, \
             mock.patch.object(nodes, "SessionLocal", return_value=FakeSession(task)), \
             mock.patch.object(nodes, "PARSED_DIR", Path(td)), \
             mock.patch.object(nodes, "decide_ocr_required", return_value=(True, trace)), \
             mock.patch.object(nodes, "registry") as m_reg, \
             mock.patch.object(nodes, "_persist_decisions"):
            m_reg.execute.return_value = {"text": "OCR 识别文本"}
            out = nodes.parse_node(_state())
        self.assertEqual(out["parsed_text"], "OCR 识别文本")
        m_reg.execute.assert_called_once_with("ocr_pdf", pdf_path="/tmp/x.pdf")


class TestExtractNode(unittest.TestCase):
    def _extract_prereqs(self, task, result):
        """extract_node 前置 mock：Session/本体/schema/registry → (ExitStack, m_reg)。"""
        m_reg = mock.MagicMock()
        m_reg.execute.return_value = result
        stack = ExitStack()
        stack.enter_context(mock.patch.object(nodes, "SessionLocal", return_value=FakeSession(task)))
        stack.enter_context(mock.patch.object(nodes, "build_extraction_schema", return_value={}))
        stack.enter_context(mock.patch.object(nodes, "load_ontology", return_value=None))
        stack.enter_context(mock.patch.object(nodes, "register_version", return_value=1))
        stack.enter_context(mock.patch.object(nodes, "registry", m_reg))
        return stack, m_reg

    def test_failed_records_decision_and_raises(self):
        fail_result = {"std_json": None, "status": "FAILED", "segments": [], "truncated": False,
                       "error": "解析失败", "conflicts": [], "token_usage": None}
        task = FakeTask()
        trace = {"node": "extract", "tool": "decide_extract_retry", "decision": "fail",
                 "status": "llm", "reason": "", "signals": {}, "usage": None, "ts": "t"}
        stack, _ = self._extract_prereqs(task, fail_result)
        with stack, \
             mock.patch.object(nodes, "decide_extract_retry", return_value=("fail", trace)) as m_decide, \
             mock.patch.object(nodes, "_persist_decisions") as m_persist:
            with self.assertRaises(RuntimeError) as cm:
                nodes.extract_node(_state(parsed_text="正文"))
        self.assertIn("解析失败", str(cm.exception))
        m_decide.assert_called_once()
        m_persist.assert_called_once_with(1, [trace])

    def _fail_result(self):
        return {"std_json": None, "status": "FAILED", "segments": [], "truncated": False,
                "error": "解析失败", "conflicts": [], "token_usage": None}

    def _ok_result(self):
        return {"std_json": {"contractTitle": "T"}, "status": "COMPLETE", "segments": ["s"],
                "truncated": False, "error": None, "conflicts": [], "token_usage": {"total_tokens": 7}}

    def test_switch_on_retry_second_success(self):
        """开关开 + LLM 建议 retry → 重试一次，二次成功照常落库。"""
        task = FakeTask()
        trace = {"node": "extract", "tool": "decide_extract_retry", "decision": "retry",
                 "status": "llm", "reason": "", "signals": {}, "usage": None, "ts": "t"}
        stack, m_reg = self._extract_prereqs(task, self._fail_result())
        m_reg.execute.side_effect = [self._fail_result(), self._ok_result()]
        with stack, \
             mock.patch.object(nodes, "decide_extract_retry", return_value=("retry", trace)), \
             mock.patch.object(nodes, "_persist_decisions"), \
             mock.patch("app.graph.nodes.settings.extract_decision_allow_llm_retry", True), \
             mock.patch.object(nodes, "split_segments", return_value=[{"index": 0, "content": "正文"}]), \
             mock.patch.object(nodes, "JsonToRdfConverter") as m_conv:
            m_conv.return_value.convert.return_value = "rdf"
            out = nodes.extract_node(_state(parsed_text="正文"))
        self.assertEqual(out["extraction_status"], "COMPLETE")
        self.assertEqual(out["extraction_usage"], {"total_tokens": 7})
        self.assertEqual(m_reg.execute.call_count, 2, "开关开 + retry 应重试一次")

    def test_switch_on_retry_second_fail(self):
        """开关开 + retry 但二次仍 FAILED → 抛异常（防循环硬上限）。"""
        task = FakeTask()
        trace = {"node": "extract", "tool": "decide_extract_retry", "decision": "retry",
                 "status": "llm", "reason": "", "signals": {}, "usage": None, "ts": "t"}
        stack, m_reg = self._extract_prereqs(task, self._fail_result())
        m_reg.execute.side_effect = [self._fail_result(), self._fail_result()]
        with stack, \
             mock.patch.object(nodes, "decide_extract_retry", return_value=("retry", trace)), \
             mock.patch.object(nodes, "_persist_decisions"), \
             mock.patch("app.graph.nodes.settings.extract_decision_allow_llm_retry", True):
            with self.assertRaises(RuntimeError) as cm:
                nodes.extract_node(_state(parsed_text="正文"))
        self.assertIn("解析失败", str(cm.exception))
        self.assertEqual(m_reg.execute.call_count, 2, "二次仍 FAILED 应抛异常，不无限重试")

    def test_switch_on_action_fail_still_raises(self):
        """开关开但 LLM 建议 fail → 不重试直接抛异常。"""
        task = FakeTask()
        trace = {"node": "extract", "tool": "decide_extract_retry", "decision": "fail",
                 "status": "llm", "reason": "", "signals": {}, "usage": None, "ts": "t"}
        stack, m_reg = self._extract_prereqs(task, self._fail_result())
        with stack, \
             mock.patch.object(nodes, "decide_extract_retry", return_value=("fail", trace)), \
             mock.patch.object(nodes, "_persist_decisions"), \
             mock.patch("app.graph.nodes.settings.extract_decision_allow_llm_retry", True):
            with self.assertRaises(RuntimeError):
                nodes.extract_node(_state(parsed_text="正文"))
        self.assertEqual(m_reg.execute.call_count, 1, "action=fail 不触发重试")

    def test_success_returns_usage(self):
        ok = self._ok_result()
        task = FakeTask()
        stack, _ = self._extract_prereqs(task, ok)
        with stack, \
             mock.patch.object(nodes, "split_segments", return_value=[{"index": 0, "content": "正文"}]), \
             mock.patch.object(nodes, "JsonToRdfConverter") as m_conv, \
             mock.patch.object(nodes, "_persist_decisions"):
            m_conv.return_value.convert.return_value = "rdf"
            out = nodes.extract_node(_state(parsed_text="正文"))
        self.assertEqual(out["extraction_status"], "COMPLETE")
        self.assertEqual(out["extraction_usage"], {"total_tokens": 7})


class TestValidateDeterministic(unittest.TestCase):
    def test_uses_registry_run_sparql(self):
        task = FakeTask()
        with mock.patch.object(nodes, "SessionLocal", return_value=FakeSession(task)), \
             mock.patch.object(nodes, "sync_rules"), \
             mock.patch.object(nodes, "get_enabled_rules", return_value=[FakeRule()]), \
             mock.patch.object(nodes, "registry") as m_reg:
            m_reg.execute.return_value = {"passed": True, "subjects": [], "rule_snapshot": "ASK"}
            out = nodes.validate_deterministic(_state())
        self.assertEqual(out["det_outcomes"], [{"rule_id": 10, "result": "PASS", "subjects": []}])
        self.assertEqual(m_reg.execute.call_args.args[0], "run_sparql")


class TestValidateSemantic(unittest.TestCase):
    def test_uses_registry_evaluate_semantic(self):
        task = FakeTask()
        outcomes = [{"rule_id": 10, "result": "PASS", "message": None, "evidence_text": None,
                     "segment_ref": None, "confidence": "HIGH"}]
        with mock.patch.object(nodes, "SessionLocal", return_value=FakeSession(task)), \
             mock.patch.object(nodes, "get_enabled_rules",
                               return_value=[FakeRule(rule_type="SEMANTIC", iri="urn:rule:manual:1")]), \
             mock.patch.object(nodes, "registry") as m_reg:
            m_reg.execute.return_value = {"outcomes": outcomes, "usage": {"total_tokens": 9}}
            out = nodes.validate_semantic(_state())
        self.assertEqual(out["sem_outcomes"], outcomes)
        self.assertEqual(out["sem_usage"], {"total_tokens": 9})
        self.assertEqual(m_reg.execute.call_args.args[0], "evaluate_semantic")


if __name__ == "__main__":
    unittest.main()
