"""llm_client.call_json 单测：LengthFinishReasonError 恢复 + 正常路径（T232）。

openai SDK 在 finish_reason="length" 时抛异常而非返回响应，call_json 必须从
e.completion 恢复 content/finish_reason/usage 按「截断」返回，上层分段重抽分支才生效。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from openai import LengthFinishReasonError

from app.llm import llm_client


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content, finish_reason="length"):
        self.message = _Msg(content)
        self.finish_reason = finish_reason


class _Usage:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return dict(self._data)


class _Completion:
    def __init__(self, choices, usage=None):
        self.choices = choices
        self.usage = usage


def _raise_length(choices, usage=None):
    """模拟 SDK：finish_reason=length 抛 LengthFinishReasonError（completion 为关键字参数）。"""
    raise LengthFinishReasonError(completion=_Completion(choices, usage))


class TestCallJson(unittest.TestCase):
    """call_json 返回三元组 (content, finish_reason, usage)。"""

    def _patch_invoke(self, side_effect):
        # llm.invoke 只接收一个参数（messages），mock 的 side_effect 签名必须是 1 参
        patcher = patch.object(llm_client, "get_chat_model")
        get_m = patcher.start()
        get_m.return_value.invoke.side_effect = side_effect
        self.addCleanup(patcher.stop)

    # ---- LengthFinishReasonError 恢复分支（T232）----

    def test_recovers_content_finish_and_usage(self):
        usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        self._patch_invoke(lambda _msg: _raise_length(
            [_Choice("部分抽取结果", "length")], _Usage(usage)))
        content, finish, out = llm_client.call_json("sys", "user")
        self.assertEqual(content, "部分抽取结果")
        self.assertEqual(finish, "length")
        self.assertEqual(out, usage)

    def test_finish_other_than_length_preserved(self):
        self._patch_invoke(lambda _msg: _raise_length(
            [_Choice("内容", "stop")], None))
        content, finish, out = llm_client.call_json("sys", "user")
        self.assertEqual(content, "内容")
        self.assertEqual(finish, "stop")
        self.assertIsNone(out)

    def test_empty_choices_no_usage(self):
        self._patch_invoke(lambda _msg: _raise_length([]))
        content, finish, out = llm_client.call_json("sys", "user")
        self.assertIsNone(content)
        self.assertEqual(finish, "length")
        self.assertIsNone(out)

    def test_content_list_joined(self):
        self._patch_invoke(lambda _msg: _raise_length(
            [_Choice([{"text": "甲"}, {"text": "乙"}])], None))
        content, finish, _ = llm_client.call_json("sys", "user")
        self.assertEqual(content, "甲乙")

    # ---- 正常返回路径 ----

    def test_normal_path(self):
        resp = SimpleNamespace(
            content="正常结果",
            response_metadata={"finish_reason": "stop", "token_usage": {"prompt_tokens": 7}},
        )
        self._patch_invoke(lambda _msg: resp)
        content, finish, out = llm_client.call_json("sys", "user")
        self.assertEqual(content, "正常结果")
        self.assertEqual(finish, "stop")
        self.assertEqual(out, {"prompt_tokens": 7})

    def test_normal_path_falls_back_to_usage_meta(self):
        resp = SimpleNamespace(
            content="正常结果",
            response_metadata={"finish_reason": "stop", "usage": {"total_tokens": 3}},
        )
        self._patch_invoke(lambda _msg: resp)
        _, _, out = llm_client.call_json("sys", "user")
        self.assertEqual(out, {"total_tokens": 3})


if __name__ == "__main__":
    unittest.main()
