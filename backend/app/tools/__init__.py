"""工具注册表模块。

进程级单例 registry：节点 `from app.tools import registry` 按名取用。
schema → executor 自动装配：executor 名约定为 exec_{tool_name}（见 executors.py）。
测试可 patch.object(registry, "execute") 或替换单例。
"""
from app.tools import executors
from app.tools.registry import Tool, ToolRegistry
from app.tools.schemas import ALL_TOOL_SCHEMAS


def _executor_for(name: str):
    """按工具名取 executor：exec_{name}。缺名抛 AttributeError（装配期快速失败）。"""
    return getattr(executors, f"exec_{name}")


registry = ToolRegistry([
    Tool(name=s["function"]["name"], schema=s, executor=_executor_for(s["function"]["name"]))
    for s in ALL_TOOL_SCHEMAS
])

__all__ = ["registry", "Tool", "ToolRegistry"]
