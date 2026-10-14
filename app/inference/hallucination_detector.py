
"""
统一幻觉检测模块 - 增强版

提供多种策略的医疗幻觉检测，包括：
- 禁忌词检测（支持变体）
- 矛盾分级检测
- 医学常识违背检测
- 数值范围有效性检测（支持更多指标）
- 上下文感知检测（描述-结论矛盾/不合理建议）
- 策略权重支持
- 执行日志记录
"""

import json
import re
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import structlog

from app.inference.grading_definitions import _GRADE_HIERARCHY, _GRADING_PATTERNS
from app.inference.verification_config import (
    VerificationConfig,
    get_verification_config,
)

logger = structlog.get_logger("app.inference.hallucination_detector")


class DetectionStrategy(Enum):
    """检测策略枚举"""
    FORBIDDEN_TERMS = "forbidden_terms"
    CONTRADICTORY_GRADING = "contradictory_grading"
    MEDICAL_COMMON_SENSE = "medical_common_sense"
    NUMERICAL_VALIDITY = "numerical_validity"
    CONTEXT_AWARE = "context_aware"


@dataclass
class DetectionIssue:
    """检测到的问题"""
    strategy: DetectionStrategy
    description: str
    location: str | None = None
    severity: int = 1
    weight: float = 1.0
    metadata: dict[str, Any] | None = None


@dataclass
class DetectionResult:
    """检测结果"""
    is_hallucination: bool
    issues: list[DetectionIssue] = field(default_factory=list)
    confidence: float = 0.0
    execution_time_ms: float = 0.0
    test_data_version: str = ""


class BaseDetector(ABC):
    """检测器基类"""

    def __init__(self, config: VerificationConfig | None = None):
        self.config = config or get_verification_config()

    @abstractmethod
    def detect(self, text: str, organ: str) -> list[DetectionIssue]:
        pass


class ForbiddenTermsDetector(BaseDetector):
    """禁忌词检测器 - 增强版"""

    NEGATION_TERMS = {
        "排除", "未见", "不考虑", "无", "未发现", "否认", "不支持", "不可能",
        "未提示", "不伴", "未查及", "未探及", "未见明显",
    }
    _SENTENCE_BOUNDARIES = re.compile(r'[。！？；\n]')

    def _is_negated(self, text: str, term_pos: int, term_len: int) -> bool:
        context_start = max(0, term_pos - 100)
        context_end = min(len(text), term_pos + term_len + 100)
        context = text[context_start:context_end]
        term_offset = term_pos - context_start
        for neg in self.NEGATION_TERMS:
            neg_pos = context.find(neg)
            while neg_pos != -1:
                if neg_pos < term_offset:
                    between_start = neg_pos + len(neg)
                    between_end = term_offset
                    between = context[between_start:between_end]
                    if not self._SENTENCE_BOUNDARIES.search(between):
                        return True
                neg_pos = context.find(neg, neg_pos + 1)
        return False

    def detect(self, text: str, organ: str) -> list[DetectionIssue]:
        issues = []
        skipped_by_context = []
        organ_terms = self.config.forbidden_terms_config.organ_terms.get(organ, [])

        for term_config in organ_terms:
            has_allow_if_context = bool(hasattr(term_config, 'allow_if_context') and term_config.allow_if_context)
            for search_text, is_variant in [(term_config.term, False)] + [(v, True) for v in term_config.variants]:
                pos = text.find(search_text)
                while pos != -1:
                    if self._is_negated(text, pos, len(search_text)):
                        pos = text.find(search_text, pos + 1)
                        continue
                    if has_allow_if_context and self._is_context_allowed(
                        text, pos, len(search_text), term_config.allow_if_context,
                        term_config.context_window_chars
                    ):
                        skipped_by_context.append((term_config, search_text, is_variant, pos))
                        pos = text.find(search_text, pos + 1)
                        continue
                    prefix = "检测到与当前器官({organ})无关的术语变体" if is_variant else "检测到与当前器官({organ})无关的术语"
                    label = f"{search_text} (原词: {term_config.term})" if is_variant else term_config.term
                    issues.append(DetectionIssue(
                        strategy=DetectionStrategy.FORBIDDEN_TERMS,
                        description=f"{prefix.format(organ=organ)}: {label} - {term_config.description}",
                        severity=term_config.severity,
                        weight=self.config.strategy_weights.forbidden_terms,
                    ))
                    pos = text.find(search_text, pos + 1)

        if skipped_by_context and issues:
            detected_terms_in_issues = set()
            for issue in issues:
                for term_config in organ_terms:
                    if term_config.term in issue.description:
                        detected_terms_in_issues.add(term_config.term)
                    for v in term_config.variants:
                        if v in issue.description:
                            detected_terms_in_issues.add(term_config.term)
            for term_config, search_text, is_variant, pos in skipped_by_context:
                if term_config.term not in detected_terms_in_issues:
                    continue
                prefix = "上下文共现警告：与当前器官({organ})无关的术语变体" if is_variant else "上下文共现警告：与当前器官({organ})无关的术语"
                label = f"{search_text} (原词: {term_config.term})" if is_variant else term_config.term
                issues.append(DetectionIssue(
                    strategy=DetectionStrategy.FORBIDDEN_TERMS,
                    description=f"{prefix.format(organ=organ)}: {label} - {term_config.description}",
                    severity=1,
                    weight=self.config.strategy_weights.forbidden_terms * 0.5,
                    metadata={"reason": "context_co_occurrence", "co_detected_term": term_config.term},
                ))

        return issues

    @staticmethod
    def _is_context_allowed(text: str, term_pos: int, term_len: int, context_word: str, window: int = 100) -> bool:
        start = max(0, term_pos - window)
        end = min(len(text), term_pos + term_len + window)
        local_context = text[start:end]
        return context_word in local_context


class ContradictoryGradingDetector(BaseDetector):
    """矛盾分级检测器"""

    def detect(self, text: str, organ: str) -> list[DetectionIssue]:
        issues = []
        patterns = _GRADING_PATTERNS.get(organ, [])

        gradings = set()
        for pattern in patterns:
            matches = pattern.findall(text)
            gradings.update(matches)

        if len(gradings) > 1:
            hierarchy = _GRADE_HIERARCHY.get(organ, {})
            primary_levels = set()
            has_subclass_diff = False
            for g in gradings:
                level = hierarchy.get(g)
                if level is not None:
                    if level[0] in primary_levels:
                        has_subclass_diff = True
                    primary_levels.add(level[0])
                else:
                    primary_levels.add(g)
            if len(primary_levels) > 1:
                issues.append(DetectionIssue(
                    strategy=DetectionStrategy.CONTRADICTORY_GRADING,
                    description=f"检测到矛盾的分级结果: {', '.join(gradings)}",
                    severity=3,
                    weight=self.config.strategy_weights.contradictory_grading,
                ))
            elif has_subclass_diff:
                issues.append(DetectionIssue(
                    strategy=DetectionStrategy.CONTRADICTORY_GRADING,
                    description=f"检测到子分级差异: {', '.join(gradings)}，需人工复核",
                    severity=2,
                    weight=self.config.strategy_weights.contradictory_grading,
                ))

        return issues


class MedicalCommonSenseDetector(BaseDetector):
    """医学常识违背检测器"""

    _BENIGN_KEYWORDS = {"良性", "无恶性", "考虑良性", "倾向良性"}
    _MALIGNANT_KEYWORDS = {"恶性", "转移", "侵袭", "浸润"}
    _ORGAN_MALIGNANT_TERMS: dict[str, set[str]] = {
        "thyroid": {"甲状腺癌", "甲状腺恶性肿瘤"},
        "liver": {"肝癌", "肝细胞癌", "肝脏恶性肿瘤", "肝转移癌"},
        "breast": {"乳腺癌", "乳腺恶性肿瘤"},
        "cardiac": set(),
    }
    _EXCLUSION_PATTERNS = {"排除", "需排除", "应排除", "待排除", "需鉴别"}
    _AUGMENT_PATTERNS = {"不除外", "不能除外", "无法除外"}

    def detect(self, text: str, organ: str) -> list[DetectionIssue]:
        issues = []

        has_benign = any(keyword in text for keyword in self._BENIGN_KEYWORDS)
        organ_malignant_terms = self._ORGAN_MALIGNANT_TERMS.get(organ, set())
        has_malignant = any(keyword in text for keyword in self._MALIGNANT_KEYWORDS)
        has_organ_malignant = any(term in text for term in organ_malignant_terms)

        if has_benign and has_malignant and not has_organ_malignant:
            is_exclusion = any(pattern in text for pattern in self._EXCLUSION_PATTERNS)
            is_augment = any(pattern in text for pattern in self._AUGMENT_PATTERNS)
            if is_augment:
                issues.append(DetectionIssue(
                    strategy=DetectionStrategy.MEDICAL_COMMON_SENSE,
                    description="文本同时包含良性和恶性描述，且存在'不除外'等增强性表述，矛盾可能性高",
                    severity=3,
                    weight=self.config.strategy_weights.medical_common_sense,
                ))
            elif is_exclusion:
                issues.append(DetectionIssue(
                    strategy=DetectionStrategy.MEDICAL_COMMON_SENSE,
                    description="文本同时包含良性和恶性描述，但存在排除性表述，需人工复核",
                    severity=1,
                    weight=self.config.strategy_weights.medical_common_sense,
                ))
            else:
                issues.append(DetectionIssue(
                    strategy=DetectionStrategy.MEDICAL_COMMON_SENSE,
                    description="文本同时包含良性和恶性描述，存在医学常识矛盾",
                    severity=3,
                    weight=self.config.strategy_weights.medical_common_sense,
                ))

        return issues


class NumericalValidityDetector(BaseDetector):
    """数值范围有效性检测器 - 增强版"""

    # LVEF 模式
    _LVEF_PATTERN = re.compile(r'LVEF\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%', re.IGNORECASE)
    # 结节大小模式 (要求前缀关键词，距离数值不超过15字符)
    _NODULE_SIZE_PATTERN = re.compile(
        r'(?:结节|占位|肿块|病灶).{0,15}?(\d+(?:\.\d+)?)\s*(cm|mm)',
        re.IGNORECASE
    )

    def detect(self, text: str, organ: str) -> list[DetectionIssue]:
        issues = []
        organ_ranges = self.config.numerical_validation_config.organ_ranges.get(organ, [])

        for range_config in organ_ranges:
            if range_config.parameter == "LVEF" and organ == "cardiac":
                self._check_lvef(text, range_config, issues)
            elif range_config.parameter in ("结节大小", "占位大小") and organ in ("thyroid", "liver", "breast"):
                self._check_nodule_size(text, range_config, issues)

        return issues

    def _check_lvef(self, text: str, range_config, issues: list[DetectionIssue]):
        matches = self._LVEF_PATTERN.findall(text)
        for match in matches:
            try:
                lvef = float(match)
                if lvef < range_config.min_value or lvef > range_config.max_value:
                    issues.append(DetectionIssue(
                        strategy=DetectionStrategy.NUMERICAL_VALIDITY,
                        description=f"LVEF值 {lvef}% 超出合理范围 [{range_config.min_value}%, {range_config.max_value}%] - {range_config.description}",
                        severity=2,
                        weight=self.config.strategy_weights.numerical_validity,
                    ))
            except ValueError:
                logger.warning("NumericalValidityDetector._check_lvef: 未预期异常，按原逻辑降级", exc_info=True)
                pass

    def _check_nodule_size(self, text: str, range_config, issues: list[DetectionIssue]):
        matches = self._NODULE_SIZE_PATTERN.findall(text)
        for value_str, unit in matches:
            try:
                value = float(value_str)
                # 转换为cm
                if unit.lower() == 'mm':
                    value_cm = value / 10.0
                else:
                    value_cm = value

                if value_cm < range_config.min_value or value_cm > range_config.max_value:
                    issues.append(DetectionIssue(
                        strategy=DetectionStrategy.NUMERICAL_VALIDITY,
                        description=f"{range_config.parameter} {value}{unit} 超出合理范围 [{range_config.min_value}{range_config.unit}, {range_config.max_value}{range_config.unit}] - {range_config.description}",
                        severity=2,
                        weight=self.config.strategy_weights.numerical_validity,
                    ))
            except ValueError:
                logger.warning("NumericalValidityDetector._check_nodule_size: 未预期异常，按原逻辑降级", exc_info=True)
                pass


# ---------------------------------------------------------------------------
# 上下文感知词表资产（v1, 2026-10-02）：JSON 资产加载，失败回退内置默认
# ---------------------------------------------------------------------------

_VOCAB_ASSET_PATH = Path(__file__).parent / "data" / "hallucination_vocab.json"

_DEFAULT_CONTEXT_AWARE_VOCAB: dict[str, set[str]] = {
    "benign_desc": {"边界清晰", "形态规则", "边界尚清晰", "形态尚规则", "无钙化", "无血流", "囊性"},
    "malignant_desc": {"边界模糊", "边界不清", "形态不规则", "微钙化", "丰富血流", "实性低回声", "纵横比大于1"},
    "benign_conclusion": {"良性", "考虑良性", "倾向良性", "TI-RADS 2", "TI-RADS 3", "LI-RADS 2", "LI-RADS 3", "BI-RADS 2", "BI-RADS 3"},
    "malignant_conclusion": {"恶性", "考虑恶性", "倾向恶性", "TI-RADS 5", "LI-RADS 5", "BI-RADS 5", "建议穿刺", "建议手术"},
    "cardiac_benign_desc": {"收缩功能正常", "舒张功能正常", "室壁运动正常", "瓣膜正常", "心包正常"},
    "cardiac_crisis_desc": {
        # 原 cardiac 恶性描述词
        "收缩功能减低", "室壁运动异常", "心包积液", "瓣膜重度反流", "全心扩大",
        # UDT 任务2 危象词（语料实证 + 规范危象术语）
        "主动脉夹层", "主动脉瘤破裂", "心包填塞", "心脏压塞", "心包积液合并心脏压塞",
        "大量心包积液", "心脏破裂", "左心室穿破", "右心室穿破", "室间隔穿孔",
        "室壁穿孔", "腱索断裂", "瓣膜赘生物", "赘生物", "血栓", "血栓形成",
        "心脏游离血栓", "感染性心内膜炎", "瓣叶穿孔", "瓣周漏", "瓣叶瘤", "重度瓣膜反流",
    },
    "cardiac_benign_conclusion": {"正常", "功能正常", "mild", "轻度"},
    "cardiac_crisis_conclusion": {"严重", "重度", "severe", "心衰", "心力衰竭"},
}


def _load_context_aware_vocab() -> dict[str, set[str]]:
    """从 JSON 资产加载上下文感知词表；失败时回退内置默认。"""
    try:
        raw = json.loads(_VOCAB_ASSET_PATH.read_text(encoding="utf-8"))
        section = raw.get("context_aware") or {}
        vocab: dict[str, set[str]] = {}
        for key, default in _DEFAULT_CONTEXT_AWARE_VOCAB.items():
            words = section.get(key)
            vocab[key] = set(words) if isinstance(words, list) and words else set(default)
        return vocab
    except Exception as exc:
        logger.warning(
            "hallucination_vocab_asset_load_failed",
            path=str(_VOCAB_ASSET_PATH),
            error=str(exc),
        )
        return {key: set(default) for key, default in _DEFAULT_CONTEXT_AWARE_VOCAB.items()}


class ContextAwareDetector(BaseDetector):
    """上下文感知检测器"""

    _VOCAB = _load_context_aware_vocab()
    _BENIGN_DESC_KEYWORDS = _VOCAB["benign_desc"]
    _MALIGNANT_DESC_KEYWORDS = _VOCAB["malignant_desc"]
    _BENIGN_CONCLUSION_KEYWORDS = _VOCAB["benign_conclusion"]
    _MALIGNANT_CONCLUSION_KEYWORDS = _VOCAB["malignant_conclusion"]

    _CARDIAC_BENIGN_DESC_KEYWORDS = _VOCAB["cardiac_benign_desc"]
    # cardiac 语义切换：原"恶性描述"并入危象词，现按"危象描述"检测
    _CARDIAC_CRISIS_DESC_KEYWORDS = _VOCAB["cardiac_crisis_desc"]
    _CARDIAC_BENIGN_CONCLUSION_KEYWORDS = _VOCAB["cardiac_benign_conclusion"]
    # cardiac 语义切换："恶性结论"改为"危象结论"
    _CARDIAC_CRISIS_CONCLUSION_KEYWORDS = _VOCAB["cardiac_crisis_conclusion"]

    _OVERREACTION_PATTERNS = [
        (re.compile(r'TI[-_]?RADS\s*2', re.IGNORECASE), re.compile(r'立即手术|紧急手术|穿刺活检|尽快手术')),
        (re.compile(r'TI[-_]?RADS\s*3', re.IGNORECASE), re.compile(r'立即手术|紧急手术|尽快手术')),
        (re.compile(r'LI[-_]?RADS\s*2', re.IGNORECASE), re.compile(r'立即手术|紧急手术|穿刺活检|尽快手术')),
        (re.compile(r'LI[-_]?RADS\s*3', re.IGNORECASE), re.compile(r'立即手术|紧急手术|尽快手术')),
        (re.compile(r'BI[-_]?RADS\s*2', re.IGNORECASE), re.compile(r'立即手术|紧急手术|穿刺活检|尽快手术')),
        (re.compile(r'BI[-_]?RADS\s*3', re.IGNORECASE), re.compile(r'立即手术|紧急手术|尽快手术')),
    ]

    _CARDIAC_OVERREACTION_PATTERNS = [
        (re.compile(r'LVEF\s*[:=]?\s*([2-3]\d|40)\s*%', re.IGNORECASE), re.compile(r'紧急手术|立即干预|紧急处理')),
        (re.compile(r'轻度', re.IGNORECASE), re.compile(r'紧急手术|立即干预')),
    ]

    def detect(self, text: str, organ: str) -> list[DetectionIssue]:
        issues = []
        if organ == "cardiac":
            self._check_cardiac_description_conclusion_conflict(text, issues)
            self._check_cardiac_overreaction(text, issues)
        elif organ in ("thyroid", "liver", "breast"):
            self._check_description_conclusion_conflict(text, organ, issues)
            self._check_overreaction(text, organ, issues)
        return issues

    def _check_description_conclusion_conflict(self, text: str, organ: str, issues: list[DetectionIssue]):
        has_benign_desc = any(kw in text for kw in self._BENIGN_DESC_KEYWORDS)
        has_malignant_desc = any(kw in text for kw in self._MALIGNANT_DESC_KEYWORDS)
        has_benign_conclusion = any(kw in text for kw in self._BENIGN_CONCLUSION_KEYWORDS)
        has_malignant_conclusion = any(kw in text for kw in self._MALIGNANT_CONCLUSION_KEYWORDS)

        if has_benign_desc and has_malignant_conclusion and not has_malignant_desc:
            issues.append(DetectionIssue(
                strategy=DetectionStrategy.CONTEXT_AWARE,
                description="描述特征倾向良性但结论提示恶性，存在描述-结论矛盾",
                severity=3,
                weight=self.config.strategy_weights.context_aware,
            ))

        if has_malignant_desc and has_benign_conclusion and not has_benign_desc:
            issues.append(DetectionIssue(
                strategy=DetectionStrategy.CONTEXT_AWARE,
                description="描述特征倾向恶性但结论提示良性，存在描述-结论矛盾",
                severity=3,
                weight=self.config.strategy_weights.context_aware,
            ))

    def _check_overreaction(self, text: str, organ: str, issues: list[DetectionIssue]):
        for grading_pattern, action_pattern in self._OVERREACTION_PATTERNS:
            if grading_pattern.search(text) and action_pattern.search(text):
                issues.append(DetectionIssue(
                    strategy=DetectionStrategy.CONTEXT_AWARE,
                    description="低风险分级但建议激进治疗，存在过度反应",
                    severity=2,
                    weight=self.config.strategy_weights.context_aware,
                ))
                break

    def _check_cardiac_description_conclusion_conflict(self, text: str, issues: list[DetectionIssue]):
        has_benign_desc = any(kw in text for kw in self._CARDIAC_BENIGN_DESC_KEYWORDS)
        has_crisis_desc = any(kw in text for kw in self._CARDIAC_CRISIS_DESC_KEYWORDS)
        has_benign_conclusion = any(kw in text for kw in self._CARDIAC_BENIGN_CONCLUSION_KEYWORDS)
        has_crisis_conclusion = any(kw in text for kw in self._CARDIAC_CRISIS_CONCLUSION_KEYWORDS)

        if has_benign_desc and has_crisis_conclusion and not has_crisis_desc:
            issues.append(DetectionIssue(
                strategy=DetectionStrategy.CONTEXT_AWARE,
                description="心脏描述特征正常但结论提示严重异常，存在描述-结论矛盾",
                severity=3,
                weight=self.config.strategy_weights.context_aware,
            ))

        if has_crisis_desc and has_benign_conclusion and not has_benign_desc:
            issues.append(DetectionIssue(
                strategy=DetectionStrategy.CONTEXT_AWARE,
                description="心脏描述特征异常但结论提示正常，存在描述-结论矛盾",
                severity=3,
                weight=self.config.strategy_weights.context_aware,
            ))

    def _check_cardiac_overreaction(self, text: str, issues: list[DetectionIssue]):
        for grading_pattern, action_pattern in self._CARDIAC_OVERREACTION_PATTERNS:
            if grading_pattern.search(text) and action_pattern.search(text):
                issues.append(DetectionIssue(
                    strategy=DetectionStrategy.CONTEXT_AWARE,
                    description="心脏功能轻度异常但建议紧急干预，存在过度反应",
                    severity=2,
                    weight=self.config.strategy_weights.context_aware,
                ))
                break


class HallucinationDetector:
    """统一幻觉检测器 - 增强版"""

    def __init__(
        self,
        strategies: list[DetectionStrategy] | None = None,
        severity_threshold: int = 2,
        config: VerificationConfig | None = None,
    ):
        """
        初始化幻觉检测器

        Args:
            strategies: 使用的检测策略列表，None表示使用所有策略
            severity_threshold: 触发幻觉判断的最低严重性
            config: 验证配置，None表示使用默认配置
        """
        self.config = config or get_verification_config()
        self.strategies = strategies or list(DetectionStrategy)
        self.severity_threshold = severity_threshold
        self._detectors = self._init_detectors()
        # 幻觉率滑动窗口（wiring-audit P1-3：供 Prometheus gauge 生产者使用）
        self._rate_window: deque = deque(maxlen=500)
        logger.info(
            "hallucination_detector_initialized",
            strategies=[s.value for s in self.strategies],
            severity_threshold=severity_threshold,
            test_data_version=self.config.test_data_version,
        )

    def _init_detectors(self) -> dict[DetectionStrategy, BaseDetector]:
        """初始化各策略检测器"""
        detectors = {}
        if DetectionStrategy.FORBIDDEN_TERMS in self.strategies:
            detectors[DetectionStrategy.FORBIDDEN_TERMS] = ForbiddenTermsDetector(self.config)
        if DetectionStrategy.CONTRADICTORY_GRADING in self.strategies:
            detectors[DetectionStrategy.CONTRADICTORY_GRADING] = ContradictoryGradingDetector(self.config)
        if DetectionStrategy.MEDICAL_COMMON_SENSE in self.strategies:
            detectors[DetectionStrategy.MEDICAL_COMMON_SENSE] = MedicalCommonSenseDetector(self.config)
        if DetectionStrategy.NUMERICAL_VALIDITY in self.strategies:
            detectors[DetectionStrategy.NUMERICAL_VALIDITY] = NumericalValidityDetector(self.config)
        if DetectionStrategy.CONTEXT_AWARE in self.strategies:
            detectors[DetectionStrategy.CONTEXT_AWARE] = ContextAwareDetector(self.config)
        return detectors

    def detect(self, text: str, organ: str) -> DetectionResult:
        """
        执行幻觉检测

        Args:
            text: 待检测文本
            organ: 器官类型

        Returns:
            DetectionResult: 检测结果
        """
        start_time = time.monotonic()

        if not text or not organ:
            result = DetectionResult(
                is_hallucination=False,
                test_data_version=self.config.test_data_version,
            )
            result.execution_time_ms = (time.monotonic() - start_time) * 1000
            return result

        all_issues = []
        for strategy, detector in self._detectors.items():
            issues = detector.detect(text, organ)
            all_issues.extend(issues)

        # 判断是否为幻觉
        is_hallucination = any(
            issue.severity >= self.severity_threshold
            for issue in all_issues
        )

        # 计算置信度（基于所有策略的最大可能权重）
        confidence = 0.0
        if all_issues:
            total_weighted_severity = sum(
                issue.severity * issue.weight
                for issue in all_issues
            )
            sw = self.config.strategy_weights
            max_possible = (
                sw.forbidden_terms + sw.contradictory_grading
                + sw.medical_common_sense + sw.numerical_validity
                + sw.context_aware
            ) * 3
            confidence = min(total_weighted_severity / max_possible, 1.0) if max_possible > 0 else 0.0

        execution_time_ms = (time.monotonic() - start_time) * 1000

        result = DetectionResult(
            is_hallucination=is_hallucination,
            issues=all_issues,
            confidence=confidence,
            execution_time_ms=execution_time_ms,
            test_data_version=self.config.test_data_version,
        )

        logger.info(
            "hallucination_detection_completed",
            organ=organ,
            is_hallucination=result.is_hallucination,
            issue_count=len(result.issues),
            confidence=result.confidence,
            execution_time_ms=result.execution_time_ms,
            test_data_version=result.test_data_version,
        )

        self._report_to_cusum(result, organ)

        return result

    def is_hallucination(self, text: str, organ: str) -> bool:
        return self.detect(text, organ).is_hallucination

    def _report_to_cusum(self, result: DetectionResult, organ: str) -> None:
        try:
            from app.observability.cusum import HallucinationCUSUMBridge
            bridge = HallucinationCUSUMBridge.get_instance()
            is_severe = result.is_hallucination and any(
                issue.severity >= 3 for issue in result.issues
            )
            bridge.report_hallucination_result(is_severe=is_severe, disease_type=organ)
            self._publish_hallucination_rate_gauges(
                is_hallucination=result.is_hallucination, is_severe=is_severe
            )
        except Exception as e:
            logger.warning("cusum_report_failed", error=str(e))

    def _publish_hallucination_rate_gauges(
        self, is_hallucination: bool, is_severe: bool
    ) -> None:
        """obs 指标：幻觉率滑动窗口 → Prometheus gauge（wiring-audit P1-3）。

        窗口口径：最近 500 次检测。
        - overall_rate：is_hallucination 占比；
        - severe_rate：is_severe（任一 issue severity>=3）占比；
        - high_risk_rate：取 CUSUM 桥接器中已注册的高风险病种监控器的
          当前率（未注册任何病种监控时不更新，gauge 保持 0）。
        指标发布失败不影响检测主流程。
        """
        try:
            from collections import deque

            window: deque = self._rate_window
            window.append(
                (1 if is_hallucination else 0, 1 if is_severe else 0)
            )
            n = len(window)
            overall = sum(w[0] for w in window) / n
            severe = sum(w[1] for w in window) / n

            from app.observability.metrics_collector import get_metrics_collector

            collector = get_metrics_collector()
            collector.set_overall_hallucination_rate(overall)
            collector.set_severe_hallucination_rate(severe)

            from app.observability.cusum import HallucinationCUSUMBridge

            bridge = HallucinationCUSUMBridge.get_instance()
            disease_rates = [
                monitor.get_state().get("current_rate", 0.0)
                for monitor in bridge._disease_monitors.values()
            ]
            if disease_rates:
                collector.set_high_risk_hallucination_rate(
                    max(disease_rates)
                )
        except Exception as e:
            logger.debug("hallucination_rate_gauge_update_failed", error=str(e))


# 全局默认检测器实例
_default_detector: HallucinationDetector | None = None
_default_detector_lock = threading.Lock()


def get_default_detector() -> HallucinationDetector:
    global _default_detector
    if _default_detector is None:
        with _default_detector_lock:
            if _default_detector is None:
                _default_detector = HallucinationDetector()
    return _default_detector


def detect_hallucination(text: str, organ: str) -> bool:
    """
    便捷函数：使用默认检测器检测幻觉

    Args:
        text: 待检测文本
        organ: 器官类型

    Returns:
        bool: 是否为幻觉
    """
    return get_default_detector().is_hallucination(text, organ)

