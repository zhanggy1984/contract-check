"""构图与编译。checkpointer 生命周期由调用方（service）用 with 管理。"""
from langgraph.graph import END, START, StateGraph

from app.config import settings
from app.graph.nodes import (
    apply_reviews, await_review, extract_node, finalize, mark_waiting, parse_node,
    persist_node, validate_deterministic, validate_semantic,
)
from app.graph.state import TaskState

DATABASE_URL = (
    f"mysql+pymysql://{settings.mysql_user}:{settings.mysql_password}"
    f"@{settings.mysql_host}:{settings.mysql_port}/{settings.mysql_database}?charset=utf8mb4"
)


def _should_wait(state: TaskState) -> str:
    """persist 后分流：有 violation 或抽取 INCOMPLETE → 人工审核；否则零 violation 自动 SUCCESS。

    - violation > 0：进 mark_waiting → interrupt（人工确认/误报）
    - extraction INCOMPLETE：即使零 violation 也进人工（D2 安全阀——INCOMPLETE 时
      enum/min/pattern 约束规则被 SKIPPED，required 恰好 PASS 时自动通过会掩盖"抽取没抽全"）
    - 其余（COMPLETE + 零 violation）：直接 finalize（SUCCESS），跳过 interrupt
    """
    if (state.get("violations_count") or 0) > 0:
        return "await"
    if state.get("extraction_status") == "INCOMPLETE":
        return "await"
    return "done"


def build_graph() -> StateGraph:
    g = StateGraph(TaskState)
    g.add_node("parse", parse_node)
    g.add_node("extract", extract_node)
    g.add_node("validate", validate_deterministic)
    g.add_node("validate_semantic", validate_semantic)
    g.add_node("persist", persist_node)
    g.add_node("mark_waiting", mark_waiting)
    g.add_node("await_human_review", await_review)
    g.add_node("apply_reviews", apply_reviews)
    g.add_node("finalize", finalize)
    g.add_edge(START, "parse")
    g.add_edge("parse", "extract")
    g.add_edge("extract", "validate")
    g.add_edge("validate", "validate_semantic")
    g.add_edge("validate_semantic", "persist")
    # 条件分流：有 violation / INCOMPLETE → 人工；零 violation COMPLETE → 自动 SUCCESS
    g.add_conditional_edges("persist", _should_wait,
                            {"await": "mark_waiting", "done": "finalize"})
    g.add_edge("mark_waiting", "await_human_review")
    g.add_edge("await_human_review", "apply_reviews")
    g.add_edge("apply_reviews", "finalize")
    g.add_edge("finalize", END)
    return g
