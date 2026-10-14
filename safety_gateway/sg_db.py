import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from safety_gateway.sg_config import sg_settings

logger = structlog.get_logger()

_async_engine = None
_async_session_local = None
_init_lock: asyncio.Lock | None = None
_init_loop: asyncio.AbstractEventLoop | None = None


def _ensure_init_lock() -> asyncio.Lock:
    """取得与当前事件循环绑定的初始化锁（必要时重建）。

    asyncio 原语与 SQLAlchemy 连接池都绑定到"创建它的那个事件循环"。
    sg_db 的引擎/sessionmaker 是模块级单例：若在导入期（无运行循环）建锁，
    或跨事件循环复用旧循环的连接池（测试每用例一个新循环、uvicorn
    reload 等），等待旧循环的 I/O 永远不会返回——表现为阈值加载等 DB 操作
    挂死，事件循环也不注册定时器（曾导致单测整轮卡住）。
    检测到循环变化时重建锁并丢弃旧引擎。
    """
    global _init_lock, _init_loop, _async_engine, _async_session_local
    loop = asyncio.get_running_loop()
    if _init_loop is not loop:
        if _init_loop is not None:
            logger.warning(
                "sg_db_rebound_to_new_event_loop",
                previous_loop_closed=_init_loop.is_closed(),
            )
        _init_loop = loop
        _init_lock = asyncio.Lock()
        _async_engine = None
        _async_session_local = None
    return _init_lock


async def _get_async_engine():
    global _async_engine
    if _async_engine is None:
        async with _ensure_init_lock():
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
        # 注意：不能在持有 _init_lock 时调用 _get_async_engine()——后者内部
        # 会再次获取同一把锁，asyncio.Lock 不可重入，会造成首次 DB 访问
        # 永久死锁（SafetyGateway 的阈值/同意查询会整条挂死）。
        engine = await _get_async_engine()
        async with _ensure_init_lock():
            if _async_session_local is None:
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
        except RuntimeError:  # silent-ok: 无运行事件循环属同步调用场景（改走 asyncio.run/直连路径）
            loop = None
        if loop is not None and loop.is_running():
            async with _ensure_init_lock():
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
