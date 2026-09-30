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
        # `command_timeout` 是 asyncpg 真正支持的**连接参数**，留在 connect_args。
        "command_timeout": settings.db_query_timeout,
        # 而 `statement_timeout` 与 `idle_in_transaction_session_timeout` 是
        # **PostgreSQL 运行时参数（GUC）**，必须经 server_settings 传递。
        #
        # 2026-09-28 现场部署暴露：原实现把它们（以及一个 `options` 字符串）直接放进
        # connect_args，asyncpg 会直接报
        #   TypeError: connect() got an unexpected keyword argument 'statement_timeout'
        # —— 即**应用侧根本连不上 PostgreSQL**。之所以长期未被发现：单测走 mock/SQLite，
        # alembic 自建 engine（不带这些参数）能连，把问题挡住了。
        "server_settings": {
            "statement_timeout": f"{settings.db_query_timeout * 1000}",
            "idle_in_transaction_session_timeout": (
                f"{settings.db_idle_in_transaction_timeout * 1000}"
            ),
        },
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

    async with async_engine.begin() as conn:
        # 仅在测试时创建表，生产环境使用Alembic
        pass

