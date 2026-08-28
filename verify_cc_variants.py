# -*- coding: utf-8 -*-
"""7.8 薄弱点①排查：single_party 规则泛化性验证（真实 cc 栈 + 真实 LLM）。

5 个变体 PDF（正文统一、仅签署区形态不同）直接 POST /api/files/upload → 轮询 → result。
断言 single_party 规则对 3 种签署缺陷形态全部检出、good 无误报：

  cc_gen_good.pdf        双方均实际签署盖章      → 期望 0 违规（无误报）
  cc_gen_v0_underline    乙方下划线空白占位(b4)  → 期望检出 single_party
  cc_gen_v1_empty        乙方盖章行冒号后为空    → 期望检出
  cc_gen_v2_missing      缺整个乙方签署区        → 期望检出
  cc_gen_v3_mixed        甲方既有署名又有空白占位 → 期望检出

宿主运行：BASE=http://127.0.0.1:8001（cc backend 宿主端口）；trust_env=False 防撞系统代理。
"""
import os
import sys
import time

import httpx

from verify_common import make_client

BASE = os.environ.get("CC_BASE", "http://127.0.0.1:8001")
DIR = r"D:\study\aiprojcet\contract-check\data\test-contracts"
FILES = [
    ("cc_gen_good.pdf", "good"),         # 期望 0 违规
    ("cc_gen_v0_underline.pdf", "v0"),   # 期望检出
    ("cc_gen_v1_empty.pdf", "v1"),       # 期望检出
    ("cc_gen_v2_missing.pdf", "v2"),     # 期望检出
    ("cc_gen_v3_mixed.pdf", "v3"),       # 期望检出
]
SP_KEY = ("single_party", "单方签署", "单方")


def main() -> int:
    results = []
    with make_client(BASE) as c:
        for fname, tag in FILES:
            with open(f"{DIR}\\{fname}", "rb") as f:
                r = c.post(f"{BASE}/api/files/upload",
                           files={"file": (fname, f, "application/pdf")})
            if r.status_code != 200:
                print(f"[{tag}] upload 失败: {r.status_code} {r.text[:200]}")
                results.append((tag, fname, "UPLOAD_FAIL", [], []))
                continue
            task_id = r.json()["task_id"]

            deadline = time.time() + 300
            status = "PENDING"
            while time.time() < deadline:
                try:
                    st = c.get(f"{BASE}/api/tasks/{task_id}").json()
                    status = st["status"]
                except Exception:
                    status = "POLL_ERR"
                if status in ("SUCCESS", "FAILED", "CANCELLED", "WAITING_REVIEW"):
                    break
                time.sleep(3)

            res = c.get(f"{BASE}/api/tasks/{task_id}/result").json()
            vios = res.get("violations") or []
            rule_results = res.get("rule_results") or []
            # single_party 规则命中判定：violations(rule_id/message) + rule_results(rule_name/result)
            sp_vio = [v for v in vios
                      if any(k in (str(v.get("rule_id") or "") + " " + str(v.get("message") or ""))
                             for k in SP_KEY)]
            sp_rule_fail = [x for x in rule_results
                            if x.get("result") == "FAIL"
                            and any(k in (str(x.get("rule_name") or "") + " " + str(x.get("rule_id") or ""))
                                   for k in SP_KEY)]
            results.append((tag, fname, status, sp_vio, sp_rule_fail, rule_results))
            print(f"[{tag}] {fname} status={status} task={task_id} "
                  f"violations={len(vios)} single_party违规={len(sp_vio)} "
                  f"single_party规则FAIL={len(sp_rule_fail)}")
            for v in vios:
                print(f"    - {v.get('severity')} [{v.get('rule_id')}] {v.get('message')}")
            for x in rule_results:
                if x.get("result") != "PASS":
                    print(f"    规则[{x.get('rule_name')}] result={x.get('result')} "
                          f"sev={x.get('severity')} msg={x.get('message')}")

    # 断言：good 期望 SUCCESS+0 违规；缺陷变体期望检出（WAITING_REVIEW=HIGH 违规待人工确认，
    # 是检出后的取数终态，正合评测决策 #41）
    fail = 0
    for tag, fname, status, sp_vio, sp_rule_fail, _ in results:
        if tag == "good":
            ok = status == "SUCCESS" and not sp_vio and not sp_rule_fail
            msg = f"good 应 SUCCESS + 0 单方违规，实际 status={status} sp={len(sp_vio)}/{len(sp_rule_fail)}"
        else:
            ok = status in ("WAITING_REVIEW", "FAILED") and bool(sp_vio or sp_rule_fail)
            msg = f"{tag} 应检出 single_party，实际 status={status} sp={len(sp_vio)}/{len(sp_rule_fail)}"
        print(("[PASS] " if ok else "[FAIL] ") + msg)
        fail += 0 if ok else 1
    print(f"===== 结果：{len(results) - fail} PASS / {fail} FAIL =====")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
