"""决策痕迹记录器：决策痕迹的唯一构造点（契约隔离不变式）。

决策痕迹（LLM 决策 + 确定性短路 + 兜底）独立于评测契约：
- 决策 usage 只进 trace，禁入 state usage（token_usage_json 聚合不受影响）
- 决策痕迹从不写 RuleCheckResult（result.tool_calls 契约零漂移）
- 落库走节点侧 _persist_decisions 即时合并（check_task.decision_json），
  不经 checkpoint——抽取失败等 FAILED 分支任务失败也能保痕，resume 不重跑决策点不重复
"""
from datetime import datetime


def make_trace(node: str, tool: str, decision: str, status: str,
               reason: str, signals: dict | None = None, usage: dict | None = None) -> dict:
    """构造单条决策痕迹。status ∈ llm / short_circuit / fallback_error / disabled。"""
    return {
        "node": node,
        "tool": tool,
        "decision": decision,          # LLM 建议动作或确定性结论（ocr/skip/retry/fail）
        "status": status,
        "reason": reason or "",
        "signals": dict(signals or {}),   # 决策输入信号（可审计）
        "usage": dict(usage) if usage else None,  # 决策 LLM token，禁入评测契约 usage
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
