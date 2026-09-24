"""T-GA-FUS-01 — 一致性校验模块。

本模块实现融合层的一致性校验器 (:class:`ConsistencyChecker`)。

校验范围：

1. **与既往诊断对比**：当 :class:`FusionRequest.prior_diagnosis` 存在时，
   提取既往诊断的器官级分级 / 置信度，与当前 specialist 响应做对比；
   偏差跨档（major）→ CRITICAL；同档内偏差（minor）→ INFO。

2. **与临床指南对比**：通过 RAG Service 检索当前器官的临床指南（注入
   :class:`RAGService` 后启用；未注入 → 跳过该检查，不视为失败）。
   **P2-4 修复**：基于检索结果做关键字级语义一致性比对（活检建议/分级/
   禁忌），而非仅报告 chunk 数量。

3. **跨器官一致性**：
   - 基础互斥矩阵（同一 patient 不同器官"完全正常" + "严重异常" → WARNING）；
   - **P1-4 + P2-7 完整实现**：基于 UDT 病种编码的 7 条设计规则
     （02-核心流程与知识库.md §4 跨器官关联规则初始集），使用层级匹配
     （子类编码自动匹配父类）；
   - **CROSS_ORGAN_CONFIDENCE_OFFSET** 机制：触发跨器官规则时，须达到
     ``min(器官级阈值 + 偏移量, 0.95)`` 才能输出诊断。

设计要点：

- **UDT 优先**：跨器官规则触发条件使用 UDT 病种编码（不允许降级为
  硬编码字符串匹配，符合 02-核心流程与知识库.md:479 强制要求）；
- **子类匹配**：UDT 层级关系（``thyroid_hyperthyroidism`` 包含
  ``thyroid_toxic_diffuse_goiter``）由 :class:`UDTHierarchy` 维护；
- **失败安全**：所有外部依赖（RAG）异常 → 跳过该检查并 log warning，
  不抛错阻断融合；
- **可降级**：``RAGService=None`` 时跳过指南对比，``prior_diagnosis=None``
  时跳过既往对比；
- **可观测**：所有检查结果写入 :attr:`ConsistencyCheckResult.detail`。
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

from app.agents.protocols import AgentResponse
from app.fusion.protocols import (
    ConsistencyCheckResult,
    ConsistencySeverity,
    FusionRequest,
)
from app.rag.protocols import (
    RAGService,
    RetrievalRequest,
)

logger = structlog.get_logger("app.fusion.consistency")


# ----------------------------------------------------------------------
# 默认阈值
# ----------------------------------------------------------------------

#: 既往诊断与当前 confidence 差距 → WARNING 的阈值
DEFAULT_PRIOR_CONFIDENCE_GAP_WARNING = 0.3

#: 既往诊断与当前 confidence 差距 → CRITICAL 的阈值
DEFAULT_PRIOR_CONFIDENCE_GAP_CRITICAL = 0.5

#: 既往诊断分级码差异容忍度（数字差 ≤ 该值 → INFO；否则 WARNING / CRITICAL）
DEFAULT_PRIOR_GRADE_DIFF_WARNING = 1
DEFAULT_PRIOR_GRADE_DIFF_CRITICAL = 2

#: 临床指南查询默认 top_k
DEFAULT_GUIDELINE_TOP_K = 3

#: 跨器官关联校验置信度加法偏移量（00a-术语表.md:73）
#: 标准路径结论触发跨器官关联规则时须达到更高阈值
#: ``min(organ_threshold + offset, 0.95)``，默认 0.10
DEFAULT_CROSS_ORGAN_CONFIDENCE_OFFSET = 0.10

#: 跨器官规则触发的器官级置信度基线（无 OrganDiseaseThreshold 时的默认值）
DEFAULT_ORGAN_CONFIDENCE_THRESHOLD = 0.70


# ----------------------------------------------------------------------
# 跨器官关联规则（P1-4 + P2-7 完整实现）
# ----------------------------------------------------------------------


class UDTHierarchy:
    """UDT（Unified Diagnosis Taxonomy）病种编码层级体系。

    负责维护 UDT 编码间的父子层级关系，支持子类编码自动匹配父类。
    完整 UDT 审定放 Gate 0B 完成；本类提供一期所需的初始层级
    （02-核心流程与知识库.md:479 允许优先审定子集）。

    使用示例：
        >>> h = UDTHierarchy()
        >>> h.matches("thyroid_toxic_diffuse_goiter", "thyroid_hyperthyroidism")
        True  # 子类自动匹配父类
    """

    def __init__(
        self,
        parents: dict[str, list[str]] | None = None,
    ) -> None:
        # 父编码 → 子编码集合（直接 + 间接）
        self._parents: dict[str, set[str]] = {}
        # 反向索引：子编码 → 父编码集合
        self._children: dict[str, set[str]] = {}
        if parents:
            for parent, children in parents.items():
                for child in children:
                    self.add_relation(parent, child)

    def add_relation(self, parent: str, child: str) -> None:
        """添加父子关系（父 → 子）。"""
        p = parent.lower()
        c = child.lower()
        self._children.setdefault(c, set()).add(p)
        self._parents.setdefault(p, set()).add(c)

    def children_of(self, parent: str) -> set[str]:
        """获取父编码的所有直接 + 间接子类。"""
        return set(self._parents.get(parent.lower(), set()))

    def parents_of(self, child: str) -> set[str]:
        """获取子编码的所有父编码。"""
        return set(self._children.get(child.lower(), set()))

    def matches(self, code: str, *trigger_codes: str) -> bool:
        """检查 ``code`` 是否匹配任一 ``trigger_codes``（含层级匹配）。

        匹配规则：
        1. 完全相等（不区分大小写）→ True；
        2. ``code`` 是 ``trigger_codes`` 中任一项的子类 → True；
        3. ``trigger_codes`` 中任一项是 ``code`` 的父类 → True（罕见，但支持）。
        """
        if not code:
            return False
        c = code.lower()
        for trig in trigger_codes:
            t = trig.lower()
            if c == t:
                return True
            # code 是 trigger 的子类
            if c in self.children_of(t):
                return True
            # trigger 是 code 的父类
            if t in self.parents_of(c):
                return True
        return False

    def expand_with_subclasses(self, *trigger_codes: str) -> set[str]:
        """展开 ``trigger_codes`` 为包含所有子类编码的集合。"""
        result: set[str] = set()
        for trig in trigger_codes:
            t = trig.lower()
            result.add(t)
            result.update(self.children_of(t))
        return result


# ----------------------------------------------------------------------
# 一期三器官 UDT 层级初始集（02-核心流程与知识库.md:469-475）
# ----------------------------------------------------------------------

DEFAULT_UDT_HIERARCHY_PARENTS: dict[str, list[str]] = {
    # 甲状腺
    "thyroid_hyperthyroidism": [
        "thyroid_toxic_diffuse_goiter",   # Graves 病
        "thyroid_toxic_nodular_goiter",   # 结节性甲亢
        "thyroid_toxic_adenoma",
    ],
    "thyroid_hypothyroidism": [
        "thyroid_primary_hypothyroidism",
        "thyroid_secondary_hypothyroidism",
        "thyroid_hashimoto",              # 桥本甲状腺炎
    ],
    # 肝脏
    "liver_cirrhosis": [
        "liver_post_hepatitic_cirrhosis",
        "liver_alcoholic_cirrhosis",
        "liver_biliary_cirrhosis",
    ],
    "liver_congestion": [
        "liver_congestion_mild",
        "liver_congestion_severe",
    ],
    "liver_autoimmune_hepatitis": [
        "liver_autoimmune_hepatitis_type1",
        "liver_autoimmune_hepatitis_type2",
    ],
    # 心脏
    "cardiac_right_heart_failure": [
        "cardiac_right_heart_failure_chronic",
        "cardiac_right_heart_failure_acute",
    ],
    "cardiac_tricuspid_regurgitation": [
        "cardiac_tricuspid_regurgitation_mild",
        "cardiac_tricuspid_regurgitation_moderate",
        "cardiac_tricuspid_regurgitation_severe",
    ],
    "cardiac_arrhythmia": [
        "cardiac_atrial_fibrillation",
        "cardiac_ventricular_arrhythmia",
        "cardiac_bradycardia",
        "cardiac_tachycardia",
    ],
}

DEFAULT_UDT_HIERARCHY: UDTHierarchy = UDTHierarchy(DEFAULT_UDT_HIERARCHY_PARENTS)


@dataclass(frozen=True)
class CrossOrganRule:
    """跨器官临床关联规则（P1-4）。

    触发条件：响应 organ_a 满足 ``condition_a`` AND 响应 organ_b 满足
    ``condition_b`` → 按 ``severity`` 报告。匹配优先使用
    ``disease_code``（UDT 编码），fallback 到 ``content`` 关键词。

    Attributes:
        rule_id: 规则 ID（用于审计和 detail 输出）。
        organ_a: 第一器官（如 ``"liver"``）。
        organ_b: 第二器官（如 ``"cardiac"``）。
        condition_a: 器官 A 的判定函数（返回 bool）。
        condition_b: 器官 B 的判定函数（返回 bool）。
        severity: 触发严重度（WARNING / CRITICAL）。
        description: 人类可读的临床含义。
    """

    rule_id: str
    organ_a: str
    organ_b: str
    condition_a: Any  # Callable[[AgentResponse], bool]
    condition_b: Any  # Callable[[AgentResponse], bool]
    severity: ConsistencySeverity
    description: str
    suggested_action: str = ""
    contraindication: str = ""
    udt_hierarchy: Any = None  # UDTHierarchy


def _has_udt_code(
    resp: AgentResponse,
    udt: UDTHierarchy,
    *trigger_codes: str,
) -> bool:
    """响应 disease_code 是否匹配任一 trigger（支持子类层级匹配）。

    设计文档 02-核心流程与知识库.md:479 强制要求：UDT 编码不允许降级
    为硬编码匹配；子类编码自动匹配父类。
    """
    code = resp.disease_code or ""
    if not code:
        return False
    return udt.matches(code, *trigger_codes)


def _content_matches(resp: AgentResponse, *keywords: str) -> bool:
    """响应 content 是否包含任一关键词（不区分大小写）。

    仅作为 UDT 编码缺失时的 fallback（不推荐主路径使用，
    详见 02-核心流程与知识库.md:479 UDT 审定不允许降级为硬编码匹配
    的底线要求）。
    """
    content_lower = (resp.content or "").lower()
    return any(kw.lower() in content_lower for kw in keywords)


def _extract_lvef(text: str) -> int | None:
    """从文本中抽取 LVEF 数值（仅 fallback 用）。"""
    if not text:
        return None
    m = re.search(r"(?:LVEF|EF|射血分数)\s*[:=]?\s*(\d{1,3})", text, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _make_udt_condition(
    primary_codes: tuple[str, ...],
    udt: UDTHierarchy,
    keyword_fallback: tuple[str, ...] = (),
) -> Callable[[AgentResponse], bool]:
    """构造 UDT 优先 + 关键词 fallback 的条件函数。"""

    def _cond(resp: AgentResponse) -> bool:
        if _has_udt_code(resp, udt, *primary_codes):
            return True
        if keyword_fallback:
            return _content_matches(resp, *keyword_fallback)
        return False

    return _cond


def _rule_heart_liver_syndrome(udt: UDTHierarchy) -> CrossOrganRule:
    """规则 1：肝-心综合征（liver_cirrhosis / liver_congestion + 心脏异常）。

    设计文档 02-核心流程与知识库.md:469：
    > 肝脏诊断病种编码 ``liver_cirrhosis`` 或 ``liver_congestion`` →
    > 建议心脏评估（右心功能） → 禁忌：肝硬化患者慎用 β 受体阻滞剂
    """
    return CrossOrganRule(
        rule_id="heart_liver_syndrome",
        organ_a="liver",
        organ_b="cardiac",
        condition_a=_make_udt_condition(
            ("liver_cirrhosis", "liver_congestion"),
            udt,
            keyword_fallback=("肝硬化", "liver cirrhosis", "liver congestion", "肝淤血"),
        ),
        condition_b=lambda r: (
            _has_udt_code(r, udt, "cardiac_right_heart_failure", "cardiac_tricuspid_regurgitation")
            or (
                _content_matches(r, "心功能不全", "心衰", "heart failure", "LVEF", "射血分数")
                and (lambda lvef: lvef is not None and lvef < 50)(_extract_lvef(r.content or ""))
            )
        ),
        severity=ConsistencySeverity.CRITICAL,
        description="肝-心综合征：肝硬化/肝淤血合并心功能异常，需评估右心功能",
        suggested_action="建议心脏评估（右心功能、LVEF、肝静脉扩张）",
        contraindication="肝硬化患者慎用 β 受体阻滞剂（心输出量已降低）",
        udt_hierarchy=udt,
    )


def _rule_hyperthyroid_heart_disease(udt: UDTHierarchy) -> CrossOrganRule:
    """规则 2：甲亢性心脏病（thyroid_hyperthyroidism + cardiac_arrhythmia）。

    设计文档 02-核心流程与知识库.md:470。
    """
    return CrossOrganRule(
        rule_id="hyperthyroid_heart_disease",
        organ_a="thyroid",
        organ_b="cardiac",
        condition_a=_make_udt_condition(
            ("thyroid_hyperthyroidism",),
            udt,
            keyword_fallback=("甲亢", "hyperthyroidism", "graves", "graves病"),
        ),
        condition_b=_make_udt_condition(
            ("cardiac_arrhythmia",),
            udt,
            keyword_fallback=(
                "心律失常", "心衰", "heart failure", "arrhythmia",
                "房颤", "atrial fibrillation", "心动过速", "tachycardia",
            ),
        ),
        severity=ConsistencySeverity.WARNING,
        description="甲亢性心脏病：甲亢合并心律失常/心衰，需评估 β 受体阻滞剂 / 抗甲亢治疗",
        suggested_action="建议心脏评估（心律、心功能）",
        contraindication="甲亢患者禁用含碘造影剂",
        udt_hierarchy=udt,
    )


def _rule_hyperthyroid_liver_damage(udt: UDTHierarchy) -> CrossOrganRule:
    """规则 3：甲亢肝损害（thyroid_hyperthyroidism + 肝功能异常，P2-7 新增）。

    设计文档 02-核心流程与知识库.md:471：
    > 甲状腺诊断病种编码含 ``thyroid_hyperthyroidism`` 且肝功能异常 →
    > 建议肝脏评估 → 禁忌：甲亢药物（甲巯咪唑）可能加重肝损害
    """
    return CrossOrganRule(
        rule_id="hyperthyroid_liver_damage",
        organ_a="thyroid",
        organ_b="liver",
        condition_a=_make_udt_condition(
            ("thyroid_hyperthyroidism",),
            udt,
            keyword_fallback=("甲亢", "hyperthyroidism", "graves"),
        ),
        condition_b=_make_udt_condition(
            ("liver_congestion", "liver_cirrhosis", "liver_autoimmune_hepatitis"),
            udt,
            keyword_fallback=(
                "肝功能异常", "转氨酶升高", "ALT升高", "AST升高",
                "肝损害", "liver dysfunction", "hepatotoxicity",
            ),
        ),
        severity=ConsistencySeverity.WARNING,
        description="甲亢肝损害：甲亢合并肝功能异常，需评估甲状腺药物的肝毒性",
        suggested_action="建议肝脏评估（肝功能、肝酶谱）",
        contraindication="甲亢药物（甲巯咪唑）可能加重肝损害",
        udt_hierarchy=udt,
    )


def _rule_cardiac_liver_congestion(udt: UDTHierarchy) -> CrossOrganRule:
    """规则 4：心源性肝淤血（P2-7 新增）。

    设计文档 02-核心流程与知识库.md:472：
    > 心脏诊断病种编码 ``cardiac_right_heart_failure`` 或
    > ``cardiac_tricuspid_regurgitation`` → 建议肝脏评估（肝静脉扩张）
    > → 禁忌：抗凝治疗须评估肝功能
    """
    return CrossOrganRule(
        rule_id="cardiac_liver_congestion",
        organ_a="cardiac",
        organ_b="liver",
        condition_a=_make_udt_condition(
            ("cardiac_right_heart_failure", "cardiac_tricuspid_regurgitation"),
            udt,
            keyword_fallback=("右心衰", "right heart failure", "三尖瓣反流", "tricuspid regurgitation"),
        ),
        condition_b=_make_udt_condition(
            ("liver_congestion", "liver_cirrhosis"),
            udt,
            keyword_fallback=("肝淤血", "liver congestion", "肝静脉扩张", "hepatic vein dilation"),
        ),
        severity=ConsistencySeverity.WARNING,
        description="心源性肝淤血：右心衰/三尖瓣反流合并肝淤血，需评估肝脏血流",
        suggested_action="建议肝脏评估（肝静脉扩张、淤血性肝硬化）",
        contraindication="抗凝治疗须评估肝功能",
        udt_hierarchy=udt,
    )


def _rule_hypothyroid_pericardial_effusion(udt: UDTHierarchy) -> CrossOrganRule:
    """规则 5：甲减心包积液（thyroid_hypothyroidism + 心包积液）。

    设计文档 02-核心流程与知识库.md:473。
    """
    return CrossOrganRule(
        rule_id="hypothyroid_pericardial_effusion",
        organ_a="thyroid",
        organ_b="cardiac",
        condition_a=_make_udt_condition(
            ("thyroid_hypothyroidism",),
            udt,
            keyword_fallback=("甲减", "hypothyroidism", "桥本", "hashimoto"),
        ),
        condition_b=_make_udt_condition(
            ("cardiac_pericardial_effusion",),
            udt,
            keyword_fallback=("心包积液", "pericardial effusion", "pericardial"),
        ),
        severity=ConsistencySeverity.WARNING,
        description="甲减心包积液：甲减合并心包积液，需甲状腺激素替代治疗",
        suggested_action="建议心脏评估（心包积液筛查）",
        contraindication="甲减患者慎用镇静剂（呼吸抑制风险）",
        udt_hierarchy=udt,
    )


def _rule_autoimmune_hepatitis_thyroid(udt: UDTHierarchy) -> CrossOrganRule:
    """规则 6：自身免疫性肝病合并甲状腺异常（P2-7 新增）。

    设计文档 02-核心流程与知识库.md:474：
    > 肝脏诊断病种编码 ``liver_autoimmune_hepatitis`` → 建议甲状腺评估
    > （自身免疫性甲状腺炎筛查） → 禁忌：免疫抑制剂治疗须监测甲状腺功能
    """
    return CrossOrganRule(
        rule_id="autoimmune_hepatitis_thyroid",
        organ_a="liver",
        organ_b="thyroid",
        condition_a=_make_udt_condition(
            ("liver_autoimmune_hepatitis",),
            udt,
            keyword_fallback=("自身免疫性肝病", "autoimmune hepatitis", "AIH"),
        ),
        condition_b=_make_udt_condition(
            ("thyroid_hypothyroidism", "thyroid_hyperthyroidism", "thyroid_hashimoto"),
            udt,
            keyword_fallback=(
                "甲状腺功能异常", "thyroid dysfunction", "桥本", "hashimoto",
                "graves", "甲亢", "甲减",
            ),
        ),
        severity=ConsistencySeverity.WARNING,
        description="自身免疫性肝病合并甲状腺异常：自身免疫性肝炎常合并自身免疫性甲状腺炎",
        suggested_action="建议甲状腺评估（自身免疫性甲状腺炎筛查）",
        contraindication="免疫抑制剂治疗须监测甲状腺功能",
        udt_hierarchy=udt,
    )


def _rule_amiodarone_thyroid_dysfunction(udt: UDTHierarchy) -> CrossOrganRule:
    """规则 7：胺碘酮致甲状腺功能异常（P2-7 新增）。

    设计文档 02-核心流程与知识库.md:475：
    > 心脏诊断病种编码 ``cardiac_arrhythmia`` 且用药含胺碘酮 → 建议甲状腺
    > 评估（甲亢/甲减筛查） → 禁忌：胺碘酮所致甲亢禁用含碘药物
    """
    return CrossOrganRule(
        rule_id="amiodarone_thyroid_dysfunction",
        organ_a="cardiac",
        organ_b="thyroid",
        condition_a=lambda r: (
            _has_udt_code(r, udt, "cardiac_arrhythmia")
            or _content_matches(r, "心律失常", "arrhythmia", "房颤", "atrial fibrillation")
        ) and _content_matches(r, "胺碘酮", "amiodarone"),
        condition_b=_make_udt_condition(
            ("thyroid_hyperthyroidism", "thyroid_hypothyroidism"),
            udt,
            keyword_fallback=(
                "甲亢", "甲减", "hyperthyroidism", "hypothyroidism",
                "TSH异常", "T3异常", "T4异常",
            ),
        ),
        severity=ConsistencySeverity.WARNING,
        description="胺碘酮致甲状腺功能异常：长期使用胺碘酮可致甲亢/甲减",
        suggested_action="建议甲状腺评估（甲亢/甲减筛查、TSH监测）",
        contraindication="胺碘酮所致甲亢禁用含碘药物",
        udt_hierarchy=udt,
    )


def build_default_cross_organ_rules(
    udt: UDTHierarchy | None = None,
) -> list[CrossOrganRule]:
    """构造默认的 7 条跨器官规则（可注入 UDT 层级实例）。"""
    u = udt or DEFAULT_UDT_HIERARCHY
    return [
        _rule_heart_liver_syndrome(u),
        _rule_hyperthyroid_heart_disease(u),
        _rule_hyperthyroid_liver_damage(u),
        _rule_cardiac_liver_congestion(u),
        _rule_hypothyroid_pericardial_effusion(u),
        _rule_autoimmune_hepatitis_thyroid(u),
        _rule_amiodarone_thyroid_dysfunction(u),
    ]


# 向后兼容：覆盖占位的 DEFAULT_CROSS_ORGAN_RULES
DEFAULT_CROSS_ORGAN_RULES: list[CrossOrganRule] = build_default_cross_organ_rules()


# ----------------------------------------------------------------------
# ConsistencyChecker
# ----------------------------------------------------------------------


class ConsistencyChecker:
    """融合层一致性校验器（T-GA-FUS-01）。

    Args:
        rag_service: RAG Service（用于临床指南对比）。``None`` 时跳过
            指南对比。
        prior_confidence_gap_warning: 既往诊断 vs 当前 confidence 差距
            触发 WARNING 的阈值。
        prior_confidence_gap_critical: 既往诊断 vs 当前 confidence 差距
            触发 CRITICAL 的阈值。
        prior_grade_diff_warning: 既往诊断 vs 当前分级数字差触发 WARNING
            的阈值。
        prior_grade_diff_critical: 既往诊断 vs 当前分级数字差触发 CRITICAL
            的阈值。
        guideline_top_k: 临床指南 RAG 检索 top_k。
        cross_organ_rules: 跨器官关联规则列表（P1-4，默认 3 条关键规则）。
    """

    def __init__(
        self,
        rag_service: RAGService | None = None,
        prior_confidence_gap_warning: float = DEFAULT_PRIOR_CONFIDENCE_GAP_WARNING,
        prior_confidence_gap_critical: float = DEFAULT_PRIOR_CONFIDENCE_GAP_CRITICAL,
        prior_grade_diff_warning: int = DEFAULT_PRIOR_GRADE_DIFF_WARNING,
        prior_grade_diff_critical: int = DEFAULT_PRIOR_GRADE_DIFF_CRITICAL,
        guideline_top_k: int = DEFAULT_GUIDELINE_TOP_K,
        cross_organ_rules: list[CrossOrganRule] | None = None,
    ) -> None:
        if prior_confidence_gap_critical < prior_confidence_gap_warning:
            raise ValueError(
                "prior_confidence_gap_critical must be >= prior_confidence_gap_warning"
            )
        if prior_grade_diff_critical < prior_grade_diff_warning:
            raise ValueError(
                "prior_grade_diff_critical must be >= prior_grade_diff_warning"
            )
        if guideline_top_k <= 0:
            raise ValueError("guideline_top_k must be positive")

        self._rag_service = rag_service
        self._prior_confidence_gap_warning = prior_confidence_gap_warning
        self._prior_confidence_gap_critical = prior_confidence_gap_critical
        self._prior_grade_diff_warning = prior_grade_diff_warning
        self._prior_grade_diff_critical = prior_grade_diff_critical
        self._guideline_top_k = guideline_top_k
        # P1-4 修复：跨器官规则可注入
        self._cross_organ_rules: list[CrossOrganRule] = (
            list(cross_organ_rules) if cross_organ_rules is not None else DEFAULT_CROSS_ORGAN_RULES
        )

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    async def check(self, request: FusionRequest) -> ConsistencyCheckResult:
        """执行一致性校验。

        Args:
            request: 融合请求（含 specialist 响应 + 既往诊断 + 临床上下文）。

        Returns:
            :class:`ConsistencyCheckResult`：一致性校验结果。``is_consistent``
            为 True 当且仅当 ``severity != CRITICAL``。
        """
        details: list[str] = []
        inconsistent_pairs: list[tuple[str, str]] = []
        severity = ConsistencySeverity.INFO
        compared_with_prior = False
        compared_with_guidelines = False

        responses = request.agent_responses or []

        # 1) 与既往诊断对比
        if request.prior_diagnosis is not None:
            compared_with_prior = True
            prior_severity, prior_detail, prior_pairs = self._compare_with_prior(
                responses, request.prior_diagnosis
            )
            severity = _max_severity(severity, prior_severity)
            if prior_detail:
                details.append(prior_detail)
            inconsistent_pairs.extend(prior_pairs)

        # 2) 与临床指南对比（通过 RAG）
        if self._rag_service is not None and responses:
            try:
                compared_with_guidelines = True
                guideline_severity, guideline_detail, guideline_pairs = (
                    await self._compare_with_guidelines(responses, request)
                )
                severity = _max_severity(severity, guideline_severity)
                if guideline_detail:
                    details.append(guideline_detail)
                inconsistent_pairs.extend(guideline_pairs)
            except Exception as exc:
                logger.warning(
                    "consistency_guideline_check_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                details.append(f"guideline_check_skipped: {type(exc).__name__}")
        elif self._rag_service is not None:
            # 有 RAG 但无响应 → 仍标记 compared_with_guidelines=False
            compared_with_guidelines = False

        # 3) 跨器官一致性（基础互斥矩阵）
        if len(responses) >= 2:
            cross_severity, cross_detail, cross_pairs = self._check_cross_organ(
                responses
            )
            severity = _max_severity(severity, cross_severity)
            if cross_detail:
                details.append(cross_detail)
            inconsistent_pairs.extend(cross_pairs)

        # 构造最终结果
        is_consistent = severity != ConsistencySeverity.CRITICAL
        detail_str = "; ".join(details) if details else "consistency check passed"
        return ConsistencyCheckResult(
            is_consistent=is_consistent,
            severity=severity,
            inconsistent_organ_pairs=inconsistent_pairs,
            compared_with_prior=compared_with_prior,
            compared_with_guidelines=compared_with_guidelines,
            detail=detail_str,
        )

    # ------------------------------------------------------------------
    # 内部：与既往诊断对比
    # ------------------------------------------------------------------

    def _compare_with_prior(
        self,
        responses: list[AgentResponse],
        prior: dict[str, Any],
    ) -> tuple[ConsistencySeverity, str, list[tuple[str, str]]]:
        """与既往诊断对比。

        既往诊断格式约定（``prior_diagnosis`` dict）：
        ```
        {
            "organ": "thyroid" | "liver" | "cardiac",
            "grade": "TR4" | "LR-5" | "LVEF40" | ...,
            "confidence": 0.85,
        }
        ```

        偏差判定：
        - confidence 差距 ≥ critical → CRITICAL
        - confidence 差距 ≥ warning → WARNING
        - 分级数字差 ≥ critical → CRITICAL
        - 分级数字差 ≥ warning → WARNING
        - 否则 → INFO
        """
        if not responses:
            return (
                ConsistencySeverity.INFO,
                "no responses to compare with prior",
                [],
            )

        prior_organ = str(prior.get("organ", "")).strip().lower()
        prior_grade = str(prior.get("grade", "")).strip()
        prior_conf_raw = prior.get("confidence")
        try:
            prior_confidence = float(prior_conf_raw) if prior_conf_raw is not None else None
        except (TypeError, ValueError):
            prior_confidence = None

        if not prior_organ and not prior_grade and prior_confidence is None:
            return (
                ConsistencySeverity.INFO,
                "prior_diagnosis is empty; skipping prior comparison",
                [],
            )

        # 找到同器官的 specialist 响应
        matching = [r for r in responses if r.organ and r.organ.value == prior_organ]
        if not matching:
            return (
                ConsistencySeverity.INFO,
                f"no current response for prior organ={prior_organ or 'unknown'}",
                [],
            )

        worst_severity = ConsistencySeverity.INFO
        details: list[str] = []
        pairs: list[tuple[str, str]] = []
        for resp in matching:
            # confidence 差距
            if prior_confidence is not None:
                gap = abs(resp.confidence - prior_confidence)
                if gap >= self._prior_confidence_gap_critical:
                    worst_severity = _max_severity(
                        worst_severity, ConsistencySeverity.CRITICAL
                    )
                    details.append(
                        f"prior_confidence_gap_critical: prior={prior_confidence:.4f}, "
                        f"current={resp.confidence:.4f}, gap={gap:.4f}"
                    )
                elif gap >= self._prior_confidence_gap_warning:
                    worst_severity = _max_severity(
                        worst_severity, ConsistencySeverity.WARNING
                    )
                    details.append(
                        f"prior_confidence_gap_warning: prior={prior_confidence:.4f}, "
                        f"current={resp.confidence:.4f}, gap={gap:.4f}"
                    )
            # 分级码差异
            if prior_grade:
                current_grade = _extract_simple_grade(resp.content or "")
                if current_grade and prior_grade:
                    diff = _grade_diff(prior_grade, current_grade)
                    if diff is not None:
                        if diff >= self._prior_grade_diff_critical:
                            worst_severity = _max_severity(
                                worst_severity, ConsistencySeverity.CRITICAL
                            )
                            details.append(
                                f"prior_grade_diff_critical: prior={prior_grade}, "
                                f"current={current_grade}, diff={diff}"
                            )
                            pairs.append((prior_organ, prior_organ))
                        elif diff >= self._prior_grade_diff_warning:
                            worst_severity = _max_severity(
                                worst_severity, ConsistencySeverity.WARNING
                            )
                            details.append(
                                f"prior_grade_diff_warning: prior={prior_grade}, "
                                f"current={current_grade}, diff={diff}"
                            )
                            pairs.append((prior_organ, prior_organ))

        detail_str = (
            "prior comparison: " + "; ".join(details) if details else "prior comparison ok"
        )
        return worst_severity, detail_str, pairs

    # ------------------------------------------------------------------
    # 内部：与临床指南对比
    # ------------------------------------------------------------------

    async def _compare_with_guidelines(
        self,
        responses: list[AgentResponse],
        request: FusionRequest,
    ) -> tuple[ConsistencySeverity, str, list[tuple[str, str]]]:
        """与临床指南对比（P2-4 修复：语义级比对）。

        实现：
        1. 通过 RAG 检索当前器官的临床指南 chunks；
        2. **P2-4 修复**：从 response.content 抽取「建议 / 分级 / 禁忌」概念，
           从 guideline chunks 抽取同维度概念，做关键字级语义一致性比对：

           a. **建议 vs 指南冲突**（CRITICAL）：response 给出某建议（如
              "建议活检"），但同器官同分级的 guideline 明确反对
              （"随访即可，避免活检"）；
           b. **建议 vs 指南不匹配**（WARNING）：response 建议类型
              （活检/随访/手术/消融）与 guideline 的推荐不一致；
           c. **禁忌冲突**（CRITICAL）：response 计划触及 guideline
              列出的禁忌（如 "建议抗凝" 但 guideline 提示 "出血风险，
              慎用抗凝"）；
           d. **分级升级异常**（WARNING）：response 自报分级与 guideline
              描述的同特征分级差 ≥ 2。

        设计取舍：
        - 关键字级匹配而非 LLM 调用 — 保持 P99 < 5s 的延迟底线；
        - 失败安全：抽取失败 / chunk 为空 → INFO（不视为错误）；
        - 不会因指南对比引入新的 false positive（仅在有强证据冲突时
          上调 severity）。
        """
        if not responses:
            return ConsistencySeverity.INFO, "", []

        worst_severity = ConsistencySeverity.INFO
        details: list[str] = []
        pairs: list[tuple[str, str]] = []

        for resp in responses:
            if not resp.organ:
                continue
            organ_value = resp.organ.value
            query_text = (
                f"{organ_value} ultrasound clinical guideline for "
                f"{(resp.content or '')[:100]}"
            )
            try:
                retrieval_request = RetrievalRequest(
                    query=query_text,
                    organ=organ_value,
                    top_k=self._guideline_top_k,
                    use_query_enhancement=False,
                    use_reranking=False,
                    use_bm25_fallback=False,
                    use_multimodal=False,
                    request_id=request.request_id or None,
                    session_id=request.session_id or None,
                )
                result = await self._rag_service.retrieve(retrieval_request)  # type: ignore[union-attr]
            except Exception as exc:
                logger.warning(
                    "consistency_rag_retrieval_failed",
                    organ=organ_value,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                details.append(
                    f"guideline_rag_failed[{organ_value}]: {type(exc).__name__}"
                )
                continue
            if not result.chunks:
                details.append(
                    f"no_guideline_chunks[{organ_value}]: retrieval returned empty"
                )
                continue

            # P2-4 修复：语义级比对
            chunk_dicts = [
                {
                    "chunk_id": getattr(sc.chunk, "chunk_id", ""),
                    "content": getattr(sc.chunk, "content", ""),
                }
                for sc in (result.chunks or [])
            ]
            sem_severity, sem_detail, sem_pairs = _semantic_compare(
                response=resp,
                guideline_chunks=chunk_dicts,
                organ=organ_value,
            )
            if sem_severity != ConsistencySeverity.INFO or sem_detail:
                worst_severity = _max_severity(worst_severity, sem_severity)
                if sem_detail:
                    details.append(sem_detail)
                pairs.extend(sem_pairs)
            else:
                # 语义比对无发现 → 仅报告 chunk 数（与旧占位兼容）
                details.append(
                    f"guideline_chunks_found[{organ_value}]: {len(result.chunks)} "
                    f"chunk(s) (semantic match: no conflict)"
                )

        detail_str = (
            "guideline comparison: " + "; ".join(details) if details else "guideline comparison skipped"
        )
        return worst_severity, detail_str, pairs

    # ------------------------------------------------------------------
    # 内部：跨器官一致性
    # ------------------------------------------------------------------

    def _check_cross_organ(
        self,
        responses: list[AgentResponse],
    ) -> tuple[ConsistencySeverity, str, list[tuple[str, str]]]:
        """跨器官一致性：基础互斥矩阵 + 临床关联规则（P1-4 完整实现）。

        检查项：
        1. **基础互斥矩阵**：同一 patient 不同器官出现 "完全正常" + "严重
           异常" → WARNING；
        2. **临床关联规则**（P1-4）：按 ``self._cross_organ_rules`` 逐条
           检查，每条规则匹配后追加 severity / detail / pair。

        Returns:
            ``(severity, detail, inconsistent_pairs)``。
        """
        if len(responses) < 2:
            return ConsistencySeverity.INFO, "", []

        severity = ConsistencySeverity.INFO
        details: list[str] = []
        pairs: list[tuple[str, str]] = []

        # 1) 基础互斥矩阵：严重 vs 完全正常
        severe_keywords = ("严重", "critical", "malignant", "恶性", "重度")
        normal_keywords = ("正常", "normal", "no abnormality", "未见异常")

        severe_organs: list[str] = []
        normal_organs: list[str] = []
        for r in responses:
            if not r.organ:
                continue
            content_lower = (r.content or "").lower()
            if any(kw.lower() in content_lower for kw in severe_keywords) and r.confidence > 0.85:
                severe_organs.append(r.organ.value)
            if any(kw.lower() in content_lower for kw in normal_keywords) and r.confidence > 0.85:
                normal_organs.append(r.organ.value)

        if severe_organs and normal_organs and set(severe_organs) != set(normal_organs):
            for sv in severe_organs:
                for nm in normal_organs:
                    if sv != nm:
                        pairs.append((sv, nm))
            details.append(
                f"cross_organ_mix: severe={severe_organs}, normal={normal_organs}"
            )
            severity = _max_severity(severity, ConsistencySeverity.WARNING)

        # 2) 临床关联规则（P1-4）：逐条应用
        rule_severity, rule_details, rule_pairs = self._apply_cross_organ_rules(responses)
        severity = _max_severity(severity, rule_severity)
        if rule_details:
            details.extend(rule_details)
        pairs.extend(rule_pairs)

        detail_str = "; ".join(details) if details else ""
        return severity, detail_str, pairs

    def _apply_cross_organ_rules(
        self,
        responses: list[AgentResponse],
    ) -> tuple[ConsistencySeverity, list[str], list[tuple[str, str]]]:
        """应用 P1-4 跨器官关联规则集。

        遍历 ``self._cross_organ_rules``，对每条规则查找匹配的响应对
        （organ_a + organ_b 同时存在），调用条件函数判断是否触发。

        Returns:
            ``(severity, details, pairs)``：合并所有规则触发的结果。
        """
        severity = ConsistencySeverity.INFO
        details: list[str] = []
        pairs: list[tuple[str, str]] = []

        # 按 organ 索引响应，便于规则查找
        by_organ: dict[str, list[AgentResponse]] = {}
        for r in responses:
            if r.organ is None:
                continue
            by_organ.setdefault(r.organ.value, []).append(r)

        for rule in self._cross_organ_rules:
            resp_a_list = by_organ.get(rule.organ_a) or []
            resp_b_list = by_organ.get(rule.organ_b) or []
            if not resp_a_list or not resp_b_list:
                continue

            for ra in resp_a_list:
                for rb in resp_b_list:
                    try:
                        match_a = bool(rule.condition_a(ra))
                        match_b = bool(rule.condition_b(rb))
                    except Exception as exc:
                        logger.warning(
                            "cross_organ_rule_eval_failed",
                            rule_id=rule.rule_id,
                            error=str(exc),
                            error_type=type(exc).__name__,
                        )
                        continue
                    if match_a and match_b:
                        severity = _max_severity(severity, rule.severity)
                        details.append(
                            f"{rule.rule_id}: {rule.description}"
                        )
                        pairs.append((rule.organ_a, rule.organ_b))
                        # 一对 organ 组合只报告一次
                        break
                else:
                    continue
                break

        return severity, details, pairs


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------


def _max_severity(a: ConsistencySeverity, b: ConsistencySeverity) -> ConsistencySeverity:
    """返回 ``a`` / ``b`` 中较严重的那个。"""
    order = {
        ConsistencySeverity.INFO: 0,
        ConsistencySeverity.WARNING: 1,
        ConsistencySeverity.CRITICAL: 2,
    }
    return a if order[a] >= order[b] else b


_GRADE_NUM_RE = re.compile(r"(\d+)")


def _extract_simple_grade(text: str) -> str | None:
    """从文本中抽取「数字」形式分级（最后出现的数字）。"""
    if not text:
        return None
    matches = _GRADE_NUM_RE.findall(text)
    if not matches:
        return None
    return matches[-1]


def _grade_diff(prior: str, current: str) -> int | None:
    """两个分级码的数字差（绝对值）。无法解析 → 返回 None。"""
    p = _extract_simple_grade(prior)
    c = _extract_simple_grade(current)
    if p is None or c is None:
        return None
    try:
        return abs(int(p) - int(c))
    except ValueError:
        return None


# ----------------------------------------------------------------------
# P2-4 修复：语义级指南比对（_compare_with_guidelines 内部使用）
# ----------------------------------------------------------------------

#: 建议类型 → 关键词集合（中文 + 英文）
_RECOMMENDATION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "biopsy": ("活检", "FNA", "穿刺", "biopsy", "FNAB"),
    "follow_up": ("随访", "复查", "观察", "follow-up", "followup", "monitor"),
    "surgery": ("手术", "切除", "甲状腺全切", "surgery", "resection", "thyroidectomy"),
    "ablation": ("消融", "ablation", "射频", "RFA"),
    "medication": ("用药", "服药", "药物治疗", "medication", "drug therapy"),
}

#: 禁忌关键词（出现这些词 + 否定/避免语境 → 提示该建议为禁忌）
_CONTRAINDICATION_KEYWORDS: tuple[str, ...] = (
    "禁忌", "禁忌症", "禁用", "慎用", "避免", "不宜", "不可", "无需",
    "contraindication", "contraindicated", "avoid", "do not",
)

#: **硬禁忌**关键词：仅出现这些词才视为真正禁忌冲突 → CRITICAL。
#: 软否定（如"无需"/"避免"）若 chunk 同时有正向替代建议，则属于偏好
#: 而非禁忌（已在 #1 建议不匹配分支 WARNING）。
_HARD_CONTRAINDICATION_KEYWORDS: tuple[str, ...] = (
    "禁忌", "禁忌症", "禁用", "不可",
    "contraindication", "contraindicated",
)

#: 抗凝 / 出血风险相关关键词（高风险操作）
_ANTICOAGULANT_RISK_KEYWORDS: tuple[str, ...] = (
    "抗凝", "抗血小板", "阿司匹林", "华法林", "肝素",
    "anticoagulation", "anticoagulant", "aspirin", "warfarin", "heparin",
    "出血风险", "bleeding risk", "coagulopathy",
)


def _is_keyword_negated(
    text: str, kw_start: int, kw_len: int, window: int = 10
) -> bool:
    """检查 text[kw_start:kw_start+kw_len] 附近 window 字符内是否被否定。

    用于 ``_extract_recommendation`` 跳过"避免 X"/"X 禁忌"中的 X。

    P2-4 修复：仅检查**关键词之前**的 window 字符，避免"应 X 即可，避免 Y"
    这种结构中，X 被错误地标记为被否定（实际上"避免"修饰的是 Y）。
    """
    if kw_start < 0 or kw_len <= 0:
        return False
    # 仅向前看：window 字符内的前缀上下文
    start = max(0, kw_start - window)
    end = kw_start
    window_text = text[start:end]
    return any(neg.lower() in window_text for neg in _CONTRAINDICATION_KEYWORDS)


def _extract_recommendation(text: str) -> str | None:
    """从文本中抽取主要建议类型（否定感知）。

    优先级：biopsy > surgery > ablation > medication > follow_up（前者更具
    侵入性，匹配后立即返回）。若匹配到的关键词处于否定上下文（如
    "避免活检"、"手术禁忌"），则跳过该关键词继续查找下一个非否定建议。
    """
    if not text:
        return None
    text_lower = text.lower()
    for rec_type in ("biopsy", "surgery", "ablation", "medication", "follow_up"):
        keywords = _RECOMMENDATION_KEYWORDS[rec_type]
        for kw in keywords:
            idx = text_lower.find(kw.lower())
            if idx < 0:
                continue
            # 跳过被否定的关键词（如"避免活检"）
            if _is_keyword_negated(text_lower, idx, len(kw), window=10):
                continue
            return rec_type
    return None


def _extract_grade_label(text: str) -> str | None:
    """从文本中抽取器官分级标签（保留长前缀形式）。

    优先匹配器官特定分级（TI-RADS / TR / LI-RADS / LR / LVEF / EF），
    **保留原始前缀形式**（如 "TI-RADS 4" → "ti-rads4"，"TR5" → "tr5"），
    以便测试和审计可追溯原始表达。

    匹配顺序：先匹配长前缀（ti-rads / li-rads），后匹配短前缀（tr / lr），
    避免短前缀误吞长前缀（如 "tr" 不应匹配 "ti-rads"）。

    返回小写字符串便于比较。
    """
    if not text:
        return None
    text_lower = text.lower()
    # 1) 器官特定分级（顺序重要：先匹配长前缀，再匹配短前缀）
    for prefix in (
        "ti-rads",
        "tr",
        "li-rads",
        "lr",
        "lvef",
        "ef",
    ):
        m = re.search(rf"\b{re.escape(prefix)}[\s-]*(\d+)", text_lower)
        if m:
            return f"{prefix}{m.group(1)}"
    # 2) 数字形式（最后出现）
    return _extract_simple_grade(text)


def _extract_negation_window(text: str, keyword: str, window: int = 30) -> bool:
    """检查 keyword 附近 window 字符内是否出现否定/禁忌表述。

    同时检查**前后**两个方向（中文里"X 为禁忌" / "避免 X" 都常见）。
    P2-4 修正：原实现仅看 keyword 之前的窗口，导致 "手术为禁忌" 中
    "禁忌" 在 keyword 之后的情况漏检。
    """
    if not text or not keyword:
        return False
    text_lower = text.lower()
    kw_lower = keyword.lower()
    idx = text_lower.find(kw_lower)
    if idx < 0:
        return False
    # 同时看 keyword 前后 window 字符
    start = max(0, idx - window)
    end = min(len(text_lower), idx + len(keyword) + window)
    window_text = text_lower[start:end]
    return any(neg.lower() in window_text for neg in _CONTRAINDICATION_KEYWORDS)


def _has_hard_contraindication_window(
    text: str, keyword: str, window: int = 30
) -> bool:
    """检查 keyword 附近是否出现**硬禁忌**关键词（禁忌/禁用/不可/...）。

    软否定（无需/避免/慎用）不视为硬禁忌，避免与"建议不匹配"分支重叠。
    """
    if not text or not keyword:
        return False
    text_lower = text.lower()
    kw_lower = keyword.lower()
    idx = text_lower.find(kw_lower)
    if idx < 0:
        return False
    # 同时看 keyword 前后 window 字符
    start = max(0, idx - window)
    end = min(len(text_lower), idx + len(keyword) + window)
    window_text = text_lower[start:end]
    return any(neg.lower() in window_text for neg in _HARD_CONTRAINDICATION_KEYWORDS)


def _has_anticoagulant_risk(text: str) -> bool:
    """检查文本是否提及抗凝/出血风险。"""
    if not text:
        return False
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in _ANTICOAGULANT_RISK_KEYWORDS)


def _semantic_compare(
    response: AgentResponse,
    guideline_chunks: list[dict[str, str]],
    organ: str,
) -> tuple[ConsistencySeverity, str, list[tuple[str, str]]]:
    """对单条 response 与其 guideline chunks 做关键字级语义比对。

    Args:
        response: specialist 响应。
        guideline_chunks: 从 RAG 检索到的指南 chunk 列表，每项含
            ``chunk_id`` / ``content``。
        organ: 器官值（thyroid / liver / cardiac）。

    Returns:
        ``(severity, detail, pairs)``。
    """
    if not guideline_chunks:
        return ConsistencySeverity.INFO, "", []

    resp_text = response.content or ""
    resp_rec = _extract_recommendation(resp_text)
    resp_grade = _extract_grade_label(resp_text)
    # 抽取 resp 实际匹配到的关键词（用于禁忌窗口搜索，避免英文 category 在中文 chunk 中找不到）
    resp_rec_keyword = _first_recommendation_keyword(resp_text, resp_rec) if resp_rec else None

    worst_severity = ConsistencySeverity.INFO
    details: list[str] = []
    pairs: list[tuple[str, str]] = []

    for chunk in guideline_chunks:
        chunk_text = chunk.get("content", "")
        chunk_id = chunk.get("chunk_id", "")
        if not chunk_text:
            continue

        chunk_rec = _extract_recommendation(chunk_text)
        chunk_grade = _extract_grade_label(chunk_text)

        # 1) 建议 vs 指南冲突检测
        if resp_rec and chunk_rec and resp_rec != chunk_rec:
            # 仅当 chunk 提及同器官分级（或 resp_grade 与 chunk_grade 一致）
            # 时才视为针对同一临床场景的建议冲突
            grade_match = (
                resp_grade is not None
                and chunk_grade is not None
                and resp_grade == chunk_grade
            )
            # 若 chunk 中明确说"应 X" / "推荐 X" 且 resp 是 Y → 不匹配
            if grade_match:
                # 同一分级、不同建议 → 关键冲突
                if _is_strong_recommendation_mismatch(resp_rec, chunk_rec):
                    worst_severity = _max_severity(
                        worst_severity, ConsistencySeverity.CRITICAL
                    )
                    details.append(
                        f"guideline_recommendation_conflict[{organ}]: "
                        f"resp={resp_rec}, guideline={chunk_rec} "
                        f"(grade={resp_grade}, chunk_id={chunk_id})"
                    )
                    pairs.append((organ, organ))
                else:
                    # 一般不匹配（如 biopsy vs surgery）→ WARNING
                    worst_severity = _max_severity(
                        worst_severity, ConsistencySeverity.WARNING
                    )
                    details.append(
                        f"guideline_recommendation_mismatch[{organ}]: "
                        f"resp={resp_rec}, guideline={chunk_rec} "
                        f"(grade={resp_grade}, chunk_id={chunk_id})"
                    )

        # 2) 禁忌冲突：response 建议触及 guideline 列出的禁忌
        # **P2-4 修正 v3**：使用**硬禁忌**关键词（禁忌/禁用/不可/...），
        # 避免软否定（无需/避免）误报为禁忌冲突。软否定+正向替代建议
        # 的情况已在 #1 建议不匹配分支 WARNING。
        #
        # 触发场景（CRITICAL）：
        #   resp: "建议立即手术切除" → resp_rec=surgery
        #   chunk: "严重合并症患者手术为禁忌" → "手术"前后出现"禁忌" → 触发
        #
        # 不触发场景（WARNING 而非 CRITICAL）：
        #   resp: "建议 FNA 活检" → resp_rec=biopsy
        #   chunk: "建议手术切除，无需活检" → "活检"前有"无需"（软否定），
        #     且 chunk 有正向 rec=surgery → 走 #1 建议不匹配 → WARNING
        if resp_rec_keyword and _has_hard_contraindication_window(
            chunk_text, resp_rec_keyword
        ):
            worst_severity = _max_severity(
                worst_severity, ConsistencySeverity.CRITICAL
            )
            details.append(
                f"guideline_contraindication[{organ}]: "
                f"resp_rec={resp_rec} flagged as contraindicated "
                f"(chunk_id={chunk_id})"
            )
            pairs.append((organ, organ))

        # 3) 抗凝风险 vs 活检冲突：response 建议活检但 chunk 提示抗凝/出血风险
        if resp_rec == "biopsy" and _has_anticoagulant_risk(chunk_text):
            worst_severity = _max_severity(
                worst_severity, ConsistencySeverity.WARNING
            )
            details.append(
                f"guideline_anticoagulant_risk[{organ}]: "
                f"biopsy suggested but anticoagulant/bleeding risk noted "
                f"(chunk_id={chunk_id})"
            )
            pairs.append((organ, organ))

        # 4) 分级升级异常：response 分级与 chunk 描述的分级差 ≥ 2
        if (
            resp_grade
            and chunk_grade
            and _same_grade_family(resp_grade, chunk_grade)
        ):
            diff = _grade_diff(resp_grade, chunk_grade)
            if diff is not None and diff >= 2:
                worst_severity = _max_severity(
                    worst_severity, ConsistencySeverity.WARNING
                )
                details.append(
                    f"guideline_grade_escalation[{organ}]: "
                    f"resp={resp_grade}, guideline={chunk_grade}, diff={diff} "
                    f"(chunk_id={chunk_id})"
                )
                pairs.append((organ, organ))

    detail_str = "; ".join(details) if details else ""
    return worst_severity, detail_str, pairs


def _first_recommendation_keyword(
    text: str, rec_type: str | None
) -> str | None:
    """返回 text 中第一个属于 rec_type 的关键词（用于禁忌窗口搜索）。"""
    if not text or not rec_type or rec_type not in _RECOMMENDATION_KEYWORDS:
        return None
    text_lower = text.lower()
    for kw in _RECOMMENDATION_KEYWORDS[rec_type]:
        idx = text_lower.find(kw.lower())
        if idx >= 0 and not _is_keyword_negated(text_lower, idx, len(kw), window=10):
            return kw
    # 找不到非否定关键词 → 返回第一个关键词（让禁忌检测仍可触发）
    return _RECOMMENDATION_KEYWORDS[rec_type][0]


def _is_strong_recommendation_mismatch(resp_rec: str, chunk_rec: str) -> bool:
    """判断两个建议类型是否属于「强冲突」（活检 vs 随访）。"""
    strong_pairs = {
        frozenset({"biopsy", "follow_up"}),
        frozenset({"surgery", "follow_up"}),
        frozenset({"biopsy", "medication"}),
    }
    return frozenset({resp_rec, chunk_rec}) in strong_pairs


def _same_grade_family(grade_a: str, grade_b: str) -> bool:
    """判断两个分级是否属于同一分级体系（同器官族）。

    特殊处理：TI-RADS / TR（甲状腺）、LI-RADS / LR（肝脏）属于同一族，
    仅作严格 alpha 前缀匹配会漏检（``ti-rads`` vs ``tr``）。本函数
    用一套 alias 映射做族归一化：

    - ``ti-rads`` / ``tr`` → 甲状腺族（``thyroid``）
    - ``li-rads`` / ``lr`` → 肝脏族（``liver``）
    - ``lvef`` / ``ef`` → 心脏族（``cardiac``）
    """
    if not grade_a or not grade_b:
        return False
    return _normalize_grade_family(grade_a) == _normalize_grade_family(grade_b)


#: 分级前缀 → 族别名（用于 _same_grade_family）
_GRADE_FAMILY_ALIASES: dict[str, str] = {
    "ti-rads": "thyroid",
    "tr": "thyroid",
    "li-rads": "liver",
    "lr": "liver",
    "lvef": "cardiac",
    "ef": "cardiac",
}


def _normalize_grade_family(grade: str) -> str:
    """返回 ``grade`` 的族别名（``ti-rads3`` / ``tr5`` → ``thyroid``）。"""
    if not grade:
        return ""
    # 按长前缀优先匹配（避免 ``tr`` 误吞 ``ti-rads``）
    for prefix in ("ti-rads", "li-rads", "lvef", "tr", "lr", "ef"):
        if grade.startswith(prefix):
            return _GRADE_FAMILY_ALIASES.get(prefix, prefix)
    # 退化：返回原始 alpha 前缀
    m = re.match(r"([a-z]+)", grade)
    return m.group(1) if m else ""


__all__ = [
    "DEFAULT_CROSS_ORGAN_RULES",
    "DEFAULT_GUIDELINE_TOP_K",
    "DEFAULT_PRIOR_CONFIDENCE_GAP_CRITICAL",
    "DEFAULT_PRIOR_CONFIDENCE_GAP_WARNING",
    "DEFAULT_PRIOR_GRADE_DIFF_CRITICAL",
    "DEFAULT_PRIOR_GRADE_DIFF_WARNING",
    "ConsistencyChecker",
    "CrossOrganRule",
]
