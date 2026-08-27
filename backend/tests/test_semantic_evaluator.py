"""SemanticEvaluator 单元测试（T3.1）：evidence 归一化防御 + 单规则汇总聚合。

核心逻辑定义见 CLAUDE.md：Service 层业务分支逻辑需单测，此处覆盖评估器关键分支。
用 unittest（不引入 pytest 依赖），mock llm_client.call_json 避免真实 LLM 调用。
"""
import unittest
from unittest.mock import patch

from app.llm.llm_client import LLMError
from app.validation.semantic_evaluator import (
    Judgment,
    JudgmentOutcome,
    SemanticEvaluator,
    _parse_judgments,
    evidence_ok,
    normalize,
)


def _seg(content: str, index: int = 0) -> dict:
    return {"index": index, "title": f"第{index + 1}段", "content": content}


def _judgment(rule_id: str, pass_: bool, evidence: str = "", applicable: bool = True) -> Judgment:
    # populate_by_name=True：可用字段名 pass_（pass 为关键字，不能作参数名）
    return Judgment(rule_id=rule_id, pass_=pass_, evidence=evidence, applicable=applicable)


class TestNormalize(unittest.TestCase):
    def test_fullwidth_halfwidth(self):
        # 全角数字/字母 → 半角
        self.assertEqual(normalize("第１条 甲方Ａ"), "第1条甲方A")

    def test_whitespace_collapse(self):
        # 换行/空格全部去除（OCR 断字防御）
        self.assertEqual(normalize("违约 责任\n条　款"), "违约责任条款")

    def test_empty(self):
        self.assertEqual(normalize(""), "")
        self.assertEqual(normalize("  \n "), "")


class TestEvidenceOk(unittest.TestCase):
    def setUp(self):
        self.segments = [_seg("合同约定甲方应当按时支付货款。违约责任：逾期每日按 1% 收取违约金。")]

    def test_exact_substring(self):
        self.assertTrue(evidence_ok(self.segments, "违约责任：逾期每日按 1% 收取违约金。"))

    def test_normalized_substring(self):
        # evidence 与原文存在全角/换行差异 → 归一化后命中
        self.assertTrue(evidence_ok(self.segments, "违约责任：逾期每日按１％收取违约金。"))

    def test_rewritten_evidence_fails(self):
        # LLM 改写/概括 → 判 False（触发防御重试/LOW）
        self.assertFalse(evidence_ok(self.segments, "合同约定了逾期付款的违约责任"))

    def test_empty_evidence_fails(self):
        self.assertFalse(evidence_ok(self.segments, ""))

    def test_ocr_break_ignored(self):
        # 段内容有断字空格，evidence 连续 → 归一化后命中
        segs = [_seg("违约 责任 由 违约方 承担")]
        self.assertTrue(evidence_ok(segs, "违约责任由违约方承担"))


class TestParseJudgments(unittest.TestCase):
    def test_valid_array(self):
        out = _parse_judgments('[{"rule_id": "r1", "pass": false, "reason": "缺违约条款", "evidence": "原文", "applicable": true}]')
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].rule_id, "r1")
        self.assertFalse(out[0].pass_)

    def test_invalid_json(self):
        self.assertIsNone(_parse_judgments("不是 JSON"))

    def test_empty(self):
        self.assertIsNone(_parse_judgments(""))
        self.assertIsNone(_parse_judgments(None))

    def test_not_list(self):
        self.assertIsNone(_parse_judgments('{"rule_id": "r1"}'))


class TestAggregate(unittest.TestCase):
    def setUp(self):
        self.ev = SemanticEvaluator()

    def test_all_pass(self):
        items = [JudgmentOutcome("r1", _judgment("r1", True, "合规"), "HIGH", 0)]
        out = self.ev._aggregate(1, items)
        self.assertEqual(out.result, "PASS")
        self.assertIsNone(out.evidence_text)

    def test_any_fail(self):
        items = [
            JudgmentOutcome("r1", _judgment("r1", True, "合规"), "HIGH", 0),
            JudgmentOutcome("r1", _judgment("r1", False, "第X条…"), "HIGH", 1),
        ]
        out = self.ev._aggregate(1, items)
        self.assertEqual(out.result, "FAIL")
        self.assertEqual(out.segment_ref, "seg-1")
        self.assertEqual(out.confidence, "HIGH")
        self.assertEqual(out.evidence_text, "第X条…")

    def test_fail_prefers_high_confidence(self):
        # 一段 LOW、一段 HIGH，取 HIGH 的 evidence
        items = [
            JudgmentOutcome("r1", _judgment("r1", False, "低置信证据"), "LOW", 0),
            JudgmentOutcome("r1", _judgment("r1", False, "精确子串证据"), "HIGH", 2),
        ]
        out = self.ev._aggregate(1, items)
        self.assertEqual(out.confidence, "HIGH")
        self.assertEqual(out.segment_ref, "seg-2")

    def test_all_not_applicable_skipped(self):
        # 全部段规则不适用 → SKIPPED，confidence 保持 HIGH（正常业务结论）
        items = [JudgmentOutcome("r1", _judgment("r1", False, "", applicable=False), "HIGH", 0)]
        out = self.ev._aggregate(1, items)
        self.assertEqual(out.result, "SKIPPED")
        self.assertEqual(out.confidence, "HIGH")

    def test_all_missing_skipped(self):
        # LLM 全段遗漏该规则 → SKIPPED 防御（不静默丢规则），标 LOW 表明评估失败
        items = [JudgmentOutcome("r1", None, "LOW", 0), JudgmentOutcome("r1", None, "LOW", 1)]
        out = self.ev._aggregate(1, items)
        self.assertEqual(out.result, "SKIPPED")
        self.assertEqual(out.confidence, "LOW")

    def test_mixed_not_applicable_and_missing_low(self):
        # 部分段不适用、部分段无判定 → SKIPPED 但 LOW（评估不完整，非规则明确不适用）
        items = [
            JudgmentOutcome("r1", _judgment("r1", False, "", applicable=False), "HIGH", 0),
            JudgmentOutcome("r1", None, "LOW", 1),
        ]
        out = self.ev._aggregate(1, items)
        self.assertEqual(out.result, "SKIPPED")
        self.assertEqual(out.confidence, "LOW")

    def test_aggregation_all_all_fail(self):
        # 缺失性检查（all）：全部适用段都 fail → FAIL
        items = [
            JudgmentOutcome("r1", _judgment("r1", False, "段1原文"), "HIGH", 0),
            JudgmentOutcome("r1", _judgment("r1", False, "段2原文"), "HIGH", 1),
        ]
        out = self.ev._aggregate(1, items, aggregation="all")
        self.assertEqual(out.result, "FAIL")
        self.assertEqual(out.confidence, "HIGH")

    def test_aggregation_all_any_pass_means_pass(self):
        # 缺失性检查（all）：任一段判"存在"（pass）→ 整体 PASS（消除单段视角误报）
        items = [
            JudgmentOutcome("r1", _judgment("r1", False, "段1原文"), "HIGH", 0),
            JudgmentOutcome("r1", _judgment("r1", True, "第四条 违约责任…"), "HIGH", 1),
        ]
        out = self.ev._aggregate(1, items, aggregation="all")
        self.assertEqual(out.result, "PASS")

    def test_aggregation_all_mixed_low_fail(self):
        # 缺失性检查（all）：全部适用段 fail 但含 LOW → FAIL LOW
        items = [
            JudgmentOutcome("r1", _judgment("r1", False, "e1"), "LOW", 0),
            JudgmentOutcome("r1", _judgment("r1", False, "e2"), "LOW", 1),
        ]
        out = self.ev._aggregate(1, items, aggregation="all")
        self.assertEqual(out.result, "FAIL")
        self.assertEqual(out.confidence, "LOW")

    def test_aggregation_any_differs_from_all(self):
        # 对照组：同输入 any 模式部分 fail 即 FAIL（区别于 all 的 PASS）
        items = [
            JudgmentOutcome("r1", _judgment("r1", False, "段1原文"), "HIGH", 0),
            JudgmentOutcome("r1", _judgment("r1", True, "段2原文"), "HIGH", 1),
        ]
        self.assertEqual(self.ev._aggregate(1, items, aggregation="any").result, "FAIL")
        self.assertEqual(self.ev._aggregate(1, items, aggregation="all").result, "PASS")

    def test_fail_low_confidence(self):
        items = [JudgmentOutcome("r1", _judgment("r1", False, "非精确子串"), "LOW", 0)]
        out = self.ev._aggregate(1, items)
        self.assertEqual(out.result, "FAIL")
        self.assertEqual(out.confidence, "LOW")


class TestEvaluateSegment(unittest.TestCase):
    def setUp(self):
        self.ev = SemanticEvaluator(max_attempts=2)
        self.rules = [{"id": 10, "rule_iri": "r1", "rule_name": "缺违约条款", "expression": "检查违约条款"}]

    def test_good_evidence_high(self):
        seg = _seg("合同约定：任何一方违约应承担违约责任，逾期每日按 1% 支付违约金。")
        payload = json_dumps_judgments([{"rule_id": "r1", "pass": False, "reason": "违约条款不完整",
                                          "evidence": "任何一方违约应承担违约责任", "applicable": True}])
        with patch("app.validation.semantic_evaluator.call_json",
                   return_value=(payload, "", {"total_tokens": 100})):
            out = self.ev._evaluate_segment(seg, self.rules)
        self.assertEqual(out["r1"].confidence, "HIGH")

    def test_bad_evidence_retry_then_low(self):
        # 第一次 evidence 非精确子串 → 重试；第二次仍非精确子串 → LOW
        seg = _seg("合同约定违约责任。")
        bad = json_dumps_judgments([{"rule_id": "r1", "pass": False, "reason": "违约",
                                      "evidence": "合同约定了违约的概括表述", "applicable": True}])
        with patch("app.validation.semantic_evaluator.call_json",
                   return_value=(bad, "", {"total_tokens": 50})):
            out = self.ev._evaluate_segment(seg, self.rules)
        self.assertEqual(out["r1"].confidence, "LOW")

    def test_empty_content_retry(self):
        seg = _seg("合同约定违约责任。")
        with patch("app.validation.semantic_evaluator.call_json",
                   side_effect=[("", "", {"total_tokens": 10}),
                                (json_dumps_judgments([{"rule_id": "r1", "pass": True, "reason": "ok",
                                                        "evidence": "合同约定违约责任", "applicable": True}]), "", {"total_tokens": 20})]):
            out = self.ev._evaluate_segment(seg, self.rules)
        self.assertEqual(out["r1"].judgment.pass_, True)
        self.assertEqual(out["r1"].confidence, "HIGH")

    def test_truncated_marks_low(self):
        # 两次都截断 → 重试耗尽 → 全 LOW（评估失败）
        seg = _seg("合同约定违约责任。")
        with patch("app.validation.semantic_evaluator.call_json",
                   return_value=("", "length", {"total_tokens": 10})):
            out = self.ev._evaluate_segment(seg, self.rules)
        self.assertEqual(out["r1"].confidence, "LOW")
        self.assertIsNone(out["r1"].judgment)

    def test_network_error_degrades_to_low(self):
        # 网络/超时/限流（LLMError）→ 整段全 LOW，不因单段 LLM 故障炸掉整个任务（异常兜底）
        seg = _seg("合同约定违约责任。")
        with patch("app.validation.semantic_evaluator.call_json",
                   side_effect=LLMError("连接超时")):
            out = self.ev._evaluate_segment(seg, self.rules)
        self.assertEqual(out["r1"].confidence, "LOW")
        self.assertIsNone(out["r1"].judgment)

    def test_truncated_retry_success(self):
        # 第一次截断 → 重试 → 第二次正常返回 → HIGH（截断不再直接放弃）
        seg = _seg("合同约定违约责任。")
        ok = json_dumps_judgments([{"rule_id": "r1", "pass": False, "reason": "违约",
                                    "evidence": "合同约定违约责任", "applicable": True}])
        with patch("app.validation.semantic_evaluator.call_json",
                   side_effect=[("", "length", {"total_tokens": 10}),
                                (ok, "", {"total_tokens": 20})]):
            out = self.ev._evaluate_segment(seg, self.rules)
        self.assertEqual(out["r1"].confidence, "HIGH")
        self.assertFalse(out["r1"].judgment.pass_)


class TestEvaluate(unittest.TestCase):
    def test_multi_segment_aggregate(self):
        rules = [{"id": 10, "rule_iri": "r1", "rule_name": "缺违约条款", "expression": "检查违约条款"},
                 {"id": 11, "rule_iri": "r2", "rule_name": "技术标准", "expression": "检查技术标准"}]
        segments = [
            _seg("第一段：甲方应支付货款。", 0),
            _seg("第二段：任何一方违约应承担违约责任。", 1),
        ]
        responses = [
            json_dumps_judgments([
                {"rule_id": "r1", "pass": False, "reason": "第一段无违约条款", "evidence": "甲方应支付货款", "applicable": True},
                {"rule_id": "r2", "pass": True, "reason": "不涉及技术标准", "evidence": "", "applicable": False},
            ]),
            json_dumps_judgments([
                {"rule_id": "r1", "pass": True, "reason": "有违约条款", "evidence": "任何一方违约应承担违约责任", "applicable": True},
                {"rule_id": "r2", "pass": True, "reason": "无技术标准", "evidence": "", "applicable": False},
            ]),
        ]
        ev = SemanticEvaluator()
        with patch("app.validation.semantic_evaluator.call_json", side_effect=[(r, "", {"total_tokens": 50}) for r in responses]):
            results = ev.evaluate(segments, rules)
        by_id = {r.rule_id: r for r in results}
        # r1 任一适用段 FAIL → FAIL（取第一个 FAIL 段 evidence）
        self.assertEqual(by_id[10].result, "FAIL")
        self.assertEqual(by_id[10].confidence, "HIGH")
        self.assertEqual(by_id[10].evidence_text, "甲方应支付货款")
        # r2 全部段 applicable=false → SKIPPED
        self.assertEqual(by_id[11].result, "SKIPPED")

    def test_all_pass(self):
        rules = [{"id": 10, "rule_iri": "r1", "rule_name": "缺违约条款", "expression": "检查违约条款"}]
        segments = [_seg("合同包含完整违约责任条款：违约方赔偿守约方全部损失。", 0)]
        payload = json_dumps_judgments([
            {"rule_id": "r1", "pass": True, "reason": "有违约条款", "evidence": "违约方赔偿守约方全部损失", "applicable": True}])
        ev = SemanticEvaluator()
        with patch("app.validation.semantic_evaluator.call_json", return_value=(payload, "", {"total_tokens": 50})):
            results = ev.evaluate(segments, rules)
        self.assertEqual(results[0].result, "PASS")


def json_dumps_judgments(judgments: list[dict]) -> str:
    return __import__("json").dumps(judgments, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
