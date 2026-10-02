"""consent_records 补 updated_at：共享表必须取双方所需列的并集

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-29

## 背景（2026-09-29 真机第 1 例真实诊断暴露）

`consent_records` 被**两个服务各自声明**：

* `app/models/database.py::ConsentRecord` —— 本表由 app 的迁移链创建（"所有者"）；
* `safety_gateway/sg_models.py::ConsentRecord` —— SG 用自己的独立 Base 声明同名表，
  并**多出 `updated_at`** 与 `CheckConstraint ck_consent_status`。

SG 的同意查询是 ORM 全列查询，于是真机上报：

    consent_check_unexpected_error error=UndefinedColumnError:
        column consent_records.updated_at does not exist
        HINT: Perhaps you meant to reference the column "consent_records.created_at".
    [SQL: SELECT consent_records.id, ..., consent_records.created_at,
          consent_records.updated_at FROM consent_records WHERE patient_id = $1 ...]

→ SG 返回 503 `consent_check_db_error` → 主服务拿不到同意状态 → 保守 fail-closed，
诊断恒为 `failed`。修法：本迁移补列，`app/models/database.py` 同步声明。

用 `ADD COLUMN IF NOT EXISTS` + `DEFAULT now()` + `NOT NULL`：存量行有合法值，且可重跑。
"""
from alembic import op

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None

_TABLE = "consent_records"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {_TABLE}
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS updated_at")
