"""规则同步与查询（T2.1）与规则管理 CRUD / dry-run（T2.5）。

- sync_rules：本体自动生成规则 + rules/manual/*.rq 人工规则，按
  (rule_iri, ontology_version_id) upsert 到 check_rule（幂等）。
- 人工规则 ontology_version_id=NULL；更新时不改动 enabled（尊重人工启停）。
- 人工规则仅 DETERMINISTIC/SEMANTIC 可建；本体生成规则只读（仅启停/severity）。
"""
import json

from app.common.constants import RuleResult, RuleSource, RuleType
from app.db.models import CheckRule, CheckTask
from app.ontology.loader import load_ontology
from app.ontology.rule_generator import generate_rules, load_manual_rules
from app.validation.semantic_evaluator import SemanticEvaluator
from app.validation.sparql_executor import SparqlExecutor, build_graph


def sync_rules(db, ontology_version_id: int) -> dict[str, int]:
    """生成并同步全部规则，返回 {rule_iri: rule_id}。"""
    onto = load_ontology()
    rules = generate_rules(onto) + load_manual_rules()
    ids: dict[str, int] = {}
    for r in rules:
        ovid = ontology_version_id if r["source"] == RuleSource.ONTOLOGY_GENERATED.value else None
        row = db.query(CheckRule).filter_by(rule_iri=r["rule_iri"], ontology_version_id=ovid).first()
        if row is None:
            row = CheckRule(
                rule_iri=r["rule_iri"], rule_name=r["rule_name"],
                rule_type=r["rule_type"], severity=r["severity"],
                source=r["source"], expression=r["expression"],
                description=r["description"], concept_iri=r.get("concept_iri"),
                property_iri=r.get("property_iri"), enabled=True,
                ontology_version_id=ovid,
            )
            db.add(row)
        else:
            # 表达式/严重级别随本体变化刷新；不触碰 enabled
            row.rule_name = r["rule_name"]
            row.rule_type = r["rule_type"]
            row.severity = r["severity"]
            row.expression = r["expression"]
            row.aggregation = r.get("aggregation") or "any"
            row.description = r["description"]
            row.concept_iri = r.get("concept_iri")
            row.property_iri = r.get("property_iri")
        db.flush()
        ids[r["rule_iri"]] = row.id
    db.commit()
    return ids


def get_enabled_rules(db, ontology_version_id: int | None = None) -> list[CheckRule]:
    """任务可用的规则集：启用的人工规则 + 启用且版本匹配的自动规则。"""
    from sqlalchemy import or_
    conds = [CheckRule.source == RuleSource.MANUAL.value]
    if ontology_version_id is not None:
        conds.append(CheckRule.ontology_version_id == ontology_version_id)
    return (
        db.query(CheckRule)
        .filter(CheckRule.enabled.is_(True), or_(*conds))
        .all()
    )


def list_rules(db, rule_type: str | None = None, source: str | None = None,
               enabled: bool | None = None, page: int = 1, size: int = 20) -> dict:
    q = db.query(CheckRule)
    if rule_type:
        q = q.filter(CheckRule.rule_type == rule_type)
    if source:
        q = q.filter(CheckRule.source == source)
    if enabled is not None:
        q = q.filter(CheckRule.enabled.is_(enabled))
    total = q.count()
    items = q.order_by(CheckRule.id).offset((page - 1) * size).limit(size).all()
    return {"total": total, "page": page, "size": size, "items": [_rule_dict(r) for r in items]}


def create_rule(db, data: dict) -> CheckRule:
    """创建人工规则（DETERMINISTIC/SEMANTIC），默认 disabled。"""
    iri = data["rule_iri"].strip()
    if data.get("type") not in (RuleType.DETERMINISTIC.value, RuleType.SEMANTIC.value):
        raise ValueError("人工规则 type 仅支持 DETERMINISTIC/SEMANTIC")
    if data.get("type") == RuleType.DETERMINISTIC.value:
        aggregation = "any"          # SPARQL 全局图查询，聚合语义无意义，强制 any
    else:
        aggregation = data.get("aggregation") or "any"
        if aggregation not in ("any", "all"):
            raise ValueError("aggregation 仅支持 any/all")
    # 人工规则 ontology_version_id=NULL，MySQL 唯一键对 NULL 不生效 → 应用层查重
    if db.query(CheckRule).filter_by(rule_iri=iri, ontology_version_id=None).first():
        raise ValueError(f"rule_iri 已存在: {iri}")
    rule = CheckRule(
        rule_iri=iri, rule_name=data["name"], rule_type=data["type"],
        severity=data["severity"], source=RuleSource.MANUAL.value,
        expression=data["expression"], aggregation=aggregation,
        description=data.get("description"),
        enabled=False,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def update_rule(db, rule_id: int, data: dict) -> CheckRule | None:
    """编辑规则。本体自动生成规则只读（仅启停/severity），人工规则全字段。"""
    rule = db.get(CheckRule, rule_id)
    if rule is None:
        return None
    if rule.source == RuleSource.ONTOLOGY_GENERATED.value:
        if "enabled" in data:
            rule.enabled = bool(data["enabled"])
        if "severity" in data:
            rule.severity = data["severity"]
    else:
        for k in ("enabled", "severity", "expression", "description", "rule_name", "aggregation"):
            if k in data and data[k] is not None:
                setattr(rule, k, data[k])
    db.commit()
    return rule


def disable_rule(db, rule_id: int) -> bool:
    """软删（失效）人工规则。本体生成规则不可删。"""
    rule = db.get(CheckRule, rule_id)
    if rule is None or rule.source != RuleSource.MANUAL.value:
        return False
    rule.enabled = False
    db.commit()
    return True


def dry_run(db, rule_id: int, task_id: int) -> dict | None:
    """复用历史任务 RDF 试跑规则，预览命中（不落库）。确定性规则无 LLM，token_cost=0。"""
    rule = db.get(CheckRule, rule_id)
    task = db.get(CheckTask, task_id)
    if rule is None or task is None:
        return None
    base = {"rule_id": rule.id, "rule_iri": rule.rule_iri, "rule_name": rule.rule_name,
            "rule_type": rule.rule_type, "severity": rule.severity,
            "aggregation": rule.aggregation}
    if rule.rule_type == RuleType.SEMANTIC.value:
        segments = json.loads(task.segments_json) if task.segments_json else []
        if not segments:
            return {**base, "result": RuleResult.SKIPPED.value, "subjects": [],
                    "message": "任务无分段原文，无法试跑", "token_cost": 0}
        evaluator = SemanticEvaluator()
        results = evaluator.evaluate(segments, [
            {"id": rule.id, "rule_iri": rule.rule_iri, "rule_name": rule.rule_name,
             "expression": rule.expression, "aggregation": rule.aggregation or "any"}])
        r = results[0]
        msg = r.message
        if r.evidence_text:
            evidence = r.evidence_text if len(r.evidence_text) <= 120 else r.evidence_text[:120] + "…"
            msg = f"{r.message or '命中'}（evidence: {evidence}）"
        return {**base, "result": r.result, "subjects": [], "message": msg,
                "confidence": r.confidence, "token_cost": evaluator.token_cost}
    graph = build_graph(task.extraction_rdf)
    res = SparqlExecutor().run(graph, rule)
    if res.passed:
        result = RuleResult.PASS.value
    elif res.subjects:
        result = RuleResult.FAIL.value
    else:
        result = RuleResult.SKIPPED.value
    return {**base, "result": result, "subjects": res.subjects,
            "token_cost": 0, "message": None}


def _rule_dict(r: CheckRule) -> dict:
    return {
        "id": r.id, "rule_iri": r.rule_iri, "rule_name": r.rule_name,
        "rule_type": r.rule_type, "severity": r.severity, "source": r.source,
        "expression": r.expression, "aggregation": r.aggregation,
        "description": r.description,
        "concept_iri": r.concept_iri, "property_iri": r.property_iri,
        "enabled": r.enabled, "ontology_version_id": r.ontology_version_id,
    }
