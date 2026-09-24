"""M7 转诊/处置建议知识表。

## 为什么是"知识表"而不是让模型自由生成

处置建议直接引导临床行动（是否穿刺、多久复查、转哪个科），是不允许自由发挥
的部分。因此这里用**显式规则表**：触发条件、目标科室、紧急度、适用机构层级
都写死在表里，模型只负责提供特征与风险等级作为输入。

## 分级分层展示

``applicable_level`` 区分「基层」「三甲」「通用」：基层医院做不了穿刺，给出
"立即穿刺"既无意义也可能被误读为必须转诊。前端按机构层级差异化展示，规则表
负责把层级标出来。

## 可维护性

规则集中在本模块常量，便于现场按本院流程替换；每条动作带
``source_reference`` 以满足可追溯性。当前条目来自公开指南的定性描述，
**必须**由医学专家复核后才可用于临床。
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence

from app.models.schemas import ClinicalAction, ClinicalActionUrgency, MalignancyRiskLevel, Organ

REFERENCE_TIRADS = "ACR TI-RADS 2017 处置建议（定性）"
REFERENCE_LIRADS = "LI-RADS v2018 处置建议（定性）"
REFERENCE_CARDIAC = "ASE 心功能评估建议（定性）"

# 单次最多给出的处置建议条数：过多会让医生难以判断优先级
MAX_ACTIONS = 3

LEVEL_BASE = "基层"
LEVEL_TERTIARY = "三甲"
LEVEL_GENERAL = "通用"


def _action(
    action_type: str,
    *,
    department: Optional[str] = None,
    urgency: ClinicalActionUrgency = ClinicalActionUrgency.ROUTINE,
    trigger: Optional[str] = None,
    description: Optional[str] = None,
    level: Optional[str] = None,
    reference: Optional[str] = None,
) -> ClinicalAction:
    return ClinicalAction(
        action_type=action_type,
        target_department=department,
        urgency=urgency,
        trigger_condition=trigger,
        description=description,
        applicable_level=level,
        source_reference=reference,
    )


def _as_level(risk_level: Optional[str]) -> Optional[str]:
    if risk_level is None:
        return None
    return getattr(risk_level, "value", risk_level)


def _thyroid_actions(
    features: Sequence[str], risk_level: Optional[str]
) -> List[ClinicalAction]:
    level = _as_level(risk_level)
    actions: List[ClinicalAction] = []

    if level == MalignancyRiskLevel.HIGH.value:
        actions.append(
            _action(
                "fine_needle_aspiration",
                department="甲状腺外科",
                urgency=ClinicalActionUrgency.SOON,
                trigger="恶性风险高或存在微钙化等高危征象",
                description="建议行细针穿刺细胞学检查（FNA）明确性质",
                level=LEVEL_TERTIARY,
                reference=REFERENCE_TIRADS,
            )
        )
        if "micro" in features:
            actions.append(
                _action(
                    "microcalcification_review",
                    department="甲状腺外科",
                    urgency=ClinicalActionUrgency.SOON,
                    trigger="检出微钙化",
                    description="微钙化为独立高危征象，建议同期评估颈部淋巴结",
                    level=LEVEL_TERTIARY,
                    reference=REFERENCE_TIRADS,
                )
            )
    elif level == MalignancyRiskLevel.MEDIUM.value:
        actions.append(
            _action(
                "short_interval_followup",
                urgency=ClinicalActionUrgency.SOON,
                trigger="中等恶性风险",
                description="建议 3-6 个月复查超声，观察结节大小与形态变化",
                level=LEVEL_GENERAL,
                reference=REFERENCE_TIRADS,
            )
        )
    elif level == MalignancyRiskLevel.LOW.value:
        actions.append(
            _action(
                "annual_followup",
                trigger="低恶性风险",
                description="建议 12 个月随访超声；结节明显增大时提前复查",
                level=LEVEL_BASE,
                reference=REFERENCE_TIRADS,
            )
        )

    actions.append(
        _action(
            "thyroid_function_test",
            trigger="甲状腺结节评估",
            description="建议结合甲状腺功能与自身抗体检查综合判断",
            level=LEVEL_BASE,
            reference=REFERENCE_TIRADS,
        )
    )
    return actions


def _liver_actions(
    features: Sequence[str], risk_level: Optional[str]
) -> List[ClinicalAction]:
    level = _as_level(risk_level)
    actions: List[ClinicalAction] = []

    if level == MalignancyRiskLevel.HIGH.value:
        actions.append(
            _action(
                "contrast_imaging",
                department="肝病科",
                urgency=ClinicalActionUrgency.URGENT,
                trigger="恶性风险高",
                description="建议增强 MRI/CT 进一步评估，并行肿瘤标志物检查",
                level=LEVEL_TERTIARY,
                reference=REFERENCE_LIRADS,
            )
        )
    elif level == MalignancyRiskLevel.MEDIUM.value:
        actions.append(
            _action(
                "short_interval_followup",
                urgency=ClinicalActionUrgency.SOON,
                trigger="中等恶性风险",
                description="建议 3 个月复查超声，必要时行增强影像",
                level=LEVEL_GENERAL,
                reference=REFERENCE_LIRADS,
            )
        )
    elif level == MalignancyRiskLevel.LOW.value:
        actions.append(
            _action(
                "six_month_followup",
                trigger="低恶性风险",
                description="建议 6 个月复查超声随访",
                level=LEVEL_BASE,
                reference=REFERENCE_LIRADS,
            )
        )

    actions.append(
        _action(
            "liver_function_test",
            trigger="肝脏病灶评估",
            description="建议结合肝功能与病毒性肝炎血清学检查",
            level=LEVEL_BASE,
            reference=REFERENCE_LIRADS,
        )
    )
    return actions


_REDUCED_FUNCTION_FEATURES = (
    "ef_mild_reduced",
    "ef_moderate_reduced",
    "ef_severe_reduced",
    "reduced_systolic_function",
    "reduced_diastolic_function",
)


def _cardiac_actions(
    features: Sequence[str], risk_level: Optional[str]
) -> List[ClinicalAction]:
    """心脏超声不产出恶性概率，处置建议按心功能特征给出。"""
    del risk_level
    actions: List[ClinicalAction] = []
    reduced = [item for item in features if item in _REDUCED_FUNCTION_FEATURES]

    if reduced:
        severe = any(
            item in ("ef_severe_reduced", "ef_moderate_reduced") for item in reduced
        )
        actions.append(
            _action(
                "cardiology_referral",
                department="心内科",
                urgency=(
                    ClinicalActionUrgency.URGENT if severe else ClinicalActionUrgency.SOON
                ),
                trigger="、" .join(reduced),
                description="建议心内科就诊评估心功能不全的病因与治疗方案",
                level=LEVEL_GENERAL,
                reference=REFERENCE_CARDIAC,
            )
        )
    else:
        actions.append(
            _action(
                "routine_followup",
                trigger="心功能未见明显减低",
                description="建议结合临床随访，出现症状时复查",
                level=LEVEL_BASE,
                reference=REFERENCE_CARDIAC,
            )
        )
    return actions


_BUILDERS: Dict[str, Callable[[Sequence[str], Optional[str]], List[ClinicalAction]]] = {
    Organ.THYROID.value: _thyroid_actions,
    Organ.LIVER.value: _liver_actions,
    Organ.CARDIAC.value: _cardiac_actions,
}


def actions_for(
    organ_code: Optional[str],
    features: Sequence[str] = (),
    risk_level: Optional[str] = None,
) -> List[ClinicalAction]:
    """按器官生成处置建议（未建模器官返回空列表）。

    注意：返回空列表表示"本器官没有内置处置规则"，而不是"不需要处置"——
    不要据此向医生展示"无需处置"。
    """
    if not organ_code:
        return []
    builder = _BUILDERS.get(organ_code.strip().lower())
    if builder is None:
        return []
    actions = builder(list(features), risk_level)
    return actions[:MAX_ACTIONS]


def summarize_actions(actions: Sequence[ClinicalAction]) -> str:
    """把处置建议汇总为报告用的一段文本。"""
    if not actions:
        return "（本次未生成结构化处置建议，请结合临床判断）"
    lines: List[str] = []
    for index, item in enumerate(actions, start=1):
        parts = [f"{index}. {item.action_type}"]
        if item.target_department:
            parts.append(f"科室：{item.target_department}")
        if item.urgency:
            parts.append(f"紧急度：{item.urgency.value}")
        if item.applicable_level and item.applicable_level != LEVEL_GENERAL:
            parts.append(f"适用：{item.applicable_level}")
        if item.description:
            parts.append(item.description)
        lines.append(" | ".join(parts))
    return "\n".join(lines)


__all__ = [
    "LEVEL_BASE",
    "LEVEL_GENERAL",
    "LEVEL_TERTIARY",
    "MAX_ACTIONS",
    "actions_for",
    "summarize_actions",
]
