# 面试演示合同库

各场景对应 solution.md §13 验收点，供演示时上传触发对应校验结果：

| 文件 | 场景 | 预期校验结果 |
|---|---|---|
| good.pdf | 合规合同 | 零/少量 violation → SUCCESS |
| b1_missing_date.pdf | 缺生效日期 | 必填 FAIL（required） |
| b2_negative_amount.pdf | 合同金额为负 | 数值下限 FAIL（min） |
| b3_bad_type.pdf | 合同类型越界 | 枚举 FAIL（⚠️ LLM 可能把"合作共赢"映射到枚举值"合作"，此时不报） |
| b4_missing_party_b.pdf | 缺乙方主体 | 人工规则 FAIL（⚠️ LLM 可能从"乙方（盖章）"推断乙方存在，此时不报） |
| b5_termination_before_effective.pdf | 终止早于生效 | 人工规则 FAIL |
| b6_missing_breach_clause.pdf | 缺违约责任条款 | 语义 FAIL（aggregation=all） |
| b7_unbalanced_obligations.pdf | 权利义务不对等 | 语义 FAIL（evidence 命中原文 → 高置信） |
| b8_service_contract.pdf | 纯服务合同无标准引用 | 技术标准规则 SKIPPED；但无违约条款 → 语义 FAIL |
| long_contract.pdf | 超 20k 字符 | 分段抽取合并（A5） |
| scanned.pdf | 扫描件（无文本层） | 触发 OCR（A3） |

演示建议：先传 good.pdf 展示全流程走通；再传 b1/b2/b6/b7 展示异常进入人工审核闭环与置信度标注；
scanned.pdf 展示 OCR 能力；long_contract.pdf 展示分段抽取与冲突低置信。
