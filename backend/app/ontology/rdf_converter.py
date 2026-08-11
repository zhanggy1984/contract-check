"""抽取 JSON → RDF 实例（T1.4）。

在独立 World 中加载本体并创建个体（避免污染缓存本体、跨任务串扰），
按 schema 判定类型：date/boolean/number/string；对象属性建立 hasParty/hasItem/hasClause 链接。
输出 N-Triples 快照，供 Phase 2 SPARQL 校验。
"""
from datetime import date
from io import BytesIO
from pathlib import Path

import owlready2 as owl
import rdflib

from app.ontology.loader import ONTOLOGY_IRI, ONTOLOGY_PATH

# IRI 前缀：本示例直接沿用本体命名空间（真实本体可替换）
_NS = "http://example.org/contract#"


class JsonToRdfConverter:
    def __init__(self, schema: dict, path: Path | None = None):
        """schema：build_extraction_schema 产物；path：本体文件（默认示例本体）。"""
        self.schema = schema
        self.world = owl.World()
        g = rdflib.Graph().parse(str(path or ONTOLOGY_PATH), format="turtle")
        self.onto = self.world.get_ontology(ONTOLOGY_IRI)
        self.onto.load(fileobj=BytesIO(g.serialize(format="nt").encode()), format="ntriples")

    def _typed(self, pschema: dict, value):
        """按 schema 转 owlready2 期望的 Python 类型。空值返回 None（不建属性）。"""
        if value is None or value == "":
            return None
        if pschema.get("format") == "date":
            try:
                return date.fromisoformat(str(value))
            except ValueError:
                return None
        if pschema.get("type") == "boolean":
            return bool(value)
        if pschema.get("type") == "number":
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        return str(value)

    def _build(self, class_name: str, schema_node: dict, data: dict, ind_name: str):
        """递归构建个体：数据属性赋值 + 对象属性建子个体并链接。"""
        cls = getattr(self.onto, class_name)
        ind = cls(ind_name)
        for key, value in data.items():
            pschema = schema_node.get("properties", {}).get(key)
            if pschema is None:
                continue
            if pschema.get("type") == "array":
                range_cls = pschema["items"]["title"]
                children = []
                for i, item in enumerate(value or []):
                    child = self._build(range_cls, pschema["items"], item, f"{ind_name}_{key}_{i}")
                    children.append(child)
                if children:
                    setattr(ind, key, children)
            else:
                v = self._typed(pschema, value)
                if v is not None:
                    prop = getattr(self.onto, key)
                    if prop.is_functional_for(cls):
                        setattr(ind, key, v)
                    else:
                        setattr(ind, key, [v])  # 非 functional 需列表赋值
        return ind

    def convert(self, std_json: dict, task_id: int) -> str:
        """std_json → N-Triples 字符串。根为 Contract 个体。"""
        root_schema = self.schema
        self._build(root_schema["title"], root_schema, std_json, f"contract_{task_id}")
        return self.world.as_rdflib_graph().serialize(format="nt")
