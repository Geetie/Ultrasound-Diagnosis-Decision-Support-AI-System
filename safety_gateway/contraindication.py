"""Safety Gateway禁忌校验引擎模块

负责检测患者病情与诊断结论之间的禁忌关系，确保诊断输出的安全性。

禁忌规则支持：
- 按器官分类的特定禁忌规则
- 全局禁忌规则（适用于所有器官）
- 规则优先级和启用/禁用状态
- 多条件匹配逻辑

数据模型：
- ContraindicationRule: 禁忌规则定义
- ContraindicationMatch: 禁忌匹配结果
"""

import os
import threading
from typing import Any, ClassVar

import structlog
import yaml
from pydantic import BaseModel

logger = structlog.get_logger()


class ContraindicationRule(BaseModel):
    id: str
    organ: str
    condition: str
    description: str
    severity: str = "high"
    enabled: bool = True
    source_reference: str | None = None


class ContraindicationMatch(BaseModel):
    rule_id: str
    organ: str
    condition: str
    description: str
    severity: str


class ContraindicationEngine:

    def __init__(self):
        self._organ_rules: dict[str, list[ContraindicationRule]] = {}
        self._global_rules: list[ContraindicationRule] = []
        self._loaded = False
        self._load_lock = threading.Lock()
        self._load_default_rules()

    def load_rules(self, rules_path: str | None = None) -> None:
        if rules_path and os.path.exists(rules_path):
            try:
                with open(rules_path, encoding="utf-8") as f:
                    rules_data = yaml.safe_load(f)
                self._parse_rules(rules_data)
                self._loaded = True
                logger.info(
                    "sg_contraindication_rules_loaded",
                    path=rules_path,
                    organ_count=len(self._organ_rules),
                    global_count=len(self._global_rules),
                )
            except Exception as e:
                logger.error("sg_contraindication_rules_load_failed", error=str(e))
                self._load_default_rules()
        else:
            self._load_default_rules()

    def _parse_rules(self, rules_data: dict[str, Any]) -> None:
        self._organ_rules = {}
        self._global_rules = []

        for rule_data in rules_data.get("rules", []):
            rule = ContraindicationRule(**rule_data)
            if not rule.enabled:
                continue
            if rule.organ == "global":
                self._global_rules.append(rule)
            else:
                if rule.organ not in self._organ_rules:
                    self._organ_rules[rule.organ] = []
                self._organ_rules[rule.organ].append(rule)

    def _load_default_rules(self) -> None:
        from safety_gateway.sg_config import sg_settings as settings
        yaml_path = getattr(settings, 'contraindication_rules_safety_path', None)
        if yaml_path and os.path.exists(yaml_path):
            try:
                with open(yaml_path, encoding="utf-8") as f:
                    rules_data = yaml.safe_load(f)
                self._parse_rules(rules_data)
                self._loaded = True
                logger.info("sg_contraindication_default_rules_from_yaml", path=yaml_path)
                return
            except Exception as e:
                logger.error("sg_contraindication_default_yaml_load_failed", error=str(e), path=yaml_path)

        self._organ_rules = {
            "thyroid": [
                ContraindicationRule(
                    id="thyroid_pregnancy_fna",
                    organ="thyroid",
                    condition="pregnancy",
                    description="妊娠期甲状腺结节FNA需谨慎评估",
                    severity="high",
                ),
                ContraindicationRule(
                    id="thyroid_anticoagulant_biopsy",
                    organ="thyroid",
                    condition="anticoagulant_therapy",
                    description="抗凝治疗期间穿刺活检出血风险增加",
                    severity="high",
                ),
            ],
            "liver": [
                ContraindicationRule(
                    id="liver_coagulopathy_biopsy",
                    organ="liver",
                    condition="coagulopathy",
                    description="凝血功能障碍肝脏活检禁忌",
                    severity="high",
                ),
                ContraindicationRule(
                    id="liver_pregnancy_ct",
                    organ="liver",
                    condition="pregnancy_ct",
                    description="妊娠期肝脏CT增强扫描需谨慎",
                    severity="medium",
                ),
            ],
            "cardiac": [
                ContraindicationRule(
                    id="cardiac_pregnancy_stress_echo",
                    organ="cardiac",
                    condition="pregnancy_stress_echo",
                    description="妊娠期负荷超声心动图需谨慎",
                    severity="medium",
                ),
                ContraindicationRule(
                    id="cardiac_severe_aortic_stenosis",
                    organ="cardiac",
                    condition="severe_aortic_stenosis",
                    description="严重主动脉瓣狭窄运动负荷禁忌",
                    severity="high",
                ),
            ],
        }
        self._global_rules = [
            ContraindicationRule(
                id="global_pregnancy_radiation",
                organ="global",
                condition="pregnancy_radiation",
                description="妊娠期放射性检查需严格评估风险收益比",
                severity="high",
            ),
            ContraindicationRule(
                id="global_contrast_allergy",
                organ="global",
                condition="contrast_allergy",
                description="造影剂过敏史患者增强扫描禁忌",
                severity="high",
            ),
        ]
        self._loaded = True
        logger.info("sg_contraindication_default_rules_loaded")

    def check(
        self,
        organ: str,
        diagnosis_data: dict[str, Any],
    ) -> list[ContraindicationMatch]:
        if not self._loaded:
            with self._load_lock:
                if not self._loaded:
                    self._load_default_rules()

        matches = []

        for rule in self._global_rules:
            if rule.enabled and self._match_condition(rule.condition, diagnosis_data):
                matches.append(ContraindicationMatch(
                    rule_id=rule.id,
                    organ=rule.organ,
                    condition=rule.condition,
                    description=rule.description,
                    severity=rule.severity,
                ))

        organ_rules = self._organ_rules.get(organ, [])
        for rule in organ_rules:
            if rule.enabled and self._match_condition(rule.condition, diagnosis_data):
                matches.append(ContraindicationMatch(
                    rule_id=rule.id,
                    organ=rule.organ,
                    condition=rule.condition,
                    description=rule.description,
                    severity=rule.severity,
                ))

        return matches

    _CONDITION_ALIASES: ClassVar[dict[str, list[str]]] = {
        "pregnancy": ["pregnancy", "pregnancy_radiation", "pregnancy_ct", "pregnancy_stress_echo", "gestation", "pregnant"],
        "anticoagulant_therapy": ["anticoagulant_therapy", "anticoagulant", "anticoagulation", "warfarin", "heparin", "anticoagulation_therapy"],
        "coagulopathy": ["coagulopathy", "coagulation_disorder", "bleeding_disorder", "clotting_disorder", "coagulation_defect"],
        "contrast_allergy": ["contrast_allergy", "contrast_reaction", "iodine_allergy", "contrast_intolerance"],
        "pregnancy_ct": ["pregnancy_ct", "pregnancy_radiation", "pregnancy_ct_scan"],
        "pregnancy_stress_echo": ["pregnancy_stress_echo", "pregnancy_stress", "pregnancy_exercise_echo"],
        # A.110 / G32：规则 `global_pregnancy_radiation` / `breast_pregnancy_radiation`
        # 的 condition 是 `pregnancy_radiation`，而别名表的**键**里没有它 →
        # `.get(condition, [condition])` 退化成只比字面量，患者勾「妊娠」永远不命中。
        # 现补键（另配下面的并查集闭包，任意别名互为同族）。
        "pregnancy_radiation": ["pregnancy_radiation", "pregnancy", "pregnancy_ct", "pregnancy_stress_echo", "gestation"],
        "severe_aortic_stenosis": ["severe_aortic_stenosis", "aortic_stenosis_severe", "critical_aortic_stenosis"],
    }

    @classmethod
    def _condition_families(cls) -> dict[str, str]:
        """别名闭包：把互相引用的别名归到**同一个代表**（并查集）。

        为什么需要闭包而不是 `.get(condition, [condition])` 的字面匹配：
        别名表本身是"多键互引"的（`pregnancy` 列了 `pregnancy_ct`，而
        `pregnancy_ct` 又是一个键）。按字面匹配时，**规则 condition 必须恰好是
        一个键**才享有别名；`pregnancy_radiation` 恰好不是键（历史漏项）→ 该规则
        永远打不中。闭包让"任意别名 → 代表"成为单射，表怎么加都不会再漏。
        """
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                # 代表取字典序最小者：**确定性**（不依赖 dict 顺序），便于断言与日志对账
                if rb < ra:
                    ra, rb = rb, ra
                parent[rb] = ra

        for key, aliases in cls._CONDITION_ALIASES.items():
            find(key)
            for alias in aliases:
                union(key, alias)
        return {name: find(name) for name in list(parent)}

    def _match_condition(self, condition: str, diagnosis_data: dict[str, Any]) -> bool:
        if "patient_conditions" in diagnosis_data:
            patient_conditions = diagnosis_data["patient_conditions"]
            if isinstance(patient_conditions, list):
                # A.110 / G32：按**同族**比较（闭包），而不是"规则 condition 必须是
                # 别名表的键"。之前 `pregnancy_radiation` 类规则因此恒不命中。
                families = self._condition_families()
                rule_family = families.get(condition, condition)
                sent = {families.get(str(c), str(c)) for c in patient_conditions}
                return rule_family in sent

        if "clinical_context" in diagnosis_data:
            clinical_context = diagnosis_data["clinical_context"]
            if isinstance(clinical_context, dict):
                return condition in clinical_context.get("conditions", [])

        logger.warning("sg_contraindication_no_condition_data", condition=condition)
        return True

    def get_rules_for_organ(self, organ: str) -> list[ContraindicationRule]:
        if not self._loaded:
            self._load_default_rules()
        return self._organ_rules.get(organ, [])

    def get_global_rules(self) -> list[ContraindicationRule]:
        if not self._loaded:
            self._load_default_rules()
        return self._global_rules


from safety_gateway.registry import sg_registry

_sg_contraindication_lock = threading.Lock()


def get_contraindication_engine() -> ContraindicationEngine:
    if not sg_registry.is_registered("contraindication_engine"):
        with _sg_contraindication_lock:
            if not sg_registry.is_registered("contraindication_engine"):
                sg_registry.register("contraindication_engine", ContraindicationEngine)
    return sg_registry.get("contraindication_engine")


def _reset_contraindication_engine() -> None:
    sg_registry.reset("contraindication_engine")
