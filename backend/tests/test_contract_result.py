"""B.4 评测契约输出层单元测试：answer 摘要 / 抽取结构摘要 / tool_call 映射 / 契约清单。

覆盖 get_task_result 的契约字段生成逻辑（CLAUDE.md 核心逻辑定义）：
- _task_answer：无违规 / 有违规按严重级排序 / FAILED 失败摘要（#B.4 修复，防评测误判合规）
- _extraction_summary：合同结构与条款统计、MODEL 排除与附加金额（#234）
- _rule_result_to_tool：规则命中 → tool_call 结构映射（D1）
- contracts.MANIFEST：契约清单端点声明
"""
import unittest
from datetime import datetime
from types import SimpleNamespace

from app.api import contracts
from app.service.check_task_service import (
    _extraction_summary,
    _rule_result_to_tool,
    _task_answer,
    _task_timing,
)


class TestTaskAnswer(unittest.TestCase):
    """_task_answer：摘要文本生成（含 #B.4 FAILED 语义修复）。"""

    def test_no_violation_no_json(self):
        self.assertEqual(_task_answer([], None), "合同校验完成，未检出违规项")

    def test_no_violation_with_summary(self):
        std = {"contractTitle": "采购合同", "totalAmount": 100000, "currency": "CNY"}
        out = _task_answer([], std)
        self.assertTrue(out.startswith("合同校验完成，未检出违规项。"))
        self.assertIn("合同名称：采购合同", out)

    def test_violations_sorted_by_severity(self):
        vs = [
            SimpleNamespace(severity="LOW", message="低风险"),
            SimpleNamespace(severity="HIGH", message="高风险"),
        ]
        out = _task_answer(vs, None)
        self.assertTrue(out.startswith("检出 2 处违规：HIGH：高风险；LOW：低风险"))

    def test_failed_no_violation_reports_failure(self):
        """#B.4：FAILED 且无 violations（抽取/校验异常）→ 失败摘要，避免评测误判合规。"""
        out = _task_answer([], None, status="FAILED")
        self.assertTrue(out.startswith("合同校验失败"))

    def test_failed_with_violation_still_lists(self):
        """FAILED 且确有 violations（人工确认）→ 正常列出违规，不走失败摘要。"""
        vs = [SimpleNamespace(severity="HIGH", message="单方签署")]
        out = _task_answer(vs, None, status="FAILED")
        self.assertTrue(out.startswith("检出 1 处违规：HIGH：单方签署"))

    def test_cancelled_no_violation_reports_cancelled(self):
        out = _task_answer([], None, status="CANCELLED")
        self.assertTrue(out.startswith("合同校验已取消"))

    def test_non_terminal_no_violation_reports_incomplete(self):
        out = _task_answer([], None, status="VALIDATING")
        self.assertTrue(out.startswith("合同校验未完成"))
        self.assertIn("VALIDATING", out)

    def test_waiting_review_no_violation_reports_pending(self):
        out = _task_answer([], None, status="WAITING_REVIEW")
        self.assertTrue(out.startswith("合同校验完成，待人工审核"))

    def test_success_no_violation_is_compliant(self):
        self.assertEqual(_task_answer([], None, status="SUCCESS"), "合同校验完成，未检出违规项")


class TestExtractionSummary(unittest.TestCase):
    """_extraction_summary：抽取结构摘要（#234）。"""

    def test_basic_info(self):
        std = {
            "contractTitle": "服务合同", "contractType": "服务",
            "totalAmount": 5000, "currency": "CNY",
            "hasParty": [{"partyName": "甲方公司"}, {"partyName": "乙方公司"}],
            "effectiveDate": "2026-01-01",
        }
        out = _extraction_summary(std)
        for frag in ("合同名称：服务合同", "合同类型：服务", "合同金额：5,000.00 元",
                     "当事人：甲方公司、乙方公司", "生效日期：2026-01-01"):
            self.assertIn(frag, out)

    def test_clause_count_excludes_model(self):
        std = {"hasClause": [
            {"clauseTitle": "第一条", "clauseText": "正文"},
            {"clauseTitle": "第二条", "clauseText": "正文2"},
            {"clauseTitle": "附加", "clauseText": "MODEL-001 单价人民币 1,000 元，数量 3 台"},
        ]}
        out = _extraction_summary(std)
        self.assertIn("条款数：3", out)  # 2 主条款 + 1 MODEL token
        self.assertIn("MODEL 附加设备条款覆盖 MODEL-001 至 MODEL-001", out)
        self.assertIn("附加设备条款金额合计：3,000.00 元", out)

    def test_empty_clauses_no_crash(self):
        self.assertEqual(_extraction_summary({"hasClause": []}), "")


class TestRuleResultToTool(unittest.TestCase):
    """_rule_result_to_tool：规则命中 → tool_call（D1 全量含 PASS/SKIPPED）。"""

    def _rule(self, **kw):
        base = dict(
            rule_id=62, rule_type="SEMANTIC", severity="HIGH",
            concept_iri=None, property_iri=None, segment_ref="seg-1",
            evidence_text="乙方（盖章）：", message="单方签署", confidence="HIGH",
            result="FAIL",
        )
        base.update(kw)
        return SimpleNamespace(**base)

    def test_structure_complete(self):
        out = _rule_result_to_tool(self._rule(), "单方签署（签署方完整性）")
        self.assertEqual(out["name"], "单方签署（签署方完整性）")
        self.assertEqual(out["args"]["rule_id"], 62)
        self.assertEqual(out["result"]["result"], "FAIL")
        self.assertEqual(out["result"]["evidence_text"], "乙方（盖章）：")

    def test_default_name_when_rule_unknown(self):
        out = _rule_result_to_tool(self._rule(rule_id=7), None)
        self.assertEqual(out["name"], "rule-7")


class TestTaskTiming(unittest.TestCase):
    """_task_timing：start/end 毫秒时间戳，同步接口不测首字（决策 #40）。"""

    def test_timestamps_millis_first_token_null(self):
        start = datetime(2026, 1, 1, 0, 0, 0)
        end = datetime(2026, 1, 1, 0, 1, 0)
        task = SimpleNamespace(create_time=start, update_time=end)
        out = _task_timing(task)
        self.assertEqual(out["start_ts"], int(start.timestamp() * 1000))
        self.assertEqual(out["end_ts"], int(end.timestamp() * 1000))
        self.assertIsNone(out["first_token_ts"])

    def test_missing_times_are_null(self):
        task = SimpleNamespace(create_time=None, update_time=None)
        out = _task_timing(task)
        self.assertIsNone(out["start_ts"])
        self.assertIsNone(out["end_ts"])


class TestContractsManifest(unittest.TestCase):
    """contracts.MANIFEST：标准契约清单（平台接口自动发现）。"""

    def test_manifest_fields(self):
        m = contracts.MANIFEST
        self.assertEqual(m["agent"], "contract-check")
        self.assertEqual(m["contract_version"], "2.0")
        paths = {i["path"] for i in m["interfaces"]}
        self.assertIn("/api/files/upload", paths)
        self.assertIn("/api/tasks/{task_id}/result", paths)
        # contract 段（manifest v2）：驱动契约必须带 upload + wait_done(poll) + request
        c = m["contract"]
        self.assertEqual(c["type"], "sync")
        self.assertEqual([p["name"] for p in c["prepare"]], ["upload", "wait_done"])
        self.assertIn("poll", c["prepare"][1])
        self.assertEqual(c["request"]["method"], "GET")

    def test_result_interface_is_llm_sync(self):
        iface = next(i for i in contracts.MANIFEST["interfaces"]
                     if i["name"] == "result")
        self.assertTrue(iface["llm"])
        self.assertEqual(iface["method"], "GET")
        self.assertEqual(iface["contract_type"], "sync")


if __name__ == "__main__":
    unittest.main()
