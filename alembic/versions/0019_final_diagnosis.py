"""diagnosis_results 增最终诊断与金标准来源字段（05-3 S1）。

背景（04 号计划 §05-3）
----------------------
现有表只有"医生确认过"（0018 的 doctor_confirmed_at/by），**没有任何字段**
回答"患者最终是什么诊断、金标准从哪来"。于是：
- 改判率（feedback_records.correction）只是代理指标，分母无患者结局；
- "漏诊率 15%→5%" 属人机协同指标，需要最终诊断 + 金标准来源**两条独立
  证据**（单一标记不得定罪：AI 判良性+医生改判恶性可能是 AI 提示后升级，
  不能直接当漏诊）。

四个字段的最小语义
------------------
- final_diagnosis：季度回顾审计（人工）给出的最终诊断（自由文本，如"恶性"/"良性"）；
- final_diagnosis_source：金标准来源，限定 pathology / followup / expert_consensus；
- final_diagnosis_at/by：谁在何时录入（审计可追溯，同 0018 的"确认≠布尔"理由）。

写法约束：沿用 0016/0017/0018 风格：op.execute 裸 SQL + IF NOT EXISTS（可重跑）。
来源枚举不做 DB CHECK：应用层（审计脚本）校验，避免历史数据回填被卡。
"""
from alembic import op

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE diagnosis_results "
        "ADD COLUMN IF NOT EXISTS final_diagnosis TEXT"
    )
    op.execute(
        "ALTER TABLE diagnosis_results "
        "ADD COLUMN IF NOT EXISTS final_diagnosis_source VARCHAR(32)"
    )
    op.execute(
        "ALTER TABLE diagnosis_results "
        "ADD COLUMN IF NOT EXISTS final_diagnosis_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE diagnosis_results "
        "ADD COLUMN IF NOT EXISTS final_diagnosis_by VARCHAR(64)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE diagnosis_results DROP COLUMN IF EXISTS final_diagnosis"
    )
    op.execute(
        "ALTER TABLE diagnosis_results "
        "DROP COLUMN IF EXISTS final_diagnosis_source"
    )
    op.execute(
        "ALTER TABLE diagnosis_results DROP COLUMN IF EXISTS final_diagnosis_at"
    )
    op.execute(
        "ALTER TABLE diagnosis_results "
        "DROP COLUMN IF EXISTS final_diagnosis_by"
    )
