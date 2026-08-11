"""本体约束的 owlready2 访问常量。

owlready2 0.51 的 C 优化解析器对 owl:Restriction 的 type 使用自有 int 编码
（与 Python 侧 _universal_abbrev 常量不同），经 T1.1 探针经验验证：
    minCardinality=27, maxCardinality=28, cardinality=26, someValuesFrom=24
"""
# 限制类型 int 编码
MIN_CARDINALITY = 27
MAX_CARDINALITY = 28
EXACT_CARDINALITY = 26
SOME_VALUES_FROM = 24
