import asyncio
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from collections.abc import AsyncGenerator

from safety_gateway.sg_config import sg_settings
import structlog
logger = structlog.get_logger()

_async_engine = None
_async_session_local = None
_init_lock = asyncio.Lock()


async def _get_async_engine():
    global _async_engine
    if _async_engine is None:
        async with _init_lock:
            if _async_engine is None:
                _async_engine = create_async_engine(
                    sg_settings.database_url,
                    echo=False,
                    future=True,
                    pool_pre_ping=True,
                    pool_size=sg_settings.db_pool_size,
                    max_overflow=sg_settings.db_max_overflow,
                    pool_timeout=sg_settings.db_pool_timeout,
                    pool_recycle=1800,
                    connect_args={
                        "command_timeout": sg_settings.db_query_timeout,
                        "server_settings": {
                            "idle_in_transaction_session_timeout": str(sg_settings.db_query_timeout * 6 * 1000),
                            "statement_timeout": str(sg_settings.db_query_timeout * 1000),
                        },
                    },
                )
    return _async_engine


async def _get_async_session_local():
    global _async_session_local
    if _async_session_local is None:
        async with _init_lock:
            if _async_session_local is None:
                engine = await _get_async_engine()
                _async_session_local = async_sessionmaker(
                    engine,
                    class_=AsyncSession,
                    expire_on_commit=False,
                    autocommit=False,
                    autoflush=False,
                )
    return _async_session_local


class _LazyAsyncEngine:
    def __getattr__(self, name):
        if _async_engine is None:
            raise RuntimeError("Async engine not initialized. Call await _get_async_engine() first.")
        return getattr(_async_engine, name)

    def __bool__(self):
        return _async_engine is not None


class _LazyAsyncSessionLocal:
    async def __call__(self, **kwargs):
        session_local = await _get_async_session_local()
        return session_local(**kwargs)


async_engine = _LazyAsyncEngine()
AsyncSessionLocal = _LazyAsyncSessionLocal()

Base = declarative_base()


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    session_local = await _get_async_session_local()
    async with session_local() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def close_db() -> None:
    global _async_engine, _async_session_local
    if _async_engine is not None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            async with _init_lock:
                if _async_engine is not None:
                    await _async_engine.dispose()
                    _async_engine = None
                    _async_session_local = None
                    logger.info("sg_db_engine_disposed")
        else:
            if _async_engine is not None:
                try:
                    await _async_engine.dispose()
                except Exception as e:
                    logger.warning("sg_db_engine_dispose_without_loop", error=str(e))
                _async_engine = None
                _async_session_local = None
                logger.info("sg_db_engine_disposed_no_loop")
