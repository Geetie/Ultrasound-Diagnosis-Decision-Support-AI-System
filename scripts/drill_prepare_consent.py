"""演练数据准备：给 phantom patient 建知情同意记录。

为什么绕过 API 直接写库
------------------------
``POST /api/v1/consent`` 带 RoleChecker（仅 SYSTEM_ADMIN/QUALITY_CONTROL/
DEPARTMENT_HEAD），而演练账号 ``smoke-doctor`` 是 ULTRASOUND_DOCTOR —— 用它调
同意端点必然 403。演练要的是"一个已同意的 phantom patient"这个**前置状态**，
不是"走完同意流程"；为此去申请管理员账号属于本末倒置。

注意：产品代码里**任何**地方都不允许这样写——同意状态是合规闸门，
只有自造演练数据 preparation 才允许直接落库。
"""
from __future__ import annotations

import asyncio
import os
import sys

SRC = os.path.expanduser("~/uai/src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)


async def main() -> int:
    import asyncpg

    from app.config.dsn import to_asyncpg_dsn
    from app.config.settings import settings

    patient_id = sys.argv[1] if len(sys.argv) > 1 else "DRILL-PATIENT-001"
    dsn = to_asyncpg_dsn(settings.database_url)
    conn = await asyncpg.connect(dsn)
    try:
        # 先看 consent_records 表的实际列
        cols = await conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'consent_records' ORDER BY ordinal_position
            """
        )
        names = [c["column_name"] for c in cols]
        print("consent_records columns:", names)

        # 患者主键也要有（外键）
        await conn.execute(
            """
            INSERT INTO patients (patient_id, created_at, updated_at)
            VALUES ($1, now(), now())
            ON CONFLICT (patient_id) DO NOTHING
            """,
            patient_id,
        )

        # 实测列（2026-10-04）：id / record_id / patient_id / status / granted_at /
        # withdrawn_at / consent_version / created_at / updated_at —— 是 `status`
        # 不是 `consent_status`；record_id 是 **NOT NULL 的整数**且无默认值
        # （传字符串报 "str object cannot be interpreted as an integer"，
        #   省略报 "null value in column record_id"），故用患者 ID 派生确定性整数。
        # status 的取值必须匹配 app/models/schemas.py 的 ConsentStatus：
        #   GRANTED = "granted"（**小写**）——写 'GRANTED' 会让
        #   check_consent_status 抛 ValueError → 建会话 500（实测踩过）。
        import hashlib

        record_id = int(hashlib.md5(patient_id.encode()).hexdigest()[:12], 16)
        await conn.execute(
            """
            INSERT INTO consent_records
                (record_id, patient_id, status, granted_at, consent_version,
                 created_at, updated_at)
            VALUES ($1, $2, 'granted', now(), '1.0', now(), now())
            ON CONFLICT DO NOTHING
            """,
            record_id,
            patient_id,
        )

        # 校验
        rows = await conn.fetch(
            "SELECT * FROM consent_records WHERE patient_id = $1", patient_id
        )
        print(f"  consent rows for {patient_id}: {len(rows)}")
        for r in rows[:2]:
            d = dict(r)
            print("   ", {k: v for k, v in d.items() if "status" in k or "consent" in k or k == "operator_id"})
        if not rows:
            print("  WRITE_FAILED")
            return 3
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
