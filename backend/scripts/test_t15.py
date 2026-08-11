# -*- coding: utf-8 -*-
"""T1.5 验收：extraction_status 兜底。

- 空 content / 非法 JSON 重试耗尽 → FAILED（std_json=None）
- 部分缺失必填 → INCOMPLETE（保留部分数据，不进假阳性洪水）
- 完整输出 → COMPLETE（回归）

全部 mock call_json，不依赖真实 DeepSeek，保证确定性。
"""
import json
from unittest.mock import patch

from app.llm.extractor import extract_contract
from app.common.constants import ExtractionStatus

TEXT = "设备采购合同，甲方北京智达科技有限公司，乙方深圳蓝海电子有限公司，金额900000元。" * 3

# ---- 1. 空 content：3 次重试后 FAILED ----
with patch("app.llm.extractor.call_json", return_value=("", "stop", None)):
    r = extract_contract(TEXT)
    assert r.status == ExtractionStatus.FAILED.value, r.status
    assert r.std_json is None
    assert r.error, "FAILED 应带 error_message"
print("[OK] 空 content → FAILED, std_json=None, error=%r" % r.error[:50])

# ---- 2. 非法 JSON：重试后仍非法 → FAILED ----
with patch("app.llm.extractor.call_json", return_value=("不是json{{", "stop", None)):
    r = extract_contract(TEXT)
    assert r.status == ExtractionStatus.FAILED.value, r.status
    assert r.std_json is None
print("[OK] 非法 JSON → FAILED")

# ---- 3. 部分缺失必填：保留部分数据 → INCOMPLETE ----
partial = {
    "contractType": "采购",
    "effectiveDate": "2024-03-15",
    "totalAmount": 900000,
    "currency": "CNY",
    # 缺 contractTitle / hasParty 必填
}
with patch("app.llm.extractor.call_json",
           return_value=(json.dumps(partial, ensure_ascii=False), "stop", None)):
    r = extract_contract(TEXT)
    assert r.status == ExtractionStatus.INCOMPLETE.value, r.status
    assert r.std_json is not None
    assert r.std_json.get("contractType") == "采购", "部分数据应保留"
print("[OK] 部分缺失 → INCOMPLETE, 保留 contractType=%s" % r.std_json["contractType"])

# ---- 4. 完整输出 → COMPLETE（回归） ----
complete = {
    "contractTitle": "设备采购合同", "contractType": "采购",
    "effectiveDate": "2024-03-15", "totalAmount": 900000, "currency": "CNY",
    "hasParty": [
        {"partyRole": "甲方", "partyName": "北京智达科技有限公司"},
        {"partyRole": "乙方", "partyName": "深圳蓝海电子有限公司"},
    ],
}
with patch("app.llm.extractor.call_json",
           return_value=(json.dumps(complete, ensure_ascii=False), "stop", None)):
    r = extract_contract(TEXT)
    assert r.status == ExtractionStatus.COMPLETE.value, r.status
    assert r.std_json["contractTitle"] == "设备采购合同"
print("[OK] 完整输出 → COMPLETE（回归通过）")

print("\nT1.5 单元部分通过 ✅")
