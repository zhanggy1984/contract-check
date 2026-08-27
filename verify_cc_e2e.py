"""B.4 contract-check 契约改造 e2e 验证（评测 §5.2 同步 JSON 变体）。

宿主运行（backend 容器映射宿主 8001）：
    backend/.venv/Scripts/python.exe verify_cc_e2e.py [合同路径]

验证内容（对已验收任务取 result，决策 #41 不 resume）：
1. 多步流程：upload → 轮询到 WAITING_REVIEW/终态 → result
2. answer：非空校验摘要文本（§5.2 必选）
3. usage：非空、prompt/completion/total_tokens 三分量非负（抽取+语义聚合 LLM token）
4. timing：start_ts/end_ts 存在，first_token_ts=None（同步接口不测首字，决策 #40）
5. tool_calls：规则命中明细全量（含 PASS/SKIPPED/FAIL），name/args/result 结构齐全（D1）
6. 评测后清理任务（决策 #41/#58，不产生假 review 记录）

选样：b1_missing_date.pdf（缺生效日 → 必填 FAIL → WAITING_REVIEW），覆盖评测取数路径。
"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows GBK 控制台编不了 ✓

BASE = os.environ.get("CC_BASE", "http://localhost:8001")
CONTRACT = sys.argv[1] if len(sys.argv) > 1 else "data/test-contracts/b1_missing_date.pdf"

_passed: list[str] = []
_failed: list[str] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        _passed.append(name)
        print(f"  ✓ {name}")
    else:
        _failed.append(name)
        print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


def _upload(client: httpx.Client, path: str) -> int:
    fname = path.rsplit("/", 1)[-1]
    with open(path, "rb") as f:
        r = client.post(f"{BASE}/api/files/upload",
                        files={"file": (fname, f, "application/pdf")})
    r.raise_for_status()
    data = r.json()
    print(f"  上传 → task_id={data['task_id']} has_scanned={data.get('has_scanned')}")
    return data["task_id"]


def _wait_task(client: httpx.Client, task_id: int, timeout: int = 600) -> dict | None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(f"{BASE}/api/tasks/{task_id}")
        r.raise_for_status()
        t = r.json()
        if t["status"] in ("WAITING_REVIEW", "SUCCESS", "FAILED", "CANCELLED"):
            return t
        time.sleep(2)
    return None


def main() -> None:
    # trust_env=False：宿主开了系统代理（Clash 127.0.0.1:15490），httpx 默认读注册表代理
    # 会把 localhost 请求也送代理 → 502；评测脚本只连内网，必须直连
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0), trust_env=False) as c:
        tid = _upload(c, CONTRACT)

        print("\n[1] 轮询任务状态（评测取数点）")
        task = _wait_task(c, tid)
        if task is None:
            print("  轮询超时")
            sys.exit(1)
        print(f"  取数点状态: {task['status']} progress={task.get('progress')}")
        _check("取数点状态合法", task["status"] in ("WAITING_REVIEW", "SUCCESS", "FAILED"),
               task["status"])

        print("\n[2] result 契约字段（§5.2 同步 JSON 变体）")
        r = c.get(f"{BASE}/api/tasks/{tid}/result")
        r.raise_for_status()
        data = r.json()
        answer = data.get("answer")
        usage = data.get("usage")
        timing = data.get("timing")
        tools = data.get("tool_calls")

        _check("answer 非空校验摘要（§5.2 必选）",
               isinstance(answer, str) and bool(answer), f"answer={answer}")
        if isinstance(answer, str) and answer:
            print(f"  answer={answer[:100]}")
        _check("usage 存在且三分量非负",
               usage is not None and all(isinstance(usage.get(k), int) and usage.get(k, 0) >= 0
                                         for k in ("prompt_tokens", "completion_tokens", "total_tokens")),
               f"usage={usage}")
        if usage:
            print(f"  usage={usage}")
        _check("timing start/end 存在", bool(timing and timing.get("start_ts") and timing.get("end_ts")),
               f"timing={timing}")
        _check("first_token_ts 为 null（决策#40 同步不测首字）",
               bool(timing) and timing.get("first_token_ts") is None, f"timing={timing}")
        _check("tool_calls 非空", isinstance(tools, list) and len(tools) > 0,
               f"tool_calls={tools}")
        if tools:
            _check("tool_calls 结构齐全（name/args/result）",
                   all(t.get("name") and isinstance(t.get("args"), dict)
                       and isinstance(t.get("result"), dict) for t in tools))
            kinds = {t["result"]["result"] for t in tools if isinstance(t.get("result"), dict)}
            _check("tool_calls 覆盖全量规则结果（含 PASS/SKIPPED）",
                   bool(kinds & {"PASS", "SKIPPED"}), f"结果分布={kinds}")
            print(f"  规则数={len(tools)} 结果分布={kinds}")
            print(f"  样例: {json.dumps(tools[0], ensure_ascii=False)[:200]}")

        print("\n[3] 评测后清理任务（决策 #41：不 resume、不留假 review）")
        r = c.delete(f"{BASE}/api/tasks/{tid}")
        _check("delete_task 成功", r.status_code == 200, f"HTTP {r.status_code} {r.text[:100]}")

    print(f"\n========== 结果: {len(_passed)} 通过 / {len(_failed)} 失败 ==========")
    if _failed:
        print(f"失败项: {_failed}")
        sys.exit(1)


if __name__ == "__main__":
    main()
