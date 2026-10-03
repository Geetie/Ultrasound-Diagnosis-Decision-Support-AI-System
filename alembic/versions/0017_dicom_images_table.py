"""新增 dicom_images 表（DICOM 影像索引，session→image 可查）。

沿用 0014/0016 的风格：op.execute 裸 SQL + IF NOT EXISTS（可重跑），
全库迁移无一处用 op.add_column / batch_alter_table。

⚠️ 写法约束：建表 SQL 里表名/列名必须**字面量直写**。守卫
tests/unit/data/test_migration_model_alignment.py 用正则 + AST 解析迁移文件
来确认"每张模型表都有建表语句"，f-string 占位（f"... {_TABLE} ..."）会让它
解析不到表名，红灯报"表在迁移链里从未被创建"。

背景（2026-10-03 接入结节分割时暴露）
------------------------------------
"哪张影像属于哪个 session"此前**只存在于 audit_logs 的 JSONB detail**：
没有表、没有 Repository、没有 API。结果是
  1) diagnosis_results.image_ids 是**客户端自报**，服务端零校验；
  2) 后台管线拿到 image_ids 也取不回字节——上传返回的 img_... ID 与落盘
     文件名（真实 SOP Instance UID + patient/session 目录层）是两套命名空间。
本表把映射显式化：image_id ↔ session_id ↔ storage_path（可直接 retrieve()）。
"""
from alembic import op

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS dicom_images (
            id BIGSERIAL PRIMARY KEY,
            image_id VARCHAR(64) NOT NULL UNIQUE,
            session_id VARCHAR(64) NOT NULL,
            patient_id VARCHAR(64),
            storage_path TEXT NOT NULL,
            sop_instance_uid VARCHAR(128),
            modality VARCHAR(8),
            width INTEGER,
            height INTEGER,
            frame_count INTEGER,
            size_bytes INTEGER,
            original_filename VARCHAR(255),
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_dicom_images_session_created "
        "ON dicom_images (session_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_dicom_images_image_id ON dicom_images (image_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dicom_images")
