# AI 合同校验系统

基于 **本体建模 + 大模型抽取 + 混合校验 + 人工审核闭环** 的合同智能审查系统。上传合同（PDF / Word / 扫描件），系统自动抽取为结构化标准数据，按本体定义的规则做**确定性 + 语义双重校验**，全量落库并进入**人工审核闭环**，最终输出校验报告。

> 技术底座：Python 3.11 · LangGraph · FastAPI · DeepSeek · MySQL 8 · Vue3 + Element Plus · PaddleOCR

---

## 项目介绍

合同审查是法务高频、重复且容错要求高的工作。本系统把"合同原文 → 结构化数据 → 规则校验 → 人工复核"全流程自动化：用 OWL 本体描述合同领域知识，由本体**同时驱动抽取 schema 与校验规则**（单一事实源）；DeepSeek 负责把合同抽取为标准化数据，确定性规则（SPARQL）抓字段级问题、语义规则（LLM + 原文证据）抓条款级问题；每条校验结果全量落库，异常进入人工审核，**人工确认结果决定任务终态**。

当前为单用户内网部署假设（合同数据仅本地处理，不发送第三方解析服务），可直接用于合同入库前审查、历史合同巡检、演示与评估。

## 功能介绍

| 模块 | 能力 |
|---|---|
| **合同上传** | 支持 PDF / DOCX；扫描件自动识别并走 PaddleOCR；50MB 上限、sha256 幂等去重 |
| **智能抽取** | 本体驱动 JSON Schema → DeepSeek 抽取标准文本 JSON + RDF 实例 + 文本分段；超长合同自动分段合并，同名当事人去重，跨段字段冲突标低置信 |
| **混合校验** | 确定性规则（本体约束自动生成 SPARQL + 人工规则）抓必填/枚举/数值/格式；语义规则（LLM 按段批跑）抓违约条款、权利义务不对等、技术标准引用等条款级问题；PASS / FAIL / SKIPPED 全量落库（含成功） |
| **人工审核** | LangGraph 官方 human-in-the-loop（interrupt/resume），逐条「确认为问题 / 标记误报」，低置信异常高亮提示复核 |
| **规则管理** | 本体规则自动维护；人工规则可创建 / 编辑 / 启停 / dry-run 试跑（SPARQL 或语义 prompt），语义规则支持 any / all 聚合粒度 |
| **历史记录** | 全部任务分页筛选，查看抽取结果、完整校验明细与异常，支持 PDF / Excel 报告导出（中文渲染） |
| **数据安全** | 文件本地解析（PyMuPDF / python-docx / PaddleOCR），不调用外部解析服务；唯一哈希去重、孤儿文件与任务 checkpoint 定期清理 |

## 技术架构

### 系统拓扑（docker compose 一键部署）

```
浏览器 ──► frontend (Vue3 + nginx :80)
              │  /api 反代
              ▼
           backend (FastAPI :8000)
              │
   ┌──────────┼───────────┐
   ▼          ▼           ▼
 MySQL 8   DeepSeek    PaddleOCR
 (业务表 +  (LLM 抽取/   (扫描件
  checkpoint)  语义校验)  识别)
```

### 校验流水线（LangGraph 状态图）

```
上传
 └► parse（解析/OCR） ─► extract（LLM 抽取 → 标准JSON + RDF + segments）
     ─► validate_deterministic（SPARQL 确定性校验）
     ─► validate_semantic（LLM 语义校验 + 原文证据）
     ─► persist（单事务幂等落库）
     ─► 条件分流：
         有异常 / 抽取不完整 ─► await_human_review（interrupt 暂停等人工）
               ─► apply_reviews（写入确认/误报）─► finalize
         零异常且完整 ─► finalize（自动 SUCCESS）
```

- 任务状态由 `check_task` 表查询；图运行状态由 `langgraph-checkpoint-mysql` 持久化（`thread_id = task-{id}`），进程重启可恢复
- 任一节点异常 / 取消 / 超时 → 任务置 FAILED / CANCELLED 兜底

### 技术栈

| 域 | 选型 |
|---|---|
| 编排 | LangGraph（官方 human-in-the-loop）+ langgraph-checkpoint-mysql（锁版本） |
| 后端 | FastAPI + SQLAlchemy 2.0 + PyMySQL |
| LLM | DeepSeek（langchain-openai，json_mode，max_tokens 8192，429 退避） |
| 本体 | owlready2（OWL/RDF），rdflib 执行 SPARQL（版本显式钉死） |
| 解析 | PyMuPDF（PDF）、python-docx（Word）、PaddleOCR 3.x（扫描件） |
| 前端 | Vue3 + Vite + Element Plus（轮询任务状态） |
| 报告 | reportlab（PDF，中文字体 bundle）+ openpyxl（Excel） |

## 技术闪光点

1. **本体单一事实源**：一份 OWL 合同本体同时驱动「抽取 JSON Schema」（`schema_mapper`）与「校验 SPARQL 规则」（`rule_generator`），换本体即换 schema + 规则集，版本化落库可回溯。
2. **混合校验**：确定性（SPARQL 闭合世界找反例，多反例合并单条 violation）+ 语义（LLM 按段批跑，`aggregation=any/all` 粒度聚合），PASS/FAIL/SKIPPED 全量落库含成功，抽取不完整时确定性降级 SKIPPED、语义照跑（基于原文不受缺失影响）。
3. **官方 HITL 落地**：`await_human_review` 为纯节点（无副作用，resume 重跑安全）；resume 用 CAS 抢占（`WAITING_REVIEW→REVIEWING`，rowcount=1 才放行，并发返回 409），invoke 失败幂等回退，前端按钮防重。
4. **终态语义正确**：存在人工确认（CONFIRMED）的异常 → FAILED；全误报或零异常 → SUCCESS——人工审核结果真正决定任务结论，杜绝"确认了异常却显示通过"。
5. **证据防御**：语义规则的 evidence 必须为原文**精确子串**（NFKC 归一化 + 去空白，容忍 OCR 断字），不满足则带反馈重试，仍不满足标 low-confidence——从机制上防止 LLM 编造证据。
6. **取消/超时语义**：取消白名单前后端一致，运行中任务靠节点入口 CANCELLED 短路（`TaskCancelledError`）确定置 CANCELLED；软超时兜底不阻塞事件循环。
7. **抽取健壮性**：必填空串视为缺失触发 LLM 重试（防止"空串被当有值、RDF 却缺失"的语义缝隙）；输出截断自动降级分段重抽；同名当事人合并、跨段冲突标低置信进人工。
8. **一致性与幂等**：`rule_check_result` + `violation` 单事务写入、`(task_id, rule_id)` 唯一键先删后插，崩溃后 resume 不重复落库。
9. **数据安全**：合同文件与解析仅在本地磁盘 + MySQL，不发送任何第三方解析服务（含 MinerU 等外部 API）；sha256 去重、孤儿文件/checkpoint 定期清理、启动恢复未完成任务。

## 部署实施

### 一键部署（Docker Compose）

```bash
# Windows
powershell -ExecutionPolicy Bypass -File start.ps1
# 或 Linux（自动预拉镜像 + 构建）
./start.sh
```

手动方式：

```bash
docker compose up -d --build
```

启动后访问：

- 前端：http://localhost
- 后端 API：http://localhost:8001（OpenAPI 文档 `/docs`）
- MySQL：localhost:3306

停止（保留数据卷）：

```bash
docker compose down
# 或 stop.ps1
```

### 配置（.env）

项目根 `.env` 为 DB 凭据单一事实源；`backend/.env` 注入 DeepSeek 配置（不入镜像）：

```
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

### 演示

`data/test-contracts/` 内置 11 个场景合同（合规、缺日期/金额为负/类型越界、缺违约条款、权利义务不对等、长合同分段、扫描件 OCR 等）。注意：异常场景依赖 LLM 抽取如实输出，个别场景（如类型越界）LLM 可能做宽松枚举映射而不触发 FAIL，演示时多传几次即可，详见目录内 README。

### 本地开发

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001     # 需本机 MySQL + .env

cd frontend
npm install
npm run dev                                    # Vite 开发服务器，/api 已反代
```

### 测试

```bash
cd backend
.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"
# 63 个单元测试：抽取健壮性、校验落库、审核闭环、取消/超时语义、报告生成
```
