"""B.4 usage 聚合单元测试：抽取/语义 LLM token 累计（评测契约 usage）。

核心逻辑定义见 CLAUDE.md：覆盖 usage 聚合关键分支。用 unittest（不引入 pytest 依赖），
mock llm_client.call_json 避免真实 LLM 调用。
"""
import unittest
from unittest.mock import patch

from app.llm.extractor import (
    SingleResult,
    _merge_usage,
    _single,
    build_model,
    extract_contract,
)
from app.validation.semantic_evaluator import SemanticEvaluator

# 7.4 cache 字段透传：_merge_usage 固定 key 列表（含 cache），USAGE 常量需与之一致
USAGE = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
         "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0}


class TestMergeUsage(unittest.TestCase):
    """extractor._merge_usage：三分量累加、空值处理。"""

    def test_merge_two_dicts(self):
        out = _merge_usage(dict(USAGE), {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        self.assertEqual(out, {"prompt_tokens": 110, "completion_tokens": 55, "total_tokens": 165,
                               "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0})

    def test_none_b_returns_a(self):
        a = dict(USAGE)
        self.assertIs(_merge_usage(a, None), a)

    def test_none_a_copies_b(self):
        out = _merge_usage(None, USAGE)
        self.assertEqual(out, USAGE)
        self.assertIsNot(out, USAGE)  # 不共享引用，避免外部改 b 污染聚合

    def test_both_none(self):
        self.assertIsNone(_merge_usage(None, None))

    def test_partial_b_defaults_zero(self):
        out = _merge_usage(None, {"prompt_tokens": 7})
        self.assertEqual(out, {"prompt_tokens": 7, "completion_tokens": 0, "total_tokens": 0,
                               "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0})


class TestSingleUsage(unittest.TestCase):
    """_single 聚合本次全部 call_json 调用（重试多次）的 token。"""

    def _schema(self):
        return {"type": "object", "title": "Contract",
                "properties": {"partyName": {"type": "string"}}, "required": []}

    def test_single_call_usage_captured(self):
        model = build_model(self._schema(), strict=True)
        with patch("app.llm.extractor.call_json",
                   return_value=('{"partyName": "甲公司"}', "", dict(USAGE))):
            r = _single("甲方：甲公司", model, self._schema())
        self.assertIsNotNone(r.obj)
        self.assertEqual(r.usage, USAGE)

    def test_retry_accumulates_usage(self):
        # 第一次非法 JSON → 重试；两次 usage 累加
        model = build_model(self._schema(), strict=True)
        with patch("app.llm.extractor.call_json",
                   side_effect=[("not json", "", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
                                ('{"partyName": "甲公司"}', "", dict(USAGE))]):
            r = _single("甲方：甲公司", model, self._schema())
        self.assertIsNotNone(r.obj)
        self.assertEqual(r.usage, {"prompt_tokens": 110, "completion_tokens": 55, "total_tokens": 165,
                                   "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0})

    def test_usage_none_stays_none(self):
        model = build_model(self._schema(), strict=True)
        with patch("app.llm.extractor.call_json",
                   return_value=('{"partyName": "甲公司"}', "", None)):
            r = _single("甲方：甲公司", model, self._schema())
        self.assertIsNone(r.usage)


class TestExtractContractUsage(unittest.TestCase):
    """extract_contract 主路径 token_usage 透出到 ExtractionResult。"""

    def _schema(self):
        return {"type": "object", "title": "Contract",
                "properties": {"partyName": {"type": "string"}}, "required": []}

    def test_complete_carries_usage(self):
        # 文本须超 MIN_TEXT_CHARS=10 才进抽取（过短直接 FAILED，无 LLM 调用）
        text = "合同编号：2026001\n甲方：甲公司\n乙方：乙公司\n本合同约定甲乙双方的权利义务。"
        with patch("app.llm.extractor.call_json",
                   return_value=('{"partyName": "甲公司"}', "", dict(USAGE))):
            res = extract_contract(text, schema=self._schema())
        self.assertEqual(res.status, "COMPLETE")
        self.assertEqual(res.token_usage, USAGE)


class TestSemanticUsage(unittest.TestCase):
    """SemanticEvaluator usage 三分量累计 + token_cost 兼容 dry-run。"""

    def test_single_call_usage_accumulates(self):
        ev = SemanticEvaluator()
        ev._add_cost(USAGE)
        self.assertEqual(ev.usage, USAGE)
        self.assertEqual(ev.token_cost, 150)  # dry-run 兼容读法

    def test_multi_call_accumulates(self):
        ev = SemanticEvaluator()
        ev._add_cost(USAGE)
        ev._add_cost({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        ev._add_cost(None)  # 无 usage 调用不影响
        self.assertEqual(ev.usage, {"prompt_tokens": 110, "completion_tokens": 55, "total_tokens": 165,
                                    "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0})

    def test_evaluate_populates_usage(self):
        # 端到端：evaluate 跑批后 evaluator.usage 为全部 LLM 调用累计
        payload = '[{"rule_id": "r1", "pass": true, "reason": "ok", "evidence": "合同约定违约责任", "applicable": true}]'
        rules = [{"id": 1, "rule_iri": "r1", "rule_name": "违约条款", "expression": "需含违约条款"}]
        segs = [{"index": 0, "title": "第1段", "content": "合同约定违约责任。"}]
        ev = SemanticEvaluator()
        with patch("app.validation.semantic_evaluator.call_json",
                   return_value=(payload, "", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})):
            ev.evaluate(segs, rules)
        self.assertEqual(ev.usage["total_tokens"], 15)
        self.assertEqual(ev.token_cost, 15)


if __name__ == "__main__":
    unittest.main()
