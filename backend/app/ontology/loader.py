"""本体加载与版本注册。

owlready2 快速解析器不支持 Turtle，统一走 rdflib → N-Triples → owlready2 桥接
（T1.1 探针验证：命名 datatype + equivalentClass 不被解析，故 TTL 内联全部约束）。
"""
import hashlib
import threading
from io import BytesIO
from pathlib import Path

import owlready2 as owl
import rdflib
from sqlalchemy.exc import IntegrityError

from app.db.models import OntologyVersion
from app.db.session import SessionLocal

# 与项目结构约定一致：backend/ontology/contract_ontology.ttl
ONTOLOGY_PATH = Path(__file__).resolve().parents[2] / "ontology" / "contract_ontology.ttl"
ONTOLOGY_IRI = "http://example.org/contract#"
ONTOLOGY_NAME = "contract_ontology"
ONTOLOGY_VERSION = "1.0"

_lock = threading.Lock()
_cache: dict[str, owl.Ontology] = {}
_version_lock = threading.Lock()   # register_version 进程内串行化（防 md5 并发首插，兜底不依赖唯一索引）


def md5_of_file(path: Path) -> str:
    """文件内容 md5，作为版本指纹。"""
    return hashlib.md5(path.read_bytes()).hexdigest()


def load_ontology(path: Path | None = None) -> owl.Ontology:
    """加载本体（进程内缓存；rdflib 桥接）。线程安全。"""
    path = Path(path) if path else ONTOLOGY_PATH
    with _lock:
        if str(path) in _cache:
            return _cache[str(path)]
        g = rdflib.Graph()
        g.parse(str(path), format="turtle")
        onto = owl.get_ontology(ONTOLOGY_IRI)
        onto.load(fileobj=BytesIO(g.serialize(format="nt").encode()), format="ntriples")
        _cache[str(path)] = onto
        return onto


def register_version(db, path: Path | None = None, version: str | None = None) -> int:
    """本体版本落库（按 md5 幂等复用），返回 ontology_version_id。

    并发首插竞态（T4.3-6）：进程内锁串行化（单实例并发直接消灭窗口，兜底不依赖唯一索引
    是否存在）+ md5 唯一约束 + begin_nested（savepoint）捕获 IntegrityError 回退复用
    （多实例防线）。必须用 savepoint 而非 rollback——register_version 内部 commit，
    直接 rollback 会误伤调用方（extract_node）同 session 未提交的 task 改动。
    """
    path = Path(path) if path else ONTOLOGY_PATH
    version = version or ONTOLOGY_VERSION
    md5 = md5_of_file(path)
    with _version_lock:
        return _register_locked(db, path, version, md5)


def _register_locked(db, path: Path, version: str, md5: str) -> int:
    """持锁执行版本注册：查复用 → 首插（begin_nested 撞键回退）。"""
    existing = db.query(OntologyVersion).filter(OntologyVersion.md5 == md5).first()
    if existing:
        return existing.id
    row = OntologyVersion(
        name=ONTOLOGY_NAME,
        file_path=str(path),
        version=version,
        md5=md5,
    )
    db.add(row)
    try:
        with db.begin_nested():
            db.flush()          # 撞唯一键（并发首插）在此抛 IntegrityError
    except IntegrityError:
        # 另一线程已插入同 md5 版本：savepoint 已回滚，移除 pending 行防重复 flush，
        # 回退复用已有版本（不破坏外层 session 的 pending 改动）
        db.expunge(row)
        existing = db.query(OntologyVersion).filter(OntologyVersion.md5 == md5).first()
        if existing is not None:
            return existing.id
        raise
    db.commit()
    db.refresh(row)
    return row.id


def ensure_loaded() -> int:
    """启动时调用：加载本体并注册版本，返回 ontology_version_id。"""
    onto = load_ontology()
    with SessionLocal() as db:
        return register_version(db)
