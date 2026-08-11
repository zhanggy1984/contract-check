"""全局枚举：任务状态、抽取质量、校验结果、异常状态。"""
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    PARSING = "PARSING"
    EXTRACTING = "EXTRACTING"
    VALIDATING = "VALIDATING"
    WAITING_REVIEW = "WAITING_REVIEW"
    REVIEWING = "REVIEWING"   # 由 resume API 置位，非图节点状态
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ExtractionStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"


class RuleResult(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


class RuleType(str, Enum):
    DETERMINISTIC = "DETERMINISTIC"
    SEMANTIC = "SEMANTIC"


class RuleSource(str, Enum):
    ONTOLOGY_GENERATED = "ONTOLOGY_GENERATED"
    MANUAL = "MANUAL"


class Severity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ViolationStatus(str, Enum):
    UNCONFIRMED = "UNCONFIRMED"
    CONFIRMED = "CONFIRMED"
    FALSE_POSITIVE = "FALSE_POSITIVE"
