"""p0 探针引导：确保 smoke-admin（SYSTEM_ADMIN）账号就绪（P4-P8 的管理面探针依赖）。

口令运行时生成一次写入 ~/uai/.smoke_admin_pw（600），哈希进程内算好只写库。
复用 scripts/server_smoke_probe.py 的 ensure_user 模式（A.28 同款）。
"""
from __future__ import annotations

import asyncio
import pathlib
import secrets
import sys

UAI = pathlib.Path.home() / "uai"
_SRC = UAI / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

USERNAME = "smoke-admin"
PW_FILE = UAI / ".smoke_admin_pw"


def admin_password() -> str:
    if PW_FILE.exists():
        return PW_FILE.read_text(encoding="utf-8").strip()
    password = secrets.token_urlsafe(24)
    PW_FILE.write_text(password, encoding="utf-8")
    PW_FILE.chmod(0o600)
    return password


async def ensure_user(password: str) -> None:
    import asyncpg

    from app.config.dsn import to_asyncpg_dsn
    from app.config.settings import settings
    from app.security.auth import get_password_hash

    dsn = to_asyncpg_dsn(settings.database_url)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO users (username, email, full_name, hashed_password, disabled, role,
                               created_at, updated_at)
            VALUES ($1, $2, $3, $4, false, 'SYSTEM_ADMIN', now(), now())
            ON CONFLICT (username) DO UPDATE
              SET hashed_password = EXCLUDED.hashed_password,
                  disabled = false,
                  role = 'SYSTEM_ADMIN',
                  updated_at = now()
            """,
            USERNAME,
            "smoke-admin@localhost",
            "SMOKE ADMIN",
            get_password_hash(password),
        )
        print(f"  admin 用户就绪: {USERNAME} (SYSTEM_ADMIN)")
    finally:
        await conn.close()


def main() -> int:
    try:
        asyncio.run(ensure_user(admin_password()))
    except Exception as exc:
        print(f"  建管理员失败: {type(exc).__name__}: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
