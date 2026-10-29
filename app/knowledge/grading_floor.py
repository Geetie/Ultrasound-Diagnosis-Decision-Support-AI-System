"""分级下限显式规则（A.88 T4-④「前两项无实现」的第二项）。

## 修的真问题

《架构真相》§3.3 把「分级下限」登记为**无实现**。临床上它的含义很具体：
某些器官的分级体系存在"报告不得低于某级"的约定（例如已穿刺证实的病例，
报告仍写 TR2 属明显偏乐观）。系统此前既没有这条规则，也没有表达它的地方。

## 为什么**默认不生效**

分级直接决定处置建议（FNA / 随访间隔 / 转诊科室）。给它加一个"自动抬级"
的副作用，等于让一个**没有临床验证的规则**改医生的结论 —— 必须显式开启、
必须留痕、必须能逐条说清"为什么抬了"。

因此本模块的三条硬约束：

1. **规则表显式**：每条规则写清 ``organ`` / ``min_grade`` / ``reason`` /
   ``reference``（可审计，不允许隐式推导）；
2. **``GRADING_FLOOR_ENABLED`` 默认 False**：关闭时 ``apply_grading_floor``
   原样返回并留痕 ``grading_floor_skipped``（**不是"啥也没干"**）；
3. **只抬不降**：新分级必须严格高于原分级才生效；原分级解析不出来时
   **不猜**（返回 ``None`` + 原因），因为"猜一个等级"比"如实说没解析出来"
   更危险。

## 分级比较用单一事实源

``app.inference.grading_definitions.grade_major_level`` 是本仓库分级的唯一
分级序来源（TI-RADS / LI-RADS / ASE 都在里面）。本模块**复用**它，不另立
一套映射 —— 两套映射必然走散（A.62 的 ``grade_unparseable`` 就是这么来的）。
"""
from __future__ import annotations

from dataclasses import dataclass

import structlog

logger = structlog.get_logger("app.knowledge.grading_floor")

# 04-3 A2：总开关已迁到 ``settings.grading_floor_enabled``（默认 False，
# 可灰度/回滚）。``apply_grading_floor(..., enabled=None)`` 时读 settings；
# 测试/调用方仍可用 ``enabled`` 参数注入（既有守卫范式不受影响）。改默认值需：
# ① 与临床专家确认每条规则的适用范围；② 台账（16 号附录 A）登记实测影响面。


@dataclass(frozen=True)
class GradingFloorRule:
    """一条显式分级下限规则。

    Attributes:
        organ: 适用器官（与 ``grade_major_level`` 的 organ 键一致）。
        min_grade: 报告分级不得低于此值（该器官分级体系内的**规范标签**）。
        reason: 一句人话（进留痕，便于回答"为什么抬了"）。
        reference: 出处（指南/院内规程），可追溯到具体条文。
        condition: 04-3 A1：触发条件（case 级）。**空串 = 无效规则**（拒绝
            加载并 warning）—— 没有条件的器官级下限等于"该器官所有病例都抬"
            （04 号 ②：会把正常病例抬成高风险）。枚举值由临床组给出（如
            ``pathology_proven``）；生产规则表保持空表，内容必须经医学专家复核。
    """

    organ: str
    min_grade: str
    reason: str
    reference: str
    condition: str = ""


#: 规则表。**当前为空**：本轮交付的是"能力真实存在 + 显式规则 + 留痕"，
#: 不是"替医生定规则"。空表 + 关闭开关意味着**临床输出零变化**；要启用，
#: 先把经医学专家复核的规则加进这里（并在 16 号附录 A 登记影响面）。
GRADING_FLOOR_RULES: tuple[GradingFloorRule, ...] = ()


@dataclass(frozen=True)
class GradingFloorOutcome:
    """一次下限判定的结果（全部字段都可进日志，便于审计）。"""

    applied: bool
    grading: str | None
    original_grading: str | None
    rule: GradingFloorRule | None = None
    reason: str = ""

    @property
    def raised(self) -> bool:
        return self.applied and self.rule is not None


def rule_for_organ(organ: str | None) -> GradingFloorRule | None:
    """取该器官的下限规则（不看条件）；没有规则返回 ``None``。"""
    if not organ:
        return None
    target = str(organ).strip().lower()
    for rule in GRADING_FLOOR_RULES:
        if rule.organ == target:
            return rule
    return None


def match_rule(
    organ: str | None, conditions: frozenset[str] | set[str] | None
) -> GradingFloorRule | None:
    """按「器官 + 触发条件」取**有效**规则；无效/不满足一律 ``None``（04-3 A1）。

    * 条件为空的规则一律视为**无效**（warning 留痕，拒绝加载）；
    * 规则有条件而本例未提供该条件 → 规则不生效（宁可不抬，不可误抬）。
    """
    rule = rule_for_organ(organ)
    if rule is None:
        return None
    if not str(rule.condition or "").strip():
        logger.warning(
            "grading_floor_invalid_rule",
            organ=rule.organ,
            hint="规则缺 condition 字段，已拒绝加载（无条件的器官级下限=全器官抬级）",
        )
        return None
    if rule.condition not in (conditions or frozenset()):
        return None
    return rule


def apply_grading_floor(
    *,
    organ: str | None,
    grading: str | None,
    enabled: bool | None = None,
    conditions: frozenset[str] | set[str] | None = None,
) -> GradingFloorOutcome:
    """把分级抬到该器官的下限（**只抬不降**）。纯函数（守卫钉住）。

    Args:
        organ: 器官标识。
        grading: 本次报告的分级（``"TR3"`` / ``"3"`` / ``"moderate"`` 等，按器官）。
        enabled: 覆盖总开关（测试/调用方注入）；``None`` 读
            ``settings.grading_floor_enabled``。
        conditions: 本例已成立的触发条件集合（04-3 A1）；规则条件不在其中
            → 规则不生效。

    Returns:
        :class:`GradingFloorOutcome`。``applied=False`` 时 ``grading`` 原样返回。

    留痕口径（每个分支一个原因，不聚合）：
    * ``disabled``：总开关关闭；
    * ``no_rule``：该器官没有登记规则；
    * ``invalid_rule``：规则缺条件被拒（无条件的器官级下限=全器官抬级）；
    * ``condition_unmet``：条件不满足（宁可不抬，不可误抬）；
    * ``no_grading``：本次没有分级可判；
    * ``unparseable``：分级解析不出等级（**不猜**）；
    * ``already_at_or_above``：已达标（不降级）；
    * 空串 = 真的抬了（``raised=True``）。
    """
    if enabled is None:
        from app.config.settings import settings

        is_enabled = bool(getattr(settings, "grading_floor_enabled", False))
    else:
        is_enabled = bool(enabled)
    if not is_enabled:
        return GradingFloorOutcome(
            applied=False, grading=grading, original_grading=grading, reason="disabled"
        )

    organ_rule = rule_for_organ(organ)
    if organ_rule is None:
        return GradingFloorOutcome(
            applied=False, grading=grading, original_grading=grading, reason="no_rule"
        )
    if not str(organ_rule.condition or "").strip():
        logger.warning(
            "grading_floor_invalid_rule",
            organ=organ_rule.organ,
            hint="规则缺 condition 字段，已拒绝加载（无条件的器官级下限=全器官抬级）",
        )
        return GradingFloorOutcome(
            applied=False, grading=grading, original_grading=grading,
            rule=organ_rule, reason="invalid_rule",
        )
    if organ_rule.condition not in (conditions or frozenset()):
        return GradingFloorOutcome(
            applied=False, grading=grading, original_grading=grading,
            rule=organ_rule, reason="condition_unmet",
        )
    rule = organ_rule

    if not grading or not str(grading).strip():
        return GradingFloorOutcome(
            applied=False, grading=grading, original_grading=grading, reason="no_grading"
        )

    from app.inference.grading_definitions import grade_major_level

    current_level = grade_major_level(str(organ or ""), str(grading))
    floor_level = grade_major_level(str(organ or ""), rule.min_grade)
    if current_level is None or floor_level is None:
        # 解析不出等级 → 如实上报，**不猜**（"猜一个等级"比"说没解析出来"更危险）
        logger.warning(
            "grading_floor_unparseable",
            organ=organ,
            grading=grading,
            floor=rule.min_grade,
        )
        return GradingFloorOutcome(
            applied=False,
            grading=grading,
            original_grading=grading,
            rule=rule,
            reason="unparseable",
        )

    if current_level >= floor_level:
        return GradingFloorOutcome(
            applied=False,
            grading=grading,
            original_grading=grading,
            rule=rule,
            reason="already_at_or_above",
        )

    logger.info(
        "grading_floor_applied",
        organ=organ,
        from_grading=grading,
        to_grading=rule.min_grade,
        reason=rule.reason,
        reference=rule.reference,
    )
    return GradingFloorOutcome(
        applied=True,
        grading=rule.min_grade,
        original_grading=grading,
        rule=rule,
        reason="",
    )


__all__ = [
    "GRADING_FLOOR_RULES",
    "GradingFloorOutcome",
    "GradingFloorRule",
    "apply_grading_floor",
    "match_rule",
    "rule_for_organ",
]
