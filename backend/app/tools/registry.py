"""工具注册表：schema → executor 映射，节点按名取用（解耦核心）。

- 缺名 KeyError 快速失败（防拼写漂移，杜绝静默降级）
- schemas(names) 返回 OpenAI tools 数组，供 LLM bind_tools（决策工具）
- execute(name, **kwargs) 异常向上抛，由调用节点决定降级策略
"""
from dataclasses import dataclass
from typing import Any, Callable, Sequence


@dataclass(frozen=True)
class Tool:
    """一个已注册工具：唯一名 + OpenAI schema + 可调用 executor。"""

    name: str
    schema: dict
    executor: Callable[..., Any]


class ToolRegistry:
    """进程内工具表。线程安全前提：注册后只读，不改表结构。"""

    def __init__(self, tools: Sequence[Tool]) -> None:
        self._tools: dict[str, Tool] = {t.name: t for t in tools}

    def names(self) -> list[str]:
        return list(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> Tool:
        """按名取工具；缺名 KeyError（快速失败）。"""
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"工具未注册: {name}") from None

    def schemas(self, names: Sequence[str] | None = None) -> list[dict]:
        """OpenAI tools 数组（含 type/function 包装）。names 缺省返回全部；空序列返回 []。"""
        if names is None:
            return [t.schema for t in self._tools.values()]
        return [self.get(n).schema for n in names]

    def execute(self, name: str, **kwargs: Any) -> Any:
        """按名执行工具；异常向上抛，调用节点决定降级。"""
        return self.get(name).executor(**kwargs)
