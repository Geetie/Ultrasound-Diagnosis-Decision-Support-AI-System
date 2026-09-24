from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.config.settings import settings

# 创建异步引擎
async_engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_recycle=1800,
    connect_args={
        "statement_timeout": f"{settings.db_query_timeout * 1000}",
        "command_timeout": settings.db_query_timeout,
        "options": f"-c idle_in_transaction_session_timeout={settings.db_idle_in_transaction_timeout * 1000}",
    },
)

# 创建异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# 声明基类
Base = declarative_base()


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """依赖注入：获取数据库会话"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


get_session = get_db_session


async def init_db() -> None:
    """初始化数据库表（使用Alembic迁移，此方法保留用于测试）"""
    # 副作用导入：把 ORM 模型注册到 Base.metadata 上，测试期建表才能覆盖全部表。
    # ruff 会把它们判为"未使用"（F401）而自动删除——这是一次真实踩到的坑，
    # tests/unit/sec/test_audit.py 有守护测试钉住这一行，勿删。
    from app.models.database import (  # noqa: F401
        AuditLog,
        ConsentRecord,
        DiagnosisReport,
        DiagnosisResult,
        DiagnosisSession,
        FeedbackRecord,
        KnowledgeBase,
        Patient,
        SafetyEvent,
        SessionArchive,
    )

    async with async_engine.begin() as conn:
        # 仅在测试时创建表，生产环境使用Alembic
        pass

