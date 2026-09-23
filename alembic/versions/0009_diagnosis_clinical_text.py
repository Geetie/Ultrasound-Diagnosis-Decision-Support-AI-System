"""diagnosis_results 增加 clinical_text 列

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-24

背景：``diagnosis_repository.create_request`` 接收 ``clinical_text`` 但从未
落库，而后台管线 ``app/services/diagnosis_pipeline.py`` 读取
``request.clinical_text`` 作为 ``AgentContext.user_query``。ORM 无该属性
→ AttributeError → ``_safe_dispatch`` 捕获后返回失败占位响应，**真实诊断
链路从未产出过结论**（INT 端到端测试因 patch 掉 ``_safe_dispatch`` 而未
暴露）。

可空列，在线迁移无阻塞风险；历史行保持 NULL，管线侧已按 None 兜底。
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE diagnosis_results ADD COLUMN IF NOT EXISTS clinical_text TEXT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE diagnosis_results DROP COLUMN IF EXISTS clinical_text")
