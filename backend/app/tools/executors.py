"""能力 executor：把外部能力包装成统一形态（入参 kwargs、出参 dict 可 JSON 序列化）。

节点与（未来）LLM 都只经此层访问能力，不直接 import 能力模块，实现解耦。
run_sparql 的 graph/rule 允许原生对象传入（rdflib 图 / ORM 规则对象不可 JSON 化），
出参仍为 dict。决策 executor 是薄壳（只归一化参数），真实决策逻辑在 app/graph/decisions.py。
"""
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


def exec_ocr_pdf(pdf_path: str) -> dict:
    """扫描 PDF OCR → 文本。PaddleOCR 未安装/异常时抛 RuntimeError（上游决定降级）。"""
    # OCR 文本噪声更大（全角/零宽/多余空白），统一过输入清洗，与文本层提取一致
    return {"text": clean_text(OcrService.ocr_pdf(pdf_path))}


def exec_run_sparql(graph, rule) -> dict:
    """单条确定性规则对 RDF 图执行 → {passed, subjects, rule_snapshot}。"""
    r = SparqlExecutor().run(graph, rule)
    return {"passed": r.passed, "subjects": r.subjects, "rule_snapshot": r.rule_snapshot}


# ---- 决策 executor（薄壳：仅参数归一化，决策逻辑在 decisions.py）----

def exec_decide_ocr(action: str, reason: str) -> dict:
    """LLM decide_ocr 决策结果归一化。"""
    return {"action": action, "reason": reason or ""}


def exec_decide_extract_retry(action: str, reason: str) -> dict:
    """LLM decide_extract_retry 决策结果归一化。"""
    return {"action": action, "reason": reason or ""}
