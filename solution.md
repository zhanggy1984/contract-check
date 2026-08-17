# AI 合同校验系统 · 解决方案

> 状态：方案（审核修正版），待评审
> 技术底座：**Python + LangGraph + FastAPI + MySQL + Vue3**（非 Java）
> 人工审核：**LangGraph 官方 human-in-the-loop（interrupt + resume）**

---

## 1. 项目背景与目标

用户上传合同文件（PDF / Word / 扫描件），系统通过**本体建模 + LLM 抽取**将合同内容抽取为**标准化数据**（RDF 实例 + 标准文本 JSON），再按**本体中定义的规则**进行混合校验；**每条规则的结果（成功/失败/跳过）全量落库 MySQL**，其中失败生成异常（violation）进入**人工审核闭环**；最终将抽取结果与校验明细返回 **Web 页面**展示。Web 页面包含：**合同上传与验证（含人工审核）**、**历史合同处理结果查看**、**规则管理**，并支持校验报告导出（PDF / Excel）。

### 已确认的约束（不可更改）

| 维度 | 决定 |
|---|---|
| 技术栈 | **Python + LangGraph**（不用 Java） |
| LLM | **DeepSeek**（OpenAI 兼容，`langchain-openai` 接入） |
| 本体 | **OWL/RDF 标准本体**，项目自建示例合同本体，后续可替换真实本体 |
| 校验 | **混合方式** = 字段级确定性校验 + 条款级语义校验（**硬校验 + low-confidence 标记**） |
| 文件 | 含扫描件 / 图片型 PDF，需 OCR |
| 存储 | **校验结果全量落库 MySQL（含成功）**，返回 Web |
| 处理 | 异步（FastAPI + asyncio + 后台任务，不上 MQ） |
| 人工审核 | **每次校验任务都中断**（官方 `interrupt()`），人工逐条确认 / 误报后 `resume` 续跑 |
| 数据安全 | **合同安全第一**：文件仅本地处理，不发送第三方解析服务；单用户内网使用假设（后续接入认证） |

---

## 2. 核心概念

- **本体（Ontology）**：合同领域的 OWL/RDF 模型，定义概念（`Contract`、`Party`、`Clause`）、属性（`hasParty`、`effectiveDate`）、取值约束（必填、枚举、格式、数值范围）。本体是「抽取 schema 的来源」和「校验规则的真实来源」。
- **标准文本（Standard Text）**：合同原文经 LLM 按本体约束抽取后得到的**结构化结果**——同时输出 **RDF 实例**（供确定性校验与审计快照）、**标准文本 JSON**（供前端展示）与**文本分段 segments**（供语义校验 / 规则 dry-run / 报告证据复用）。
- **校验规则（Rule）**：来源于本体约束（自动生成确定性规则）+ 人工规则（SPARQL / 自然语言语义规则）。
- **异常（Violation）**：校验不满足时产生的统一结构，包含规则、严重级别、涉及的字段、**原文证据**、期望值/实际值，经人工审核确认或标记误报。
- **校验明细（RuleCheckResult）**：每条规则对每份合同的执行结果（PASS / FAIL / SKIPPED），**成功也落库**，供历史页面查看完整校验情况。

---

## 3. 总体数据流

```
上传(PDF/DOCX/图片)
  → 文件解析（有文本层直接提取；无文本层标记 → PaddleOCR）
  → LangGraph 编排：
      parse → extract(LLM 抽取 → RDF实例 + 标准文本JSON + 文本分段)
            → validate_deterministic(SPARQL 约束校验)
            → validate_semantic(LLM 语义校验，带原文证据)
            → persist_results(校验结果全量落库，含 PASS)
            → await_human_review(interrupt 暂停，等人工审核)
            → resume → apply_reviews → finalize
  → 前端轮询任务状态 → WAITING_REVIEW 展示异常 → 人工确认/误报 → 提交续跑 → SUCCESS
```

一切以校验任务 `check_task` 为中枢。

---

## 4. 技术选型

| 域 | 选型 | 说明 |
|---|---|---|
| 语言/运行时 | Python 3.11+，venv / uv | Windows 本机开发 |
| Agent 编排 | **LangGraph** | 线性状态图 + `interrupt()` 人工审核；不用 LCEL 自写状态机 |
| 图持久化 | **`langgraph-checkpoint-mysql`（社区维护，须锁版本）** | `thread_id = task-{task_id}`；实施前先做 HITL spike 验证版本兼容；降级备选 SQLite checkpointer |
| LLM | `langchain-openai` + DeepSeek | `base_url=https://api.deepseek.com`，`model=deepseek-chat`，**`max_tokens=8192`**；结构化输出用 `with_structured_output(method="json_mode")`（DeepSeek 不支持 json_schema），prompt 模板固定含 "JSON" 字样（json_object 模式前置要求）；Pydantic 校验 + 失败重试 + 空 content 兜底 |
| Web 后端 | FastAPI + uvicorn | 异步原生，自动 OpenAPI 文档 |
| 本体 | **owlready2** | 读 OWL、遍历概念/约束生成 schema、建 RDF 实例；SPARQL 校验用 `as_rdflib_graph()`（rdflib 为传递依赖，SPARQL 前缀需显式绑定，**rdflib 版本在 requirements 显式钉死**） |
| PDF | **PyMuPDF (fitz)** 为主 | 有文本层直接提；无文本层标记需 OCR；复杂表格版式再引入 pdfplumber |
| Word | python-docx | 仅 `.docx`；旧版 `.doc` 明确拒绝并提示 |
| OCR | **PaddleOCR 3.x**（`OcrService` 接口可插拔） | Windows 可用；3.x 用 `predict()` 且需 `lang='ch'`，paddlepaddle/paddleocr 版本须配对 |
| DB | SQLAlchemy 2.0 + PyMySQL | MySQL 8.0 |
| 前端 | Vue3 + Vite + Element Plus + Axios | 轮询任务状态（无需 WebSocket） |
| 报告导出 | PDF: reportlab（注册 Windows 中文字体）；Excel: openpyxl | 校验报告下载 |
| 配置 | 环境变量（`DEEPSEEK_API_KEY`、MySQL 连接串） | 不引入配置中心 |

**不使用**：Spring Boot / MQ / Redis / 自写任务状态机（图运行状态交给 checkpointer，业务表只做查询视图）。

**数据安全**：合同文件与文本仅存本地（磁盘 + MySQL），**不发送任何第三方解析服务**（含 MinerU 等外部 API）；DeepSeek 抽取属 API 调用，需知悉数据出境并按合规评估。当前为单用户内网使用假设，后续如需多人使用须接入认证与访问控制。文件上传设大小上限（如 50MB）、OCR 中间图与临时文件随任务结束清理、`sha256` 唯一约束支持重复上传幂等去重。

---

## 5. LangGraph 工作流设计（官方 human-in-the-loop）

### 5.1 图状态 `State`

```python
class State(TypedDict):
    task_id: int
    file_path: str
    parse_result: dict   # 文本 + 分段 + 是否 OCR
    extraction_json: dict
    rdf_ntriples: str    # N-Triples 快照
    standard_json: dict  # 标准文本，供前端展示
    violations: list
    reviews: list | None # resume 回来的人工决策 [{violation_id, action}]
    error_msg: str | None
```

### 5.2 节点与边

```
START → parse_file → extract → validate_deterministic → validate_semantic
  → persist_results → await_human_review ──(resume)──→ apply_reviews → finalize → END
  └──────── 任一节点异常 → fail_node(置 FAILED + error_message) ──────────┘
```

- 每个节点入口更新 `check_task.status / progress`（PENDING→PARSING→EXTRACTING→VALIDATING→WAITING_REVIEW→[REVIEWING 由 resume API 置位，非图节点状态]→SUCCESS / FAILED / CANCELLED）；**每个节点入口检查 `CANCELLED` 标志，命中即短路抛 CancelledError**（配合 asyncio 取消后台任务，构成取消双保险）；**REVIEWING 与 WAITING_REVIEW 均可取消**。
- `validate_deterministic` / `validate_semantic`：分别产出确定性（SPARQL ASK）与语义（LLM 按段批跑）结果，**只算不落库**，以纯 dict 挂 state（`det_outcomes` / `sem_outcomes`，保证 checkpointer 可序列化）。抽取 `INCOMPLETE` 时**确定性规则全部 SKIPPED**（空图防假阳性洪水），**语义规则照跑**（基于原文 segments，不受抽取缺失影响）。
- `persist_results`（**独立节点**）：合并确定性 + 语义的**每条规则结果**写入 `rule_check_result`（PASS / FAIL / SKIPPED 全量落库，**成功也存**），`FAIL` 行生成 `violation`；语义 FAIL 行携带 `evidence_text` / `segment_ref` / `confidence` 并回填 `violation_id`；**两表写入同一数据库事务**；**幂等**（`(task_id, rule_id)` 唯一键，先删后插），避免崩溃后 resume 重跑重复写库。
- `await_human_review`：**纯节点（无任何副作用）**——LangGraph 在 resume 时**从头重跑该节点**，因此本节点只执行 `decision = interrupt({"task_id":..., "violation_summary":[...]})` 并 `return {"reviews": decision}`；`WAITING_REVIEW` 由 `persist_results` 置，**不在本节点写状态**（避免 resume 重跑时状态回闪）。
- 前端提交人工审核 → 后端**以 CAS 抢占** `UPDATE check_task SET status='REVIEWING' WHERE id=:id AND status='WAITING_REVIEW'`，仅 `rowcount==1` 才 `graph.ainvoke(Command(resume={"reviews":[...]}), {"configurable":{"thread_id": f"task-{task_id}"}})`；并发双击 / 前端重试返回 409。**invoke 外层 try/except**：失败时幂等回退 `UPDATE check_task SET status='WAITING_REVIEW' WHERE id=:id AND status='REVIEWING'`（条件带 status 防竞态），任务可重新审核。
- **人工审核完整性**：前端强制对每条 violation 逐条决策；后端校验该任务下**无 `UNCONFIRMED` 残留**才放行 SUCCESS。
- `apply_reviews`：把 `reviews` 写回 violation 表（CONFIRMED / FALSE_POSITIVE）。
- `finalize`：任务置 SUCCESS。
- **节点执行模型（线程）**：节点采用**全 async** 写法，阻塞调用（SPARQL / PyMySQL / OCR / LLM）逐个用 `asyncio.to_thread` 执行——不阻塞事件循环、支持 asyncio 取消；`cancel` 双保险在此模型下成立。

### 5.3 Checkpointer（替代自管状态）

- 图运行状态由 **`langgraph-checkpoint-mysql`** 持久化（**社区维护包，非官方**，要求 MySQL ≥ 8.0.19）。须锁定 `langgraph` / `langgraph-checkpoint-mysql` / `langchain-core` 版本，Phase 0 先做 interrupt+resume 全链路 spike 验证兼容；备选降级 `SQLiteSaver`。
- `thread_id = f"task-{task_id}"`，与 `check_task.id` 一一对应；进程重启后可恢复继续，`resume` 不丢上下文。
- **已完成 / 已取消任务的 checkpoint 定期清理**，避免 MySQL 持续膨胀（与取消/超时联动）。
- `check_task` 只作业务查询视图：`status` 供前端轮询、结果快照（`extraction_rdf / standard_json / segments_json`）。

### 5.4 `extract` 节点（分段抽取与合并）

- `owlready2` 读本体 → `OntologySchemaMapper` 生成 JSON Schema → DeepSeek 按 schema 抽取（`method="json_mode"` + Pydantic 校验 + 失败重试，仍失败标 low-confidence）→ `JsonToRdfConverter` 生成 RDF 实例 + 标准文本 JSON。
- **分段策略**：优先**整篇抽取**；分段阈值考虑**输入与输出双预算**——DeepSeek 输出 `max_tokens` 上限 8192，嵌套条款原样输出易截断，故**合同文本超过约 20k 字符即分段**抽取再合并。检测到 `finish_reason=length`（输出截断）时**降级为分段重抽**，而非原地重试。
- **合并规则**：同名/同统一社会信用代码的 Party 合并为同一个体；金额/日期多处出现且冲突时标 `low-confidence` 进人工复核；跨段引用统一命名空间。
- **抽取失败兜底**：整篇抽取重试后仍失败时——**空结果** → 任务置 `FAILED`（带 error_message）；**部分字段缺失** → `extraction_status=INCOMPLETE` 并**跳过确定性校验**（避免空图触发"必填缺失"假阳性洪水），仅跑语义校验提示人工。`extraction_status` 落 `check_task` 列，结果接口与报告展示该状态。
- **segments 生成保证**：分段器**总是运行**（无论是否整篇抽取），短合同至少产出一段整文写入 `segments_json` 落库，供语义校验、规则 dry-run 与报告"原文证据"复用。

---

## 6. 本体设计与示例

项目自带示例本体 `backend/ontology/contract_ontology.ttl`（Turtle 格式），后续可替换为真实本体。

### 6.1 合同主体模型（类层次）

```
:Contract 合同（根概念）
├── :Party 当事人                 # hasParty（对象属性，1..*）
│     ├── 甲方（partyRole=A）
│     └── 乙方（partyRole=B）
├── :ContractItem 合同标的         # hasItem（对象属性，0..*）
├── :Clause 合同条款               # hasClause（对象属性，0..*）
└── 日期/金额等数据属性（effectiveDate / terminationDate / totalAmount …）
```

### 6.2 数据属性（DatatypeProperty）

**Contract**

| 属性 | 类型 | 约束 | 说明 |
|---|---|---|---|
| contractTitle | xsd:string | 必填 | 合同名称 |
| contractNo | xsd:string | 可选 | 合同编号 |
| contractType | 枚举 | 必填 | 采购 / 销售 / 服务 / 劳务 / 租赁 / 合作… |
| signedDate | xsd:date | 可选 | 签订日期 |
| effectiveDate | xsd:date | 必填 | 生效日期 |
| terminationDate | xsd:date | 可选 | 终止日期（业务规则：须晚于生效日） |
| signingPlace | xsd:string | 可选 | 签订地点 |
| totalAmount | xsd:decimal | 必填，minInclusive 0 | 合同总金额 |
| currency | 枚举 | 必填 | CNY / USD / EUR… |
| status | 枚举 | 可选 | 执行中 / 已完成 / 已解除… |
| depositAmount | xsd:decimal | 可选，minInclusive 0 | 保证金金额 |
| depositType | 枚举 | 可选 | 履约保证金 / 质量保证金 / 投标保证金 / 预付款保证金 / 其他 |
| depositRefundCondition | xsd:string | 可选 | 保证金退还条件 |
| taxRate | xsd:decimal | 可选，minInclusive 0 | 税率（如 13% → 0.13） |
| taxInclusive | 布尔 | 可选 | 合同金额是否含税 |
| invoiceType | 枚举 | 可选 | 增值税专用发票 / 增值税普通发票 / 电子发票 / 其他 |
| invoiceRequirements | xsd:string | 可选 | 发票开具要求（抬头 / 时间 / 说明） |

**Party**

| 属性 | 类型 | 约束 | 说明 |
|---|---|---|---|
| partyRole | 枚举 | 必填 | 甲方 / 乙方 / 丙方 |
| partyName | xsd:string | 必填 | 当事人名称 |
| unifiedSocialCreditCode | xsd:string + pattern | 可选 | 统一社会信用代码（法人） |
| legalRepresentative | xsd:string | 可选 | 法定代表人 |
| address | xsd:string | 可选 | 地址 |
| contact | xsd:string | 可选 | 联系人 / 电话 |

**Clause**

| 属性 | 类型 | 约束 | 说明 |
|---|---|---|---|
| clauseType | 枚举 | 必填 | 付款 / 交付 / 违约责任 / 保密 / 知识产权 / 争议解决 / 不可抗力 / 解除 / 通知 / 其他 |
| clauseTitle | xsd:string | 可选 | 条款标题 |
| clauseText | xsd:string | 必填 | 条款原文 |

**ContractItem**

| 属性 | 类型 | 约束 |
|---|---|---|
| itemName | xsd:string | 必填 |
| quantity | xsd:decimal | 可选 |
| unitPrice | xsd:decimal | 可选 |
| itemAmount | xsd:decimal | 可选 |

### 6.3 对象属性（ObjectProperty）

| 属性 | domain → range | 基数 |
|---|---|---|
| hasParty | Contract → Party | 1..* |
| hasClause | Contract → Clause | 0..* |
| hasItem | Contract → ContractItem | 0..* |

### 6.4 自动生成校验规则的 OWL 约束（清单）

| OWL 构造 | 生成的规则 |
|---|---|
| `minCardinality 1` / FunctionalProperty | 必填缺失 |
| `maxCardinality N` | 取值个数超上限 |
| `owl:oneOf` 枚举 | 枚举越界 |
| `owl:hasValue` | 固定值不符 |
| xsd facet（pattern / minInclusive …） | 格式 / 数值范围 |
| 对象属性必引用 | 引用缺失 |

### 6.5 示例规则

- **确定性**（自动生成）：`Contract` 缺 `effectiveDate` / `totalAmount` / `contractType`；`contractType` / `currency` / `partyRole` 不在枚举内；`totalAmount < 0`。
- **确定性**（人工 `.rq`）：终止日期早于生效日期；**合同缺少甲方或乙方任一主体**（minCardinality 只能保证 hasParty ≥ 1，"甲方乙方各一"需按 partyRole 语义判定，归人工规则）。
- **语义**（LLM）：违约责任条款缺失；权利义务不对等（**evidence 必须是合同原文精确子串**）。

---

## 7. 校验规则体系（混合校验）

### 7.1 确定性校验（SPARQL）

- `OntologyRuleGenerator`：将本体约束自动转为 SPARQL **ASK 查询**（找反例，闭合世界语义，不引入推理器——OWL 开放世界假设与「校验抓缺失」语义相悖）。
- `rules/manual/*.rq`：人工 SPARQL 规则，处理跨字段逻辑（如 `terminationDate > effectiveDate`、甲方乙方主体齐全）。
- `SparqlExecutor`：对实例图逐个执行；ASK=true 时用 SELECT 定位具体 `?s` 与错误值 → 生成 `rule_check_result(FAIL)` + `violation`。**规则约定：每条规则对每份合同至多产出一条 FAIL**，多实例反例合并进同一条 violation 的 message（列出全部 `?s`）——与 `(task_id, rule_id)` 唯一键一致，避免反例被静默丢弃。

示例：

```sparql
# 必填缺失
ASK WHERE { ?doc a :Contract . FILTER NOT EXISTS { ?doc :effectiveDate ?v } }
# 枚举越界
ASK WHERE { ?doc a :Contract . ?doc :contractType ?v . FILTER(?v NOT IN (:a, :b)) }
```

### 7.2 语义校验（LLM + 原文证据）

- `SegmentSplitter`：按「第 X 条 / 一、二、三」章节切片，控制 token 窗口（T1.4 已实现，恒运行落 `segments_json`，短合同至少一段整文，Phase 3 直接复用）。
- `SemanticEvaluator`：**按段批跑**——每段一个 prompt（段原文 + 全部语义规则），LLM 返回 JSON 数组 `[{rule_id, pass, reason, evidence, applicable}]`，降低 LLM 调用量与限流风险。`applicable=false` 表示规则与合同类型不适用（如租赁合同无采购条款），计入 **SKIPPED** 而非 FAIL。
- **单规则单结果**：一条语义规则只产出一条 `rule_check_result`（与 `(task_id, rule_id)` 唯一键一致）。聚合粒度由规则的 `aggregation` 元数据决定（`check_rule.aggregation`，seed JSON 声明）：
  - `any`（默认）：任一适用段 `pass=false` → FAIL（如"权利义务不对等"，一段失衡即违约），取置信度最高段的 `evidence` / `segment_ref` / `reason`。
  - `all`（缺失性检查，如"缺违约条款" / "技术标准引用完整性"）：**全部适用段都 `pass=false` 才 FAIL**，任一段判"存在"即 PASS——消除按段批跑的单段视角误报（某段恰好无违约条款不代表全文缺失）。
  - 所有适用段 `applicable=false` → SKIPPED（规则不适用，HIGH）；存在无判定段（LLM 遗漏/截断）→ SKIPPED（评估失败，LOW）。
- **防御校验**：`evidence` 必须为原文**精确子串**——先**归一化**（NFKC 全半角 + 去换行/空白，容忍 OCR 断字）再比较；不满足则带反馈重试一次；仍不满足或 `evidence` 为空 → 标 `confidence=LOW` 提示人工（保留 LLM evidence 供参考），**防止 LLM 编造证据**。缺失性检查（如"缺违约条款"）约定：合同完全无相关内容时 `evidence` 留空，直接判 LOW。
- **定位（已确认）：硬校验 + low-confidence 标记**——语义规则不满足即作为正式 violation 进入审核闭环（与确定性规则同等地位，都需人工确认），同时按证据防御结果标注置信度（HIGH/LOW 落 `rule_check_result` / `violation`），供人工重点复核。

### 7.3 统一异常结构与校验结果落库

```
ruleId, ruleType(DETERMINISTIC/SEMANTIC), severity(HIGH/MEDIUM/LOW),
conceptIri, propertyIri, segmentRef(页码#条款), evidenceText(原文证据),
confidence(HIGH/LOW), message, expectedValue, actualValue,
status(UNCONFIRMED/CONFIRMED/FALSE_POSITIVE)
```

**校验结果落库**：每条规则的执行结果（PASS / FAIL / SKIPPED）写入 `rule_check_result`（**成功也落库**）；其中 `FAIL` 行生成 `violation`（状态 `UNCONFIRMED`），并在 `rule_check_result` 回填 `violation_id`。**两表同一事务写入**，保证不出现"有 FAIL 明细却无 violation"；**幂等**：`(task_id, rule_id)` 唯一键，先删后插。SKIPPED 含义：规则 disabled、与合同类型不适用（语义规则 `applicable=false`）、或抽取失败跳过确定性校验。

**状态流转**：`UNCONFIRMED` → `await_human_review` 中断等待 → 人工在 Web 审核后经 `resume` 统一改为 `CONFIRMED` / `FALSE_POSITIVE`（`apply_reviews` 节点写库）。**resume 是修改审核状态的唯一入口**；图完成后的 `PATCH /api/violations/{id}/status` 仅作纠偏，不与 checkpoint 冲突。

---

## 8. 数据库设计（MySQL）

### 8.1 业务表

**`contract_file`** — 上传文件元数据
`id, file_name, file_type(PDF/DOCX/IMAGE), storage_path, file_size, sha256 UNIQUE, has_scanned, ocr_applied, create_time`
- 上传大小上限（如 50MB）；`sha256` 唯一约束支持重复上传幂等去重；OCR 中间图与临时文件随任务结束清理。

**`check_task`** — 校验任务（业务查询视图）
`id, contract_file_id FK, status(PENDING/PARSING/EXTRACTING/VALIDATING/WAITING_REVIEW/REVIEWING/SUCCESS/FAILED/CANCELLED), progress, error_message, ontology_version_id FK, llm_model, extraction_status(COMPLETE/INCOMPLETE/FAILED，抽取质量标记), extraction_rdf MEDIUMTEXT(N-Triples 快照), standard_json LONGTEXT, segments_json LONGTEXT(文本分段，供 dry-run/报告复用), create_time, update_time`

**`ontology_version`** — 本体版本
`id, name, file_path, version, md5, loaded_time`

**`check_rule`** — 校验规则（**版本化**）
`id, rule_iri, rule_name, rule_type(DETERMINISTIC/SEMANTIC), severity, source(ONTOLOGY_GENERATED/MANUAL), expression(SPARQL 或 prompt), description, enabled, ontology_version_id FK NULL, create_time, update_time`
- 复合唯一索引 `(rule_iri, ontology_version_id)`；人工规则 `ontology_version_id=NULL`。换本体版本时按新版本生成/更新规则，旧版本规则保留（disabled），任务按 `ontology_version_id` 查询当时规则集。

**`rule_check_result`** — 规则校验明细（**成功/失败都落库**）
`id, task_id FK, rule_id FK, rule_snapshot(规则表达式/hash 冗余，保障审计), result(PASS/FAIL/SKIPPED), rule_type, severity, concept_iri, property_iri, segment_ref, evidence_text TEXT, confidence(HIGH/LOW), message, expected_value, actual_value, violation_id FK NULL(FAIL 行回填), create_time`
- 唯一索引 `(task_id, rule_id)`（幂等，先删后插）；`violation_id` 在 FAIL 行回填；**单规则多反例合并为一条**（见 §7.1 规则约定）。

**`violation`** — 校验异常（`rule_check_result` 中 `result=FAIL` 的明细 + 人工审核状态）
`id, task_id FK, rule_id FK, rule_snapshot(与 rule_check_result 一致，规则编辑后仍可回溯), rule_type, severity, concept_iri, property_iri, segment_ref, evidence_text TEXT, confidence(HIGH/LOW), message, expected_value, actual_value, status(UNCONFIRMED/CONFIRMED/FALSE_POSITIVE), confirm_user, confirm_time, create_time`

索引：`violation(task_id)`、`violation(status)`、`check_task(status)`、`rule_check_result(task_id)`、`rule_check_result(rule_id)`。

### 8.2 LangGraph checkpoint 表

由 **`langgraph-checkpoint-mysql`** 自动创建（同库）：`checkpoints / checkpoint_blobs / checkpoint_writes` — 图运行状态持久化；`thread_id = task-{task_id}`。**不手动建**；已完成任务的 checkpoint **定期清理**。

---

## 9. 规则管理模块与 REST API

### 9.1 规则管理模块详解

**规则分类与来源**

| 类型 | 来源 | 表达式 | 可编辑 |
|---|---|---|---|
| DETERMINISTIC | ONTOLOGY_GENERATED（本体约束自动生成） | SPARQL ASK | 否（只读，随本体版本更新） |
| DETERMINISTIC | MANUAL（人工 `.rq` 规则） | SPARQL | 是 |
| SEMANTIC | MANUAL（人工语义规则） | LLM prompt | 是 |

**规则生命周期**

1. **创建**（仅人工规则）：名称、类型、严重级别、表达式（SPARQL 或 prompt）、描述 → 默认 `disabled`。语义规则可声明 `aggregation`（`any`/`all`，缺失性检查用 `all`，聚合语义见 §7.2）；确定性规则恒 `any`（SPARQL 全局图查询，无聚合语义）。
2. **试运行 dry-run**：对选定的历史任务试跑规则，预览命中情况（**不落库**）——验证表达式正确性的核心体验。复用该任务的 RDF 实例与 `segments_json`（文本分段已随任务落库）；确定性规则跑 SPARQL 返回命中行，语义规则跑 LLM 返回 pass/evidence（**Phase 3 落地**：复用 `segments_json` 按段批跑，返回预计 token 成本）；**标注预计 token 成本**。
3. **启停**：`enabled` 后进入校验管线。
4. **编辑 / 失效**：表达式、严重级别、描述；本体自动生成规则只读。
5. **执行**：确定性规则由 `SparqlExecutor` 执行、语义规则由 `SemanticEvaluator` 执行，结果写入 `rule_check_result`。

**本体版本与规则同步**：`check_rule` 主键为 `(rule_iri, ontology_version_id)`（版本化，见 §8.1）。加载新本体 → 按新版本**新增/更新**自动生成规则（人工规则 `ontology_version_id=NULL` 不动）→ 旧版本规则保留并置 `disabled`；`check_task.ontology_version_id` 保证任务与当时所用规则集一致，历史审计可解释。

### 9.2 REST API

```
POST /api/files/upload             multipart → {taskId}
GET  /api/tasks                    ?status&fileName&page&size → 历史任务列表（分页筛选）
GET  /api/tasks/{id}               → {status, progress, message}     # 前端轮询
POST /api/tasks/{id}/resume        {reviews: 覆盖全部 UNCONFIRMED 的 [{violation_id, action}]} → CAS 抢占 WAITING_REVIEW→REVIEWING 后触发 Command(resume)；并发/重试返回 409；invoke 失败幂等回退 WAITING_REVIEW
POST /api/tasks/{id}/cancel        → PENDING/WAITING_REVIEW/REVIEWING 可取消，置 CANCELLED；运行中任务靠节点入口取消标志短路 + asyncio 取消；拒绝后续 resume，清理该线程 checkpoint
GET  /api/tasks/{id}/result        → {standardJson, segments, extractionStatus, ruleCheckResults[], violations[]}   # 最终结果（含完整校验明细）
GET  /api/tasks/{id}/report        ?format=pdf|excel → 校验报告下载（抽取摘要 + 校验明细 + violations + 原文证据）
GET  /api/violations               ?taskId&status&ruleType&severity&page&size
PATCH /api/violations/{id}/status  {status: CONFIRMED|FALSE_POSITIVE, confirmUser}  # SUCCESS 后纠偏（非审核主通道）
GET  /api/rules                    ?ruleType&source&enabled&page&size → 规则列表
POST /api/rules                    {rule_iri, name, type, severity, expression, aggregation?, description} → 创建人工规则（默认 disabled；语义规则可带 aggregation=any/all，非法值 422）
PUT  /api/rules/{id}               {enabled, severity, expression, aggregation?, description} → 编辑（本体生成规则仅启停/severity）
DELETE /api/rules/{id}             → 失效人工规则（软删）
POST /api/rules/{id}/dry-run       {taskId} → 模拟运行，预览命中（不落库）
```

---

## 10. 前端设计（Vue3 + Element Plus）

页面集（3 个核心页 + 1 个辅助页）：

- **① 合同上传与验证**（工作台，主流程）：拖拽/选择文件上传 → 任务实时轮询（进度条 + 状态流转）→ `WAITING_REVIEW` 时进入人工审核（violation 按严重级别着色、证据原文高亮，逐条「确认为问题 / 标记误报」，提交按钮防重；提交后 `REVIEWING` 显示"审核处理中"）→ SUCCESS 后展示最终结果（标准文本 JSON + **完整校验明细**（每条规则 PASS/FAIL/SKIPPED，SKIPPED 区分"抽取失败"与"规则不适用"）+ 经审核后的 violation 列表，**可导出 PDF / Excel 报告**），可再发起新任务。
- **② 历史合同处理结果**：全部历史任务列表（分页，按状态 / 文件名 / 时间筛选），行内显示状态徽标；点击查看任一历史任务的抽取结果、完整校验明细与 violations（复用①的结果展示组件），已完成任务**可导出 PDF / Excel 报告**。
- **③ 规则管理**（辅助）：规则列表（类型 / 来源 / 启停 / severity 筛选）+ 规则编辑抽屉（SPARQL 编辑器带语法校验与 **dry-run 试运行**、语义 prompt 编辑器 + **aggregation 聚合方式选择 any/all**）+ 启停开关；本体自动生成规则只读展示。
- 布局：左侧菜单（工作台 / 历史记录 / 规则管理）+ 主内容区。

---

## 11. 项目结构

```
contract-check/
├── README.md
├── solution.md
├── docker-compose.yml         # 整套系统部署（mysql + backend + frontend），见 §14
├── backend/
│   ├── requirements.txt / pyproject.toml  # 含 python-multipart（FastAPI 上传必需）
│   ├── .env.example              # DEEPSEEK_API_KEY, MYSQL_*
│   ├── app/
│   │   ├── main.py               # FastAPI 入口，挂路由、启动加载本体
│   │   ├── config.py
│   │   ├── api/                  # routers: files / tasks / violations / rules
│   │   ├── graph/                # state.py, build.py, nodes/
│   │   ├── parser/               # pdf / docx / ocr_service / segment_splitter
│   │   ├── ontology/             # loader / schema_mapper / rule_generator / rdf_converter
│   │   ├── llm/                  # llm_client.py（DeepSeek 封装，含重试限流）
│   │   ├── report/               # pdf_generator / excel_generator（校验报告导出）
│   │   ├── validation/           # sparql_executor / semantic_evaluator / models
│   │   ├── service/              # check_task_service / rule_service
│   │   └── db/                   # models.py(SQLAlchemy), session.py, schema.sql
│   ├── ontology/
│   │   └── contract_ontology.ttl # 示例合同本体
│   └── rules/
│       └── manual/*.rq           # 人工 SPARQL 规则
└── frontend/                     # Vue3 + Vite
    └── src/                      # 工作台(上传+审核+结果) / 历史记录 / 规则管理
```

---

## 12. 风险与应对

| # | 风险 | 应对 |
|---|---|---|
| 1 | **LLM 抽取幻觉/错值** | prompt 内嵌 schema + Pydantic 严格校验 + 失败重试 + 仍失败标 low-confidence 进人工复核 |
| 2 | **OWL 复杂度→映射有损**（匿名类/复杂 restriction 难自动转 schema/SPARQL） | 限定支持的本体 profile 写入 README；不支持的构造 fail-fast + 告警 |
| 3 | **扫描件 OCR 中文精度差**（垃圾进垃圾出） | `OcrService` 可插拔 + 图像预处理 + 置信度阈值 + 失败降级提示人工 |
| 4 | **长合同超上下文 / 输出截断 / 分段合并冲突** | 文本超约 20k 字符即分段（输入输出双预算）；`finish_reason=length` 检测降级分段重抽；合并去重 + 冲突标 low-confidence |
| 5 | **任务卡死/进程崩溃 / REVIEWING 死端** | checkpointer 持久化 + 启动时恢复未完成任务 + resume invoke 失败幂等回退 WAITING_REVIEW + 超时兜底 |
| 6 | **人工审核悬置 / 取消** | 前端醒目提示 + 任务超时告警 + `cancel` API（置 CANCELLED 并拒绝 resume） |
| 7 | **DeepSeek 限流 / 并发 / 成本** | `llm_client` 统一 429 重试 + 指数退避 + 并发控制；语义规则按段批跑；dry-run 标注 token 成本 |
| 8 | **本体版本变更与规则脱节** | 新本体 → 重新生成规则 → 旧版本自动生成规则禁用、人工规则不动；任务绑定 `ontology_version_id` |
| 9 | **rule_check_result 与 violation 不一致** | 双表**同一事务**写入 + `violation_id` 回填 |
| 10 | **checkpoint 累积 / 版本漂移** | 锁定版本 + Phase 0 spike 验证 + 已完成/已取消任务 checkpoint 定期清理 |
| 11 | **resume/cancel 并发竞态** | resume 用 CAS 抢占（WAITING_REVIEW→REVIEWING），并发返回 409，invoke 失败回退 WAITING_REVIEW；前端按钮防重；cancel 靠节点入口取消标志 + asyncio 双保险 |
| 12 | **抽取失败 → 假阳性洪水** | 抽取整篇失败置 FAILED 或 `extraction_incomplete` 跳过确定性校验 |
| 13 | **FastAPI 事件循环阻塞** | 同步 PyMySQL / checkpointer 调用放线程池（`asyncio.to_thread`），后台任务不阻塞轮询 |
| 14 | **PaddleOCR 依赖重**（paddlepaddle 数百 MB，镜像大/构建慢） | 容器**内置 OCR**（已确认必含）；Dockerfile 依赖层分层缓存；本机先行验证 paddleocr 3.x + paddlepaddle 版本兼容与 CPU 推理 |
| 15 | **reportlab 中文字体缺失**（Linux 容器无系统中文字体） | 容器 bundle 开源字体（思源黑体），reportlab 注册 TTF；本机开发复用系统字体 |

---

## 13. 实现计划（分阶段，每阶段可独立验证）

| 阶段 | 内容 | 验证方式 |
|---|---|---|
| **Phase 0 骨架 + HITL 闭环** | docker-compose 起 MySQL + backend 骨架 + FastAPI + 建表 + 上传接口 + PDF/Word 解析 + **checkpoint 版本锁定与 spike**（含并发 resume CAS、resume 中途失败回退、取消短路、线程模型验证）+ interrupt/resume 主链路 + 前端骨架 + 轮询 | 上传文本 PDF → **停在 WAITING_REVIEW** → 测试内自动 resume → SUCCESS；spike 实测 DeepSeek 输出截断概率与抽取失败链路 |
| **Phase 1 本体与抽取** | 示例 OWL + owlready2 + OWL→JSON Schema + DeepSeek 抽取（json_mode + Pydantic 校验重试）+ RDF 实例 + 标准文本 JSON + **segments 落库** | 抽查抽取 JSON/RDF 与原文一致；构造缺字段合同验证校验降级 |
| **Phase 2 确定性校验 + 人工审核闭环** | OntologyRuleGenerator + SPARQL + **双表原子落库** + 规则管理 API（CRUD + dry-run）+ 审核/结果 API + 前端审核视图 | 构造违约合同 → WAITING_REVIEW → 人工确认/误报 → resume → SUCCESS；rule_check_result 与 violation 一致 |
| **Phase 3 语义校验** | SegmentSplitter（复用 T1.4）+ SemanticEvaluator（**按段批跑 + evidence 归一化防御 + confidence 标注**）；validate 节点拆分为 deterministic / semantic / persist 三节点（单事务统一落库）；预置语义规则 seed（缺违约条款 / 权利义务不对等 / 技术标准引用）；语义 dry-run 接入 | 条款级规则返回带原文证据与置信度（HIGH/LOW）的异常，进入同一审核闭环；INCOMPLETE 时确定性 SKIPPED、语义照跑 |
| **Phase 4 OCR 与加固** | **T4.1** PaddleOCR 3.x 接入（`app/ocr/ocr_service.py`：lang='ch'、置信度阈值、失败降级、惰性加载；图新增 `ocr_node`：has_scanned 且未 OCR → 逐页转图识别 → 落文本 + `ocr_applied=True`）；**T4.4** 报告导出（`app/report/`：reportlab PDF + openpyxl Excel，含抽取摘要+校验明细+violations+原文证据+置信度）；**T4.3** 加固（任务超时兜底、checkpoint 定期清理、文件生命周期、API 入参出参 debug 日志）；**T4.2** 规则管理 UI（规则列表筛选 + 编辑抽屉：SPARQL 编辑器/dry-run、语义 prompt 编辑器 + aggregation 选择）；**T4.5** docker compose 整套部署（见 §14） | 扫描型 PDF 全流程（A3）；浏览器创建/编辑规则 + dry-run（T4.2 验收）；超时/取消正确（C7）；checkpoint 清理可执行（E4）；PDF/Excel 报告中文正常（G1）；`docker compose up -d --build` 一键启动、浏览器 http://localhost 完整可用（H3） |

### 端到端验证

1. 准备 4 份测试合同：正常文本 PDF、含违约的文本 PDF、扫描图片 PDF、Word(.docx)。
2. `uvicorn` 启动后端 + Vite 启动前端 → 浏览器上传 → 轮询至 `WAITING_REVIEW`。
3. 前端对 violation 确认/误报 → 提交 `resume` → 图续跑至 SUCCESS → 校验 violation 状态已更新、`rule_check_result` 与 violation 一致。
4. 验证 `cancel`：对 PENDING / WAITING_REVIEW 任务取消 → 置 CANCELLED → 拒绝后续 resume。
5. MySQL 查 `check_task.extraction_rdf`（N-Triples 快照）、`standard_json`、`segments_json`、`rule_check_result`、`violation`、LangGraph checkpoint 表数据落库正确。
6. 单元测试：核心抽取解析、规则生成、SPARQL 校验逻辑、语义聚合、报告生成。
7. 报告导出：SUCCESS 任务下载 PDF/Excel，中文正常、含证据（G1）。
8. 扫描 PDF：OCR 全流程 → `has_scanned/ocr_applied` 标记正确、抽取可跑（A3）。
9. 整套部署：`docker compose up -d --build` 一键启动，浏览器 http://localhost 全功能可用（H3）。

---

## 14. Docker Compose 整套部署（Phase 4 T4.5）

**拓扑**：`mysql:8.0` + `backend` + `frontend(nginx)` 三个服务。

**backend/Dockerfile**
- 基础镜像 `python:3.11-slim`；先 copy `requirements.txt` 安装依赖（**PaddleOCR/paddlepaddle 为容器必需**，依赖层分层缓存，构建慢可接受）→ 再 copy `app/ontology/rules` 等代码。
- OCR 模型：PaddleOCR 首次调用自动下载模型，Dockerfile 预下载到镜像内（避免运行期外网下载）或启动懒加载（选懒加载，控制镜像体积）；CPU 版 paddlepaddle。
- **中文字体 bundle**：开源字体（思源黑体 SourceHanSans）TTF 复制进镜像，`REPORT_FONT` 环境变量指向该 TTF（`app/report/font.py` 依次探测 `REPORT_FONT` → Windows 系统字体 → Linux 容器常见路径）。
- 启动 `uvicorn app.main:app --host 0.0.0.0 --port 8000`；`DEEPSEEK_API_KEY` 经 compose `env_file: .env` 注入，不入镜像。

**frontend/Dockerfile**
- 多阶段：`node:20` 构建（`npm run build`）→ `nginx:alpine` 拷贝 `dist` + `nginx.conf`（`/` 静态资源、`/api/` 反代 `backend:8000`）。

**docker-compose.yml 扩展**（在 Phase 0 的 mysql 服务上追加）
- `backend`：`depends_on` mysql（`condition: service_healthy`）、`env_file: .env`、volume 挂载数据目录（`data/uploads` / `data/parsed` 持久化）。
- `frontend`：端口 `80:80`，`depends_on` backend。
- 一键脚本：`docker compose up -d --build` → 浏览器 http://localhost 全功能（上传→校验→审核→报告导出）。

**验收**：H3 一键启动浏览器全功能可用；A3 扫描 PDF（容器内 OCR）；G1 报告中文正常（容器内开源字体）。
