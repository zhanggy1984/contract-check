"""防御路径单测：D3 截断降级 / D4 空 content、非法 JSON 重试 / C6 resume 失败回退。

背景：端到端验收中这些分支依赖真实 LLM 异常，难以自然触发（验收缺口）。
此处 mock call_json / _run_flow 隔离 LLM 与图执行，直接覆盖分支逻辑。
unittest 风格（与 test_task_timeout.py 一致），pytest 作 runner，不触真实 DB/LLM。
"""
import unittest
from unittest.mock import patch

from app.llm import extractor
from app.llm.extractor import extract_contract
from app.llm.llm_client import LLMError

# schema required = [contractTitle, currency, hasParty]，hasParty 允许空数组
VALID_JSON = '{"contractTitle": "测试合同", "currency": "CNY", "hasParty": []}'
TEXT = (
    "购销合同\n甲方：甲公司\n乙方：乙公司\n"
    "第一条 合同标的：服务器，金额 10000 元。\n"
    "第二条 违约责任：任何一方违约应赔偿对方损失。\n"
)


class TestExtractRetry(unittest.TestCase):
    """D4a 空 content → feedback 重试后成功；D4b 非法 JSON → feedback 重试后成功。"""

    def test_empty_content_retries_then_succeeds(self):
        calls = []

        def fake_call_json(system, user):
            calls.append(user)
            if len(calls) == 1:
                return (None, "stop", None)   # 空 content
            return (VALID_JSON, "stop", None)

        with patch.object(extractor, "call_json", side_effect=fake_call_json):
            r = extract_contract(TEXT)
        self.assertEqual(len(calls), 2, "空 content 应触发一次重试")
        self.assertEqual(r.status, "COMPLETE")
        self.assertIsNotNone(r.std_json)

    def test_invalid_json_retries_then_succeeds(self):
        calls = []

        def fake_call_json(system, user):
            calls.append(user)
            if len(calls) == 1:
                return ("{not-json", "stop", None)   # 非法 JSON
            return (VALID_JSON, "stop", None)

        with patch.object(extractor, "call_json", side_effect=fake_call_json):
            r = extract_contract(TEXT)
        self.assertEqual(len(calls), 2, "非法 JSON 应触发一次重试")
        self.assertEqual(r.status, "COMPLETE")


class TestTruncateFallback(unittest.TestCase):
    """D3 输出截断（finish_reason=length）→ 降级分段重抽。"""

    def test_truncated_falls_back_to_segmented(self):
        calls = []

        def fake_call_json(system, user):
            calls.append(user)
            if len(calls) == 1:
                return ("", "length", None)   # 单段截断
            return (VALID_JSON, "stop", None)

        with patch.object(extractor, "call_json", side_effect=fake_call_json):
            r = extract_contract(TEXT)
        self.assertEqual(len(calls), 2, "截断后应走分段重抽")
        self.assertEqual(r.status, "COMPLETE", "降级分段重抽应成功而非 FAILED")
        # 注：r.truncated 不保证为 True——单段入口截断转 segmented 后，
        # any_truncated 按分段实际截断重算且 truncated 无下游消费方，故不断言。

    def test_retruncated_segment_bisects_half(self):
        """#234：段首抽仍截断 → 按 SEGMENT_CHAR_LIMIT//2 二分真正降级。

        旧代码 `_split_text(seg)` 用默认 limit=20000，_split_text 内 len<=limit 返回 [text]，
        二分递归是死代码 → 截断段结果整段丢弃（long 合同 run 829 漏核心字段根因）。
        """
        from app.llm.extractor import SEGMENT_CHAR_LIMIT, build_model, extract_contract_segmented
        from app.ontology.loader import load_ontology
        from app.ontology.schema_mapper import build_extraction_schema

        schema = build_extraction_schema(load_ontology())
        model = build_model(schema, strict=True)
        calls = []

        def fake_call_json(system, user):
            calls.append(1)
            if len(calls) == 1:
                return ("", "length", None)   # 段首抽截断 → 触发二分降级
            return (VALID_JSON, "stop", None)

        # 单段略超分块阈值：首抽截断后按半阈值硬切（无换行的超长段按字符切）
        seg = "第一条 标的 " + "x" * (SEGMENT_CHAR_LIMIT + 100)
        with patch.object(extractor, "call_json", side_effect=fake_call_json):
            r = extract_contract_segmented(seg, schema, [seg], model)
        self.assertGreater(len(calls), 1, "截断后应二分递归重抽而非直接丢段")
        self.assertEqual(r.status, "COMPLETE", "二分降级合并后应成功")
        self.assertIsNotNone(r.std_json)


class TestResumeRollback(unittest.TestCase):
    """C6 resume 图执行异常 → REVIEWING 幂等回退 WAITING_REVIEW，返回 False。"""

    def test_resume_failure_rolls_back(self):
        import app.service.check_task_service as svc

        class _FakeResult:
            def __init__(self, rowcount):
                self.rowcount = rowcount

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def query(self, *a, **k):
                return self

            def filter_by(self, *a, **k):
                return self

            def all(self):
                return []                       # 无 UNCONFIRMED violation

            def commit(self):
                pass

            def execute(self, *a, **k):
                self.execute_count = getattr(self, "execute_count", 0) + 1
                return _FakeResult(1)           # CAS / 回退 UPDATE 均成功

        fake = FakeSession()
        with patch.object(svc, "SessionLocal", return_value=fake), \
             patch.object(svc, "_run_flow", side_effect=RuntimeError("图执行异常")):
            ok = svc.resume_task(999, [])
        self.assertFalse(ok, "resume 失败应返回 False")
        self.assertEqual(fake.execute_count, 2,
                         "CAS 抢占 + 回退各 1 次 UPDATE，证明失败后回退发生")


class TestBlankRequiredField(unittest.TestCase):
    """方案 A：必填字段空串视为缺失 → 触发重试，而非被 Pydantic 当"有值"放行。"""

    def test_blank_required_triggers_retry(self):
        calls = []

        def fake_call_json(system, user):
            calls.append(user)
            if len(calls) == 1:
                return ('{"contractTitle": "", "currency": "CNY", "hasParty": []}', "stop", None)
            return (VALID_JSON, "stop", None)

        with patch.object(extractor, "call_json", side_effect=fake_call_json):
            r = extract_contract(TEXT)
        self.assertEqual(len(calls), 2, "必填空串应触发一次重试")
        self.assertEqual(r.status, "COMPLETE")

    def test_blank_required_still_blank_leads_incomplete(self):
        calls = []

        def fake_call_json(system, user):
            calls.append(user)
            return ('{"contractTitle": "", "currency": "CNY", "hasParty": []}', "stop", None)

        with patch.object(extractor, "call_json", side_effect=fake_call_json):
            r = extract_contract(TEXT)
        self.assertEqual(len(calls), 3, "重试仍空串 → 重试耗尽（MAX_ATTEMPTS=3）")
        self.assertEqual(r.status, "INCOMPLETE", "必填空串重试仍空 → INCOMPLETE 而非 COMPLETE")


class TestFallbackTitle(unittest.TestCase):
    """#234：LLM 偶发漏抽必填 contractTitle → 从原文首行提取标题兜底（run 841 286）。"""

    def test_keeps_existing_title(self):
        from app.llm.extractor import _fallback_title
        self.assertEqual(_fallback_title("购销合同\n甲方：甲", "已有标题"), "已有标题", "已有标题不动")

    def test_first_line_title(self):
        from app.llm.extractor import _fallback_title
        self.assertEqual(_fallback_title("购销合同\n甲方：甲公司\n乙方：乙公司", None), "购销合同")

    def test_skips_key_value_lines(self):
        from app.llm.extractor import _fallback_title
        text = "甲方：甲公司\n乙方：乙公司\n技术服务合同\n"
        self.assertEqual(_fallback_title(text, None), "技术服务合同", "应跳过键值行取标题行")

    def test_short_line_before_long_header(self):
        from app.llm.extractor import _fallback_title
        text = "合同编号：HT-2026-001\n" + "长" * 60 + "\n购销协议"
        self.assertEqual(_fallback_title(text, None), "购销协议", "跳过编号行与超长行")

    def test_no_title_returns_none(self):
        from app.llm.extractor import _fallback_title
        self.assertIsNone(_fallback_title("甲方：甲\n乙方：乙\n第一条 标的\n", None), "无标题行不编造")

    def test_incomplete_contract_backfills_title(self):
        """INCOMPLETE（contractTitle 漏抽）→ extract_contract 兜底补原文首行标题。"""
        from app.llm.extractor import SingleResult, extract_contract

        obj = {"contractType": "采购", "currency": "CNY", "hasParty": [],
               "totalAmount": 100000.0}  # 缺 contractTitle
        with patch.object(extractor, "_single", return_value=SingleResult(obj, False, 3, "必填缺失")):
            r = extract_contract("购销合同\n第一条 标的\n")
        self.assertEqual(r.status, "INCOMPLETE", "必填缺失仍应 INCOMPLETE")
        self.assertEqual(r.std_json["contractTitle"], "购销合同", "兜底应补原文首行标题")


class TestMergeClauseDedup(unittest.TestCase):
    """#234：分段合并 hasClause 按 clauseText 去重（同正文不同标题的重复条款）。

    结构去重（整 dict 相等）判不全同正文配不同 clauseTitle 的重复（LLM 标题漂移），
    导致条款数虚高——run 840 抽重 MODEL-151/187 致 251 条 vs 实际 249（judge 逐字比对扣分）。
    """

    def test_dedupes_clause_by_text(self):
        from app.llm.extractor import _merge_contracts

        schema = {
            "properties": {
                "contractTitle": {"type": "string"},
                "hasClause": {"type": "array", "items": {"type": "object"}},
            }
        }
        c1 = {"clauseText": "MODEL-012 服务器一台", "clauseTitle": "第12条 附加条款"}
        c1_dup = {"clauseText": "MODEL-012 服务器一台", "clauseTitle": "附加条款"}  # 标题漂移、正文逐字相同
        c2 = {"clauseText": "MODEL-013 交换机一台", "clauseTitle": "第13条 附加条款"}
        merged, _ = _merge_contracts(
            [{"contractTitle": "T", "hasClause": [c1, c1_dup]},
             {"contractTitle": "", "hasClause": [c2]}],
            schema,
        )
        self.assertEqual(len(merged["hasClause"]), 2, "同正文不同标题的重复应去重（保留首个）")
        self.assertEqual(merged["contractTitle"], "T", "标量取首个非空")

    def test_distinct_text_kept(self):
        from app.llm.extractor import _merge_contracts

        schema = {"properties": {"hasClause": {"type": "array", "items": {"type": "object"}}}}
        a = {"clauseText": "第一条 标的", "clauseTitle": "标的"}
        b = {"clauseText": "第二条 违约", "clauseTitle": "违约"}
        merged, _ = _merge_contracts([{"hasClause": [a]}, {"hasClause": [b]}], schema)
        self.assertEqual(len(merged["hasClause"]), 2, "正文不同的条款不得误删")


class TestSplitTextNoLoss(unittest.TestCase):
    """P0 review 修复：_split_text 遇超长段落时，先前累积的短段不得被丢弃。"""

    def test_long_paragraph_keeps_accumulated_text(self):
        from app.llm.extractor import SEGMENT_CHAR_LIMIT, _split_text

        text = "短段落A\n" + "长" * (SEGMENT_CHAR_LIMIT + 100) + "\n短段落B"
        chunks = _split_text(text)
        joined = "".join(chunks)
        self.assertIn("短段落A", joined, "超长段前累积的段落不应丢失")
        self.assertIn("短段落B", joined)
        # 字符级无丢失：全部字符都保留在 chunks 拼接结果中
        expected_len = len("短段落A") + (SEGMENT_CHAR_LIMIT + 100) + len("短段落B")
        self.assertEqual(sum(len(c) for c in chunks), expected_len)


class TestExtractLLMError(unittest.TestCase):
    """LLM 网络异常兜底：SDK 重试耗尽后干净失败（FAILED 带 error + usage），不再裸抛。"""

    def test_single_segment_llm_error_clean_failed(self):
        with patch.object(extractor, "call_json", side_effect=LLMError("连接超时")):
            r = extract_contract(TEXT)
        self.assertEqual(r.status, "FAILED", "网络失败应落 FAILED 而非裸抛")
        self.assertIsNone(r.std_json)
        self.assertIn("LLM 调用失败", r.error or "", "error 应含可审计原因")

    def test_segmented_llm_error_clean_failed(self):
        from app.llm.extractor import SINGLE_SEGMENT_CHAR_LIMIT

        # 超长文本走分段路径：每段 call_json 均抛 LLMError → 全部分段失败 → 干净 FAILED
        long_text = "第一条 标的 服务器壹台。\n" * (SINGLE_SEGMENT_CHAR_LIMIT // 12 + 100)
        with patch.object(extractor, "call_json", side_effect=LLMError("限流")):
            r = extract_contract(long_text)
        self.assertEqual(r.status, "FAILED", "分段全失败应落 FAILED 而非裸抛")
        self.assertEqual(r.error, "全部分段抽取失败")


if __name__ == "__main__":
    unittest.main()
