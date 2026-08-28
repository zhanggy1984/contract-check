# -*- coding: utf-8 -*-
"""7.8 薄弱点①延伸：single_party 规则对签署形态边界（m 系列）的验证（真实 cc + LLM）。

m1-m5 合法签署形态（期望 SUCCESS + 0 违规）；m6-m7 签字/签章空白缺陷（期望检出）。
"""
import os
import sys
import time

import httpx

from verify_common import make_client

BASE = os.environ.get("CC_BASE", "http://127.0.0.1:8001")
DIR = r"D:/study/aiprojcet/contract-check/data/test-contracts"
CASES = [
    ("cc_gen_m1_rep_sign.pdf", "m1_rep_sign", "legal"),
    ("cc_gen_m2_legal_rep_only.pdf", "m2_legal_rep_only", "legal"),
    ("cc_gen_m3_sign_seal_word.pdf", "m3_sign_seal_word", "legal"),
    ("cc_gen_m4_seal_note.pdf", "m4_seal_note", "legal"),
    ("cc_gen_m5_esign_ca.pdf", "m5_esign_ca", "legal"),
    ("cc_gen_m6_sign_blank.pdf", "m6_sign_blank", "missing"),
    ("cc_gen_m7_signseal_blank.pdf", "m7_signseal_blank", "missing"),
]
TERMINAL = ("SUCCESS", "FAILED", "CANCELLED", "WAITING_REVIEW")


def main() -> int:
    results = []
    with make_client(BASE) as c:
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
            rr_fail = [x.get("rule_name") for x in (res.get("rule_results") or [])
                       if x.get("result") == "FAIL"
                       and any(k in (str(x.get("rule_name")) + " " + str(x.get("rule_id") or ""))
                              for k in ("single_party", "单方签署"))]
            results.append((tag, kind, status, sp, rr_fail))
            print(f"[{tag}] {fname} status={status} violations={len(vios)} single_party={len(sp)} rr_fail={rr_fail}")
            for v in sp:
                print(f"    - {v.get('severity')} [{v.get('rule_id')}] {v.get('message')}")

    fail = 0
    for tag, kind, status, sp, rr_fail in results:
        hit = bool(sp or rr_fail)
        if status not in ("SUCCESS", "WAITING_REVIEW"):
            ok, msg = False, f"{tag} 状态异常（{status}）"
        elif kind == "legal":
            ok = status == "SUCCESS" and not hit
            msg = f"{tag} 合法形态应 SUCCESS + 0 单方违规，实际 status={status} hit={hit}"
        else:
            ok = status == "WAITING_REVIEW" and hit
            msg = f"{tag} 缺陷应检出 single_party，实际 status={status} hit={hit}"
        print(("[PASS] " if ok else "[FAIL] ") + msg)
        fail += 0 if ok else 1
    print(f"===== 结果：{len(results) - fail} PASS / {fail} FAIL =====")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
