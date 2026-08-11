# -*- coding: utf-8 -*-
"""T1.1 探针 v2：确认 owlready2 0.51 的约束取数 API。"""
from io import BytesIO

import owlready2 as owl
import rdflib

g = rdflib.Graph().parse("D:/study/aiprojcet/contract-check/backend/ontology/contract_ontology.ttl", format="turtle")
onto = owl.get_ontology("http://example.org/contract#")
onto.load(fileobj=BytesIO(g.serialize(format="nt").encode()), format="ntriples")


def is_data(p):
    return isinstance(p, owl.DataPropertyClass)


def is_obj(p):
    return isinstance(p, owl.ObjectPropertyClass)


def restri(cls):
    """类上的 OWL 限制（必填/基数）。"""
    out = []
    for sup in cls.is_a:
        if isinstance(sup, owl.Restriction):
            r = sup
            out.append((r.property.name, r.type, r.cardinality))
    return out


print("== Contract 限制 ==")
for x in restri(onto.Contract):
    print("  ", x)

print("\n== 各核心类的属性 ==")
for cname in ["Contract", "Party", "Clause", "ContractItem"]:
    cls = getattr(onto, cname)
    print("  %s:" % cname)
    for p in cls.get_class_properties():
        print("      %-28s data=%s obj=%s range=%s" % (p.name, is_data(p), is_obj(p), p.range))

print("\n== 枚举 range oneOf ==")
for pname in ["contractType", "currency", "partyRole", "clauseType", "depositType", "invoiceType"]:
    rng = getattr(onto, pname).range
    one_of = list(rng.oneOf) if getattr(rng, "oneOf", None) else None
    print("  %-14s -> %s | oneOf=%s" % (pname, rng, one_of))

print("\n== 数值约束（NonNegativeDecimal / totalAmount） ==")
for rng in [onto.totalAmount.range]:
    print("  range=%r type=%s" % (rng, type(rng).__name__))
    print("    is_a:", [repr(x) for x in rng.is_a])
    print("    oneOf:", getattr(rng, "oneOf", None))

print("\n== 匿名 pattern range（unifiedSocialCreditCode） ==")
rng = onto.unifiedSocialCreditCode.range
print("  range=%r type=%s" % (rng, type(rng).__name__))
print("    is_a:", [repr(x) for x in rng.is_a])
print("    on_datatype:", getattr(rng, "on_datatype", "n/a"))
print("    restrictions:", getattr(rng, "restrictions", "n/a"))

print("\n== 对象属性 ==")
for p in onto.object_properties():
    print("  ", p.name, "domain=", p.domain, "range=", p.range)
