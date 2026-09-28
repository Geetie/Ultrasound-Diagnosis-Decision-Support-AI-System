"""守卫：DSN 归一出错会静默降级成"两个不同的库"；日志脱敏出错会泄漏口令。"""
from __future__ import annotations

import pathlib

from app.config.dsn import redact_url, to_asyncpg_dsn

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


class TestToAsyncpgDsn:
    def test_strips_dialect_suffix(self):
        assert (
            to_asyncpg_dsn("postgresql+asyncpg://u:p@127.0.0.1:15432/uai")
            == "postgresql://u:p@127.0.0.1:15432/uai"
        )

    def test_is_idempotent(self):
        once = to_asyncpg_dsn("postgresql+asyncpg://u:p@h:1/d")

        assert to_asyncpg_dsn(once) == once

    def test_keeps_host_port_user_and_db(self):
        """只许动 scheme：改到别的 host/库是最难查的隐患。"""
        result = to_asyncpg_dsn("postgresql+asyncpg://u:p@10.0.0.5:5432/dbname?sslmode=require")

        assert result == "postgresql://u:p@10.0.0.5:5432/dbname?sslmode=require"

    def test_passthrough_without_scheme(self):
        assert to_asyncpg_dsn("not-a-url") == "not-a-url"


class TestRedactUrl:
    def test_hides_password(self):
        redacted = redact_url("redis://:s3cr3t@127.0.0.1:16379/0")

        assert "s3cr3t" not in redacted
        assert "127.0.0.1:16379/0" in redacted

    def test_hides_user_and_password(self):
        redacted = redact_url("postgresql://uai:s3cr3t@127.0.0.1:15432/uai")

        assert "s3cr3t" not in redacted
        assert "uai:" not in redacted.replace("uai@", "")
        assert "127.0.0.1:15432/uai" in redacted

    def test_keeps_url_without_credentials(self):
        assert redact_url("http://127.0.0.1:8428") == "http://127.0.0.1:8428"

    def test_unparsable_never_leaks_original(self):
        assert redact_url("garbage-s3cr3t") == "<redacted-url>"


class TestLogSitesUseRedaction:
    """源码级守卫：这两处曾把带口令的 URL 写进真机日志文件。"""

    def test_infra_health_redacts(self):
        src = (REPO_ROOT / "app/observability/infra_health.py").read_text(encoding="utf-8")

        assert "redact_url(redis_url)" in src

    def test_safety_gateway_redis_pool_redacts(self):
        src = (REPO_ROOT / "safety_gateway/redis_pool.py").read_text(encoding="utf-8")

        assert "redact_url(sg_redis_url)" in src
