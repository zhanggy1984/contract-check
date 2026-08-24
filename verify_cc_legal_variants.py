# -*- coding: utf-8 -*-
"""7.8 薄弱点①延伸：single_party 规则对合法签署形态的误报验证（真实 cc + 真实 LLM）。

l1-l6 为合法签署形态（期望 SUCCESS + 0 违规，即不误报）；
l7 为电子签章形态下的缺方对照（期望检出 single_party，验证电子签章不因格式特殊而漏检）。
"""
import sys
import time

import httpx

BASE = "http://127.0.0.1:8001"
DIR = r"D:/study/aiprojcet/contract-check/data/test-contracts"
CASES = [
    ("cc_gen_l1_esign.pdf", "l1_esign", "legal"),
    ("cc_gen_l2_sign_only.pdf", "l2_sign_only", "legal"),
    ("cc_gen_l3_seal_sign.pdf", "l3_seal_sign", "legal"),
    ("cc_gen_l4_no_colon.pdf", "l4_no_colon", "legal"),
    ("cc_gen_l5_gongzhang.pdf", "l5_gongzhang", "legal"),
    ("cc_gen_l6_mixed_legal.pdf", "l6_mixed_legal", "legal"),
    ("cc_gen_l7_esign_missing.pdf", "l7_esign_missing", "missing"),
]
TERMINAL = ("SUCCESS", "FAILED", "CANCELLED", "WAITING_REVIEW")


def main() -> int:
    results = []
    with httpx.Client(timeout=120, trust_env=False) as c:
        for fname, tag, kind in CASES:
            with open(DIR + "/" + fname, "rb") as f:
                r = c.post(BASE + "/api/files/upload", files={"file": (fname, f, "application/pdf")})
            if r.status_code != 200:
                print(f"[{tag}] upload 失败: {r.status_code} {r.text[:200]}")
                results.append((tag, kind, "UPLOAD_FAIL", [], []))
                continue
            tid = r.json()["task_id"]
            deadline = time.time() + 240
            status = "PENDING"
            while time.time() < deadline:
                status = c.get(BASE + f"/api/tasks/{tid}").json()["status"]
                if status in TERMINAL:
                    break
                time.sleep(3)
            res = c.get(BASE + f"/api/tasks/{tid}/result").json()
            vios = res.get("violations") or []
            sp = [v for v in vios if any(k in (str(v.get("rule_id")) + " " + str(v.get("message") or ""))
                                         for k in ("single_party", "单方签署", "单方"))]
            results.append((tag, kind, status, vios, sp))
            print(f"[{tag}] {fname} status={status} violations={len(vios)} single_party={len(sp)}")
            for v in sp:
                print(f"    - {v.get('severity')} [{v.get('rule_id')}] {v.get('message')}")

    fail = 0
    for tag, kind, status, vios, sp in results:
        if status not in ("SUCCESS", "WAITING_REVIEW"):
            ok, msg = False, f"{tag} 状态异常（{status}）"
        elif kind == "legal":
            ok = status == "SUCCESS" and not sp
            msg = f"{tag} 合法形态应 SUCCESS + 0 单方违规，实际 status={status} sp={len(sp)}"
        else:
            ok = bool(sp) and status == "WAITING_REVIEW"
            msg = f"{tag} 缺方应检出 single_party，实际 status={status} sp={len(sp)}"
        print(("[PASS] " if ok else "[FAIL] ") + msg)
        fail += 0 if ok else 1
    print(f"===== 结果：{len(results) - fail} PASS / {fail} FAIL =====")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
