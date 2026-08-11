"""确定性规则 SPARQL 执行器（T2.2）。

- 对每个规则执行 ASK 判反例（闭合世界语义）
- ASK=true → 将 ASK 转为 SELECT DISTINCT ?s 定位全部反例个体
- 约定（solution.md §7.1）：每条规则至多产出一条 FAIL，多实例反例合并进
  message（列出全部 ?s），与 (task_id, rule_id) 唯一键一致，不丢反例。
- 约定：反例定位变量统一为 ?s（生成器与人工 .rq 均遵守）。
"""
from dataclasses import dataclass, field

import rdflib
from rdflib.plugins.sparql import prepareQuery


@dataclass
class DeterministicResult:
    passed: bool
    subjects: list[str] = field(default_factory=list)  # 反例个体 IRI
    rule_snapshot: str = ""


def _ask_to_select(expression: str) -> str:
    """ASK {...} → SELECT DISTINCT ?s WHERE {...}。"""
    expr = expression.strip()
    if not expr.upper().startswith("ASK"):
        raise ValueError(f"规则必须是 ASK 查询: {expr[:80]}")
    return "SELECT DISTINCT ?s WHERE " + expr[3:].lstrip()


def build_graph(rdf_nt: str | None) -> rdflib.Graph | None:
    """N-Triples 快照 → rdflib Graph；空返回 None（由上层判 SKIPPED）。"""
    if not rdf_nt or not rdf_nt.strip():
        return None
    g = rdflib.Graph()
    g.parse(data=rdf_nt, format="nt")
    return g


def _expr(rule) -> str:
    """rule 兼容 CheckRule 对象或规则 dict。"""
    return rule["expression"] if isinstance(rule, dict) else rule.expression


class SparqlExecutor:
    """单规则执行器（无状态，可并发）。"""

    def run(self, graph: rdflib.Graph | None, rule) -> DeterministicResult:
        """graph 为空 → passed=False + 空反例（上层判 SKIPPED 而非 FAIL）。"""
        expression = _expr(rule)
        if graph is None:
            return DeterministicResult(False, [], expression)
        res = graph.query(prepareQuery(expression))
        if not res.askAnswer:
            return DeterministicResult(True, [], expression)
        sel = prepareQuery(_ask_to_select(expression))
        subjects = sorted({str(row[0]) for row in graph.query(sel)})
        return DeterministicResult(False, subjects, expression)
