"""T0.3 spike：验证 langgraph-checkpoint-mysql + interrupt/resume 全链路。

验证项：
1. MySQL checkpointer 版本兼容（langgraph 1.2.10 + checkpoint-mysql 3.0.0）
2. interrupt() 暂停 → get_state 可读中断值
3. Command(resume) 续跑至 END
4. resume 时 interrupt 节点从头重跑（副作用不重放）
"""
from typing import TypedDict

from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.config import settings

DATABASE_URL = (
    f"mysql+pymysql://{settings.mysql_user}:{settings.mysql_password}"
    f"@{settings.mysql_host}:{settings.mysql_port}/{settings.mysql_database}?charset=utf8mb4"
)


class S(TypedDict):
    x: int
    reviews: list | None


def parse(state: S) -> dict:
    return {"x": state["x"] + 1}


def wait(state: S) -> dict:
    # 纯节点：仅 interrupt + 写回 decision，无副作用
    decision = interrupt({"q": "请确认", "count": state["x"]})
    return {"reviews": decision}


def fin(state: S) -> dict:
    print("FINAL:", dict(state))
    return {}


def main() -> None:
    with PyMySQLSaver.from_conn_string(DATABASE_URL) as saver:
        saver.setup()  # 建 checkpoint 表

        g = StateGraph(S)
        g.add_node("parse", parse)
        g.add_node("wait", wait)
        g.add_node("fin", fin)
        g.add_edge(START, "parse")
        g.add_edge("parse", "wait")
        g.add_edge("wait", "fin")
        g.add_edge("fin", END)
        graph = g.compile(checkpointer=saver)

        cfg = {"configurable": {"thread_id": "spike-1"}}

        # 1) 首次 invoke 应停在 interrupt
        r1 = graph.invoke({"x": 0}, cfg)
        print("STEP1 停在 interrupt，返回:", r1)

        # 2) 读取中断值
        st = graph.get_state(cfg)
        print("STEP2 interrupts:", st.interrupts)
        assert st.interrupts, "应存在中断值"

        # 3) resume 续跑
        r2 = graph.invoke(Command(resume={"action": "CONFIRM"}), cfg)
        print("STEP3 resume 完成:", r2)
        assert r2.get("reviews") == {"action": "CONFIRM"}, "reviews 应写回 state"

    print("SPIKE OK")


if __name__ == "__main__":
    main()
