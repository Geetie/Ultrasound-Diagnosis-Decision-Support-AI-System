"""Align knowledge_chunks.embedding with bge-m3 1024d (P0-1/02 技术债).

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-01

背景（16 号 A.5 实测）：
- ORM `KnowledgeChunk.embedding` 已改为 Vector(1024)（bge-m3），但 alembic
  迁移链缺对应迁移——0006 建列时是 Vector(768)（Ultrasound-CLIP 时代），
  真机当时用裸 ALTER TABLE 补的（07 号既定降级动作），本迁移把该操作
  正式化进迁移链。
- 旧 768 维向量与新底座（bge-m3 1024 维）空间不可比，全部失效：迁移先
  置 NULL 再改类型，避免 USING 转换在维度不匹配上炸掉；重建向量索引
  由灌数脚本（server_populate_vector_index.py --purge）负责。
"""
from alembic import op

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE knowledge_chunks SET embedding = NULL")
    op.execute(
        "ALTER TABLE knowledge_chunks "
        "ALTER COLUMN embedding TYPE vector(1024) "
        "USING embedding::text::vector"
    )


def downgrade() -> None:
    # 回退同样需要置 NULL（1024 → 768 无损转换不存在）
    op.execute("UPDATE knowledge_chunks SET embedding = NULL")
    op.execute(
        "ALTER TABLE knowledge_chunks "
        "ALTER COLUMN embedding TYPE vector(768) "
        "USING embedding::text::vector"
    )
