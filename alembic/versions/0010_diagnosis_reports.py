"""新增 diagnosis_reports 表（T-GB-API-02 报告生成与签发）

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-24

背景：报告生成与签发（模板填充 → HSM 数字签名 → PACS/HIS 推送）此前完全
缺失——``app/api/v1/`` 下无 report 模块，PACS 在全仓只出现在文档里。

本表承载一次诊断产出的报告：结构化内容（content）、渲染正文（rendered）、
正文摘要（content_digest，签名与幂等键）、数字签名与密钥名、以及 PACS 推送
结果（适配器名、状态、消息、尝试次数）。

索引 ``idx_reports_request_status`` 服务于"按诊断请求取最新报告"的高频查询。
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS diagnosis_reports (
            id BIGSERIAL PRIMARY KEY,
            report_id VARCHAR(64) NOT NULL UNIQUE,
            request_id VARCHAR(64) NOT NULL,
            session_id VARCHAR(64),
            patient_id VARCHAR(64),
            organ VARCHAR(32),
            status VARCHAR(32) NOT NULL DEFAULT 'draft',
            content JSONB,
            rendered TEXT,
            content_digest VARCHAR(64),
            template_version VARCHAR(32),
            signature TEXT,
            signing_key_name VARCHAR(64),
            signed_at TIMESTAMPTZ,
            signed_by VARCHAR(64),
            pacs_adapter VARCHAR(32),
            pacs_status VARCHAR(32),
            pacs_message TEXT,
            pacs_attempts INTEGER DEFAULT 0,
            pushed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_diagnosis_reports_report_id "
        "ON diagnosis_reports (report_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_diagnosis_reports_request_id "
        "ON diagnosis_reports (request_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_diagnosis_reports_session_id "
        "ON diagnosis_reports (session_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_diagnosis_reports_patient_id "
        "ON diagnosis_reports (patient_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_reports_request_status "
        "ON diagnosis_reports (request_id, status)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS diagnosis_reports")
