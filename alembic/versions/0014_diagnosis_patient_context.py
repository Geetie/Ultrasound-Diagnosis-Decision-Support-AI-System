"""diagnosis_results 补 patient_context：让后台管线拿得到患者条件

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-29

## 背景（2026-09-29 真机第 1 例真实诊断暴露）

`POST /api/v1/diagnosis/request` 接收 `patient_context`（age/gender/relevant_history
以及新增的 `conditions`），`DiagnosisRepository.create_request(patient_context=...)`
也收下这个参数，但**从未写库**（`diagnosis_results` 表压根没有这一列）。

后果是一条完整的失效链：

    create_request 丢掉 patient_context
      → 后台管线 `_safe_safety_check` 只能把 organ/findings/recommendation 送给 SG
      → SG 的禁忌判定取不到 `diagnosis_data["patient_conditions"]`
      → 落到"缺条件数据"分支，而该分支**按设计返回 True**（保守，见
        tests/unit/sg/test_sg_contraindication.py::test_thyroid_contraindication_missing_conditions_conservative）
      → 每次诊断命中全部全局禁忌 → `requires_manual_review=True`
      → 系统**永远给不出自动结论**（`completed` 不可达）

补列后，带 `conditions` 的请求会被如实转发（空列表表示"已确认无条件"，正常评估）；
不带该键的请求维持 SG 的保守行为不变。
"""
from alembic import op

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None

_TABLE = "diagnosis_results"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS patient_context JSONB"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS patient_context")
