"""Phase 3 E2E：上传 → 轮询 WAITING_REVIEW → 展示语义 violation → 全确认 resume → SUCCESS。

用法：python scripts/e2e_phase3.py <pdf1> <pdf2> ...
"""
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000/api"


def upload(client: httpx.Client, path: str) -> int:
    with open(path, "rb") as f:
        r = client.post(f"{BASE}/files/upload",
                        files={"file": (path.rsplit("/", 1)[-1], f, "application/pdf")})
    r.raise_for_status()
    return r.json()["task_id"]


def wait_status(client: httpx.Client, tid: int, targets: set, timeout: int = 300) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        t = client.get(f"{BASE}/tasks/{tid}").json()
        if t["status"] in targets:
            return t
        time.sleep(2)
    raise TimeoutError(f"task {tid} 未到达 {targets}")


def run(client: httpx.Client, path: str) -> None:
    name = path.rsplit("/", 1)[-1]
    tid = upload(client, path)
    print(f"\n===== {name} task_id={tid} =====")
    t = wait_status(client, tid, {"WAITING_REVIEW", "SUCCESS", "FAILED", "CANCELLED"})
    print(f"status={t['status']} extraction={t.get('extraction_status')}")
    if t["status"] != "WAITING_REVIEW":
        if t["status"] == "FAILED":
            print("FAILED message:", t.get("message"))
        return

    res = client.get(f"{BASE}/tasks/{tid}/result").json()
    print(f"violations ({len(res['violations'])}):")
    for v in res["violations"]:
        print(f"  id={v['id']} {v['rule_type']} {v['severity']} conf={v.get('confidence')} "
              f"seg={v.get('segment_ref')}\n    evidence={v.get('evidence_text')!r}\n    msg={v.get('message')}")

    # 全部确认 → resume
    reviews = [{"violation_id": v["id"], "action": "CONFIRMED"} for v in res["violations"]]
    r = client.post(f"{BASE}/tasks/{tid}/resume", json={"reviews": reviews})
    r.raise_for_status()
    t = wait_status(client, tid, {"SUCCESS", "FAILED"})
    print(f"final status={t['status']}")

    res2 = client.get(f"{BASE}/tasks/{tid}/result").json()
    print("semantic rule results:")
    for r in res2["rule_results"]:
        if r["rule_type"] == "SEMANTIC":
            print(f"  {r['result']} conf={r.get('confidence')} seg={r.get('segment_ref')} msg={r.get('message')}")


def main() -> None:
    paths = sys.argv[1:]
    with httpx.Client(timeout=60) as client:
        for p in paths:
            try:
                run(client, p)
            except Exception as e:
                print(f"[{p}] ERROR: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
