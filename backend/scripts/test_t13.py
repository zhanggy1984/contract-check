# -*- coding: utf-8 -*-
"""T1.3 验收：动态模型 + 重试反馈 + 截断降级分段重抽 + 真实 DeepSeek 抽取。"""
import json

from pydantic import ValidationError

from app.common.constants import ExtractionStatus
from app.llm.extractor import (
    build_model, extract_contract, _split_text, _merge_contracts, _feedback_of,
)
from app.ontology.loader import load_ontology
from app.ontology.schema_mapper import build_extraction_schema

schema = build_extraction_schema(load_ontology())

# ---- 1. 动态模型：合法 JSON 通过 ----
model = build_model(schema, strict=True)
good = {
    "contractTitle": "设备采购合同", "contractType": "采购",
    "effectiveDate": "2024-03-15", "totalAmount": 900000, "currency": "CNY",
    "hasParty": [
        {"partyRole": "甲方", "partyName": "北京智达科技有限公司",
         "unifiedSocialCreditCode": "91110108MA01ABCDE2"},
        {"partyRole": "乙方", "partyName": "深圳蓝海电子有限公司"},
    ],
    "hasItem": [{"itemName": "服务器主机", "quantity": 10, "unitPrice": 80000}],
    "hasClause": [{"clauseType": "违约责任", "clauseText": "如乙方未能按期交货，每逾期一日按合同总金额的0.5%支付违约金。"}],
}
try:
    valid = model.model_validate(good)
    assert valid.contractType == "采购"
    assert valid.totalAmount == 900000
    assert valid.hasParty[1].partyName == "深圳蓝海电子有限公司"
    print("[OK] 严格模型校验通过（含嵌套数组）")
except ValidationError as e:
    raise AssertionError(f"模型校验失败: {e}")

# ---- 2. 必填缺失 / 枚举越界 → 校验失败反馈 ----
bad = dict(good)
bad["contractType"] = "采购类型"          # 枚举越界
try:
    model.model_validate(bad)
    raise AssertionError("枚举越界应失败")
except ValidationError as e:
    fb = _feedback_of(e.errors())
    assert "contractType" in fb and "literal" in fb
    print(f"[OK] 枚举越界反馈: {fb[:60]}...")

bad2 = {k: v for k, v in good.items() if k != "contractTitle"}
try:
    model.model_validate(bad2)
    raise AssertionError("缺必填应失败")
except ValidationError as e:
    fb = _feedback_of(e.errors())
    assert "contractTitle" in fb
    print(f"[OK] 缺必填反馈: {fb[:60]}...")

# ---- 3. 数值范围 minimum 校验 ----
bad3 = dict(good)
bad3["totalAmount"] = -100
try:
    model.model_validate(bad3)
    raise AssertionError("负数金额应失败")
except ValidationError as e:
    assert "totalAmount" in _feedback_of(e.errors())
    print("[OK] 负数金额被 minimum 拦截")

# ---- 4. 分段工具 ----
segs = _split_text("段1\n\n段2\n\n段3", limit=3)
assert len(segs) == 3 and all(len(s) <= 3 for s in segs), segs
merged = _merge_contracts(
    [{"hasParty": [{"partyRole": "甲方", "partyName": "A"}]},
     {"hasParty": [{"partyRole": "甲方", "partyName": "A"}, {"partyRole": "乙方", "partyName": "B"}]},
     {"totalAmount": 100, "contractTitle": "T"}],
    schema,
)
assert len(merged["hasParty"]) == 2, "同名 Party 应去重"
assert merged["contractTitle"] == "T"
print("[OK] 分段/合并去重工具正常")

# ---- 5. 真实 DeepSeek 抽取 ----
SAMPLE = """
设备采购合同
合同编号：HT-2024-001
甲方（采购方）：北京智达科技有限公司
统一社会信用代码：91110108MA01ABCDE2
法定代表人：王强
地址：北京市海淀区中关村大街1号
乙方（供应方）：深圳蓝海电子有限公司
统一社会信用代码：91440300MA5KXYZ123
第一条 合同标的
1. 服务器主机，数量10台，单价80000元，金额800000元
2. 网络交换机，数量5台，单价20000元，金额100000元
第二条 合同金额
合同总金额为人民币900000元（含税），币种CNY，税率13%。
开具增值税专用发票。
第三条 违约责任
如乙方未能按期交货，每逾期一日按合同总金额的0.5%支付违约金。
如甲方逾期付款，每逾期一日按未付金额的0.1%支付违约金。
第四条 保密条款
双方对本合同内容负有保密义务，不得向第三方披露。
本合同自双方签字盖章之日起生效，生效日期2024年3月15日。
"""
result = extract_contract(SAMPLE, schema)
print("\n[抽取] status=%s truncated=%s" % (result.status, result.truncated))
assert result.status == ExtractionStatus.COMPLETE.value, f"抽取应 COMPLETE: {result.error}"
d = result.std_json
assert d["contractTitle"] == "设备采购合同", d["contractTitle"]
assert d["contractType"] == "采购", d["contractType"]
assert d["currency"] == "CNY"
assert d["totalAmount"] == 900000, d["totalAmount"]
assert d["effectiveDate"] == "2024-03-15", d["effectiveDate"]
assert d["taxRate"] == 0.13, d["taxRate"]
assert d["taxInclusive"] is True
assert d["invoiceType"] == "增值税专用发票"
roles = {p["partyRole"] for p in d["hasParty"]}
assert {"甲方", "乙方"} <= roles, roles
assert len(d["hasItem"]) == 2
assert len(d["hasClause"]) >= 2
print("[OK] 抽取字段与原文一致")
print(json.dumps(d, ensure_ascii=False, indent=2))

print("\nT1.3 全部通过 ✅")
