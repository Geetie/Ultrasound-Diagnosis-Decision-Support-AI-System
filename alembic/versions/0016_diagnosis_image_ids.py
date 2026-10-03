"""diagnosis_results 增加 image_ids 列（接入结节分割的取图地基）。

沿用 0014（patient_context）的风格：只用 op.execute 裸 SQL + IF NOT EXISTS（可重跑），
全库 15 个迁移无一处用 op.add_column / batch_alter_table。

背景：API 层 DiagnosisRequest.image_ids 必填并传入 create_request，但从未落库
（database.py 此前连列都没有），后台管线拿不到任何影像标识。
"""
from alembic import op

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None

_TABLE = "diagnosis_results"


def upgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS image_ids TEXT[]")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS image_ids")
