"""DSN 工具：连接串的归一（SQLAlchemy 风格 ↔ 裸驱动）与**日志脱敏**。

## 为什么需要这个模块（2026-09-28 真机部署暴露）

项目里只有一个连接串来源 `settings.database_url`，形如
``postgresql+asyncpg://user:pw@host:port/db``。**SQLAlchemy 消化 `+asyncpg`
是靠它挑 dialect**，但同一字符串喂给**裸驱动**就非法了：

* `asyncpg.create_pool("postgresql+asyncpg://...")` → 建池直接失败
  （真机：`pgvector_health_check_failed [E2003_VEC_CONNECTION_FAILED]`，
  随后 `rag_service_init_failed`，向量检索整体不可用）；
* `app/observability/infra_health.py` 的 PG 巡检同理。

所以凡是"绕开 SQLAlchemy 直接调 asyncpg"的地方，都必须先经过本模块归一。
集中一处实现，避免多个调用点各写一份、日后只改一处。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def to_asyncpg_dsn(database_url: str) -> str:
    """去掉 scheme 里的 ``+dialect`` 后缀，其余部分**原样保留**。

    只动 scheme：不改 host/port/账号/库名，避免出现"看起来连上了，其实是另一个库"
    这种最难查的隐患。

    >>> to_asyncpg_dsn('postgresql+asyncpg://u:p@127.0.0.1:5432/db')
    'postgresql://u:p@127.0.0.1:5432/db'
    >>> to_asyncpg_dsn('postgresql://u:p@127.0.0.1:5432/db')
    'postgresql://u:p@127.0.0.1:5432/db'
    """
    scheme, sep, rest = database_url.partition("://")
    if not sep:
        return database_url
    if "+" in scheme:
        scheme = scheme.split("+", 1)[0]
    return f"{scheme}{sep}{rest}"


def redact_url(url: str) -> str:
    """抹掉连接串里的 ``user:password``，只保留 ``scheme://host:port/path``。

    ## 为什么（2026-09-28 真机部署暴露）

    日志里出现明文口令是合规红线，而真机上**有两处**把完整连接串打了出来：
    SafetyGateway 启动时的 ``sg_redis_pool_initialized url=redis://:<口令>@...``，
    以及 ``infra_health`` 的 ``redis_health_check_error redis_url=redis://:<口令>@...``。
    两处都会把口令写进共享服务器上的日志文件。

    任何"打印连接串以助排查"的地方都必须先过这个函数。无法解析时返回 `<redacted-url>`，
    **宁可少一条排查信息，也不泄漏口令**。
    """
    try:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(url)
        if not parsed.hostname:
            return "<redacted-url>"
        netloc = parsed.hostname
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        if parsed.username:
            netloc = f"<redacted>@{netloc}"
        return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        logger.warning("redact_url: 未预期异常，按原逻辑降级", exc_info=True)
        return "<redacted-url>"
