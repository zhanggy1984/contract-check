"""SQLAlchemy ORM 模型，对应 solution.md §8.1 与 schema.sql。"""
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

MEDIUMTEXT = Text().with_variant(mysql.MEDIUMTEXT, "mysql")
LONGTEXT = Text().with_variant(mysql.LONGTEXT, "mysql")


class ContractFile(Base):
    __tablename__ = "contract_file"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)  # PDF/DOCX/IMAGE
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    has_scanned: Mapped[bool] = mapped_column(Boolean, default=False)  # 存在扫描页→需 OCR
    ocr_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    page_texts_json: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True)  # 页级文本 JSON（逐页 OCR 的单一事实来源）
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class OntologyVersion(Base):
    __tablename__ = "ontology_version"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    md5: Mapped[str] = mapped_column(String(32), nullable=False)
    loaded_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class CheckTask(Base):
    __tablename__ = "check_task"
    __table_args__ = (Index("idx_check_task_status", "status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contract_file_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("contract_file.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    ontology_version_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("ontology_version.id"), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    extraction_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # COMPLETE/INCOMPLETE/FAILED
    extraction_rdf: Mapped[str | None] = mapped_column(MEDIUMTEXT, nullable=True)      # N-Triples 快照
    standard_json: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True)
    segments_json: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True)
    extraction_conflicts: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True)  # 分段抽取字段冲突 JSON 数组（A5）
    token_usage_json: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True)      # 评测契约 usage 聚合（抽取+语义，B.4）
    decision_json: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True)         # 决策痕迹列表（LLM+短路+兜底，契约隔离，不入 tool_calls/usage）
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
    contract_file: Mapped["ContractFile"] = relationship()


class CheckRule(Base):
    __tablename__ = "check_rule"
    __table_args__ = (UniqueConstraint("rule_iri", "ontology_version_id", name="uk_rule_iri_version"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rule_iri: Mapped[str] = mapped_column(String(255), nullable=False)
    rule_name: Mapped[str] = mapped_column(String(200), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(20), nullable=False)   # DETERMINISTIC/SEMANTIC
    severity: Mapped[str] = mapped_column(String(10), nullable=False)    # HIGH/MEDIUM/LOW
    source: Mapped[str] = mapped_column(String(20), nullable=False)      # ONTOLOGY_GENERATED/MANUAL
    expression: Mapped[str] = mapped_column(LONGTEXT, nullable=False)    # SPARQL 或 prompt
    aggregation: Mapped[str] = mapped_column(String(10), default="any", nullable=False)  # 语义规则聚合：any/all（缺失性检查用 all）
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    concept_iri: Mapped[str | None] = mapped_column(String(255), nullable=True)   # 规则作用的概念/属性（自描述）
    property_iri: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    ontology_version_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("ontology_version.id"), nullable=True)  # 人工规则为 NULL
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    update_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class RuleCheckResult(Base):
    __tablename__ = "rule_check_result"
    __table_args__ = (
        UniqueConstraint("task_id", "rule_id", name="uk_task_rule"),
        Index("idx_rcr_task_id", "task_id"),
        Index("idx_rcr_rule_id", "rule_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("check_task.id"), nullable=False)
    rule_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("check_rule.id"), nullable=False)
    rule_snapshot: Mapped[str] = mapped_column(LONGTEXT, nullable=False)  # 规则表达式冗余，保障审计
    result: Mapped[str] = mapped_column(String(20), nullable=False)       # PASS/FAIL/SKIPPED
    rule_type: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    concept_iri: Mapped[str | None] = mapped_column(String(255), nullable=True)
    property_iri: Mapped[str | None] = mapped_column(String(255), nullable=True)
    segment_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[str] = mapped_column(String(10), default="HIGH", nullable=False)
    message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    expected_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    actual_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    violation_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("violation.id"), nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Violation(Base):
    __tablename__ = "violation"
    __table_args__ = (
        Index("idx_violation_task_id", "task_id"),
        Index("idx_violation_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("check_task.id"), nullable=False)
    rule_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("check_rule.id"), nullable=False)
    rule_snapshot: Mapped[str] = mapped_column(LONGTEXT, nullable=False)
    rule_type: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    concept_iri: Mapped[str | None] = mapped_column(String(255), nullable=True)
    property_iri: Mapped[str | None] = mapped_column(String(255), nullable=True)
    segment_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[str] = mapped_column(String(10), default="HIGH", nullable=False)
    message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    expected_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    actual_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="UNCONFIRMED", nullable=False)
    confirm_user: Mapped[str | None] = mapped_column(String(50), nullable=True)
    confirm_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
