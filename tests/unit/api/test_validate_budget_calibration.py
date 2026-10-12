"""守卫（A.52）：validate 级预算不得被调到「常态区间」内 —— 否则共享 GPU 上
会把正常请求误降级成 needs_review（医生侧看到"需人工复核"，验收演练判 FAIL）。

实测证据（2026-10-12）：
- 安静窗口：validate P99 0.88s（A.51 p14c）；正常请求 775.3ms / 140.5ms
- 14 人共享 GPU 争用：一次演练请求 validate_ms=3001.93（正好卡在旧 3.0s 被截断）
  → needs_review → `deploy_acceptance_gate.sh` 从 PASS=24 掉到 PASS=20
- 历史病态尾：14s

判据：预算必须显著大于常态（≥10× 常态），同时仍能封顶病态尾（<15s）。
"""
from __future__ import annotations

from app.config.settings import settings

NORMAL_UPPER_BOUND_S = 1.0  # 常态上限（安静窗口 P99 0.88s 向上取整）
PATHOLOGICAL_TAIL_S = 15.0  # 历史最坏 14s 之上不再有"封顶"意义


def test_validate_budget_is_above_normal_range():
    """预算必须 ≥10× 常态 —— 否则 LLM 排队（离群）会误触发降级。"""
    assert settings.pipeline_validate_budget_s >= 10 * NORMAL_UPPER_BOUND_S, (
        f"pipeline_validate_budget_s={settings.pipeline_validate_budget_s}s 过紧："
        f"共享 GPU 下 LLM 排队会让正常请求 validate 超过预算 → 被降级为 needs_review"
        f"（A.52 实测 3001.93ms 卡在旧 3.0s 预算）"
    )


def test_validate_budget_still_caps_pathological_tail():
    """但也不能放到失去意义 —— 必须仍小于历史病态尾。"""
    assert settings.pipeline_validate_budget_s < PATHOLOGICAL_TAIL_S
