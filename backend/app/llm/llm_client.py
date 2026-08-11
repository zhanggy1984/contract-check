"""DeepSeek LLM 客户端封装（T1.3）。

- response_format=json_object（DeepSeek 要求 prompt 中含 "JSON" 字样）
- max_tokens=8192（DeepSeek 上限）
- 429 限流退避：ChatOpenAI 内置 max_retries + 指数退避
- 截断检测：finish_reason == "length" 时上层降级分段重抽
"""
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import settings

MAX_TOKENS = 8192


def get_chat_model() -> ChatOpenAI:
    """DeepSeek chat 模型（json_object 模式，幂等创建不缓存以便调整参数）。"""
    return ChatOpenAI(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        max_tokens=MAX_TOKENS,
        temperature=0.1,
        max_retries=3,                 # 429/5xx 指数退避（openai 客户端内置）
        timeout=120,
        model_kwargs={"response_format": {"type": "json_object"}},
    )


def call_json(system: str, user: str) -> tuple[str | None, str, dict[str, Any] | None]:
    """单次调用，返回 (content, finish_reason, usage)。

    finish_reason == "length" 表示输出被 max_tokens 截断，调用方须分段重抽。
    """
    llm = get_chat_model()
    resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    content = resp.content
    if isinstance(content, list):  # 个别情况 content 为块列表，取文本
        content = "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    meta = resp.response_metadata
    finish_reason = str(meta.get("finish_reason", "") or "")
    usage = meta.get("token_usage") or meta.get("usage") or None
    return (content if isinstance(content, str) else None, finish_reason, usage)
