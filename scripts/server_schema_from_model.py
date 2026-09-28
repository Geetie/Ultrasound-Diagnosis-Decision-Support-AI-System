"""用模型建表（现场部署变通），再手工 stamp —— **不是**迁移链的正确修法。

背景（2026-09-28 现场部署暴露，缺陷是叠加的）：
    ① `alembic.ini` 缺 [loggers]/[handlers]/[formatters] → fileConfig KeyError（已修）
    ② 迁移用了不存在的 `sa.JSONB`（0001/0004，已修）
    ③ 0001 表名写成 `diagnosis_sessions`，模型是 `sessions`（已修）
    ④ 0001 建 `sessions` 时漏列（`target_organs`），0002 建 GIN 索引即失败
       → **迁移集合与模型整体不一致**，需专门修复批次（逐表比对列），不宜在部署现场盲改
    ⑤ **应用侧 engine 在本环境连不上**：`app.config.database.engine` 会向 asyncpg 传
       `statement_timeout`，当前 asyncpg 不接受该关键字 →
       `TypeError: connect() got an unexpected keyword argument 'statement_timeout'`
       （alembic 自建 engine 无此参数，故能连上——这也说明该问题只在应用侧出现）

本脚本：**自建 engine**（不带 connect_args）按模型建表，并打印版本以便定位 ⑤。
用法：cd <项目根> && DATABASE_URL=... python scripts/server_schema_from_model.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 本脚本在 scripts/ 下，项目根是**上一级**（写成 parent 会 `No module named 'app'`）
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def _main() -> int:
    import asyncpg
    import sqlalchemy
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.models.database import Base

    print(f"[versions] sqlalchemy={sqlalchemy.__version__} asyncpg={asyncpg.__version__}")

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("需要 DATABASE_URL 环境变量")

    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[ok] create_all 完成（自建 engine，未使用应用的 connect_args）")

    tables = sorted(Base.metadata.tables)
    print(f"[info] 模型表数: {len(tables)}")
    for name in tables:
        print("   -", name)

    # 顺带验证：应用侧 engine 是否可用（用于确认 ⑤ 是否真的影响运行期）
    try:
        import importlib

        module = importlib.import_module("app.config.database")
        app_engine = getattr(module, "engine", None)
        if app_engine is not None:
            async with app_engine.connect() as conn:
                await conn.execute(sqlalchemy.text("select 1"))
            print("[app-engine] 应用侧 engine 连接正常")
        else:
            print("[app-engine] 未找到导出名 engine，跳过")
    except Exception as exc:
        print(f"[app-engine] 应用侧 engine 连接失败: {type(exc).__name__}: {exc}")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
