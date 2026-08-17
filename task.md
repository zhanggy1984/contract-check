# 任务拆分（task.md）

> 对应 `solution.md` §13 实现计划。每个任务含**验收标准**，按阶段顺序执行，阶段内任务尽量串行、独立可验证。

---

## Phase 0 环境与骨架 + HITL 闭环

| # | 任务 | 内容 | 验收标准 |
|---|---|---|---|
| T0.1 | 基础设施 | `docker-compose.yml`（mysql:8.0 + 持久化卷 + 健康检查）；`backend/.env`、`requirements.txt`（含 python-multipart、钉死 rdflib/langgraph 版本）；FastAPI 骨架（main/config/db/session）；`schema.sql` 建全表 | `docker compose up -d mysql` 成功；uvicorn 启动；MySQL 连通；5 张业务表 + checkpoint 表建表成功 |
| T0.2 | 上传与解析 | `POST /api/files/upload`；contract_file 落库（sha256 唯一）；PyMuPDF 解析文本型 PDF；python-docx 解析 .docx；无文本层标记 has_scanned；大小上限 50MB | 上传 PDF/Word 后文本提取成功入库；无文本层文件 has_scanned=1；超限文件拒绝 |
| T0.3 | LangGraph + checkpointer spike | 构图骨架（占位节点）；`langgraph-checkpoint-mysql` 锁版本；**spike**：interrupt→resume 全链路、resume 中途失败回退、并发 resume CAS、线程模型（全 async + to_thread）、取消短路 | spike 用例全部通过（checkpoint 版本兼容、resume 失败幂等回退、并发 409、不阻塞事件循环） |
| T0.4 | 任务状态机与异步 | CheckTaskService；check_task 状态流转（PENDING→…→SUCCESS/FAILED/CANCELLED）；后台任务执行图；启动恢复未完成任务；轮询接口 | 上传→解析→（测试自动 resume）→SUCCESS 状态正确流转；进程重启后 PENDING 任务可恢复 |
| T0.5 | 前端骨架 | Vite+Vue3+Element Plus；上传页；任务轮询页（进度条 + 状态徽标） | 浏览器上传 PDF → 任务状态实时更新至 SUCCESS |

## Phase 1 本体与抽取

| # | 任务 | 内容 | 验收标准 |
|---|---|---|---|
| T1.1 | 示例本体 | `contract_ontology.ttl`（§6 主体模型：Contract/Party/Item/Clause + 枚举/必填/数值约束）；owlready2 加载；ontology_version 落库（md5/version） | owlready2 加载无错；类/属性/约束可遍历；版本记录入库 |
| T1.2 | OWL→JSON Schema | `OntologySchemaMapper`：类层级→抽取 schema（必填/枚举/数值范围/格式） | 生成 schema 正确反映本体的必填、枚举、minInclusive 约束 |
| T1.3 | DeepSeek 抽取 | `llm_client`（json_mode、prompt 含 "JSON"、max_tokens=8192、429 退避、空 content/length 截断检测）；`LlmExtractor` 按 schema 抽取 + Pydantic 校验 + 失败重试 | 抽取 JSON 与原文一致；缺字段/非法值触发重试；输出截断时降级分段重抽 |
| T1.4 | RDF 与标准文本 | `JsonToRdfConverter`（individual/类型/属性）；standard_json 生成；SegmentSplitter **总是运行** 产出 segments_json | RDF N-Triples 正确（含类型与属性）；standard_json 前端可展示；segments_json 落库（短合同至少一段） |
| T1.5 | 抽取失败兜底 | `extraction_status`（COMPLETE/INCOMPLETE/FAILED）落 check_task；空结果→FAILED；部分缺失→INCOMPLETE 跳过确定性校验 | 空结果任务 FAILED；部分缺失任务 INCOMPLETE 且确定性规则标记 SKIPPED（不进假阳性洪水） |

## Phase 2 确定性校验 + 人工审核闭环

| # | 任务 | 内容 | 验收标准 |
|---|---|---|---|
| T2.1 | 规则生成与加载 | `OntologyRuleGenerator`（OWL 约束→SPARQL ASK）；`rules/manual/*.rq` 加载（终止日>生效日、甲乙双方各一）；check_rule 版本化落库 | 自动规则 + 人工规则正确加载；`(rule_iri, ontology_version_id)` 唯一 |
| T2.2 | SPARQL 执行器 | `SparqlExecutor`：ASK 判反例 + SELECT 定位 ?s；**多反例合并一条 violation（message 列出全部 ?s）** | 违约合同（缺生效日/金额为负/缺乙方）命中规则；多实例规则不丢反例 |
| T2.3 | 校验结果落库 | `persist_results`：rule_check_result 全量（PASS/FAIL/SKIPPED + rule_snapshot）+ violation 生成（violation_id 回填）；**同一事务 + (task_id,rule_id) 唯一幂等** | 全量落库无重复行；FAIL→violation 一致；重跑不产生重复 |
| T2.4 | 人工审核闭环 | `await_human_review`（纯节点 interrupt）；`resume` API（CAS 抢占→REVIEWING、invoke 失败回退、reviews 须覆盖全部 UNCONFIRMED）；`apply_reviews`；`cancel` API（PENDING/WAITING_REVIEW/REVIEWING） | 任务停在 WAITING_REVIEW→人工提交→REVIEWING→SUCCESS；并发 409；resume 失败回退可重试；cancel 生效且拒绝 resume |
| T2.5 | 规则管理 API | CRUD + `dry-run`（复用历史任务 RDF/segments，不落库，标 token 成本） | 建/启停/编辑/失效规则；dry-run 返回模拟命中 |
| T2.6 | 前端审核与结果 | 审核视图（WAITING_REVIEW 逐条确认/误报、按钮防重、REVIEWING 提示）；结果页（标准文本 + 校验明细 PASS/FAIL/SKIPPED + violations）；历史记录页 | 浏览器走通"上传→审核→SUCCESS→查看明细/历史"全流程 |

## Phase 3 语义校验

| # | 任务 | 内容 | 验收标准 |
|---|---|---|---|
| T3.1 | 分段与评估器 | `SegmentSplitter` 章节切片；`SemanticEvaluator`（批跑、`{pass,reason,evidence,applicable}`、evidence 归一化防御+重试、low-confidence） | 语义规则返回 4 字段；evidence 是原文精确子串（归一化后）；applicable=false 计入 SKIPPED |
| T3.2 | 语义规则接入管线 | 语义规则落 check_rule；与确定性同一闭环（violation 进审核、low-confidence 前端标注） | 语义 violation 进入审核闭环并被人工确认/误报；前端区分 low-confidence |

## Phase 4 OCR 与加固

| # | 任务 | 内容 | 验收标准 |
|---|---|---|---|
| T4.1 | PaddleOCR 接入 | `OcrService` 实现（PaddleOCR 3.x `predict()`、lang='ch'、置信度阈值、失败降级）；扫描 PDF→文本 | 扫描图片型 PDF 全流程可跑；has_scanned/ocr_applied 标记正确 |
| T4.2 | 规则管理 UI | 规则列表（筛选）+ 编辑抽屉（SPARQL 编辑器 + dry-run 按钮、语义 prompt 编辑器） | 浏览器创建/编辑规则并 dry-run 预览命中 |
| T4.3 | 加固 | 任务超时兜底；checkpoint 定期清理（已完成/已取消）；文件生命周期（大小上限、临时文件清理、sha256 幂等去重）；日志（入参出参 debug） | 超时/取消场景正确；重复上传去重；checkpoint 清理脚本可执行 |
| T4.4 | 报告导出 | `report` 模块：PDF（reportlab 中文字体）/ Excel（openpyxl），含抽取摘要+校验明细+violations+证据 | 下载 PDF/Excel 报告内容正确、中文正常 |
| T4.5 | docker compose 整套部署 | backend/frontend Dockerfile；compose 拓扑（mysql+backend+frontend，nginx 反代 /api）；一键脚本 | `docker compose up -d --build` 一键启动；浏览器 http://localhost 完整可用 |

---

## 端到端验收（场景全覆盖）

按类别逐条验收，全部通过方算完成。

### A. 正常路径
- A1 正常文本 PDF 全流程（上传→SUCCESS，零或少量 violation）
- A2 Word(.docx) 全流程
- A3 扫描图片 PDF（PaddleOCR）全流程
- A4 短合同（<20k 字符，整篇抽取）→ segments_json 至少一段整文
- A5 长合同（>20k 字符，分段抽取合并）→ 同名 Party 去重、无重复个体、金额冲突标 low-confidence

### B. 校验命中（确定性 / 语义）
- B1 缺生效日期 → 必填 FAIL
- B2 合同金额为负 → 数值 FAIL
- B3 合同类型越界 → 枚举 FAIL
- B4 缺乙方主体 → 人工 .rq FAIL
- B5 终止日早于生效日 → 人工 .rq FAIL
- B6 缺违约条款 → 语义 FAIL（evidence 为原文精确子串）
- B7 权利义务不对等 → 语义 FAIL（low-confidence 标注）
- B8 语义规则不适用（applicable=false）→ SKIPPED（区分"规则不适用"与"抽取失败"）
- B9 合规合同 → 全部 PASS 落库、零 violation
- B10 单规则多反例（如两个标的物单价为负）→ 合并为一条 violation，message 列出全部 ?

### C. 人工审核
- C1 全部确认 → violation 全部 CONFIRMED，SUCCESS
- C2 全部误报 → 全部 FALSE_POSITIVE
- C3 混合决策（部分确认部分误报）
- C4 部分提交（遗漏 UNCONFIRMED）→ 后端拒绝，不进入 SUCCESS
- C5 双击提交 resume → 第二次 409
- C6 resume 中途失败 → 幂等回退 WAITING_REVIEW，可重新审核
- C7 cancel（PENDING / WAITING_REVIEW / REVIEWING 三种状态）→ CANCELLED，拒绝后续 resume

### D. 抽取稳定性
- D1 空结果 → FAILED + error_message
- D2 部分字段缺失 → INCOMPLETE，确定性规则 SKIPPED（无假阳性洪水）
- D3 输出截断（finish_reason=length）→ 降级分段重抽成功
- D4 空 content / 非法 JSON → 重试后成功或标 low-confidence
- D5 DeepSeek 429 限流 → 退避重试成功

### E. 数据一致性与恢复
- E1 rule_check_result（PASS/FAIL/SKIPPED）与 violation 一致、无重复行
- E2 崩溃后 resume 重跑 → 幂等不重复写库
- E3 进程重启 → 恢复 PENDING / WAITING_REVIEW 任务
- E4 checkpoint 清理后 → 已完成任务业务数据仍可查（不受影响）

### F. 文件与边界
- F1 超 50MB 文件 → 拒绝并提示
- F2 非支持格式（.exe / .zip）→ 拒绝
- F3 旧版 .doc → 明确拒绝提示
- F4 损坏 / 空文件 → 解析失败 FAILED 并提示
- F5 重复上传 → sha256 幂等去重
- F6 多任务并发上传 → 状态互不串扰

### G. 报告与历史
- G1 PDF / Excel 报告导出（含抽取摘要 + 校验明细 + violations + 证据，中文正常）
- G2 历史列表分页 / 筛选（状态 / 文件名 / 时间） / 详情查看

### H. 安全与部署
- H1 .env 未被版本管理跟踪（.gitignore 生效，key 不泄露）
- H2 上传文件仅本地处理（无任何第三方解析调用）
- H3 `docker compose up -d --build` 一键启动，浏览器 http://localhost 全功能可用
