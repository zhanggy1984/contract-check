"""LangGraph 任务状态。Phase 2 会扩展校验相关字段。"""
from typing import Any, TypedDict


class TaskState(TypedDict, total=False):
    task_id: int
    parsed_text: str
    # 抽取阶段产物（T1.4）
    extraction_json: dict[str, Any] | None   # 标准文本 JSON
    extraction_status: str | None            # COMPLETE/INCOMPLETE/FAILED
    extraction_rdf: str | None               # N-Triples 快照
    segments: list[dict] | None              # 章节分段（Phase 3 语义规则用）
    det_outcomes: list | None                # 确定性校验结果（纯 dict，persist 节点落库）
    sem_outcomes: list | None                # 语义校验结果（纯 dict）
    extraction_usage: dict | None            # 抽取 LLM 聚合 token（persist 统一落库，B.4）
    sem_usage: dict | None                   # 语义校验 LLM 聚合 token（persist 统一落库，B.4）
    violations_count: int | None             # 校验产生的 violation 数（persist 返回，条件边分流用）
    sem_degraded: bool | None                # 语义评估整体降级标记（validate_semantic 返回；_should_wait 分流用）
    reviews: list | None                     # resume 回来的人工决策
    error_msg: str | None
