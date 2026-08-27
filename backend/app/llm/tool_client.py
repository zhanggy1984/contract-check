"""带 tools 的 LLM 决策调用（决策工具专用通道）。

与 call_json 的关键差异：
- 决策走 bind_tools，OpenAI/DeepSeek 不接受 response_format=json_object 与 tools 同传
  （参数冲突），故 _decision_model() 不带 response_format。
- 返回结构化 ToolResponse：content + tool_calls + finish_reason + usage。
- 仅单轮调用；多轮循环（MAX_TOOL_ROUNDS）由 decisions.py 编排。
- finish_reason=="length" 时 openai SDK 抛 LengthFinishReasonError，从异常自带
  completion 恢复 ToolResponse（与 llm_client.call_json 同一恢复策略）。
"""
import json
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import LengthFinishReasonError

from app.config import settings


@dataclass(frozen=True)
class ToolCall:
    """LLM 决定调用的工具。arguments 已解析为 dict。id 供多轮回传 tool 消息。"""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str = ""


@dataclass(frozen=True)
class ToolResponse:
    """单轮工具调用结果。content 可为 None（纯 tool 决策无文本）。"""

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict[str, Any] | None = None


def _decision_model() -> ChatOpenAI:
    """决策模型：无 response_format（与 tools 冲突），token/timeout 更小（决策话术短）。"""
    return ChatOpenAI(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        max_tokens=settings.tool_decision_max_tokens,
        temperature=0.1,
        max_retries=3,                 # 429/5xx 指数退避
        timeout=settings.tool_decision_timeout,
    )


def _coerce_content(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # 个别情况 content 为块列表，取文本
        return "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return None


def _parse_aimessage(resp) -> ToolResponse:
    """langchain AIMessage → ToolResponse。tool_calls 为已规范化的 dict 列表。"""
    content = _coerce_content(resp.content)
    tool_calls = [
        ToolCall(name=tc["name"], arguments=tc.get("args") or {}, id=tc.get("id") or "")
        for tc in (resp.tool_calls or [])
    ]
    meta = resp.response_metadata or {}
    return ToolResponse(
        content=content,
        tool_calls=tool_calls,
        finish_reason=str(meta.get("finish_reason", "") or ""),
        usage=meta.get("token_usage") or meta.get("usage"),
    )


def _parse_openai_completion(comp) -> ToolResponse:
    """LengthFinishReasonError 恢复：从异常自带的 ChatCompletion 恢复 ToolResponse。"""
    choice = comp.choices[0] if comp.choices else None
    msg = choice.message if choice else None
    tool_calls: list[ToolCall] = []
    for tc in (msg.tool_calls if msg else None) or []:
        try:
            arguments = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            arguments = {}
        tool_calls.append(ToolCall(name=tc.function.name, arguments=arguments, id=tc.id))
    return ToolResponse(
        content=_coerce_content(msg.content if msg else None),
        tool_calls=tool_calls,
        finish_reason=str(choice.finish_reason or "length") if choice else "length",
        usage=comp.usage.model_dump() if comp.usage else None,
    )


def call_with_tools(system: str, user: str, tools: list[dict]) -> ToolResponse:
    """单轮带工具决策调用（tool_choice=auto，不强制）。

    tools 为 OpenAI function schema 数组（registry.schemas() 产出）。
    异常（网络/限流/解析）向上抛，由 decisions.py 兜底记录 fallback_error。
    """
    llm = _decision_model()
    try:
        resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)], tools=tools)
    except LengthFinishReasonError as e:
        return _parse_openai_completion(e.completion)
    return _parse_aimessage(resp)
