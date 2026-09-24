"""规则引擎抽象层（二期 §11）。

规格要求「一期已定义 RuleEngine 抽象接口」——核对后**实际不存在**（全仓 0 命中），
既有规则是若干各自为政的具体实现：``ContraindicationEngine``、
``SGDesensitizationRule``、``DesensitizationRule``。本模块提供统一协议与默认解释器，
并给出把既有禁忌引擎适配到该协议的适配器，使"换引擎"有可验证的等价基线。

安全语义必须逐字继承，不能"顺手优化"
------------------------------------
既有禁忌引擎在**既无 patient_conditions 又无 clinical_context** 时返回 True（命中
规则），即"缺数据时保守拦截"。这条语义看着别扭（没有依据却判命中），但它是安全侧
的兜底：宁可多提示一条禁忌，也不要因为数据缺失而漏掉。新引擎原样保留，并在测试里
用"等价性"锁住——换引擎时任何行为漂移都会被 200 条规则 × 1000 组输入的对拍抓到。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, runtime_checkable

import structlog

logger = structlog.get_logger()

# 与 safety_gateway/contraindication.py 保持同口径：条件别名表是"医学表述不一致"的
# 现实补偿（同一情况在不同来源里叫法不同），两边不一致会导致换引擎后拦截率变化。
CONDITION_ALIASES: Dict[str, List[str]] = {
    "pregnancy": [
        "pregnancy",
        "pregnancy_radiation",
        "pregnancy_ct",
        "pregnancy_stress_echo",
        "gestation",
        "pregnant",
    ],
    "anticoagulant_therapy": [
        "anticoagulant_therapy",
        "anticoagulant",
        "anticoagulation",
        "warfarin",
        "heparin",
        "anticoagulation_therapy",
    ],
    "coagulopathy": [
        "coagulopathy",
        "coagulation_disorder",
        "bleeding_disorder",
        "clotting_disorder",
        "coagulation_defect",
    ],
    "contrast_allergy": [
        "contrast_allergy",
        "contrast_reaction",
        "iodine_allergy",
        "contrast_intolerance",
    ],
    "pregnancy_ct": ["pregnancy_ct", "pregnancy_radiation", "pregnancy_ct_scan"],
    "pregnancy_stress_echo": [
        "pregnancy_stress_echo",
        "pregnancy_stress",
        "pregnancy_exercise_echo",
    ],
    "severe_aortic_stenosis": [
        "severe_aortic_stenosis",
        "aortic_stenosis_severe",
        "critical_aortic_stenosis",
    ],
}

GLOBAL_SCOPE = "global"


@dataclass(frozen=True)
class Rule:
    """与引擎无关的规则定义（引擎只负责解释它）。"""

    rule_id: str
    condition: str
    description: str = ""
    severity: str = "high"
    organ: str = GLOBAL_SCOPE
    enabled: bool = True

    @property
    def scope(self) -> str:
        return self.organ or GLOBAL_SCOPE


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    condition: str
    severity: str = "high"
    description: str = ""
    organ: str = GLOBAL_SCOPE


@runtime_checkable
class RuleEngine(Protocol):
    """规则引擎协议：加载规则 + 对事实求值。"""

    @property
    def engine_name(self) -> str:  # pragma: no cover - 协议声明
        ...

    def load(self, rules: Sequence[Rule]) -> None:  # pragma: no cover - 协议声明
        ...

    def evaluate(
        self, facts: Mapping[str, Any], *, organ: Optional[str] = None
    ) -> List[RuleMatch]:  # pragma: no cover - 协议声明
        ...


def match_condition(condition: str, facts: Mapping[str, Any]) -> bool:
    """条件匹配（与既有禁忌引擎逐字同义，含缺数据兜底 True）。"""
    if "patient_conditions" in facts:
        patient_conditions = facts["patient_conditions"]
        if isinstance(patient_conditions, list):
            aliases = CONDITION_ALIASES.get(condition, [condition])
            return any(item in aliases for item in patient_conditions)

    if "clinical_context" in facts:
        clinical_context = facts["clinical_context"]
        if isinstance(clinical_context, dict):
            return condition in clinical_context.get("conditions", [])

    logger.warning("rule_engine_no_condition_data", condition=condition)
    return True


class SimpleRuleEngine:
    """默认解释器：内存规则表 + 线性求值。

    实现刻意保持朴素——二期要的是"可替换的抽象"，不是"更聪明的引擎"；任何优化都
    必须在等价性验证（``verify_equivalence``）之后才允许替换默认实现。
    """

    def __init__(self, rules: Optional[Sequence[Rule]] = None) -> None:
        self._global_rules: List[Rule] = []
        self._organ_rules: Dict[str, List[Rule]] = {}
        if rules:
            self.load(rules)

    @property
    def engine_name(self) -> str:
        return "simple-v1"

    def load(self, rules: Sequence[Rule]) -> None:
        self._global_rules = []
        self._organ_rules = {}
        for rule in rules:
            if rule.scope == GLOBAL_SCOPE:
                self._global_rules.append(rule)
            else:
                self._organ_rules.setdefault(rule.scope, []).append(rule)

    def rules(self) -> List[Rule]:
        all_rules = list(self._global_rules)
        for organ_rules in self._organ_rules.values():
            all_rules.extend(organ_rules)
        return all_rules

    def evaluate(
        self, facts: Mapping[str, Any], *, organ: Optional[str] = None
    ) -> List[RuleMatch]:
        matches: List[RuleMatch] = []
        candidates: List[Rule] = list(self._global_rules)
        if organ:
            candidates.extend(self._organ_rules.get(organ, []))
        for rule in candidates:
            if rule.enabled and match_condition(rule.condition, facts):
                matches.append(
                    RuleMatch(
                        rule_id=rule.rule_id,
                        condition=rule.condition,
                        severity=rule.severity,
                        description=rule.description,
                        organ=rule.scope,
                    )
                )
        return matches


class ContraindicationRuleEngineAdapter:
    """把既有 ``ContraindicationEngine`` 适配到 :class:`RuleEngine` 协议。

    适配器不重写匹配逻辑，只做数据结构转换：等价性验证需要一个"参照实现"，
    如果参照实现也重写了，验证就变成了自证。
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    @property
    def engine_name(self) -> str:
        return f"legacy-contraindication:{type(self._engine).__name__}"

    def load(self, rules: Sequence[Rule]) -> None:
        from safety_gateway.contraindication import ContraindicationRule

        global_rules = []
        organ_rules: Dict[str, List[Any]] = {}
        for rule in rules:
            converted = ContraindicationRule(
                id=rule.rule_id,
                organ=rule.scope,
                condition=rule.condition,
                description=rule.description,
                severity=rule.severity,
                enabled=rule.enabled,
            )
            if rule.scope == GLOBAL_SCOPE:
                global_rules.append(converted)
            else:
                organ_rules.setdefault(rule.scope, []).append(converted)
        self._engine._global_rules = global_rules
        self._engine._organ_rules = organ_rules
        self._engine._loaded = True

    def evaluate(
        self, facts: Mapping[str, Any], *, organ: Optional[str] = None
    ) -> List[RuleMatch]:
        matches = self._engine.check(organ or "", dict(facts))
        return [
            RuleMatch(
                rule_id=match.rule_id,
                condition=match.condition,
                severity=match.severity,
                description=match.description,
                organ=match.organ,
            )
            for match in matches
        ]


def get_rule_engine() -> RuleEngine:
    """默认引擎单例（换实现只需换这里的一行）。"""
    global _engine
    if _engine is None:
        _engine = SimpleRuleEngine()
    return _engine


def set_rule_engine(engine: Optional[RuleEngine]) -> None:
    global _engine
    _engine = engine


_engine: Optional[SimpleRuleEngine] = None


@dataclass
class EquivalenceReport:
    """两个引擎在同一批输入上的对拍结果。"""

    total_inputs: int = 0
    matched_inputs: int = 0
    mismatches: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def equivalent(self) -> bool:
        return not self.mismatches

    @property
    def mismatch_rate(self) -> float:
        return len(self.mismatches) / self.total_inputs if self.total_inputs else 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "total_inputs": self.total_inputs,
            "matched_inputs": self.matched_inputs,
            "equivalent": self.equivalent,
            "mismatch_rate": round(self.mismatch_rate, 6),
            "mismatch_count": len(self.mismatches),
            "mismatches": self.mismatches[:20],
        }


def verify_equivalence(
    engine: RuleEngine,
    reference: RuleEngine,
    rules: Sequence[Rule],
    inputs: Sequence[Mapping[str, Any]],
    *,
    organs: Optional[Sequence[Optional[str]]] = None,
    max_examples: int = 20,
) -> EquivalenceReport:
    """对拍两个引擎：每条输入的**命中规则集合**必须完全一致。

    比较命中集合而不是"是否命中"：只比布尔值会漏掉"命中了不同规则"这类偏差，
    而安全侧的偏差正是体现在"该拦 A 却拦了 B"。

    比对前对规则 ID 排序：命中顺序取决于规则表遍历顺序，而调用方只关心集合。
    """
    engine.load(rules)
    reference.load(rules)

    report = EquivalenceReport()
    organ_choices = list(organs) if organs is not None else [None]

    for index, facts in enumerate(inputs):
        organ = organ_choices[index % len(organ_choices)]
        expected = sorted(match.rule_id for match in reference.evaluate(facts, organ=organ))
        actual = sorted(match.rule_id for match in engine.evaluate(facts, organ=organ))
        report.total_inputs += 1
        if expected == actual:
            if expected:
                report.matched_inputs += 1
            continue
        if len(report.mismatches) < max_examples:
            report.mismatches.append(
                {
                    "input_index": index,
                    "organ": organ,
                    "facts": dict(facts),
                    "expected": expected,
                    "actual": actual,
                }
            )

    if not report.equivalent:
        logger.error(
            "rule_engine_equivalence_failed",
            total=report.total_inputs,
            mismatches=len(report.mismatches),
            engine=engine.engine_name,
            reference=reference.engine_name,
        )
    return report
