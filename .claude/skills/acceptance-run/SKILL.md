---
name: acceptance-run
description: 合同校验系统端到端验收跑批（task.md 场景全覆盖）：上传测试件→轮询→查 violations→按 A-H 期望矩阵核对。docker compose 环境 backend :8001。附带 acceptance_upload.sh 辅助脚本。
---

# 验收跑批（acceptance-run）

对 contract-check 做端到端验收：上传测试件 → 任务跑到终态 → 查 violations → 按 task.md 的 A-H 期望矩阵核对命中。

## 前置条件

- docker compose 三容器 healthy（`docker compose ps` 确认 backend/mysql/frontend 均 healthy）
- 验收一律走 **docker 容器**（backend :8001，前端 :80）。宿主 dev 已下线，勿用 :8000/:5173
- 后端代码/本体/规则有改动 → **先重建容器**再验收：`docker compose up -d --build backend`
- 测试件在 `backend/data/acceptance/`（生成器见"测试件"节）

## 测试件清单

`backend/data/acceptance/`（`scripts/acceptance_gen.py` 生成大部分）：

| 文件 | 场景 | 期望 |
|---|---|---|
| good.pdf / good.docx | B9 合规 / A1-A2 | 确定性+语义零 violation |
| b1_missing_date.pdf | B1 缺生效日/金额 | required FAIL（缺生效日/总金额） |
| b2_negative.pdf | B2/B10 负金额 | min FAIL（totalAmount=-13000；unitPrice/itemAmount 各合并 2 ?s） |
| b3_bad_type.pdf | B3 类型越界 | 已知局限（LLM 枚举归一），不要求 FAIL |
| b4_no_party_b.pdf | B4 缺乙方 | 人工 .rq FAIL |
| b5v2_termination.pdf | B5 终止早于生效 | rule 30 FAIL |
| b7_unbalanced.pdf | B7 权利义务不对等 | 语义 rule 28 FAIL + LOW |
| a5_conflict.pdf | A5 金额冲突 | conflicts 含 totalAmount |
| long.pdf | A5 长合同 | Party 去重（2 个体，无重复） |
| short.pdf | A4 短合同 | COMPLETE，segments 至少 1 段 |
| empty_text.pdf | D1 空文本 | FAILED + 明确 error_message |
| corrupt.pdf | F4 损坏文件 | 400 拒绝 |
| oversize.pdf | F1 超限 | 413 拒绝 |

b6/b8 由语义规则对 b1/b2/b5（泛泛违约条款→rule 26 FAIL）和 good（标准不适用→SKIPPED）间接覆盖。新测试件生成参考 `scripts/gen_a5_conflict.py`（大文本必须逐行 insert_text + textwrap，insert_textbox 超框会静默丢文本导致无文本层被判扫描件）。

## 跑批流程

用辅助脚本 `scripts/acceptance_upload.sh`（Git Bash 环境，PowerShell 的 `$i:` 会被当 drive 变量）：

```bash
bash .claude/skills/acceptance-run/scripts/acceptance_upload.sh backend/data/acceptance/b1_missing_date.pdf
```

脚本输出：task_id → 终态 → extraction_status / conflicts → violations 明细。等价手写循环模板：

```bash
BASE=http://localhost:8001
t=$(curl -s -F "file=@backend/data/acceptance/good.pdf" $BASE/api/files/upload | python -c 'import sys,json;print(json.load(sys.stdin)["task_id"])')
for i in $(seq 1 90); do
  st=$(curl -s $BASE/api/tasks/$t | python -c 'import sys,json;print(json.load(sys.stdin)["status"])')
  case "$st" in PENDING|PARSING|EXTRACTING|VALIDATING|ANALYZING) sleep 4;; *) break;; esac
done
curl -s $BASE/api/tasks/$t | python -m json.tool        # status/extraction_status/conflicts
curl -s "$BASE/api/violations?task_id=$t" | python -m json.tool   # violations 明细
```

**终态识别**：`WAITING_REVIEW`（有 violation，interrupt 停在人工审核）、`SUCCESS`（零 violation 自动走完）、`FAILED`、`CANCELLED`。WAITING_REVIEW 是**预期终态**（有 violation 就该停），不是异常。

**核对要点**：
- violation 的 `rule_id` + message 决定命中；规则定义用 **rule_iri 查**（`docker exec contract-check-mysql mysql -N -e "SELECT id,rule_iri FROM check_rule WHERE id IN (...)"`），**不要用中文 rule_name**（Windows 管道输出乱码）
- 规则种类从 rule_iri 前缀判断：`urn:rule:{required|enum|min|pattern|manual}:...`（manual 的 .rq 是人工规则）
- 语义规则命中看 confidence：LOW = 需人工复核（evidence 非精确子串/空）

## 期望矩阵（A-H）

### A 正常路径
- A1/A2 good.pdf/.docx 全流程 → 走到终态（合规则 SUCCESS，有语义提示则 WAITING_REVIEW）
- A3 扫描 PDF → has_scanned=1、ocr_applied=1、文本可抽取
- A4 短合同 → COMPLETE，segments_json ≥1 段
- A5 长合同 → Party 按 role 归并（2 个体）；`conflicts` 标记跨段标量冲突

### B 校验命中
| 场景 | 期望 |
|---|---|
| B1 缺生效日 | required:Contract.effectiveDate FAIL |
| B2 负金额 | min:Contract.totalAmount FAIL（totalAmount 保留负值） |
| B3 类型越界 | 不要求（LLM 枚举归一，固有局限） |
| B4 缺乙方 | 人工 .rq FAIL |
| B5 终止早于生效 | rule 30（manual）FAIL |
| B6 缺/泛泛违约条款 | rule 26（语义）FAIL；含具体违约金（10%）则 PASS |
| B7 权利义务不对等 | rule 28（语义）FAIL + LOW |
| B8 规则不适用 | SKIPPED（applicable=false） |
| B9 合规 | violations=0（确定性+语义全过） |
| B10 多反例 | 一条 FAIL 合并全部 ?s（message 列出） |

### C 人工审核 / D 抽取 / E 一致性 / F 文件 / G 报告 / H 部署
C1-C7 需走前端（浏览器 http://localhost）或 resume/cancel API；D2 部分缺失→INCOMPLETE 时确定性 SKIPPED（required/manual 照跑）；E1 幂等先删后插；F1/F2/F3/F4 各类拒绝；G1 报告中文正常；H3 一键启动。C/D/E/G 多为既有闭环验证，改动只影响 B 系列时重点回归 A1/B 系列 + D1/F4。

## 踩坑清单

1. **PowerShell 写轮询循环**：`"$i:"` 被解析成 drive 限定变量 → 语法错误。用 bash 循环或 `${i}:`
2. **mysql 客户端输出编码**：Windows 管道下中文转 `?`/GBK 乱码。查业务数据用 host python + pymysql 连 `localhost:3306/contract_check?charset=utf8mb4`（backend/.venv 有依赖），别用 docker exec mysql 读中文
3. **sha256 幂等**：同文件重传复用 contract_file、新建 task_id（upload 返回新的 task_id，勿拿旧 task 断言）
4. **语义规则 LLM 判定不稳定**：同一合同多跑几次看趋势再下结论；误报优先查 segments 是否完整（split_segments 标题行 bug）再改 prompt
5. **新测试件 PDF**：大文本（>20k）必须分页+逐行 insert_text（width≈55），insert_textbox 超框丢弃文本 → 无文本层 → 被判扫描件触发 OCR
6. **改规则/本体后**：本体（ontology/）和规则（rules/manual/）打进镜像，必须 `docker compose up -d --build backend` 才生效；规则集由 validate 节点 sync_rules 自动同步，无需手动导 DB
7. **D1 空文本**：extract_contract 有 `MIN_TEXT_CHARS=10` 守卫，空/极短文本直接 FAILED（勿当 bug）
8. **DB 结构改动**：models.py 加列后需手动 ALTER TABLE（schema.sql 不自动执行）
