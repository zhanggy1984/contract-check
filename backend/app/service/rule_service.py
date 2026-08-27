"""规则同步与查询（T2.1）与规则管理 CRUD / dry-run（T2.5）。

- sync_rules：本体自动生成规则 + rules/manual/*.rq 人工规则，按
  (rule_iri, ontology_version_id) upsert 到 check_rule（幂等）。
- 人工规则 ontology_version_id=NULL；更新时不改动 enabled（尊重人工启停）。
- 人工规则仅 DETERMINISTIC/SEMANTIC 可建；本体生成规则只读（仅启停/severity）。
"""
import json
import logging
import threading

from sqlalchemy.exc import IntegrityError, OperationalError

from app.common.constants import RuleResult, RuleSource, RuleType
from app.db.models import CheckRule, CheckTask, RuleCheckResult, Violation
from app.ontology.loader import load_ontology, register_version
from app.ontology.rule_generator import generate_rules, load_manual_rules
from app.validation.semantic_evaluator import SemanticEvaluator
from app.validation.sparql_executor import SparqlExecutor, build_graph

logger = logging.getLogger(__name__)

# sync_rules 记忆化（T4.3-6）：同版本只同步一次，版本变化才重同步（顺带收敛并发窗口）
_sync_lock = threading.Lock()
_synced_ids: dict[int, dict[str, int]] = {}
_SYNC_RETRY_MAX = 2  # 死锁重试上限（1213/40001）


def _is_deadlock(e: OperationalError) -> bool:
    """1213 死锁 / 40001 序列化失败（与 nodes.persist 同款判据）。"""
    args = getattr(e.orig, "args", ()) if e.orig is not None else ()
    return bool(args) and args[0] in (1213, 40001)


def sync_rules(db, ontology_version_id: int) -> dict[str, int]:
    """生成并同步全部规则，返回 {rule_iri: rule_id}。

    记忆化（T4.3-6）：同版本只同步一次，版本变化才重同步——顺带把并发窗口收敛到
    「版本首次引入」一瞬。并发首插兜底：begin_nested 捕获唯一键冲突回退复用；
    死锁（1213/40001）顶层重试。消费方是 get_enabled_rules（实时读 DB），
    人工规则增删/启停不受缓存影响；返回值仅历史/测试契约，任务流程不消费。

    契约：
    - 运行中修改 rules/manual/*.rq 需重启或版本变更才生效（同版本缓存不重读文件）
    - 调用方进入前须 commit 自己的 pending 改动——死锁时 InnoDB 回滚整个事务，
      db.rollback() 会一并回滚 session 未提交内容（validate_deterministic 已先 commit，安全）
    """
    with _sync_lock:
        cached = _synced_ids.get(ontology_version_id)
        if cached is not None:
            return cached
    onto = load_ontology()
    rules = generate_rules(onto) + load_manual_rules()
    for attempt in range(1, _SYNC_RETRY_MAX + 1):
        try:
            ids = _sync_once(db, ontology_version_id, rules)
            break
        except OperationalError as e:
            if _is_deadlock(e) and attempt < _SYNC_RETRY_MAX:
                db.rollback()   # 死锁已回滚整个事务，重置 session 状态重试
                logger.warning("sync_rules 死锁重试 %d/%d", attempt, _SYNC_RETRY_MAX)
                continue
            raise
    with _sync_lock:
        _synced_ids[ontology_version_id] = ids
    return ids


def _sync_once(db, ontology_version_id: int, rules: list[dict]) -> dict[str, int]:
    """单轮规则 upsert：返回 {rule_iri: rule_id}（同版本并发时靠唯一键兜底）。"""
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
            try:
                with db.begin_nested():
                    db.flush()   # 撞 uk_rule_iri_version（并发首插）在此抛 IntegrityError
            except IntegrityError:
                # 并发首插竞态：另一线程已插入同 (rule_iri, version)，回退复用
                db.expunge(row)
                row = db.query(CheckRule).filter_by(
                    rule_iri=r["rule_iri"], ontology_version_id=ovid).first()
                if row is None:
                    raise
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
        db.flush()   # 保证 ids 读到真实 id（非 pending）
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
    """规则列表：本体自动规则只展示当前版本，人工规则全量展示。

    本体每次变更会注册新版本，旧版本规则会积累（同约束多条重复）；列表按
    当前本体文件 md5 指纹对应的版本过滤，历史版本仅在校验记录里按 rule_id 引用。
    """
    from sqlalchemy import or_
    cur_ovid = register_version(db)
    q = db.query(CheckRule).filter(or_(
        CheckRule.source == RuleSource.MANUAL.value,
        CheckRule.ontology_version_id == cur_ovid,
    ))
    if rule_type:
        q = q.filter(CheckRule.rule_type == rule_type)
    if source:
        q = q.filter(CheckRule.source == source)
    if enabled is not None:
        q = q.filter(CheckRule.enabled.is_(enabled))
    total = q.count()
    items = q.order_by(CheckRule.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"total": total, "page": page, "size": size, "items": [_rule_dict(r) for r in items]}


def _gen_rule_iri(db, name: str) -> str:
    """由规则名自动生成 rule_iri（urn:rule:manual:{名}），冲突追加 -2/-3 后缀。

    rule_iri 是内部唯一标识（语义评估按它聚合、LLM prompt 按它标签），用户不需要填，
    前端新建表单已去掉该输入；此处兜底保证唯一。rule_name 为空时退化为随机后缀。
    """
    base = f"urn:rule:manual:{name.strip()}"
    if base == "urn:rule:manual:":
        base += "untitled"
    iri = base
    n = 2
    while db.query(CheckRule).filter_by(rule_iri=iri, ontology_version_id=None).first():
        iri = f"{base}-{n}"
        n += 1
    return iri


def create_rule(db, data: dict) -> CheckRule:
    """创建人工语义规则（仅 SEMANTIC/LLM，默认 disabled）。

    确定性规则由本体 OWL 自动生成，人工规则只支持语义校验——SPARQL 门槛高，
    用户写不准确，已在前端取消确定性类型入口，此处后端兜底强制。
    rule_iri 可选：缺省由规则名自动生成（用户不用填技术标识）。
    """
    if data.get("type") != RuleType.SEMANTIC.value:
        raise ValueError("新建规则仅支持 SEMANTIC（语义 LLM）类型")
    iri = (data.get("rule_iri") or "").strip() or _gen_rule_iri(db, data["name"])
    aggregation = data.get("aggregation") or "any"
    if aggregation not in ("any", "all"):
        raise ValueError("aggregation 仅支持 any/all")
    # 人工规则 ontology_version_id=NULL，MySQL 唯一键对 NULL 不生效 → 应用层查重
    if db.query(CheckRule).filter_by(rule_iri=iri, ontology_version_id=None).first():
        raise ValueError(f"rule_iri 已存在: {iri}")
    rule = CheckRule(
        rule_iri=iri, rule_name=data["name"], rule_type=RuleType.SEMANTIC.value,
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


def delete_rule(db, rule_id: int) -> bool:
    """物理删除人工规则。

    本体生成规则不可删；已被校验记录（rule_check_result / violation）引用的规则
    删除会破坏历史审计（FK 约束），拒绝并提示改「失效」（软删）。无引用的自定义规则彻底删除。
    """
    rule = db.get(CheckRule, rule_id)
    if rule is None:
        return False
    if rule.source != RuleSource.MANUAL.value:
        raise ValueError("本体自动生成的规则不可删除")
    refs = (db.query(RuleCheckResult).filter_by(rule_id=rule_id).count()
            + db.query(Violation).filter_by(rule_id=rule_id).count())
    if refs:
        raise ValueError(f"该规则已被 {refs} 条校验记录引用，删除会破坏历史审计，请改用「失效」")
    db.delete(rule)
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
