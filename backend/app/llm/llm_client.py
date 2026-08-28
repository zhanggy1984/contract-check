"""DeepSeek LLM 客户端封装（T1.3）。

- response_format=json_object（DeepSeek 要求 prompt 中含 "JSON" 字样）
- max_tokens=8192（DeepSeek 上限）
- 429 限流退避：ChatOpenAI 内置 max_retries + 指数退避
- 截断检测：finish_reason == "length" 时上层降级分段重抽
"""
import functools
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import LengthFinishReasonError, OpenAIError

from app.config import settings

MAX_TOKENS = 8192


class LLMError(RuntimeError):
    """LLM 调用失败（网络/超时/限流/服务端错误，SDK 重试耗尽）。调用方应降级而非裸抛。"""


@functools.lru_cache(maxsize=1)
def get_chat_model() -> ChatOpenAI:
    """DeepSeek chat 模型（json_object 模式）。

    惰性单例（四层分层·资源层收编）：extractor/semantic_evaluator 多次 call_json 共享同一
    ChatOpenAI 实例，省去每次构造的配置解析与连接开销（openai 客户端线程安全，可跨任务并发；
    换模型/端点需重启进程——环境变量在启动时解析）。
    """
    return ChatOpenAI(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        max_tokens=MAX_TOKENS,
        temperature=0.1,
        max_retries=settings.llm_max_retries,  # 429/5xx 指数退避（openai 客户端内置）
        timeout=settings.llm_timeout,
        model_kwargs={"response_format": {"type": "json_object"}},
    )


def call_json(system: str, user: str) -> tuple[str | None, str, dict[str, Any] | None]:
    """单次调用，返回 (content, finish_reason, usage)。

    finish_reason == "length" 表示输出被 max_tokens 截断，调用方须分段重抽。
    """
    llm = get_chat_model()
    try:
        resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    except LengthFinishReasonError as e:
        # T232：openai SDK 在 finish_reason="length" 时抛 LengthFinishReasonError 而非正常返回，
        # 异常自带完整响应对象。此处恢复 content/finish_reason/usage 按截断返回，让 extractor/
        # semantic_evaluator 的 finish_reason=="length" 降级分支真正生效（否则降级分支是死代码，
        # 超长合同抽取直接抛异常 FAILED，usage 也不聚合落库 → 平台判 no_usage）。
        comp = e.completion
        content = comp.choices[0].message.content if comp.choices else None
        if isinstance(content, list):  # 个别情况 content 为块列表，取文本
            content = "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
        finish_reason = str(comp.choices[0].finish_reason or "length") if comp.choices else "length"
        usage = comp.usage.model_dump() if comp.usage else None
        return (content if isinstance(content, str) else None, finish_reason, usage)
    except OpenAIError as e:
        # 网络/超时/限流/服务端错误：SDK 已按 max_retries 重试耗尽，此处包装为 LLMError
        # 交调用方（extractor/semantic_evaluator）降级，避免裸抛导致任务 FAILED 且无审计信息
        raise LLMError(f"LLM 调用失败: {e}") from e
    content = resp.content
    if isinstance(content, list):  # 个别情况 content 为块列表，取文本
        content = "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    meta = resp.response_metadata
    finish_reason = str(meta.get("finish_reason", "") or "")
    usage = meta.get("token_usage") or meta.get("usage") or None
    return (content if isinstance(content, str) else None, finish_reason, usage)
