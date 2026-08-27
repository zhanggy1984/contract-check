"""ToolRegistry 纯逻辑单测：注册/取用/schema 数组/缺名快速失败。"""
import unittest

from app.tools.registry import Tool, ToolRegistry


def _make_registry() -> ToolRegistry:
    return ToolRegistry([
        Tool("t1", {"type": "function", "function": {"name": "t1"}}, lambda **kw: {"got": kw}),
        Tool("t2", {"type": "function", "function": {"name": "t2"}}, lambda: "t2-ok"),
    ])


class TestToolRegistry(unittest.TestCase):
    def test_names(self):
        self.assertEqual(set(_make_registry().names()), {"t1", "t2"})

    def test_get_returns_tool(self):
        t = _make_registry().get("t1")
        self.assertEqual(t.name, "t1")
        self.assertEqual(t.schema["function"]["name"], "t1")

    def test_get_missing_raises_keyerror(self):
        with self.assertRaises(KeyError):
            _make_registry().get("nope")

    def test_contains(self):
        reg = _make_registry()
        self.assertIn("t1", reg)
        self.assertNotIn("nope", reg)

    def test_execute_passes_kwargs(self):
        reg = _make_registry()
        self.assertEqual(reg.execute("t1", a=1, b=2), {"got": {"a": 1, "b": 2}})

    def test_execute_no_args(self):
        self.assertEqual(_make_registry().execute("t2"), "t2-ok")

    def test_execute_missing_raises(self):
        with self.assertRaises(KeyError):
            _make_registry().execute("nope")

    def test_execute_exception_propagates(self):
        def boom(**_):
            raise RuntimeError("boom")
        reg = ToolRegistry([Tool("t1", {"function": {"name": "t1"}}, boom)])
        with self.assertRaises(RuntimeError):
            reg.execute("t1")

    def test_schemas_all(self):
        s = _make_registry().schemas()
        self.assertEqual(len(s), 2)
        self.assertEqual({x["function"]["name"] for x in s}, {"t1", "t2"})

    def test_schemas_filtered(self):
        s = _make_registry().schemas(["t1"])
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["function"]["name"], "t1")

    def test_schemas_empty_sequence(self):
        self.assertEqual(_make_registry().schemas([]), [])

    def test_schemas_missing_raises(self):
        with self.assertRaises(KeyError):
            _make_registry().schemas(["nope"])


if __name__ == "__main__":
    unittest.main()
