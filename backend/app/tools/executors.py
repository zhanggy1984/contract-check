"""能力 executor：把外部能力包装成统一形态（入参 kwargs、出参 dict 可 JSON 序列化）。

节点与（未来）LLM 都只经此层访问能力，不直接 import 能力模块，实现解耦。
run_sparql 的 graph/rule 允许原生对象传入（rdflib 图 / ORM 规则对象不可 JSON 化），
出参仍为 dict。决策 executor 是薄壳（只归一化参数），真实决策逻辑在 app/graph/decisions.py。
"""
import unicodedata
from dataclasses import asdict

from app.llm.extractor import extract_contract
from app.ocr.ocr_service import OcrService
from app.parser.text_cleaner import clean_text
from app.validation.semantic_evaluator import SemanticEvaluator
from app.validation.sparql_executor import SparqlExecutor


def exec_extract_contract(text: str, schema: dict | None = None) -> dict:
    """LLM 抽取合同 → dict（含 token_usage，供节点透出评测契约 usage）。"""
    r = extract_contract(text, schema)
    return {
        "std_json": r.std_json,
        "status": r.status,
        "segments": r.segments,
        "truncated": r.truncated,
        "error": r.error,
        "conflicts": r.conflicts,
        "token_usage": r.token_usage,
    }


def exec_evaluate_semantic(segments: list, rules: list) -> dict:
    """语义规则按段批跑 → outcomes dict 列表 + 聚合 usage。"""
    ev = SemanticEvaluator()
    outcomes = ev.evaluate(segments, rules)
    return {"outcomes": [asdict(o) for o in outcomes], "usage": ev.usage}


def exec_ocr_pdf(pdf_path: str, pages: list[int] | None = None) -> dict:
    """扫描 PDF 页 OCR → {页索引: 清洗文本, stats: 识别质量指标}。

    stats（T4.3-9）供日志观测；nodes 只消费 ["pages"]，加字段不破坏既有调用。
    PaddleOCR 未安装/全部页失败时抛 RuntimeError。
    """
    # OCR 文本噪声更大（全角/零宽/多余空白），统一过输入清洗，与文本层提取一致
    results, stats = OcrService.ocr_pdf_with_stats(pdf_path, pages=pages)
    return {"pages": {i: clean_text(t) for i, t in results.items()}, "stats": stats}


def exec_run_sparql(graph, rule) -> dict:
    """单条确定性规则对 RDF 图执行 → {passed, subjects, rule_snapshot}。"""
    r = SparqlExecutor().run(graph, rule)
    return {"passed": r.passed, "subjects": r.subjects, "rule_snapshot": r.rule_snapshot}


# ---- 决策 executor（薄壳：仅参数归一化，决策逻辑在 decisions.py）----

def _normalize_action(raw, allowed: set[str]) -> str | None:
    """LLM 返回 action 归一化：NFKC 全角转半角 / 去首尾空白 / 小写 / 去结尾标点
    → 命中 allowed 返回规范化值；非法返回 None（上游保守兜底，不猜值）。
    防御 LLM 返回 "skip。" / "Skip" / "ocr " 等噪声导致 decisions.py 判断失效。"""
    if not isinstance(raw, str):
        return None
    a = unicodedata.normalize("NFKC", raw).strip().lower()
    a = a.rstrip("。.！!？?；; ").strip()
    return a if a in allowed else None


def _clean_reason(raw, max_len: int = 200) -> str:
    """reason 清洗：非 str 归 ""，strip，超长截断（防 LLM 废话污染 traces/DB）。"""
    if not isinstance(raw, str):
        return ""
    r = raw.strip()
    return r[:max_len] if len(r) > max_len else r


def _decide_shell(action, reason, allowed: set[str]) -> dict:
    """决策薄壳共享实现：action 严格匹配 allowed（非法→None 上游兜底），reason 清洗截断。
    invalid_action 保留 LLM 原始值（含噪声）供审计，action 合法时恒为 None。"""
    a = _normalize_action(action, allowed)
    return {"action": a, "reason": _clean_reason(reason),
            "invalid_action": action if a is None else None}


def exec_decide_ocr(action, reason) -> dict:
    """LLM decide_ocr 决策结果归一化（action 严格 ocr/skip，非法→None 上游兜底）。"""
    return _decide_shell(action, reason, {"ocr", "skip"})


def exec_decide_extract_retry(action, reason) -> dict:
    """LLM decide_extract_retry 决策结果归一化（action 严格 retry/fail）。"""
    return _decide_shell(action, reason, {"retry", "fail"})
