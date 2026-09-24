"""M12 跨模态对照提示知识表。

## 定位

"这个超声征象在 CT/MRI/病理上通常对应什么"是**教科书级知识**，不是模型推理
的产物。因此本模块是一张显式知识表：按器官与征象查找对照提示，模型不参与
生成——避免让模型凭印象编造影像-病理对应关系。

文献出处写在每条 ``source_reference`` 里；当前条目为定性描述，**必须**由医学
专家复核后再用于临床展示（与 M5/M7 的规则表同样的边界）。

## 使用方式

前端把它放在「依据溯源」下的「跨模态对照」子标签：医生看结论时若想知道"这个
征象在别的模态上会是什么样"，可以就地展开，而不必另开资料。
"""
from __future__ import annotations

from collections.abc import Sequence

from app.knowledge.risk_rules import FEATURE_ASPECT_RATIO_TALLER_THAN_WIDE
from app.models.schemas import CrossModalityHint, Organ

MAX_HINTS = 3

REFERENCE_TIRADS = "ACR TI-RADS 2017 术语与影像-病理对照（定性）"
REFERENCE_LIRADS = "LI-RADS v2018 影像-病理对照（定性）"
REFERENCE_CARDIAC = "ASE 指南：超声与其它模态对照（定性）"


# (器官, 征象) → 对照提示
_HINT_TABLE: dict[tuple[str, str], tuple[CrossModalityHint, ...]] = {
    (Organ.THYROID.value, "micro"): (
        CrossModalityHint(
            modality="CT",
            finding="CT 对微钙化的敏感性低于超声，常难以显示",
            clinical_value="CT 阴性不能排除微钙化，甲状腺评估仍以超声为准",
            source_reference=REFERENCE_TIRADS,
        ),
        CrossModalityHint(
            modality="pathology",
            finding="微钙化对应砂粒体，是乳头状癌的病理特征之一",
            clinical_value="病理为最终确诊依据，微钙化提示需积极评估",
            source_reference=REFERENCE_TIRADS,
        ),
    ),
    (Organ.THYROID.value, "posterior_attenuation"): (
        CrossModalityHint(
            modality="MRI",
            finding="MRI 上该征象无直接对应，需结合 T2 信号与强化方式判断",
            clinical_value="跨模态判断需整体评估，不宜单征象对照",
            source_reference=REFERENCE_TIRADS,
        ),
    ),
    (Organ.THYROID.value, FEATURE_ASPECT_RATIO_TALLER_THAN_WIDE): (
        CrossModalityHint(
            modality="pathology",
            finding="纵横比>1 对应结节呈「立位」生长，与恶性浸润相关",
            clinical_value="支持穿刺活检的决策",
            source_reference=REFERENCE_TIRADS,
        ),
    ),
    (Organ.LIVER.value, "internal"): (
        CrossModalityHint(
            modality="CT",
            finding="内部血流提示血供丰富，需对应增强 CT 的动脉期强化",
            clinical_value="与增强影像的强化方式对照可提高定性准确性",
            source_reference=REFERENCE_LIRADS,
        ),
        CrossModalityHint(
            modality="MRI",
            finding="MRI 增强可更准确评估 APHE 与廓清，是 LI-RADS 的核心依据",
            clinical_value="建议以 MRI 增强作为 LI-RADS 定级的主要依据",
            source_reference=REFERENCE_LIRADS,
        ),
    ),
    (Organ.LIVER.value, "anechoic"): (
        CrossModalityHint(
            modality="CT",
            finding="无回声病灶在 CT 上对应水样密度、无强化",
            clinical_value="支持单纯性囊肿的诊断，通常无需进一步检查",
            source_reference=REFERENCE_LIRADS,
        ),
    ),
    (Organ.CARDIAC.value, "reduced_systolic_function"): (
        CrossModalityHint(
            modality="MRI",
            finding="心脏 MRI 可定量评估射血分数与心肌纤维化",
            clinical_value="超声提示收缩功能减低时，MRI 有助于明确病因",
            source_reference=REFERENCE_CARDIAC,
        ),
    ),
    (Organ.CARDIAC.value, "small_effusion"): (
        CrossModalityHint(
            modality="CT",
            finding="CT 可显示心包积液并评估密度与分布",
            clinical_value="少量积液以超声随访为主，CT 用于复杂病例",
            source_reference=REFERENCE_CARDIAC,
        ),
    ),
}


def hints_for(
    organ_code: str | None,
    features: Sequence[str] = (),
    max_hints: int = MAX_HINTS,
) -> list[CrossModalityHint]:
    """按器官与征象取跨模态对照提示（未命中返回空列表）。"""
    if not organ_code:
        return []
    organ_key = organ_code.strip().lower()

    collected: list[CrossModalityHint] = []
    seen: set = set()
    for feature in features:
        for hint in _HINT_TABLE.get((organ_key, feature), ()):
            key = (hint.modality, hint.finding)
            if key in seen:
                continue
            seen.add(key)
            collected.append(hint)
            if len(collected) >= max_hints:
                return collected
    return collected


__all__ = ["MAX_HINTS", "hints_for"]
