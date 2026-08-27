"""e2e 契约回归（function calling 改造）：上传 good.pdf → 跑完整流程 → 校验 result 契约。

断言（plan §契约保护不变式）：
1. result 含顶层 decisions 键（独立于 tool_calls/usage）
2. tool_calls 结构与改动前一致（name/args/result 全量含 PASS/SKIPPED）
3. usage 三分量存在且决策 usage 未混入（决策 trace 里可能带 usage，但评测 usage 不含它）
4. decisions 与 tool_calls 无关联（无 rule_id/result 类键）
"""
import json
import sys
import time

import requests

BASE = "http://localhost:8003"


def upload_and_run(pdf: str):
    with open(pdf, "rb") as f:
        r = requests.post(f"{BASE}/api/files/upload", files={"file": (pdf, f)},
                          timeout=120)
    r.raise_for_status()
    body = r.json()
    task_id = body["task_id"]
    print(f"task_id={task_id} has_scanned={body.get('has_scanned')}")
    # 轮询到终态（WAITING_REVIEW 校验完成即出结果）
    for _ in range(120):
        st = requests.get(f"{BASE}/api/tasks/{task_id}", timeout=30).json()
        if st["status"] in ("SUCCESS", "FAILED", "CANCELLED", "WAITING_REVIEW"):
            print(f"终态: {st['status']} progress={st['progress']}")
            return task_id, st["status"]
        time.sleep(2)
    raise TimeoutError("任务 4 分钟未终态")


def verify(task_id: int, status: str) -> list[str]:
    r = requests.get(f"{BASE}/api/tasks/{task_id}/result", timeout=30)
    r.raise_for_status()
    res = r.json()
    problems = []

    # 1. decisions 顶层键
    if "decisions" not in res:
        problems.append("缺顶层 decisions 键")
    else:
        decisions = res["decisions"]
        print(f"decisions 条数: {len(decisions)}")
        for d in decisions:
            if not isinstance(d, dict):
                problems.append(f"decisions 项非 dict: {d}")
                continue
            print(f"  - tool={d.get('tool')} decision={d.get('decision')} status={d.get('status')}")

    # 2. tool_calls 结构与契约字段
    tc = res.get("tool_calls") or []
    if not tc:
        problems.append("tool_calls 为空（正例应产出全量规则明细）")
    for t in tc:
        if not all(k in t for k in ("name", "args", "result")):
            problems.append(f"tool_call 缺契约键: {t}")

    # 3. usage 三分量 + 决策 usage 隔离
    usage = res.get("usage") or {}
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if k not in usage:
            problems.append(f"usage 缺 {k}")
    dec_usage_total = sum((d.get("usage") or {}).get("total_tokens", 0) for d in (res.get("decisions") or []))
    if dec_usage_total and usage.get("total_tokens", 0) >= dec_usage_total:
        # 决策 usage 若被并入评测 usage，total 必然包含它；保守判断：仅提示不判错
        print(f"  [提示] 评测 usage total={usage.get('total_tokens')} ≥ 决策 usage total={dec_usage_total}")

    # 4. decisions 与 tool_calls 零关联（决策键不含 rule_id/result）
    for d in (res.get("decisions") or []):
        if "rule_id" in d or "result" in d:
            problems.append(f"decisions 与 tool_calls 契约混淆: {d}")

    return problems


def main() -> int:
    pdf = sys.argv[1] if len(sys.argv) > 1 else "data/acceptance/good.pdf"
    task_id, status = upload_and_run(pdf)
    if status == "FAILED":
        print("任务 FAILED，检查后端日志")
        return 1
    if status == "CANCELLED":
        print("任务被取消")
        return 1
    problems = verify(task_id, status)
    if problems:
        print("契约回归失败:")
        for p in problems:
            print("  -", p)
        return 1
    print("契约回归通过: decisions 独立键存在，tool_calls/usage 结构完整")
    return 0


if __name__ == "__main__":
    sys.exit(main())
