"""数据库会话与引擎。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import settings

DATABASE_URL = (
    f"mysql+pymysql://{settings.mysql_user}:{settings.mysql_password}"
    f"@{settings.mysql_host}:{settings.mysql_port}/{settings.mysql_database}?charset=utf8mb4"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 依赖：请求级会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
