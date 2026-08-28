"""契约隔离不变式单测：决策痕迹独立于评测契约 tool_calls/usage。

不变式（plan §契约保护）：
1. tool_calls 由 _rule_result_to_tool 生成，决策痕迹从不进入
2. usage 仅聚合抽取+语义 token（token_usage_json），决策 usage 只进 trace
3. result 顶层新增 decisions 独立键，与 tool_calls/usage 零关联
"""
import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from app.db.models import CheckRule
from app.service import check_task_service as svc


class _Q:
    def __init__(self, rows):
        self._rows = rows

    def filter_by(self, **kw):
        return self

    def order_by(self, *a):
        return self

    def all(self):
        return self._rows


class FakeDb:
    def __init__(self, task):
        self._task = task

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, model, task_id):
        return self._task

    def query(self, model):
        if model is CheckRule:
            return _Q([])
        return _Q([])  # Violation / RuleCheckResult 均空，聚焦契约结构


def _get_result(task):
    """经 svc.get_task_result 取结果：桩 SessionLocal 注入 FakeDb，隔离真实 DB。"""
    with mock.patch.object(svc, "SessionLocal", return_value=FakeDb(task)):
        return svc.get_task_result(1)


def _task_with(decision_json, usage_json):
    return SimpleNamespace(
        id=1, status="SUCCESS", extraction_status="COMPLETE",
        standard_json=json.dumps({"contractTitle": "采购合同"}),
        token_usage_json=usage_json,
        decision_json=decision_json,
        llm_model="deepseek-chat",
        create_time=datetime(2026, 1, 1, 0, 0, 0),
        update_time=datetime(2026, 1, 1, 0, 1, 0),
    )


class TestContractProtection(unittest.TestCase):
    def test_decisions_independent_top_level_key(self):
        decisions = [{"node": "parse", "tool": "decide_ocr", "decision": "skip",
                      "status": "short_circuit", "reason": "文本层可读", "signals": {},
                      "usage": {"total_tokens": 3}, "ts": "2026-01-01T00:00:00"}]
        task = _task_with(json.dumps(decisions, ensure_ascii=False),
                          json.dumps({"total_tokens": 100}, ensure_ascii=False))
        result = _get_result(task)
        self.assertIn("decisions", result, "result 必须含独立 decisions 顶层键")
        self.assertEqual(result["decisions"], decisions, "decisions 逐字等于 decision_json")

    def test_decision_usage_not_in_eval_usage(self):
        """决策 LLM token 只进 trace，不得并入评测契约 usage。"""
        task = _task_with(
            json.dumps([{"tool": "decide_ocr", "usage": {"total_tokens": 3}}]),
            json.dumps({"total_tokens": 100}),
        )
        result = _get_result(task)
        self.assertEqual(result["usage"]["total_tokens"], 100, "决策 usage 不得混入评测契约")
        self.assertEqual(result["decisions"][0]["usage"]["total_tokens"], 3)

    def test_trace_never_in_tool_calls(self):
        """决策痕迹从不进入 tool_calls（tool_calls 由规则结果生成，此处无规则 → 空）。"""
        task = _task_with(
            json.dumps([{"tool": "decide_ocr", "decision": "ocr"}]),
            json.dumps({"total_tokens": 1}),
        )
        result = _get_result(task)
        self.assertEqual(result["tool_calls"], [])
        self.assertNotIn("decide_ocr", json.dumps(result["tool_calls"], ensure_ascii=False))

    def test_contract_fields_untouched(self):
        """tool_calls/usage/answer/meta/timing 等契约字段照旧存在且结构不变。"""
        task = _task_with(
            json.dumps([{"tool": "decide_ocr", "decision": "skip"}]),
            json.dumps({"total_tokens": 100}),
        )
        result = _get_result(task)
        for key in ("answer", "usage", "timing", "tool_calls", "meta",
                    "standard_json", "violations", "rule_results", "id", "status"):
            self.assertIn(key, result)
        self.assertEqual(result["tool_calls"], [])
        self.assertTrue(result["answer"].startswith("合同校验完成"))
        self.assertEqual(result["meta"]["agent"], "contract-check")


if __name__ == "__main__":
    unittest.main()
