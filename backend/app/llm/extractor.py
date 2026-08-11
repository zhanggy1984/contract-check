"""DeepSeek 合同抽取器（T1.3）。

- 按 OWL 生成 schema → 动态构建 Pydantic 模型（严格/宽松两套）
- json_object 输出，解析 + Pydantic 校验；失败携带错误信息重试
- finish_reason=length 截断 → 按段降级重抽并合并（同名 Party 去重）
- 返回 ExtractionResult（std_json + extraction_status + segments）
"""
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Optional, get_args

from pydantic import BaseModel, Field, ValidationError, create_model

from app.common.constants import ExtractionStatus
from app.llm.llm_client import call_json
from app.ontology.loader import load_ontology
from app.ontology.schema_mapper import build_extraction_schema

# 超过此字符数走分段抽取（DeepSeek 输出窗口安全余量）
SEGMENT_CHAR_LIMIT = 20000
# 单段校验/解析最多重试次数
MAX_ATTEMPTS = 3

SYSTEM_PROMPT = (
    "你是资深合同审查专家，负责把中文合同文本抽取为结构化 JSON。\n"
    "抽取原则：\n"
    "1. 严格依据给定 JSON Schema 输出，只输出 JSON 本身，不要任何解释或前后缀。\n"
    "2. 字段值必须取自原文；原文未出现的字段一律留空或省略（不要编造）。\n"
    "   即使是必填字段，原文缺失也留空——宁可抽取结果不完整，也不要编造合理值、默认值或\n"
    "   凑数；字段缺失或异常由后续校验规则自动发现，你只负责如实抽取原文出现的内容。\n"
    "3. 枚举字段必须使用给定的枚举值之一；日期统一 YYYY-MM-DD；金额为数字。\n"
    "4. 条款原文 clauseText 必须与合同原文逐字一致，不得改写。\n"
    "5. 百分数转小数：如“税率13%”应抽取为 0.13。\n"
    "6. 金额单位统一为“元”，原文“万元”需换算为“元”。"
)


def _literal_type(values: list[str]) -> type:
    if len(values) == 1:
        return Literal[values[0]]
    return Literal[tuple(values)]  # type: ignore[arg-type]


def _field_type(schema: dict[str, Any], model_cache: dict, name: str, model_name: str):
    """叶子 schema 节点 → Python 类型注解（object/array 由 _annot 递归处理）。"""
    if "enum" in schema:
        return _literal_type(schema["enum"])
    t = schema.get("type")
    if t == "boolean":
        return bool
    if t == "number":
        return float
    if schema.get("format") == "date":
        return date
    return str


def _field_kwargs(schema: dict[str, Any]) -> dict:
    """约束（minimum/pattern）→ pydantic Field 参数。"""
    kwargs: dict[str, Any] = {}
    if schema.get("minimum") is not None:
        kwargs["ge"] = schema["minimum"]
    if schema.get("maximum") is not None:
        kwargs["le"] = schema["maximum"]
    if schema.get("pattern"):
        kwargs["pattern"] = schema["pattern"]
    if schema.get("description"):
        kwargs["description"] = schema["description"]
    return kwargs


def _annot(
    pname: str, pschema: dict[str, Any], model_cache: dict,
    required: bool, strict: bool,
) -> tuple[type, Field]:
    """单字段 → (类型注解, Field)。后序递归：先构建嵌套子模型。"""
    t = pschema.get("type")
    if t == "array" and pschema.get("items", {}).get("type") == "object":
        base = list[_model_for(pschema["items"], model_cache, strict, name_hint=f"{pname}Item")]
    elif t == "object":
        base = _model_for(pschema, model_cache, strict, name_hint=pname)
    else:
        base = _field_type(pschema, model_cache, pname, "")
    kwargs = _field_kwargs(pschema)
    if required and strict:
        return base, Field(**kwargs)          # 无默认值 → 必填
    return Optional[base], Field(default=None, **kwargs)


def _model_for(
    schema: dict[str, Any], model_cache: dict, strict: bool = True, name_hint: str = "Model"
) -> type[BaseModel]:
    """object schema → 动态 Pydantic 模型（后序构建，按 title 缓存）。"""
    name = schema.get("title") or name_hint
    if name in model_cache:
        return model_cache[name]
    required = set(schema.get("required", []))
    fields: dict[str, Any] = {}
    for pname, pschema in schema.get("properties", {}).items():
        fields[pname] = _annot(pname, pschema, model_cache, pname in required, strict)
    model = create_model(name, **fields)
    model_cache[name] = model
    return model


def build_model(schema: dict[str, Any], strict: bool = True) -> type[BaseModel]:
    """从抽取 schema 构建 Pydantic 模型（strict=False 时必填降为可选，用于分段抽取）。"""
    return _model_for(schema, {}, strict=strict)


def _type_desc(pschema: dict[str, Any]) -> str:
    """字段类型说明（枚举/格式/数值下限/pattern）。"""
    t = pschema.get("type", "string")
    parts = [t]
    if "enum" in pschema:
        parts.append("枚举[" + "/".join(pschema["enum"]) + "]")
    if "format" in pschema:
        parts.append("日期(YYYY-MM-DD)")
    if "minimum" in pschema:
        parts.append("≥%s" % pschema["minimum"])
    if "pattern" in pschema:
        parts.append("pattern:%s" % pschema["pattern"])
    return " ".join(parts)


def _compact_schema(schema: dict[str, Any], indent: int = 0) -> str:
    """schema → 分层可读的字段说明（* 必填；数组展开每项字段）。"""
    lines = []
    pad = "  " * indent
    for pname, pschema in schema.get("properties", {}).items():
        req = "*" if pname in schema.get("required", []) else ""
        if pschema.get("type") == "array":
            items = pschema["items"]
            reqs = ",".join(items.get("required", []))
            lines.append(f"{pad}{pname}{req}: 数组，每项含必填[{reqs}]：")
            lines.append(_compact_schema(items, indent + 1))
        else:
            lines.append(f"{pad}{pname}{req}: {_type_desc(pschema)}")
    return "\n".join(lines)


def _build_prompt(text: str, schema: dict[str, Any], feedback: str | None = None) -> str:
    """用户提示：schema 字段说明 + 合同原文 + 上一次失败反馈。"""
    parts = [f"抽取目标 JSON Schema 字段说明（* 为必填）：\n{_compact_schema(schema)}"]
    if feedback:
        parts.append(f"\n上一次输出被拒绝，原因：{feedback}\n请修正后重新只输出 JSON。")
    parts.append(f"\n合同原文如下：\n{text}")
    return "\n".join(parts)


@dataclass
class SingleResult:
    obj: dict[str, Any] | None
    truncated: bool
    attempts: int
    error: str | None = None


def _drop_at(data: Any, schema: dict[str, Any], loc: tuple) -> bool:
    """按 pydantic loc 删除 data 值；必填字段拒绝删除。返回是否删除。"""
    if not loc:
        return False
    key = loc[0]
    if isinstance(key, int):
        if not isinstance(data, list) or key >= len(data):
            return False
        return _drop_at(data[key], schema.get("items", schema), loc[1:])
    if not isinstance(data, dict) or key not in data:
        return False
    node = schema.get("properties", {}).get(key, {})
    if len(loc) == 1:
        if key in set(schema.get("required", [])):
            return False
        del data[key]
        return True
    return _drop_at(data[key], node, loc[1:])


def _blank_to_missing(data: dict, schema: dict) -> None:
    """必填字段空串视为缺失（del 键触发 Pydantic 必填错误，进入重试）。

    只处理空串：空列表/None 维持现状（None 由 Pydantic 拦，空列表是 hasParty
    允许空数组的既有设计，test_defensive_paths 依赖）。
    """
    for key in set(schema.get("required", [])):
        if data.get(key) == "":
            del data[key]
    for key, pschema in (schema.get("properties") or {}).items():
        v = data.get(key)
        if v is None:
            continue
        if pschema.get("type") == "array" and isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    _blank_to_missing(item, pschema.get("items") or {})
        elif pschema.get("type") == "object" and isinstance(v, dict):
            _blank_to_missing(v, pschema)


def _validate(data: dict, model: type[BaseModel], schema: dict[str, Any]) -> tuple[dict | None, list]:
    """校验：可选字段非法值自动剔除；仅返回必填字段错误（供重试反馈）。

    必填空串先视为缺失（_blank_to_missing）：否则 Pydantic 把 "" 当"有值"放行，
    空串既不触发重试、RDF 端又转 None 不建三元组 → required 规则误判 FAIL（good.pdf 根因）。
    """
    _blank_to_missing(data, schema)
    while True:
        try:
            return model.model_validate(data).model_dump(mode="json"), []
        except ValidationError as e:
            removed = False
            required_errs = []
            for err in e.errors():
                if _drop_at(data, schema, err["loc"]):
                    removed = True
                else:
                    required_errs.append(err)
            if not removed:
                return None, required_errs


def _feedback_of(errs: list) -> str:
    """pydantic 错误列表 → 精简反馈（前 5 条，含字段与错误类型）。"""
    msgs = []
    for err in errs[:5]:
        loc = ".".join(str(x) for x in err["loc"]) or "(root)"
        msgs.append(f"{loc}: {err['msg']} (type={err['type']})")
    return "；".join(msgs)


def _single(text: str, model: type[BaseModel], schema: dict[str, Any]) -> SingleResult:
    """单次抽取（含校验失败重试）。截断返回 truncated；重试耗尽返回最佳 data + error（由上层判 INCOMPLETE）。"""
    feedback = None
    last_data: dict[str, Any] | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        content, finish_reason, _usage = call_json(SYSTEM_PROMPT, _build_prompt(text, schema, feedback))
        if finish_reason == "length":
            return SingleResult(None, True, attempt)  # 截断 → 分段降级
        if not content or not content.strip():
            feedback = "模型返回空内容，请重新输出 JSON"
            continue
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            feedback = f"输出不是合法 JSON（{e}），请只输出 JSON 对象"
            continue
        valid, errs = _validate(data, model, schema)
        if valid is not None:
            return SingleResult(valid, False, attempt)
        last_data = data  # 保留最佳部分结果（已剔除可选非法值）
        feedback = _feedback_of(errs) or "输出不符合抽取 schema，请修正后重新输出 JSON"
        continue
    return SingleResult(last_data, False, MAX_ATTEMPTS, error=feedback)


def _split_text(text: str, limit: int = SEGMENT_CHAR_LIMIT) -> list[str]:
    """按段落切分，每个片段 ≤ limit（优先在段落边界断开）。"""
    text = text.strip()
    if len(text) <= limit:
        return [text]
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    chunks, cur = [], ""
    for p in paragraphs:
        if len(p) > limit:  # 超长单段按字符硬切（先 flush 已累积的 cur，避免丢段）
            if cur:
                chunks.append(cur)
                cur = ""
            for i in range(0, len(p), limit):
                chunks.append(p[i:i + limit])
            continue
        if cur and len(cur) + len(p) + 1 > limit:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks


_PARTY_ROLES = {"甲方", "乙方", "丙方"}


def _party_placeholder(name: str | None, role: str | None) -> bool:
    """partyName 是否只是角色占位（无真实名称，如 role=甲方 时 name 也是"甲方"）。"""
    n = (name or "").strip()
    return (not n) or (n in _PARTY_ROLES) or (n == (role or "").strip())


def _merge_parties(items: list[dict]) -> list[dict]:
    """按 partyRole 归并 party 列表（A5：分段抽取同名/同角色 party 去重，消除重复个体）。

    同一角色只保留一个个体；partyName 优先实体名（非角色占位，如"甲公司"胜于"甲方"），
    其余字段（信用代码等）非空者优先补全。
    """
    by_role: dict[str, dict] = {}
    for item in items or []:
        role = (item.get("partyRole") or "").strip()
        if not role:
            continue
        cur = by_role.get(role)
        if cur is None:
            by_role[role] = dict(item)
            continue
        merged = dict(cur)
        for k, v in item.items():
            if not v:
                continue
            if k == "partyName":
                if _party_placeholder(v, role):
                    continue
                cur_name = merged.get("partyName")
                if _party_placeholder(cur_name, role) or len(str(v)) > len(str(cur_name or "")):
                    merged["partyName"] = v
            elif not merged.get(k):
                merged[k] = v
        by_role[role] = merged
    return list(by_role.values())


def _merge_contracts(parts: list[dict[str, Any]], schema: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """分段抽取结果合并：标量取首个非空；数组拼接并去重（hasParty 按角色归并）。

    返回 (merged, conflicts)：跨段同一标量字段（如 totalAmount）值不一致 → 记入 conflicts
    （A5 验收：金额冲突标 low-confidence，供人工复核，抽取端不替 LLM 选值）。
    """
    merged: dict[str, Any] = {}
    conflicts: list[str] = []
    array_props = {n for n, p in schema.get("properties", {}).items() if p.get("type") == "array"}
    for part in parts:
        for key, value in part.items():
            if key in array_props:
                merged.setdefault(key, [])
                if key == "hasParty":
                    merged[key] = _merge_parties(merged[key] + list(value or []))
                else:
                    for item in value or []:
                        if item not in merged[key]:  # 结构去重
                            merged[key].append(item)
            elif value is not None:
                if key in merged:
                    if merged[key] != value and key not in conflicts:
                        conflicts.append(key)
                else:
                    merged[key] = value
    return merged, conflicts


@dataclass
class ExtractionResult:
    std_json: dict[str, Any] | None
    status: str                       # COMPLETE / INCOMPLETE / FAILED
    segments: list[str] = field(default_factory=list)
    truncated: bool = False           # 是否发生过截断降级
    error: str | None = None
    conflicts: list[str] = field(default_factory=list)  # 分段抽取跨段值冲突的字段（标 low-confidence）


# D1：空/极短文本不抽取（防 LLM 对空文本编造合同），直接 FAILED
MIN_TEXT_CHARS = 10


def extract_contract(text: str, schema: dict[str, Any] | None = None) -> ExtractionResult:
    """合同全文抽取。入口：整篇或分段，返回标准文本 JSON + 抽取状态。"""
    schema = schema or build_extraction_schema(load_ontology())
    stripped = text.strip()
    if len(stripped) < MIN_TEXT_CHARS:
        return ExtractionResult(None, ExtractionStatus.FAILED.value, [text], False,
                                "合同文本为空或过短，无法抽取")
    full_model = build_model(schema, strict=True)
    segments = [stripped] if len(stripped) <= SEGMENT_CHAR_LIMIT else _split_text(text)
    if len(segments) == 1:
        r = _single(text, full_model, schema)
        if r.truncated:
            return extract_contract_segmented(text, schema, segments, full_model)
        if r.obj is None:
            return ExtractionResult(None, ExtractionStatus.FAILED.value, [text], False, r.error)
        if r.error is None:
            return ExtractionResult(r.obj, ExtractionStatus.COMPLETE.value, [text], False)
        # 有部分数据但校验不完整 → INCOMPLETE（T1.5：跳过确定性校验，不进假阳性洪水）
        return ExtractionResult(r.obj, ExtractionStatus.INCOMPLETE.value, [text], False, r.error)
    return extract_contract_segmented(text, schema, segments, full_model)


def extract_contract_segmented(
    text: str, schema: dict[str, Any], segments: list[str], full_model: type[BaseModel]
) -> ExtractionResult:
    """分段抽取：逐段宽松抽取 → 合并 → 严格校验。"""
    partial_model = build_model(schema, strict=False)
    parts: list[dict[str, Any]] = []
    any_truncated = False
    for seg in segments:
        r = _single(seg, partial_model, schema)
        if r.truncated:
            any_truncated = True
            # 段仍超窗 → 进一步二分递归
            half = _split_text(seg)
            for sub in half:
                sub_r = _single(sub, partial_model, schema)
                if sub_r.obj:
                    parts.append(sub_r.obj)
                any_truncated = any_truncated or sub_r.truncated
        elif r.obj is not None:
            parts.append(r.obj)
    if not parts:
        return ExtractionResult(None, ExtractionStatus.FAILED.value, segments, any_truncated, "全部分段抽取失败")
    merged, conflicts = _merge_contracts(parts, schema)
    _blank_to_missing(merged, schema)   # 分段合并结果的必填空串同样暴露为缺失
    try:
        valid = full_model.model_validate(merged)
        return ExtractionResult(valid.model_dump(mode="json"), ExtractionStatus.COMPLETE.value,
                                segments, any_truncated, conflicts=conflicts)
    except ValidationError:
        # 合并后仍缺必填 → INCOMPLETE（T1.5 语义：跳过确定性校验）
        return ExtractionResult(merged, ExtractionStatus.INCOMPLETE.value, segments, any_truncated,
                                conflicts=conflicts)
