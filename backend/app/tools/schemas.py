"""工具 schema 唯一真源（OpenAI function calling 格式）。

两类工具，命名空间物理隔离：
- 能力工具（4 个）：extract_contract / evaluate_semantic / ocr_pdf / run_sparql —— 由图节点经 registry 确定性调用，
  只作为统一契约描述；不 bind 给 LLM（防 LLM 触发昂贵能力造成循环/失控）。
- 决策工具（2 个）：decide_ocr / decide_extract_retry —— bind 给 LLM 做受约束决策，executor 是薄壳，
  真实决策逻辑在 app/graph/decisions.py（确定性否决权 + 兜底）。

schema 结构：{type:"function", function:{name, description, parameters{properties, required, additionalProperties}}}。
"""

# ---- 能力工具（节点确定性调用）----

EXTRACT_CONTRACT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "extract_contract",
        "description": "把合同原文抽取为结构化 JSON。返回 {status: COMPLETE|INCOMPLETE|FAILED, std_json, "
                       "segments, truncated, error, conflicts, token_usage}。",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "合同原文全文"},
                "schema": {"type": "object", "description": "抽取目标 JSON Schema（可选，缺省从本体生成）"},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
}

EVALUATE_SEMANTIC_SCHEMA = {
    "type": "function",
    "function": {
        "name": "evaluate_semantic",
        "description": "按段批跑语义规则，逐条判定合同原文片段。返回 {outcomes: [{rule_id, result, message, "
                       "evidence_text, segment_ref, confidence}], usage}。",
        "parameters": {
            "type": "object",
            "properties": {
                "segments": {"type": "array", "description": "合同分段 [{index, title, content}]"},
                "rules": {"type": "array", "description": "语义规则 [{id, rule_iri, rule_name, expression, aggregation}]"},
            },
            "required": ["segments", "rules"],
            "additionalProperties": False,
        },
    },
}

OCR_PDF_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ocr_pdf",
        "description": "扫描型 PDF 指定页 OCR 产出文本。返回 {pages: {页索引: 文本}}。PDF 存在扫描页时按页调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "pdf_path": {"type": "string", "description": "PDF 存储路径"},
                "pages": {"type": "array", "items": {"type": "integer"},
                          "description": "待 OCR 的页索引列表；缺省 = 全部页"},
            },
            "required": ["pdf_path"],
            "additionalProperties": False,
        },
    },
}

RUN_SPARQL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_sparql",
        "description": "对抽取 RDF 图执行单条确定性校验规则。返回 {passed, subjects, rule_snapshot}。",
        "parameters": {
            "type": "object",
            "properties": {
                "graph": {"type": "object", "description": "rdflib 图对象"},
                "rule": {"type": "object", "description": "确定性规则对象"},
            },
            "required": ["graph", "rule"],
            "additionalProperties": False,
        },
    },
}

# ---- 决策工具（LLM 受约束决策，bind_tools 用）----

DECIDE_OCR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "decide_ocr",
        "description": "判断扫描型 PDF 是否值得执行 OCR（OCR 分钟级、可能超时）。action=ocr 表示值得执行；"
                       "action=skip 表示文本层已可读或质量过低不值得 OCR（下游将快速失败）。"
                       "仅当文件被标记为扫描件（has_scanned）且当前无可用文本时才需要调用。"
                       "action 必须严格等于 ocr 或 skip，不要附加标点、空格、引号或大小写变体，"
                       "否则视为无效决策；reason 简明客观，只写判断依据，不超过 200 字。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["ocr", "skip"]},
                "reason": {"type": "string", "maxLength": 200,
                           "description": "判断依据（文件信号：页数/图片数/文本长度等）"},
            },
            "required": ["action", "reason"],
            "additionalProperties": False,
        },
    },
}

DECIDE_EXTRACT_RETRY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "decide_extract_retry",
        "description": "合同抽取失败后决定是否重试一次还是放弃。action=retry 表示值得用同一文本重抽一次；"
                       "action=fail 表示放弃（任务将标记失败）。仅当抽取返回 FAILED 时调用。"
                       "action 必须严格等于 retry 或 fail，不要附加标点、空格、引号或大小写变体，"
                       "否则视为无效决策；reason 简明客观，只写判断依据，不超过 200 字。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["retry", "fail"]},
                "reason": {"type": "string", "maxLength": 200,
                           "description": "判断依据（失败原因分类 + 文本统计）"},
            },
            "required": ["action", "reason"],
            "additionalProperties": False,
        },
    },
}

# 全部工具 schema（registry 构建 + 单测断言用）
ALL_TOOL_SCHEMAS = (
    EXTRACT_CONTRACT_SCHEMA,
    EVALUATE_SEMANTIC_SCHEMA,
    OCR_PDF_SCHEMA,
    RUN_SPARQL_SCHEMA,
    DECIDE_OCR_SCHEMA,
    DECIDE_EXTRACT_RETRY_SCHEMA,
)

# 决策工具（LLM 可调）名称白名单——registry.schemas(decision_names) 用
DECISION_TOOL_NAMES = ("decide_ocr", "decide_extract_retry")
