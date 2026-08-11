"""OWL 本体 → LLM 抽取用 JSON Schema（T1.2）。

读取 owlready2 已加载本体，将 Contract 类层级映射为抽取 schema：
- 必填：类上 minCardinality 限制
- 枚举：属性 range 的 owl:oneOf → enum
- 数值范围/格式：ConstrainedDatatype 的 min_inclusive → minimum、pattern → pattern
- 对象属性：range 类递归生成嵌套 array（item schema）
"""
from datetime import date
from decimal import Decimal

import owlready2 as owl

from app.ontology.constraints import MIN_CARDINALITY

# 抽取根类（本体中合同的概念）
ROOT_CLASS = "Contract"

# B1/B2/B3 修复（根因：strict schema 强制 LLM 填必填字段 → 编造合理值，规则永远够不到异常）：
# 这些关键字段在抽取端放宽必填，原文缺失就让 LLM 留空，缺失/非法由确定性 required/min 规则抓，
# 不再靠 LLM 猜值（B1 编造生效日、B2 编造 0.0、B3 枚举映射）。
OPTIONAL_OVERRIDE = {"effectiveDate", "totalAmount", "contractType"}


def _prop_name(prop) -> str:
    return prop.name


def _required_fields(cls) -> set[str]:
    """类上 minCardinality 限制对应的属性名（必填）。"""
    req = set()
    for sup in cls.is_a:
        if isinstance(sup, owl.Restriction) and sup.type == MIN_CARDINALITY:
            req.add(sup.property.name)
    return req


def _facet_minimum(rng) -> float | None:
    """数值下限（minInclusive）。"""
    return getattr(rng, "min_inclusive", None)


def _enum_values(rng) -> list[str] | None:
    """枚举取值（owl:oneOf）。"""
    if isinstance(rng, owl.OneOf):
        return [str(v) for v in rng.instances]
    return None


def _datatype_schema(rng) -> dict:
    """数据类型属性 → JSON Schema 片段。

    只带类型/枚举/日期，不带数值下限与 pattern：这些是校验约束，归确定性规则层
    （min/pattern 规则）抓，若在抽取端强制会逼 LLM 编造合法值掩盖异常（B2 根因）。
    """
    # owlready2 将 xsd 类型映射为 Python 类型
    if isinstance(rng, owl.OneOf):                      # 枚举
        return {"type": "string", "enum": _enum_values(rng)}
    if isinstance(rng, owl.ConstrainedDatatype):        # 受限 datatype：只取其基础类型
        return _base_schema(rng.base_datatype)
    return _base_schema(rng)


def _base_schema(base) -> dict:
    """Python 类型（owlready2 映射后的 xsd 类型）→ 基础 JSON 类型。"""
    if base is bool:
        return {"type": "boolean"}
    if base is int or base is float or base is Decimal:
        return {"type": "number"}
    if base is date:
        return {"type": "string", "format": "date"}
    return {"type": "string"}


def _domain_properties(onto, cls):
    """该类的全部属性（按 domain 过滤，绕过 get_class_properties 只返回有限制属性的限制）。"""
    for prop in list(onto.properties()):
        if cls in prop.domain:
            yield prop


def build_class_schema(onto, cls) -> dict:
    """单个类的 JSON Schema（含嵌套对象属性）。"""
    required = _required_fields(cls) - OPTIONAL_OVERRIDE
    properties = {}
    for prop in _domain_properties(onto, cls):
        if isinstance(prop, owl.ObjectPropertyClass):
            range_cls = prop.range[0]
            properties[_prop_name(prop)] = {
                "type": "array",
                "items": build_class_schema(onto, range_cls),
            }
        elif isinstance(prop, owl.DataPropertyClass):
            properties[_prop_name(prop)] = _datatype_schema(prop.range[0])
        # 其余（annotation 等）跳过
    node = {"type": "object", "title": cls.name, "properties": properties}
    if required:
        node["required"] = sorted(required)
    return node


def build_extraction_schema(onto, root: str = ROOT_CLASS) -> dict:
    """生成抽取 JSON Schema（根为 Contract）。"""
    cls = getattr(onto, root)
    node = build_class_schema(onto, cls)
    node["title"] = root
    return node
