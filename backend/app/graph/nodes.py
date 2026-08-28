"""图节点：解析→抽取→确定性校验→待审核→应用审核→定稿。

语义校验节点在 Phase 3 接入；await_human_review 保持纯节点。
"""
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from langgraph.types import interrupt
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from app.common.constants import ExtractionStatus, RuleResult, RuleType, TaskStatus, ViolationStatus
from app.common.errors import TaskCancelledError
from app.config import settings
from app.db.models import CheckTask, Violation
from app.db.session import SessionLocal
from app.graph.decisions import decide_extract_retry, decide_ocr_required
from app.graph.decision_recorder import make_trace
from app.graph.state import TaskState
from app.ontology.loader import load_ontology, register_version
from app.ontology.rdf_converter import JsonToRdfConverter
from app.ontology.schema_mapper import build_extraction_schema
from app.parser.segment_splitter import split_segments
from app.service.rule_service import get_enabled_rules, sync_rules
from app.tools import registry
from app.validation.persist import RuleOutcome, persist_results
from app.validation.sparql_executor import build_graph

PARSED_DIR = Path("data/parsed")

logger = logging.getLogger(__name__)

# persist 死锁重试（T232）：多任务并发时各自 DELETE+INSERT rule_check_result/violation，
# InnoDB 间隙锁/插入意向锁竞争死锁（1213/40001）是常态，MySQL 自动回滚受害者事务，
# 业务层整体重试即可——重试必须覆盖「usage 落库 + persist」整个事务（_persist_once）。
_PERSIST_RETRY_MAX = 3


def _is_deadlock(e: OperationalError) -> bool:
    """1213 死锁 / 40001 序列化失败（InnoDB 均须重试受害者事务）。"""
    args = getattr(e.orig, "args", ()) if e.orig is not None else ()
    return bool(args) and args[0] in (1213, 40001)


# 外部标记的终态（超时 FAILED / 取消 CANCELLED / 定稿 SUCCESS）：
# 图执行遇此状态必须终止，防僵尸线程（软超时后无法中断的 to_thread）继续执行覆盖判定
_TERMINAL_STATUSES = (
    TaskStatus.FAILED.value, TaskStatus.SUCCESS.value, TaskStatus.CANCELLED.value,
)

# 规则种类从 rule_iri 解析：urn:rule:{kind}:...（本体生成 required/enum/min/pattern，人工 manual）
_RULE_KIND_RE = re.compile(r"^urn:rule:([a-z]+):")


def _rule_kind(rule_iri: str | None) -> str | None:
    """规则种类（用于 INCOMPLETE 时按依赖分派）。rule_iri 取不到种类时返回 None。"""
    if not rule_iri:
        return None
    m = _RULE_KIND_RE.match(rule_iri)
    return m.group(1) if m else None


def _merge_usage(a: dict | None, b: dict | None) -> dict | None:
    """评测契约 usage 聚合（B.4）：抽取与语义 token 全字段求和（含 7.4 cache）。两者皆空返回 None（不落库）。"""
    if not a and not b:
        return None
    return {
        k: (a.get(k, 0) if a else 0) + (b.get(k, 0) if b else 0)
        for k in ("prompt_tokens", "completion_tokens", "total_tokens",
                  "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
    }


def _persist_decisions(task_id: int, traces: list) -> None:
    """决策痕迹即时落库（不经 checkpoint：抽取失败等 FAILED 分支任务失败也能保痕）。

    与现有 decision_json 合并追加；纯审计数据，落库失败静默降级，不影响主流程。
    """
    if not traces:
        return
    try:
        with SessionLocal() as db:
            task = db.get(CheckTask, task_id)
            if task is None:
                return
            existing = json.loads(task.decision_json) if task.decision_json else []
            task.decision_json = json.dumps(existing + traces, ensure_ascii=False)
            db.commit()
    except Exception:  # noqa: BLE001 审计数据落库失败不阻断主流程
        logger.warning("决策痕迹落库失败 task_id=%s", task_id)


def _ensure_runnable(db, task, task_id: int) -> None:
    """节点入口终态短路（T4.3 review P1 僵尸线程防覆盖）。

    软超时（check_task_service.run_task_async）后 to_thread 图线程无法中断，若不拦会继续
    执行后续节点，把外部判定（超时 FAILED / 取消 CANCELLED）覆盖成中间态、WAITING_REVIEW
    或 SUCCESS，掩埋超时事实。CANCELLED → TaskCancelledError（保留置 CANCELLED 语义）；
    FAILED/SUCCESS → RuntimeError（终态已判定，忽略僵尸图执行——其异常无人捕获，不会二次改状态）。
    """
    if task is None:
        raise RuntimeError(f"任务 {task_id} 不存在")
    if task.status == TaskStatus.CANCELLED.value:
        raise TaskCancelledError("任务已取消")
    if task.status in _TERMINAL_STATUSES:
        raise RuntimeError(f"任务已被外部标记 {task.status}，忽略僵尸图执行")


def parse_node(state: TaskState) -> dict:
    """读取已解析文本并置 PARSING；扫描页（无文本层）在此触发 OCR（T4.1）；CANCELLED 时短路。

    P0 页级修复：优先用上传时落库的页级文本（page_texts_json，单一事实来源），
    对扫描页（清洗后为空）逐页 OCR 按页序合并——混合扫描 PDF 只补空页，不重扫有文本层的页。
    无页级数据的历史任务回退：has_scanned 布尔 + 全文 OCR。
    OCR 失败（模型缺失/识别异常）抛异常 → 任务 FAILED 带提示，不做"空文本进抽取"。
    """
    with SessionLocal() as db:
        task = db.get(CheckTask, state["task_id"])
        _ensure_runnable(db, task, state["task_id"])
        task.status = TaskStatus.PARSING.value
        task.progress = 20
        db.commit()
        cf = task.contract_file
        sha = cf.sha256
        storage_path = cf.storage_path
        file_name, file_size = cf.file_name, cf.file_size
        ocr_applied = bool(cf.ocr_applied)
        try:
            page_texts = json.loads(cf.page_texts_json) if cf.page_texts_json else None
        except (json.JSONDecodeError, TypeError):
            page_texts = None   # 历史脏数据防御：非法 JSON 按无页级数据回退
        if not isinstance(page_texts, list) or not all(isinstance(t, str) for t in page_texts):
            page_texts = None   # 非列表 / 含非字符串元素同样回退（防 t.strip() 崩任务）

    txt = PARSED_DIR / f"{sha}.txt"
    text = txt.read_text(encoding="utf-8") if txt.exists() else ""
    # 受约束 OCR 决策：确定性否决权优先，LLM 仅歧义场景判断；保守模式执行与旧版一致
    if page_texts is not None:
        # 页级路径：扫描页 = 清洗后为空的页；文本层可读不构成跳过理由（混合扫描 PDF）
        scanned = [i for i, t in enumerate(page_texts) if not t.strip()]
        need_ocr, ocr_trace = decide_ocr_required(
            scanned_pages=scanned, ocr_applied=ocr_applied,
            existing_text=text, pdf_path=storage_path, file_name=file_name, file_size=file_size,
        )
        _persist_decisions(state["task_id"], [ocr_trace])
        if need_ocr:
            out = registry.execute("ocr_pdf", pdf_path=storage_path, pages=scanned)
            ocr_map = out["pages"]
            for idx, t in ocr_map.items():
                page_texts[idx] = t
            text = "\n".join(page_texts)
            logger.info("任务 %s OCR 质量(页级): %s", state["task_id"], out.get("stats"))
    else:
        # 历史任务回退：无页级数据，沿用 has_scanned + 全文 OCR
        need_ocr, ocr_trace = decide_ocr_required(
            has_scanned=bool(cf.has_scanned), ocr_applied=ocr_applied,
            existing_text=text, pdf_path=storage_path, file_name=file_name, file_size=file_size,
        )
        _persist_decisions(state["task_id"], [ocr_trace])
        if need_ocr:
            # 全文 OCR：exec_ocr_pdf 返回 {页索引: 文本, stats}，按页序合并（页索引有序）
            out = registry.execute("ocr_pdf", pdf_path=storage_path)
            ocr_map = out["pages"]
            text = "\n".join(ocr_map.values())
            logger.info("任务 %s OCR 质量(全文): %s", state["task_id"], out.get("stats"))
    if need_ocr:
        txt.write_text(text, encoding="utf-8")
        with SessionLocal() as db:
            task = db.get(CheckTask, state["task_id"])
            task.contract_file.ocr_applied = True
            if page_texts is not None:
                task.contract_file.page_texts_json = json.dumps(page_texts, ensure_ascii=False)
            db.commit()
    return {"parsed_text": text}


def _reuse_extraction(task) -> dict:
    """崩溃重放复用已落库抽取结果（T4.3-5 防重复计费）：读回快照字段直接返回 state。

    复用前提：extract_node 先落库后返回，崩溃在「落库后→checkpoint 前」窗口 →
    recover 重放本节点若重新执行 registry.execute 会重复计费。已落库（COMPLETE/INCOMPLETE）
    即视为抽取已完成，跳过 LLM 与 register_version；usage 从 extraction_usage_json 单次快照读
    （token_usage_json 是 persist 写的聚合值，不可当单次用量复用，否则二次聚合会翻倍）。
    """
    return {
        "extraction_json": json.loads(task.standard_json) if task.standard_json else None,
        "extraction_status": task.extraction_status,
        "extraction_rdf": task.extraction_rdf or "",
        "segments": json.loads(task.segments_json) if task.segments_json else [],
        "extraction_usage": json.loads(task.extraction_usage_json) if task.extraction_usage_json else None,
    }


def extract_node(state: TaskState) -> dict:
    """LLM 抽取 + RDF 转换 + segments 恒落库（T1.4/T1.5）。

    空结果（FAILED）→ 抛异常走失败分支；部分缺失（INCOMPLETE）→ 照常落库待人工。
    """
    task_id = state["task_id"]
    with SessionLocal() as db:
        task = db.get(CheckTask, task_id)
        _ensure_runnable(db, task, task_id)
        # 崩溃重放守卫（T4.3-5 重复计费）：extract_node 先落库后返回，崩溃在
        # 「落库后→checkpoint 前」窗口 → recover 重放本节点会重调 LLM。已落库
        # （COMPLETE/INCOMPLETE）直接复用快照跳过 registry.execute 与 register_version；
        # FAILED 不落库不触发（失败重试仍走 LLM，属合理重试非重复计费）
        if task.extraction_status in (ExtractionStatus.COMPLETE.value,
                                      ExtractionStatus.INCOMPLETE.value):
            return _reuse_extraction(task)
        task.status = TaskStatus.EXTRACTING.value
        task.progress = 40
        task.llm_model = settings.deepseek_model
        task.ontology_version_id = register_version(db)
        db.commit()

    schema = build_extraction_schema(load_ontology())
    result = registry.execute("extract_contract", text=state["parsed_text"], schema=schema)
    if result["status"] == ExtractionStatus.FAILED.value:
        # 受约束失败处置决策：LLM 判断是否重试，决策痕迹即时落库不丢。
        # 执行权：仅当开关放开且 LLM 建议 retry 才重试一次（防循环硬上限），否则任务 FAILED
        action, extract_trace = decide_extract_retry(
            text=state["parsed_text"], result_status=result["status"],
            error=result["error"], std_json=result["std_json"],
        )
        _persist_decisions(task_id, [extract_trace])
        if not (settings.extract_decision_allow_llm_retry and action == "retry"):
            raise RuntimeError(f"抽取失败: {result['error']}")
        result = registry.execute("extract_contract", text=state["parsed_text"], schema=schema)
        if result["status"] == ExtractionStatus.FAILED.value:
            raise RuntimeError(f"抽取失败: {result['error']}")

    segments = split_segments(state["parsed_text"])
    rdf_nt = ""
    if result["std_json"]:
        rdf_nt = JsonToRdfConverter(schema).convert(result["std_json"], task_id)

    with SessionLocal() as db:
        task = db.get(CheckTask, task_id)
        task.extraction_status = result["status"]
        task.standard_json = json.dumps(result["std_json"], ensure_ascii=False) if result["std_json"] else None
        task.segments_json = json.dumps(segments, ensure_ascii=False)
        task.extraction_rdf = rdf_nt or None
        task.extraction_conflicts = json.dumps(result["conflicts"], ensure_ascii=False) if result["conflicts"] else None
        task.extraction_usage_json = json.dumps(result["token_usage"], ensure_ascii=False) if result["token_usage"] else None  # 崩溃重放复用快照
        task.progress = 60
        db.commit()

    return {
        "extraction_json": result["std_json"],
        "extraction_status": result["status"],
        "extraction_rdf": rdf_nt,
        "segments": segments,
        "extraction_usage": result["token_usage"],   # 评测契约 usage（B.4），persist 统一落库
    }


def validate_deterministic(state: TaskState) -> dict:
    """确定性校验（T2.2 改造）：SPARQL 规则执行，只算不落库。

    D2 精化（B1/B2/B3 修复）：抽取 INCOMPLETE 时不再全量跳过——
    不依赖数据完整性的规则照跑（required 抓缺失、manual 结构逻辑），
    依赖完整数据的约束规则（enum/min/pattern）跳过防误报（部分数据判枚举/下限不可靠）。
    RDF 完全缺失时全部 SKIPPED（T1.5 防御：部分数据不进假阳性洪水）。
    结果以纯 dict 挂 state（det_outcomes），供 persist 节点统一落库（checkpointer 可序列化）。
    """
    task_id = state["task_id"]
    with SessionLocal() as db:
        task = db.get(CheckTask, task_id)
        _ensure_runnable(db, task, task_id)
        task.status = TaskStatus.VALIDATING.value
        task.progress = 70
        db.commit()
        ovid = task.ontology_version_id
        incomplete = task.extraction_status == ExtractionStatus.INCOMPLETE.value
        rdf_nt = task.extraction_rdf
        sync_rules(db, ovid)                 # 保证规则集最新（表达式/严重级别）
        rules = [r for r in get_enabled_rules(db, ovid)
                 if r.rule_type == RuleType.DETERMINISTIC.value]
        if incomplete:
            runnable, skippable = [], []
            for r in rules:
                (runnable if _rule_kind(r.rule_iri) in ("required", "manual") else skippable).append(r)
        else:
            runnable, skippable = rules, []

    graph = build_graph(rdf_nt) if runnable else None
    outcomes: list[dict] = []
    for rule in skippable:
        outcomes.append({"rule_id": rule.id, "result": RuleResult.SKIPPED.value, "subjects": []})
    for rule in runnable:
        res = registry.execute("run_sparql", graph=graph, rule=rule)
        if res["passed"]:
            result = RuleResult.PASS.value
        elif res["subjects"]:
            result = RuleResult.FAIL.value
        else:
            result = RuleResult.SKIPPED.value   # 空图无反例可判（防御）
        outcomes.append({"rule_id": rule.id, "result": result, "subjects": res["subjects"]})
    return {"det_outcomes": outcomes}


def _is_sem_degraded(outcomes: list[dict]) -> bool:
    """语义评估整体降级：全部规则 SKIPPED 且 confidence 全 LOW（评估失败而非规则不适用）。

    正常业务结论（规则明确不适用 → SKIPPED/HIGH）与真降级（LLM 不可用 → SKIPPED/LOW）
    由此区分；整体降级时任务仍按无 violation 走 SUCCESS，但须留审计标记（c1）。
    """
    if not outcomes:
        return False
    return all(o.get("result") == RuleResult.SKIPPED.value and o.get("confidence") == "LOW"
               for o in outcomes)


def validate_semantic(state: TaskState) -> dict:
    """语义校验（T3.2）：SEMANTIC 规则按段批跑，只算不落库。

    基于原文 segments（不受抽取 INCOMPLETE 影响）；segments 缺失时全部 SKIPPED 防御。
    整体降级（LLM 不可用 → 全 SKIPPED/LOW）留决策审计，终态不静默（c1）。
    """
    task_id = state["task_id"]
    with SessionLocal() as db:
        task = db.get(CheckTask, task_id)
        _ensure_runnable(db, task, task_id)
        task.progress = 78
        db.commit()
        ovid = task.ontology_version_id
        rules = [r for r in get_enabled_rules(db, ovid)
                 if r.rule_type == RuleType.SEMANTIC.value]
        segments = json.loads(task.segments_json) if task.segments_json else []
        # 崩溃重放守卫（T4.3-5 重复计费）：语义结果快照已落库 → 复用跳过 evaluate_semantic。
        # 无规则/无段分支不落库不触发；降级审计第一次运行已写，复用不重复（审计少一条可接受）
        if task.sem_outcomes_json:
            # 崩溃重放：从快照恢复时重算降级标记——首跑若在「快照后→checkpoint 前」窗口
            # 崩溃，降级标记未随 checkpoint 落盘，这里必须补算，否则又静默自动 SUCCESS
            replayed = json.loads(task.sem_outcomes_json)
            return {
                "sem_outcomes": replayed,
                "sem_usage": json.loads(task.sem_usage_json) if task.sem_usage_json else None,
                "sem_degraded": _is_sem_degraded(replayed),
            }

    if not rules:
        return {"sem_outcomes": [], "sem_usage": None}
    if not segments:
        # 无分段原文可评 = 评估失败（而非规则不适用），标 LOW 供审计识别
        outcomes = [
            {"rule_id": r.id, "result": RuleResult.SKIPPED.value, "confidence": "LOW"}
            for r in rules
        ]
        usage = None
    else:
        res = registry.execute(
            "evaluate_semantic",
            segments=segments,
            rules=[{"id": rr.id, "rule_iri": rr.rule_iri, "rule_name": rr.rule_name,
                    "expression": rr.expression, "aggregation": rr.aggregation or "any"} for rr in rules],
        )
        outcomes = res["outcomes"]
        usage = res["usage"]
        # 崩溃重放快照（T4.3-5）：结果落库供入口复用（防重复计费）。独立事务先于节点返回，
        # 崩溃在「快照后→checkpoint 前」窗口重放时直接复用；persist 仍从 state 统一写库
        with SessionLocal() as db:
            task = db.get(CheckTask, task_id)
            task.sem_outcomes_json = json.dumps(outcomes, ensure_ascii=False)
            task.sem_usage_json = json.dumps(usage, ensure_ascii=False) if usage else None
            db.commit()
    degraded = _is_sem_degraded(outcomes)
    if degraded:
        # 语义评估整体降级（LLM 不可用/段原文缺失）→ 留决策审计 + 日志；不强行 FAILED（SKIPPED 非
        # violation，强行 FAILED 会误报），靠 sem_degraded 标记让 _should_wait 转人工确认而非自动通过
        _persist_decisions(task_id, [make_trace(
            "validate_semantic", "sem_degraded", "degrade", "fallback_error",
            f"语义评估整体降级：全部 {len(outcomes)} 条规则 SKIPPED/LOW，任务无 violation 但评估不完整",
            {"sem_rules": len(outcomes)})])
        logger.warning("任务 %s 语义评估整体降级（%d 条规则全 SKIPPED/LOW）", task_id, len(outcomes))
    return {"sem_outcomes": outcomes, "sem_usage": usage, "sem_degraded": degraded}   # 评测契约 usage（B.4），persist 统一落库


def persist_node(state: TaskState) -> dict:
    """校验结果统一落库（T2.3/T3.2）：确定性 + 语义合并，单事务幂等写 rule_check_result + violation。"""
    task_id = state["task_id"]
    det = state.get("det_outcomes") or []
    sem = state.get("sem_outcomes") or []
    for attempt in range(1, _PERSIST_RETRY_MAX + 1):
        try:
            return _persist_once(state, task_id, det, sem)
        except OperationalError as e:
            if _is_deadlock(e) and attempt < _PERSIST_RETRY_MAX:
                logger.warning("persist 死锁重试 %d/%d task_id=%s", attempt, _PERSIST_RETRY_MAX, task_id)
                continue
            raise


def _persist_once(state: TaskState, task_id: int, det: list, sem: list) -> dict:
    """单次事务：usage 落库 + 幂等写 rule_check_result + violation（死锁由 persist_node 重试整事务）。"""
    with SessionLocal() as db:
        task = db.get(CheckTask, task_id)
        _ensure_runnable(db, task, task_id)
        rules = {r.id: r for r in get_enabled_rules(db, task.ontology_version_id)}
        outcomes: list[RuleOutcome] = []
        for o in det:
            rule = rules.get(o["rule_id"])
            if rule is not None:
                outcomes.append(RuleOutcome(rule, o["result"], o.get("subjects") or []))
        for o in sem:
            rule = rules.get(o["rule_id"])
            if rule is not None:
                outcomes.append(RuleOutcome(
                    rule, o["result"], [],
                    message=o.get("message"),
                    segment_ref=o.get("segment_ref"),
                    evidence_text=o.get("evidence_text"),
                    confidence=o.get("confidence") or "HIGH",
                ))
        # 评测契约 usage 聚合落库（B.4）：抽取 + 语义 token 三分量，与校验结果同事务
        usage = _merge_usage(state.get("extraction_usage"), state.get("sem_usage"))
        if usage:
            task.token_usage_json = json.dumps(usage, ensure_ascii=False)
        info = persist_results(db, task_id, outcomes)
    return {"violations_count": info["violations"], "rule_results_count": info["rows"]}


def mark_waiting(state: TaskState) -> dict:
    """校验结果就绪，进入待人工审核（WAITING_REVIEW 由本节点置，不在纯节点内写状态）。

    僵尸线程防覆盖（T4.3 review P1）：本节点是"写状态后即 interrupt 停驻"的节点，
    入口检查拦不住与外部判定（超时 FAILED）的节点内竞态——用条件更新（CAS）
    WHERE status NOT IN 终态，外部终态已落库则 rowcount=0，抛异常终止图（不写 checkpoint）。
    """
    task_id = state["task_id"]
    with SessionLocal() as db:
        task = db.get(CheckTask, task_id)
        _ensure_runnable(db, task, task_id)
        res = db.execute(
            update(CheckTask)
            .where(CheckTask.id == task_id, CheckTask.status.not_in(_TERMINAL_STATUSES))
            .values(status=TaskStatus.WAITING_REVIEW.value, progress=100)
        )
        db.commit()
        if res.rowcount == 0:
            raise RuntimeError(f"任务 {task_id} 已被外部标记终态，跳过待审核")
    return {}


def await_review(state: TaskState) -> dict:
    """纯节点：仅 interrupt，无副作用（resume 时从头重跑无影响）。"""
    decision = interrupt({"task_id": state["task_id"]})
    return {"reviews": decision}


def apply_reviews(state: TaskState) -> dict:
    """把人工审核结果写入 violation（确认/误报），resume 时执行。

    后端不信任前端载荷：action 仅接受 CONFIRMED/FALSE_POSITIVE，violation_id
    不可转换或不属于本任务则静默跳过（resume 失败不应搞崩任务）。
    """
    raw = state.get("reviews") or []
    if isinstance(raw, dict):        # resume 载荷可能是 {reviews:[...]} 包装
        raw = raw.get("reviews") or []
    valid_actions = {ViolationStatus.CONFIRMED.value, ViolationStatus.FALSE_POSITIVE.value}
    task_id = state["task_id"]
    with SessionLocal() as db:
        task = db.get(CheckTask, task_id)
        _ensure_runnable(db, task, task_id)
        for r in raw:
            action = r.get("action")
            if action not in valid_actions:
                continue
            try:
                vid = int(r["violation_id"])
            except (TypeError, ValueError):
                continue
            v = db.get(Violation, vid)
            if v is not None and v.task_id == task_id:
                v.status = action
                v.confirm_user = r.get("confirm_user")
                v.confirm_time = datetime.now()
        db.commit()
    return {}


def finalize(state: TaskState) -> dict:
    """终态由 violation 确认结果决定（修复：人工确认的异常不应 SUCCESS）。

    有人工确认（CONFIRMED）的异常 → FAILED（验证失败）；全部误报或零异常 → SUCCESS
    （验证通过）。resume 后 apply_reviews 已把本任务 UNCONFIRMED 全量转成
    CONFIRMED/FALSE_POSITIVE，此处按 CONFIRMED 存在与否定终态即可。
    终态 CAS（T4.3 review P1）：首次运行零 violation 也走本节点（build._should_wait
    done 分支），是僵尸线程复活终态（超时 FAILED → SUCCESS）的直接路径——条件更新
    WHERE status NOT IN 终态，外部终态已落库则 rowcount=0，不覆盖超时判错。
    """
    task_id = state["task_id"]
    with SessionLocal() as db:
        task = db.get(CheckTask, task_id)
        _ensure_runnable(db, task, task_id)
        has_confirmed = db.query(Violation.id).filter_by(
            task_id=task_id, status=ViolationStatus.CONFIRMED.value
        ).first()
        res = db.execute(
            update(CheckTask)
            .where(CheckTask.id == task_id, CheckTask.status.not_in(_TERMINAL_STATUSES))
            .values(
                status=TaskStatus.FAILED.value if has_confirmed else TaskStatus.SUCCESS.value,
                progress=100,
            )
        )
        db.commit()
        if res.rowcount == 0:
            raise RuntimeError(f"任务 {task_id} 已被外部标记终态，跳过定稿")
    return {}
