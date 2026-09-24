"""新增 cold_archive_manifests 表（T-GB-SEC-02 审计日志冷热分层）

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-24

背景：审计日志与已归档会话此前只有"同库搬迁 + 过期删除"，没有真正的
对象存储冷层；规格要求"PG 热存储 + 对象存储冷归档"。

冷归档流程为"上传成功后才删除热数据"，因此必须持久化每批次的清单
（对象键、记录数、首末 ID、checksum、时间范围、状态），否则一旦进程
重启就无法确认冷层是否真的存住了、也无从恢复与校验。
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS cold_archive_manifests (
            id BIGSERIAL PRIMARY KEY,
            manifest_id VARCHAR(64) NOT NULL UNIQUE,
            category VARCHAR(32) NOT NULL,
            object_key VARCHAR(512),
            record_count INTEGER DEFAULT 0,
            first_record_id BIGINT,
            last_record_id BIGINT,
            checksum VARCHAR(64),
            size_bytes BIGINT DEFAULT 0,
            time_range_start TIMESTAMPTZ,
            time_range_end TIMESTAMPTZ,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            adapter VARCHAR(32),
            message TEXT,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            archived_at TIMESTAMPTZ,
            purged_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cold_archive_manifests_manifest_id "
        "ON cold_archive_manifests (manifest_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cold_archive_manifests_category "
        "ON cold_archive_manifests (category)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cold_manifest_category_status "
        "ON cold_archive_manifests (category, status)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cold_archive_manifests")
