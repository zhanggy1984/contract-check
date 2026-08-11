"""报告数据组装（T4.4）：从 DB 一次查全，生成生成器友好的纯数据。"""
import json
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.db.models import CheckRule, CheckTask, ContractFile, RuleCheckResult, Violation

# 抽取摘要：standard_json 标量字段 → 中文标签（已知字段；未知字段不展示，避免报告暴露原始键名）
SUMMARY_FIELDS = [
    ("contractTitle", "合同名称"),
    ("contractType", "合同类型"),
    ("totalAmount", "合同总金额"),
    ("currency", "币种"),
    ("signedDate", "签署日期"),
    ("effectiveDate", "生效日期"),
    ("status", "合同状态"),
    ("contractNo", "合同编号"),
    ("signingPlace", "签署地点"),
    ("depositAmount", "保证金金额"),
    ("depositType", "保证金类型"),
    ("depositRefundCondition", "保证金退还条件"),
    ("taxInclusive", "是否含税"),
    ("taxRate", "税率"),
    ("invoiceType", "发票类型"),
    ("invoiceRequirements", "发票要求"),
]

RESULT_LABEL = {"PASS": "通过", "FAIL": "异常", "SKIPPED": "跳过"}
STATUS_LABEL = {
    "UNCONFIRMED": "待确认", "CONFIRMED": "已确认", "FALSE_POSITIVE": "误报",
}
SEVERITY_LABEL = {"HIGH": "高", "MEDIUM": "中", "LOW": "低"}


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "是" if v else "否"
    return str(v)


@dataclass
class ReportData:
    task_id: int
    task_status: str
    extraction_status: str | None
    llm_model: str | None
    create_time: str | None
    file_name: str
    file_type: str
    file_size: str
    has_scanned: bool
    ocr_applied: bool
    summary: list[tuple[str, str]] = field(default_factory=list)   # (标签, 值)
    parties: list[list[tuple[str, str]]] = field(default_factory=list)  # 每方一组 (标签, 值)
    items: list[list[tuple[str, str]]] = field(default_factory=list)    # 每个标的物一组
    rule_results: list[dict] = field(default_factory=list)
    violations: list[dict] = field(default_factory=list)


def _entity_fields(obj: dict, labels: dict[str, str]) -> list[tuple[str, str]]:
    out = []
    for k, label in labels.items():
        v = _fmt(obj.get(k))
        if v:
            out.append((label, v))
    return out


PARTY_LABELS = {
    "partyRole": "角色", "partyName": "名称", "unifiedSocialCreditCode": "统一社会信用代码",
    "legalRepresentative": "法定代表人", "contact": "联系人", "address": "地址",
}
ITEM_LABELS = {
    "itemName": "标的名称", "itemType": "类型", "specification": "规格",
    "quantity": "数量", "unit": "单位", "unitPrice": "单价", "itemAmount": "金额",
    "deliveryDate": "交付日期", "servicePeriod": "服务期限",
}


def build_report_data(db: Session, task_id: int) -> ReportData:
    task = db.get(CheckTask, task_id)
    if task is None:
        raise ValueError(f"任务 {task_id} 不存在")
    cf: ContractFile = task.contract_file

    rule_names = {r.id: r.rule_name for r in db.query(CheckRule).all()}
    results = db.query(RuleCheckResult).filter_by(task_id=task_id).order_by(RuleCheckResult.id).all()
    violations = db.query(Violation).filter_by(task_id=task_id).order_by(Violation.id).all()

    std = json.loads(task.standard_json) if task.standard_json else {}
    summary = [(label, _fmt(std.get(k))) for k, label in SUMMARY_FIELDS if _fmt(std.get(k))]
    parties = [_entity_fields(p, PARTY_LABELS) for p in (std.get("hasParty") or []) if isinstance(p, dict)]
    items = [_entity_fields(i, ITEM_LABELS) for i in (std.get("hasItem") or []) if isinstance(i, dict)]

    def _rcr(r: RuleCheckResult) -> dict:
        return {
            "rule_id": r.rule_id, "rule_name": rule_names.get(r.rule_id, str(r.rule_id)),
            "rule_type": r.rule_type, "result": RESULT_LABEL.get(r.result, r.result),
            "severity": SEVERITY_LABEL.get(r.severity, r.severity),
            "confidence": r.confidence, "segment_ref": r.segment_ref or "",
            "message": r.message or "",
        }

    def _v(v: Violation) -> dict:
        return {
            "rule_id": v.rule_id, "rule_name": rule_names.get(v.rule_id, str(v.rule_id)),
            "rule_type": v.rule_type, "severity": SEVERITY_LABEL.get(v.severity, v.severity),
            "confidence": v.confidence, "segment_ref": v.segment_ref or "",
            "evidence_text": v.evidence_text or "", "message": v.message or "",
            "status": STATUS_LABEL.get(v.status, v.status),
            "confirm_user": v.confirm_user or "",
            "confirm_time": v.confirm_time.isoformat() if v.confirm_time else "",
        }

    return ReportData(
        task_id=task.id, task_status=task.status, extraction_status=task.extraction_status,
        llm_model=task.llm_model,
        create_time=task.create_time.isoformat() if task.create_time else None,
        file_name=cf.file_name, file_type=cf.file_type,
        file_size=f"{cf.file_size / 1024:.1f} KB", has_scanned=cf.has_scanned, ocr_applied=cf.ocr_applied,
        summary=summary, parties=parties, items=items,
        rule_results=[_rcr(r) for r in results], violations=[_v(v) for v in violations],
    )
