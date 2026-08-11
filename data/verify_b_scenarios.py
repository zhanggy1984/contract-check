# -*- coding: utf-8 -*-
"""批量验证 b 系列演示场景：上传 → 轮询终态 → 汇总 violation（对照 README 预期）。"""
import json
import os
import time

import requests

# 忽略系统代理（本地直连 8001，不走 127.0.0.1:11500 代理）
s = requests.Session()
s.trust_env = False

BASE = "http://localhost:8001"
DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test-contracts")

# 场景 → (文件名, README 预期)
SCENARIOS = [
    ("b1", "b1_missing_date.pdf", "缺生效日期 → 必填 FAIL"),
    ("b2", "b2_negative_amount.pdf", "金额为负 → min FAIL"),
    ("b3", "b3_bad_type.pdf", "类型越界 → enum FAIL"),
    ("b4", "b4_missing_party_b.pdf", "缺乙方 → 人工规则 FAIL"),
    ("b5", "b5_termination_before_effective.pdf", "终止早于生效 → 人工规则 FAIL"),
    ("b6", "b6_missing_breach_clause.pdf", "缺违约条款 → 语义 FAIL"),
    ("b7", "b7_unbalanced_obligations.pdf", "权利义务不对等 → 语义 FAIL(高置信，evidence 命中原文)"),
    ("b8", "b8_service_contract.pdf", "技术标准规则 SKIPPED；但缺违约条款 → 语义 FAIL"),
]

out = {}
for name, fname, expect in SCENARIOS:
    with open(os.path.join(DIR, fname), "rb") as fh:
        r = s.post(f"{BASE}/api/files/upload",
                   files={"file": (fname, fh)})
    tid = r.json()["task_id"]
    status = None
    for _ in range(150):  # 最长 ~5 分钟
        t = s.get(f"{BASE}/api/tasks/{tid}").json()
        status = t["status"]
        if status in ("SUCCESS", "FAILED", "CANCELLED", "WAITING_REVIEW", "REVIEWING"):
            break
        time.sleep(2)
    res = s.get(f"{BASE}/api/tasks/{tid}/result").json()
    vs = [(v["rule_type"], v["severity"], v["confidence"],
           (v["message"] or "")[:36]) for v in (res.get("violations") or [])]
    skips = [(r["rule_id"], r["result"]) for r in (res.get("rule_results") or [])
             if r["result"] == "SKIPPED"]
    out[name] = {"task": tid, "status": status, "expect": expect,
                 "violations": vs, "skipped": skips,
                 "extraction_status": res.get("extraction_status")}

print(json.dumps(out, ensure_ascii=True, indent=1))
