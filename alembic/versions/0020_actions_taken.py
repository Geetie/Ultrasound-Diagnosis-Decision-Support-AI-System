"""diagnosis_results 增"实际处置"字段（G35 建议采纳对照）。

背景（缺口 G35）
----------------
系统给出结构化处置建议（`clinical_actions`，含 A.127 的 `rule_id`），但**没有任何字段**
记录"医生实际执行了哪几条"。于是"建议采纳率/偏离率"无法计算，反馈表只有
"改判"（correction），回答不了"这条建议有没有被采纳"。

三个字段的最小语义
------------------
- actions_taken：医生勾选/实际执行的规则 ID 列表（JSONB 数组，元素形如
  `<organ>:<action_type>`，与 ClinicalAction.rule_id 同源）；
- actions_taken_at/by：谁在何时录入（审计可追溯，同 0018/0019 的口径）。

写法约束：沿用 0016~0019 风格：`op.execute` 裸 SQL + `IF NOT EXISTS`（可重跑）。
数组内容不做 DB CHECK：与应用层 rule_id 口径解耦，避免规则改名卡住历史数据。
"""
from alembic import op

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE diagnosis_results "
        "ADD COLUMN IF NOT EXISTS actions_taken JSONB"
    )
    op.execute(
        "ALTER TABLE diagnosis_results "
        "ADD COLUMN IF NOT EXISTS actions_taken_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE diagnosis_results "
        "ADD COLUMN IF NOT EXISTS actions_taken_by VARCHAR(64)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE diagnosis_results DROP COLUMN IF EXISTS actions_taken_by")
    op.execute("ALTER TABLE diagnosis_results DROP COLUMN IF EXISTS actions_taken_at")
    op.execute("ALTER TABLE diagnosis_results DROP COLUMN IF EXISTS actions_taken")
