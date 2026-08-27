"""tool_client 单测：patch _decision_model，断言 tool_calls 解析、无 json_object、length 恢复。"""
import unittest
from types import SimpleNamespace
from unittest import mock

from openai import LengthFinishReasonError

from app.config import settings
from app.llm.tool_client import ToolCall, ToolResponse, _decision_model, call_with_tools


def _fake_aimessage(content=None, tool_calls=None, finish="stop", usage=None):
    return SimpleNamespace(
        content=content,
        tool_calls=tool_calls or [],
        response_metadata={"finish_reason": finish, "token_usage": usage or {"total_tokens": 5}},
    )


def _fake_toolcall(name, args, tid="call_1"):
    return {"name": name, "args": args, "id": tid, "type": "tool_call"}


class TestCallWithTools(unittest.TestCase):
    @mock.patch("app.llm.tool_client._decision_model")
    def test_tool_calls_parsed(self, m):
        m.return_value.invoke.return_value = _fake_aimessage(
            tool_calls=[_fake_toolcall("decide_ocr", {"action": "ocr", "reason": "文本层不可读"})])
        r = call_with_tools("sys", "user", [{"function": {"name": "decide_ocr"}}])
        self.assertIsInstance(r, ToolResponse)
        self.assertEqual(len(r.tool_calls), 1)
        tc = r.tool_calls[0]
        self.assertIsInstance(tc, ToolCall)
        self.assertEqual(tc.name, "decide_ocr")
        self.assertEqual(tc.arguments, {"action": "ocr", "reason": "文本层不可读"})
        self.assertEqual(tc.id, "call_1")

    @mock.patch("app.llm.tool_client._decision_model")
    def test_no_tool_calls_pure_text(self, m):
        m.return_value.invoke.return_value = _fake_aimessage(content="无需调用")
        r = call_with_tools("sys", "user", [])
        self.assertEqual(r.content, "无需调用")
        self.assertEqual(r.tool_calls, [])
        self.assertEqual(r.finish_reason, "stop")

    @mock.patch("app.llm.tool_client._decision_model")
    def test_invoke_passes_tools(self, m):
        tools = [{"function": {"name": "decide_ocr"}}]
        m.return_value.invoke.return_value = _fake_aimessage(tool_calls=[])
        call_with_tools("sys", "user", tools)
        _, kwargs = m.return_value.invoke.call_args
        self.assertEqual(kwargs["tools"], tools)

    @mock.patch("app.llm.tool_client._decision_model")
    def test_length_finish_reason_restored(self, m):
        completion = SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=None, tool_calls=[]),
                finish_reason="length",
            )],
            usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 3}),
        )
        m.return_value.invoke.side_effect = LengthFinishReasonError(completion=completion)
        r = call_with_tools("sys", "user", [])
        self.assertEqual(r.finish_reason, "length")
        self.assertEqual(r.tool_calls, [])

    def test_decision_model_has_no_json_object(self):
        # 决策通道必须无 response_format（与 tools 冲突），否则 DeepSeek 拒调
        with mock.patch("app.llm.tool_client.settings.deepseek_api_key", "sk-test"):
            mdl = _decision_model()
        self.assertNotIn("response_format", mdl.model_kwargs or {})
        self.assertEqual(mdl.max_tokens, settings.tool_decision_max_tokens)


if __name__ == "__main__":
    unittest.main()
