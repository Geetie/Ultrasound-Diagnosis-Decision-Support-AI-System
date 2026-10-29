"""新增医生确认回写字段（diagnosis_results.doctor_confirmed_at / doctor_confirmed_by）。

背景（2026-10-28，界面修复 P0-4）
--------------------------------
`能力盘点-自下而上.md` §Q2 查出：工作区的「确认结论」按钮只执行
`setAcknowledged(true)`，全仓**无任何地方读 `acknowledged`**，后端也无端点、
无字段。也就是说医生点「确认」在系统里不留任何痕迹。

而设计文档 `前端设计/17-医生侧展示契约.md` 把它定为**不可省的操作**：

    | 5 | 语音确认／修正 | **不可省**（防 automation bias） |
    | 8 | 签发 | **不可省**（法律责任节点） |

**两者是两件事，不能合并**：
- 确认 = 医生读过了（防 automation bias），确认后「AI 层标记」必须消失；
- 签发 = 医生负法律责任（HSM 签名 + 缺段拒签门禁）。

所以本迁移只加「确认」的两个字段，签发沿用既有的 report 签名链路，
不引入任何"确认即授权签发"的语义。

为什么不用布尔 acknowledged
---------------------------
要能回答"谁在什么时候确认的"，审计才成立。单个 boolean 只能回答"确认过没有"，
无法追溯，且无法区分多个医生的确认。故存时间戳 + 医生标识。

写法约束
--------
沿用 0016/0017 的风格：`op.execute` 裸 SQL + `IF NOT EXISTS`（可重跑）。
"""
from alembic import op

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE diagnosis_results "
        "ADD COLUMN IF NOT EXISTS doctor_confirmed_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE diagnosis_results "
        "ADD COLUMN IF NOT EXISTS doctor_confirmed_by VARCHAR(64)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE diagnosis_results DROP COLUMN IF EXISTS doctor_confirmed_at"
    )
    op.execute(
        "ALTER TABLE diagnosis_results DROP COLUMN IF EXISTS doctor_confirmed_by"
    )
