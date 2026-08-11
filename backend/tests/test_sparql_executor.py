"""确定性规则执行器单测：ASK→SELECT 转换、反例定位、空图防御（T2.2 核心逻辑）。"""
import unittest

import rdflib

from app.validation.sparql_executor import SparqlExecutor, _ask_to_select, build_graph


class TestAskToSelect(unittest.TestCase):
    def test_ask_to_select_adds_where(self):
        expr = "ASK { ?s a <http://example.org/contract#Contract> . }"
        sel = _ask_to_select(expr)
        self.assertTrue(sel.startswith("SELECT DISTINCT ?s"), sel)
        self.assertIn("WHERE {", sel)
        self.assertIn("?s a <http://example.org/contract#Contract>", sel)

    def test_non_ask_raises(self):
        with self.assertRaises(ValueError):
            _ask_to_select("SELECT ?s WHERE {}")


class _FakeAskRes:
    def __init__(self, ask):
        self.askAnswer = ask


class _FakeGraph:
    """模拟 rdflib.Graph.query：第一次（ASK）返回 askAnswer，第二次（SELECT）返回反例行。"""

    def __init__(self, ask_answer, subjects):
        self.ask_answer = ask_answer
        self.subjects = subjects
        self._first = True

    def query(self, q):
        if self._first:
            self._first = False
            return _FakeAskRes(self.ask_answer)
        return iter([(rdflib.URIRef(s),) for s in self.subjects])


class _FakeRule:
    expression = "ASK { ?s a <http://example.org/contract#Contract> . }"


class TestSparqlExecutor(unittest.TestCase):
    def test_empty_graph_skipped(self):
        res = SparqlExecutor().run(None, _FakeRule())
        self.assertFalse(res.passed)
        self.assertEqual(res.subjects, [], "空图无反例可判 → 上层标 SKIPPED")

    def test_no_counterexample_passes(self):
        res = SparqlExecutor().run(_FakeGraph(False, []), _FakeRule())
        self.assertTrue(res.passed)

    def test_counterexample_fails_with_subjects(self):
        res = SparqlExecutor().run(_FakeGraph(True, ["http://example.org/contract#c1"]), _FakeRule())
        self.assertFalse(res.passed)
        self.assertEqual(res.subjects, ["http://example.org/contract#c1"])

    def test_multi_subjects_sorted(self):
        res = SparqlExecutor().run(_FakeGraph(True, ["b", "a"]), _FakeRule())
        self.assertEqual(res.subjects, ["a", "b"], "多反例合并进一条 violation（不丢反例）")


class TestBuildGraph(unittest.TestCase):
    def test_empty_rdf_returns_none(self):
        self.assertIsNone(build_graph(None))
        self.assertIsNone(build_graph("  "))


if __name__ == "__main__":
    unittest.main()
