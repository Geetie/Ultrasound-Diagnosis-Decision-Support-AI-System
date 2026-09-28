"""Add immutability RULEs for audit_logs and audit_cross_chain tables

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-17
"""

from alembic import op

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

# 规则名 → (表名, 触发事件)。显式写死，不从名字里猜。
_RULES: tuple[tuple[str, str], ...] = (
    ("audit_log_no_update", "audit_logs"),
    ("audit_log_no_delete", "audit_logs"),
    ("audit_cross_chain_no_update", "audit_cross_chain"),
    ("audit_cross_chain_no_delete", "audit_cross_chain"),
)
_EVENT: dict[str, str] = {
    "audit_log_no_update": "UPDATE",
    "audit_log_no_delete": "DELETE",
    "audit_cross_chain_no_update": "UPDATE",
    "audit_cross_chain_no_delete": "DELETE",
}


def upgrade() -> None:
    # 2026-09-28 真机修复（两处）：
    # 1) **一条 op.execute 里塞多条语句**在 asyncpg 驱动下会直接失败
    #    （`cannot insert multiple commands into a prepared statement`）；
    #    拆成"一条语句一次 execute"。
    # 2) `CREATE RULE` **没有 IF NOT EXISTS**，重跑即报 "rule already exists"；
    #    先 `DROP RULE IF EXISTS` 再建，让这一步幂等。
    # 规则语义不变：审计表只允许 INSERT（追加即不可篡改）。
    for rule_name, table in _RULES:
        op.execute(f"DROP RULE IF EXISTS {rule_name} ON {table}")
        op.execute(f"CREATE RULE {rule_name} AS ON {_EVENT[rule_name]} TO {table} DO INSTEAD NOTHING")


def downgrade() -> None:
    for rule_name, table in _RULES:
        op.execute(f"DROP RULE IF EXISTS {rule_name} ON {table}")
