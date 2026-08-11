"""OWL 本体约束 → SPARQL ASK 规则（T2.1）。

闭合世界语义（CWA）：抽取实例图本身即"全部事实"，本体约束
（必填 minCardinality / 枚举 oneOf / 数值 minInclusive / 格式 pattern）
转为 ASK 找反例，不引入推理器——OWL 开放世界假设与"校验抓缺失"语义相悖。

支持清单见 solution.md §6.4；不支持的构造不生成（fail-fast 由调用方负责）。
"""
import json
from pathlib import Path

import owlready2 as owl

from app.common.constants import RuleSource, RuleType, Severity
from app.ontology.loader import ONTOLOGY_IRI
from app.ontology.schema_mapper import (
    _domain_properties,
    _enum_values,
    _facet_minimum,
    _required_fields,
)

# 人工规则目录：backend/rules/manual/*.rq
MANUAL_RULES_DIR = Path(__file__).resolve().parents[2] / "rules" / "manual"

# 本体短名 → 大白话（合同业务法律术语）。规则名称面向业务用户展示，
# 本体 IRI 短名（Contract/clauseType…）用户看不懂，生成规则时统一替换。
CLASS_LABELS = {
    "Contract": "合同",
    "Party": "当事人",
    "ContractItem": "合同标的",
    "Clause": "合同条款",
}
PROP_LABELS = {
    # Contract 数据属性
    "contractTitle": "合同名称", "contractNo": "合同编号", "contractType": "合同类型",
    "signedDate": "签订日期", "effectiveDate": "生效日期", "terminationDate": "终止日期",
    "signingPlace": "签订地点", "totalAmount": "合同总金额", "currency": "币种",
    "status": "合同状态", "depositAmount": "保证金金额", "depositType": "保证金类型",
    "depositRefundCondition": "保证金退还条件", "taxRate": "税率", "taxInclusive": "是否含税",
    "invoiceType": "发票类型", "invoiceRequirements": "发票要求",
    # Party 数据属性
    "partyRole": "当事人角色", "partyName": "当事人名称",
    "unifiedSocialCreditCode": "统一社会信用代码", "legalRepresentative": "法定代表人",
    "address": "地址", "contact": "联系方式",
    # Clause 数据属性
    "clauseType": "条款类型", "clauseTitle": "条款标题", "clauseText": "条款内容",
    # ContractItem 数据属性
    "itemName": "标的名称", "quantity": "数量", "unitPrice": "单价", "itemAmount": "标的金额",
    # 对象属性
    "hasParty": "当事人", "hasItem": "合同标的", "hasClause": "合同条款",
}
# pattern 正则 → 大白话说明（无映射则回退显示正则原文）
PATTERN_HINTS = {
    "unifiedSocialCreditCode": "18位数字或大写字母",
}


def _cls_label(name: str) -> str:
    """类短名 → 大白话（未收录则回退短名，换真实本体时不崩）。"""
    return CLASS_LABELS.get(name, name)


def _prop_label(name: str) -> str:
    """属性短名 → 大白话（未收录则回退短名）。"""
    return PROP_LABELS.get(name, name)


def _fmt_num(v) -> str:
    """数值下限文案：整数显示整数（0.0 → 0）。"""
    return str(int(v)) if float(v).is_integer() else str(v)


def _iri(name: str) -> str:
    return f"<{ONTOLOGY_IRI}{name}>"


def _reachable_classes(onto) -> list:
    """Contract 及其经对象属性可达的全部类（每个类单独生成规则）。"""
    root = getattr(onto, "Contract")
    visited: set[str] = set()
    stack = [root]
    while stack:
        cls = stack.pop()
        if cls.name in visited:
            continue
        visited.add(cls.name)
        for prop in onto.properties():
            if isinstance(prop, owl.ObjectPropertyClass) and cls in prop.domain:
                stack.extend(prop.range)
    return [getattr(onto, n) for n in sorted(visited)]


def _sparql_required(cls_name: str, prop_name: str) -> str:
    """必填缺失：?s 是该类实例且无该属性值。"""
    return (
        f"ASK {{\n"
        f"  ?s a {_iri(cls_name)} .\n"
        f"  FILTER NOT EXISTS {{ ?s {_iri(prop_name)} ?v }}\n"
        f"}}"
    )


def _sparql_enum(cls_name: str, prop_name: str, values: list[str]) -> str:
    # 注意：rdflib 的 IN/NOT IN 走术语级比较，直接比较 ?v 对 xsd:string 字面量会误判，
    # 故统一 str(?v) 取词法形式再比（对语言标签/数据类型都健壮）。
    vals = ", ".join(f'"{v}"' for v in values)
    return (
        f"ASK {{\n"
        f"  ?s a {_iri(cls_name)} ; {_iri(prop_name)} ?v .\n"
        f"  FILTER (str(?v) NOT IN ({vals}))\n"
        f"}}"
    )


def _sparql_min(cls_name: str, prop_name: str, min_v: float) -> str:
    return (
        f"ASK {{\n"
        f"  ?s a {_iri(cls_name)} ; {_iri(prop_name)} ?v .\n"
        f"  FILTER (?v < {float(min_v)})\n"
        f"}}"
    )


def _sparql_pattern(cls_name: str, prop_name: str, pattern: str) -> str:
    return (
        f"ASK {{\n"
        f"  ?s a {_iri(cls_name)} ; {_iri(prop_name)} ?v .\n"
        f"  FILTER (!regex(str(?v), \"{pattern}\"))\n"
        f"}}"
    )


def _rule(cls_name: str, prop_name: str, kind: str, expression: str,
          severity: str, description: str) -> dict:
    return {
        "rule_iri": f"urn:rule:{kind}:{cls_name}.{prop_name}",
        "rule_name": description,
        "rule_type": RuleType.DETERMINISTIC.value,
        "severity": severity,
        "source": RuleSource.ONTOLOGY_GENERATED.value,
        "expression": expression,
        "description": description,
        "concept_iri": f"{ONTOLOGY_IRI}{cls_name}",
        "property_iri": f"{ONTOLOGY_IRI}{prop_name}",
    }


def generate_rules(onto) -> list[dict]:
    """从本体自动生成确定性规则（一条约束一条规则）。"""
    rules: list[dict] = []
    for cls in _reachable_classes(onto):
        required = _required_fields(cls)
        for prop in _domain_properties(onto, cls):
            pname = prop.name
            if isinstance(prop, owl.ObjectPropertyClass):
                # 对象属性必引用（minCardinality 1）
                if pname in required:
                    rules.append(_rule(
                        cls.name, pname, "required",
                        _sparql_required(cls.name, pname),
                        Severity.HIGH.value,
                        f"{_cls_label(cls.name)}必须至少包含一个{_cls_label(prop.range[0].name)}",
                    ))
                continue
            rng = prop.range[0] if prop.range else None
            if rng is None:
                continue
            # 必填缺失（数据属性）
            if pname in required:
                rules.append(_rule(
                    cls.name, pname, "required",
                    _sparql_required(cls.name, pname),
                    Severity.HIGH.value,
                    f"{_cls_label(cls.name)}缺少必填信息：{_prop_label(pname)}",
                ))
            # 枚举越界
            enums = _enum_values(rng)
            if enums:
                rules.append(_rule(
                    cls.name, pname, "enum",
                    _sparql_enum(cls.name, pname, enums),
                    Severity.MEDIUM.value,
                    f"{_prop_label(pname)}必须为以下之一：{'/'.join(enums)}",
                ))
                continue
            # 数值下限 / 格式
            min_v = _facet_minimum(rng)
            if min_v is not None:
                rules.append(_rule(
                    cls.name, pname, "min",
                    _sparql_min(cls.name, pname, min_v),
                    Severity.MEDIUM.value,
                    f"{_prop_label(pname)}不得小于 {_fmt_num(min_v)}",
                ))
            pattern = getattr(rng, "pattern", None)
            if pattern:
                rules.append(_rule(
                    cls.name, pname, "pattern",
                    _sparql_pattern(cls.name, pname, pattern),
                    Severity.MEDIUM.value,
                    f"{_prop_label(pname)}格式不符合要求（正确格式：{PATTERN_HINTS.get(pname, pattern)}）",
                ))
    return rules


def _split_header(text: str) -> tuple[list[str], str]:
    """拆分 .rq 文件：前导 `# key: value` 注释为元信息，其后为 SPARQL。"""
    header, body_lines = [], []
    in_header = True
    for line in text.splitlines():
        stripped = line.strip()
        if in_header and stripped.startswith("#"):
            header.append(stripped.lstrip("# ").strip())
        else:
            in_header = False
            body_lines.append(line)
    return header, "\n".join(body_lines).strip()


def load_manual_rules(directory: Path | None = None) -> list[dict]:
    """加载 rules/manual/*.rq（确定性 SPARQL）与 *.json（语义 prompt）人工规则。rule_iri 必填。"""
    base = directory or MANUAL_RULES_DIR
    rules: list[dict] = []
    for f in sorted(base.glob("*.rq")):
        header, body = _split_header(f.read_text(encoding="utf-8"))
        meta: dict[str, str] = {}
        for line in header:
            if ":" in line:
                key, _, val = line.partition(":")
                meta[key.strip()] = val.strip()
        rule_iri = meta.get("rule_iri") or f"urn:rule:manual:{f.stem}"
        rules.append({
            "rule_iri": rule_iri,
            "rule_name": meta.get("rule_name") or f.stem,
            "rule_type": RuleType.DETERMINISTIC.value,
            "severity": meta.get("severity") or Severity.HIGH.value,
            "source": RuleSource.MANUAL.value,
            "expression": body,
            "description": meta.get("description") or meta.get("rule_name") or f.stem,
            "concept_iri": meta.get("concept_iri"),
            "property_iri": meta.get("property_iri"),
        })
    for f in sorted(base.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        rule_iri = data.get("rule_iri")
        expression = data.get("expression")
        if not rule_iri or not expression:
            raise ValueError(f"语义规则文件缺少 rule_iri/expression: {f.name}")
        aggregation = data.get("aggregation") or "any"
        if aggregation not in ("any", "all"):
            raise ValueError(f"语义规则文件 aggregation 仅支持 any/all: {f.name}")
        rules.append({
            "rule_iri": rule_iri,
            "rule_name": data.get("rule_name") or f.stem,
            "rule_type": RuleType.SEMANTIC.value,
            "severity": data.get("severity") or Severity.HIGH.value,
            "source": RuleSource.MANUAL.value,
            "expression": expression,
            "aggregation": aggregation,
            "description": data.get("description") or data.get("rule_name") or f.stem,
            "concept_iri": data.get("concept_iri"),
            "property_iri": data.get("property_iri"),
        })
    return rules
