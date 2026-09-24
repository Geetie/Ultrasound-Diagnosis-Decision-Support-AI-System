"""知识库条目衰减所需列（二期 §6 时间衰减 + 一期漏做的反馈衰减）

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-24

背景：知识条目此前"入库即永久等价"，无论多久没被引用、被质疑多少次，检索时
权重都一样。二期要求连续 90 天未被引用则置信度 ×0.9 并提示复审；一期规格里的
"被质疑 ≥2 次 ×0.7"实际也从未落地——本迁移把这两条衰减所需的客观事实落到列上。

四列各自的必要性：
- last_referenced_at：时间衰减的唯一依据。**NOT NULL DEFAULT now()** 而非可空——
  可空会让"从未被引用"与"引用时间缺失"混在一起，而这两种情况都不该被当作
  "长期未引用"的证据去压置信度。存量行按迁移时刻初始化，等价于"刚刚被看到"。
- reference_count：引用次数，供看板统计与"零引用条目"排查。
- dispute_count：反馈衰减依据（被质疑次数），由反馈链路累加。
- confidence_score：检索侧按其加权。落库而不是查询时实时算，避免给每个候选项
  增加表达式求值；代价是需要周期性作业（见 app/knowledge/decay.py）。
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None

_TABLE = "knowledge_chunks"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {_TABLE}
            ADD COLUMN IF NOT EXISTS last_referenced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            ADD COLUMN IF NOT EXISTS reference_count INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS dispute_count INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS confidence_score DOUBLE PRECISION NOT NULL DEFAULT 1.0
        """
    )
    # 衰减作业按"空闲到期"筛选候选（last_referenced_at <= now() - 90d），
    # 没有索引时每次作业都是全表扫描；条目量随知识库增长只会越来越大。
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_kc_last_referenced_at
            ON {_TABLE} (last_referenced_at)
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_kc_confidence_score
            ON {_TABLE} (confidence_score)
        """
    )
    # 约束兜住写入方的手误：置信度是检索加权依据，越界值会放大或抹掉条目的影响力。
    op.execute(
        f"""
        ALTER TABLE {_TABLE}
            ADD CONSTRAINT ck_kc_confidence_score_range
            CHECK (confidence_score >= 0.0 AND confidence_score <= 1.0)
        """
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS ck_kc_confidence_score_range"
    )
    op.execute("DROP INDEX IF EXISTS idx_kc_confidence_score")
    op.execute("DROP INDEX IF EXISTS idx_kc_last_referenced_at")
    op.execute(
        f"""
        ALTER TABLE {_TABLE}
            DROP COLUMN IF EXISTS confidence_score,
            DROP COLUMN IF EXISTS dispute_count,
            DROP COLUMN IF EXISTS reference_count,
            DROP COLUMN IF EXISTS last_referenced_at
        """
    )
