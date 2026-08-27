-- 合同校验系统建表脚本（MySQL 8.0），与 SQLAlchemy 模型一致，二选一执行即可
-- 注：LangGraph checkpoint 表由 langgraph-checkpoint-mysql 自动创建，勿手建

CREATE TABLE IF NOT EXISTS contract_file (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  file_name VARCHAR(255) NOT NULL,
  file_type VARCHAR(20) NOT NULL,            -- PDF/DOCX/IMAGE
  storage_path VARCHAR(500) NOT NULL,
  file_size BIGINT NOT NULL,
  sha256 CHAR(64) UNIQUE NOT NULL,
  has_scanned TINYINT(1) DEFAULT 0,
  ocr_applied TINYINT(1) DEFAULT 0,
  page_texts_json LONGTEXT,                    -- 页级文本 JSON（混合扫描 PDF 逐页 OCR 的单一事实来源）
  create_time DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ontology_version (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  file_path VARCHAR(500) NOT NULL,
  version VARCHAR(50) NOT NULL,
  md5 CHAR(32) NOT NULL,
  loaded_time DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_ontology_version_md5 (md5)   -- 并发首插防重复版本（T4.3-6）
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS check_task (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  contract_file_id BIGINT NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
  progress INT DEFAULT 0,
  error_message VARCHAR(1000),
  ontology_version_id BIGINT,
  llm_model VARCHAR(100),
  extraction_status VARCHAR(20),              -- COMPLETE/INCOMPLETE/FAILED
  extraction_rdf MEDIUMTEXT,
  standard_json LONGTEXT,
  segments_json LONGTEXT,
  extraction_conflicts LONGTEXT,       -- 分段抽取跨段字段冲突 JSON 数组
  token_usage_json LONGTEXT,           -- 评测契约 usage 聚合（抽取+语义 LLM token，B.4）
  extraction_usage_json LONGTEXT,      -- 抽取 LLM usage 快照（崩溃重放复用，防重复计费）
  sem_outcomes_json LONGTEXT,          -- 语义评估结果快照（崩溃重放复用，防重复计费）
  sem_usage_json LONGTEXT,             -- 语义 LLM usage 快照（崩溃重放复用）
  decision_json LONGTEXT,              -- 决策痕迹列表（function calling 决策引擎，契约隔离）
  create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
  update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_check_task_status (status),
  CONSTRAINT fk_task_file FOREIGN KEY (contract_file_id) REFERENCES contract_file(id),
  CONSTRAINT fk_task_onto FOREIGN KEY (ontology_version_id) REFERENCES ontology_version(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS check_rule (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  rule_iri VARCHAR(255) NOT NULL,
  rule_name VARCHAR(200) NOT NULL,
  rule_type VARCHAR(20) NOT NULL,             -- DETERMINISTIC/SEMANTIC
  severity VARCHAR(10) NOT NULL,              -- HIGH/MEDIUM/LOW
  source VARCHAR(20) NOT NULL,                -- ONTOLOGY_GENERATED/MANUAL
  expression LONGTEXT NOT NULL,
  aggregation VARCHAR(10) NOT NULL DEFAULT 'any',
  description VARCHAR(1000),
  concept_iri VARCHAR(255),
  property_iri VARCHAR(255),
  enabled TINYINT(1) DEFAULT 1,
  ontology_version_id BIGINT,                 -- 人工规则为 NULL
  create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
  update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_rule_iri_version (rule_iri, ontology_version_id),
  CONSTRAINT fk_rule_onto FOREIGN KEY (ontology_version_id) REFERENCES ontology_version(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS violation (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_id BIGINT NOT NULL,
  rule_id BIGINT NOT NULL,
  rule_snapshot LONGTEXT NOT NULL,
  rule_type VARCHAR(20) NOT NULL,
  severity VARCHAR(10) NOT NULL,
  concept_iri VARCHAR(255),
  property_iri VARCHAR(255),
  segment_ref VARCHAR(255),
  evidence_text TEXT,
  confidence VARCHAR(10) NOT NULL DEFAULT 'HIGH',
  message VARCHAR(1000),
  expected_value VARCHAR(500),
  actual_value VARCHAR(500),
  status VARCHAR(20) NOT NULL DEFAULT 'UNCONFIRMED',
  confirm_user VARCHAR(50),
  confirm_time DATETIME,
  create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_violation_task_id (task_id),
  KEY idx_violation_status (status),
  CONSTRAINT fk_viol_task FOREIGN KEY (task_id) REFERENCES check_task(id),
  CONSTRAINT fk_viol_rule FOREIGN KEY (rule_id) REFERENCES check_rule(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS rule_check_result (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_id BIGINT NOT NULL,
  rule_id BIGINT NOT NULL,
  rule_snapshot LONGTEXT NOT NULL,
  result VARCHAR(20) NOT NULL,                -- PASS/FAIL/SKIPPED
  rule_type VARCHAR(20) NOT NULL,
  severity VARCHAR(10) NOT NULL,
  concept_iri VARCHAR(255),
  property_iri VARCHAR(255),
  segment_ref VARCHAR(255),
  evidence_text TEXT,
  confidence VARCHAR(10) NOT NULL DEFAULT 'HIGH',
  message VARCHAR(1000),
  expected_value VARCHAR(500),
  actual_value VARCHAR(500),
  violation_id BIGINT,
  create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_task_rule (task_id, rule_id),
  KEY idx_rcr_task_id (task_id),
  KEY idx_rcr_rule_id (rule_id),
  CONSTRAINT fk_rcr_task FOREIGN KEY (task_id) REFERENCES check_task(id),
  CONSTRAINT fk_rcr_rule FOREIGN KEY (rule_id) REFERENCES check_rule(id),
  CONSTRAINT fk_rcr_violation FOREIGN KEY (violation_id) REFERENCES violation(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
