
"""
验证配置管理模块

提供边界配置、禁忌词配置、检测策略权重等配置管理功能。
"""

import json
import threading
from pathlib import Path

import structlog
from pydantic import BaseModel, Field, field_validator

logger = structlog.get_logger("app.inference.verification_config")


class BoundaryPairConfig(BaseModel):
    """边界对配置"""
    from_grade: str = Field(..., description="起始等级")
    to_grade: str = Field(..., description="目标等级")
    weight: float = Field(1.0, description="权重")
    is_critical: bool = Field(False, description="是否为关键边界")
    is_directional: bool = Field(True, description="是否有向边界，False表示双向匹配")

    @field_validator("from_grade", "to_grade")
    @classmethod
    def grade_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("grade must not be empty")
        return v

    @field_validator("weight")
    @classmethod
    def weight_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("weight must be non-negative")
        return v


class BoundaryConfig(BaseModel):
    """边界配置"""
    version: str = Field("1.0", description="配置版本")
    organ_boundaries: dict[str, list[BoundaryPairConfig]] = Field(
        default_factory=dict,
        description="各器官的边界对配置"
    )


class ForbiddenTermConfig(BaseModel):
    """禁忌词配置"""
    term: str = Field(..., description="禁忌词")
    variants: list[str] = Field(default_factory=list, description="术语变体")
    severity: int = Field(2, description="严重程度", ge=1, le=3)
    description: str = Field("", description="描述")
    allow_if_context: str = Field("", description="上下文允许关键词，若文本包含此词则跳过检测")
    context_window_chars: int = Field(100, description="上下文允许检测窗口大小(字符数)")


class ForbiddenTermsConfig(BaseModel):
    """禁忌词配置"""
    version: str = Field("1.0", description="配置版本")
    organ_terms: dict[str, list[ForbiddenTermConfig]] = Field(
        default_factory=dict,
        description="各器官的禁忌词配置"
    )


class StrategyWeightConfig(BaseModel):
    """检测策略权重配置"""
    forbidden_terms: float = Field(1.0, description="禁忌词检测权重")
    contradictory_grading: float = Field(1.5, description="矛盾分级检测权重")
    medical_common_sense: float = Field(2.0, description="医学常识检测权重")
    numerical_validity: float = Field(1.0, description="数值有效性检测权重")
    context_aware: float = Field(1.5, description="上下文感知检测权重")

    @field_validator("forbidden_terms", "contradictory_grading", "medical_common_sense", "numerical_validity", "context_aware")
    @classmethod
    def weight_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("weight must be non-negative")
        return v


class NumericalRangeConfig(BaseModel):
    """数值范围配置"""
    parameter: str = Field(..., description="参数名称")
    min_value: float = Field(..., description="最小值")
    max_value: float = Field(..., description="最大值")
    unit: str = Field("", description="单位")
    description: str = Field("", description="描述")

    @field_validator("max_value")
    @classmethod
    def max_must_exceed_min(cls, v, info):
        if "min_value" in info.data and v <= info.data["min_value"]:
            raise ValueError("max_value must be greater than min_value")
        return v


class NumericalValidationConfig(BaseModel):
    """数值验证配置"""
    version: str = Field("1.0", description="配置版本")
    organ_ranges: dict[str, list[NumericalRangeConfig]] = Field(
        default_factory=dict,
        description="各器官的数值范围配置"
    )


class AdaptiveThresholdConfig(BaseModel):
    """自适应阈值配置（预留接口，尚未集成到验证流程）"""
    metric_name: str = Field(..., description="指标名称")
    base_threshold: float = Field(..., description="基础阈值")
    min_threshold: float = Field(..., description="最小阈值")
    max_threshold: float = Field(..., description="最大阈值")
    adjustment_factor: float = Field(0.1, description="调整因子")


def compute_adaptive_threshold(
    config: AdaptiveThresholdConfig,
    historical_pass_rates: list[float],
) -> float:
    if not historical_pass_rates:
        return config.base_threshold
    avg_pass_rate = sum(historical_pass_rates) / len(historical_pass_rates)
    if avg_pass_rate >= 0.98:
        adjusted = config.base_threshold + config.adjustment_factor
    elif avg_pass_rate < 0.90:
        adjusted = config.base_threshold - config.adjustment_factor
    else:
        adjusted = config.base_threshold
    return max(config.min_threshold, min(config.max_threshold, adjusted))


class TextSimilarityConfig(BaseModel):
    """文本相似度计算配置"""
    trigram_weight: float = Field(0.4, description="trigram Jaccard权重")
    edit_ratio_weight: float = Field(0.4, description="编辑距离比率权重")
    bigram_weight: float = Field(0.2, description="bigram Jaccard权重")
    head_chars: int = Field(300, description="截断头部字符数")
    tail_chars: int = Field(200, description="截断尾部字符数")
    semantic_equivalent_threshold: float = Field(0.7, description="语义等价文本相似度阈值")
    non_equivalent_threshold: float = Field(0.85, description="非等价文本相似度阈值")
    contradiction_similarity_threshold: float = Field(0.85, description="矛盾检测相似度阈值(对应06-开发环境与进度规划.md CONTRADICTION_SIMILARITY_THRESHOLD)")


class ConsistencyThresholdConfig(BaseModel):
    """一致性验证阈值配置"""
    grading_consistency_rate: float = Field(0.95, description="分级一致性率阈值")
    semantic_grading_consistency_rate: float = Field(0.98, description="语义分级一致率阈值")
    clinical_boundary_diff_rate: float = Field(0.01, description="临床边界差异率阈值(Phase2标准≤1%)")
    phase1_clinical_boundary_diff_rate: float = Field(0.03, description="阶段一(Gate0A)临床边界差异率阈值(点估计≤3%)")
    weighted_boundary_diff_rate: float = Field(0.01, description="加权边界差异率阈值")
    critical_boundary_diff_rate: float = Field(0.005, description="关键边界差异率阈值(critical级别)")
    hallucination_rate_diff: float = Field(0.005, description="跨引擎幻觉率差异阈值(非量化幻觉增量，量化幻觉增量见BenchmarkThresholdConfig.hallucination_increment)")
    latency_diff_pct: float = Field(20.0, description="延迟差异百分比阈值(单位:百分比,如20.0表示20%)")
    latency_diff_abs_ms: float = Field(200.0, description="延迟差异绝对值阈值(ms)")
    latency_diff_gate_logic: str = Field("or", description="延迟差异判定逻辑(or=百分比或绝对值任一满足即通过,and=两者同时满足)")
    lvef_conflict_rate_threshold: float = Field(0.02, description="LVEF-描述矛盾率阈值")
    text_consistency_rate: float = Field(0.90, description="文本一致率阈值(分级语义等价视为一致)")
    text_exact_match_rate: float = Field(0.05, description="文本精确匹配率阈值(参考指标,不作为Gate必要条件)")
    hallucination_rate_warning: float = Field(0.003, description="幻觉率警告绝对阈值(整体幻觉率预警)")
    hallucination_rate_critical: float = Field(0.005, description="幻觉率临界绝对阈值(严重幻觉率≤0.5%)")
    absolute_hallucination_rate_threshold: float = Field(0.05, description="单引擎整体幻觉率绝对阈值(≤5%,对应00-项目总览核心指标)")
    conditional_grading_consistency_rate: float = Field(0.90, description="条件通过分级一致率阈值")
    conditional_clinical_boundary_diff_rate: float = Field(0.02, description="条件通过临床边界差异率阈值")
    conditional_text_consistency_rate: float = Field(0.80, description="条件通过文本一致率阈值")
    conditional_hallucination_rate_diff_multiplier: float = Field(2.0, description="条件通过幻觉差异放宽倍数")
    conditional_latency_diff_pct_multiplier: float = Field(1.5, description="条件通过延迟差异放宽倍数")
    verification_phase: str = Field("phase1", description="验证阶段(phase1=Gate0A,phase2=Gate0B)")
    phase2_min_cases_per_organ: int = Field(300, description="阶段二每器官最小用例数")
    phase2_boundary_ci_upper_threshold: float = Field(0.02, description="阶段二边界差异率95%CI上界阈值")
    auto_upgrade_phase: bool = Field(True, description="是否自动从phase1升级到phase2(当样本量满足时)")
    phase2_min_cases_per_grading_category: int = Field(100, description="阶段二每个分级类别最小用例数")
    latency_p99_min_samples: int = Field(500, description="P99延迟可靠性最小样本数")
    gpu_fragmentation_warning_threshold: float = Field(0.30, description="GPU显存碎片化率警告阈值(30%)")
    gpu_fragmentation_critical_threshold: float = Field(0.40, description="GPU显存碎片化率临界阈值(40%,触发热重载)")
    gpu_memory_monitor_duration_seconds: float = Field(14400.0, description="GPU显存监控持续时间(秒,默认4小时)")


class BenchmarkThresholdConfig(BaseModel):
    """基准测试阈值配置"""
    accuracy: float = Field(0.89, description="准确率阈值(基层≥89%)")
    latency_p99_ms: float = Field(3000.0, description="P99延迟阈值(ms)")
    latency_p99_concurrent_ms: float = Field(8000.0, description="2并发P99延迟阈值(ms)")
    hallucination_increment: float = Field(0.005, description="幻觉增量阈值")
    accuracy_sensitivity_thresholds: list[float] = Field(
        [0.80, 0.85, 0.89, 0.90, 0.93, 0.95, 0.97],
        description="准确率敏感性分析阈值列表"
    )
    latency_sensitivity_thresholds_ms: list[float] = Field(
        [1000, 2000, 3000, 4000, 5000],
        description="延迟敏感性分析阈值列表(ms)"
    )
    hallucination_sensitivity_thresholds: list[float] = Field(
        [0.003, 0.005, 0.01, 0.02, 0.03],
        description="幻觉率敏感性分析阈值列表"
    )


class VerificationConfig(BaseModel):
    """验证配置"""
    version: str = Field("1.0", description="配置版本")
    boundary_config: BoundaryConfig = Field(
        default_factory=BoundaryConfig,
        description="边界配置"
    )
    forbidden_terms_config: ForbiddenTermsConfig = Field(
        default_factory=ForbiddenTermsConfig,
        description="禁忌词配置"
    )
    strategy_weights: StrategyWeightConfig = Field(
        default_factory=StrategyWeightConfig,
        description="策略权重配置"
    )
    numerical_validation_config: NumericalValidationConfig = Field(
        default_factory=NumericalValidationConfig,
        description="数值验证配置"
    )
    adaptive_thresholds: list[AdaptiveThresholdConfig] = Field(
        default_factory=list,
        description="自适应阈值配置列表"
    )
    consistency_thresholds: ConsistencyThresholdConfig = Field(
        default_factory=ConsistencyThresholdConfig,
        description="一致性验证阈值配置"
    )
    text_similarity_config: TextSimilarityConfig = Field(
        default_factory=TextSimilarityConfig,
        description="文本相似度计算配置"
    )
    benchmark_thresholds: BenchmarkThresholdConfig = Field(
        default_factory=BenchmarkThresholdConfig,
        description="基准测试阈值配置"
    )
    test_data_version: str = Field("2.0", description="测试数据版本")


def get_default_boundary_config() -> BoundaryConfig:
    """获取默认边界配置"""
    thyroid_boundaries = [
        BoundaryPairConfig(from_grade="3", to_grade="4", weight=1.0, is_critical=True),
        BoundaryPairConfig(from_grade="4", to_grade="3", weight=1.0, is_critical=True),
        BoundaryPairConfig(from_grade="4a", to_grade="4b", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="4b", to_grade="4a", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="4a", to_grade="3", weight=1.0, is_critical=True),
        BoundaryPairConfig(from_grade="3", to_grade="4a", weight=1.0, is_critical=True),
        BoundaryPairConfig(from_grade="4", to_grade="5", weight=2.0, is_critical=True),
        BoundaryPairConfig(from_grade="5", to_grade="4", weight=2.0, is_critical=True),
        BoundaryPairConfig(from_grade="4a", to_grade="4c", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="4c", to_grade="4a", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="4b", to_grade="4c", weight=1.0, is_critical=False),
        BoundaryPairConfig(from_grade="4c", to_grade="4b", weight=1.0, is_critical=False),
        BoundaryPairConfig(from_grade="3", to_grade="4b", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="4b", to_grade="3", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="3", to_grade="5", weight=3.0, is_critical=True),
        BoundaryPairConfig(from_grade="5", to_grade="3", weight=3.0, is_critical=True),
        BoundaryPairConfig(from_grade="4a", to_grade="5", weight=2.0, is_critical=True),
        BoundaryPairConfig(from_grade="5", to_grade="4a", weight=2.0, is_critical=True),
        BoundaryPairConfig(from_grade="4b", to_grade="5", weight=2.0, is_critical=True),
        BoundaryPairConfig(from_grade="5", to_grade="4b", weight=2.0, is_critical=True),
        BoundaryPairConfig(from_grade="4c", to_grade="5", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="5", to_grade="4c", weight=1.5, is_critical=True),
    ]

    liver_boundaries = [
        BoundaryPairConfig(from_grade="3", to_grade="4", weight=1.0, is_critical=True),
        BoundaryPairConfig(from_grade="4", to_grade="3", weight=1.0, is_critical=True),
        BoundaryPairConfig(from_grade="4", to_grade="5", weight=2.0, is_critical=True),
        BoundaryPairConfig(from_grade="5", to_grade="4", weight=2.0, is_critical=True),
        BoundaryPairConfig(from_grade="3", to_grade="5", weight=3.0, is_critical=True),
        BoundaryPairConfig(from_grade="5", to_grade="3", weight=3.0, is_critical=True),
    ]

    cardiac_boundaries = [
        BoundaryPairConfig(from_grade="normal", to_grade="mild", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="mild", to_grade="normal", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="mild", to_grade="moderate", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="moderate", to_grade="mild", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="moderate", to_grade="severe", weight=2.0, is_critical=True),
        BoundaryPairConfig(from_grade="severe", to_grade="moderate", weight=2.0, is_critical=True),
        BoundaryPairConfig(from_grade="normal", to_grade="moderate", weight=2.0, is_critical=True),
        BoundaryPairConfig(from_grade="moderate", to_grade="normal", weight=2.0, is_critical=True),
        BoundaryPairConfig(from_grade="normal", to_grade="severe", weight=3.0, is_critical=True),
        BoundaryPairConfig(from_grade="severe", to_grade="normal", weight=3.0, is_critical=True),
        BoundaryPairConfig(from_grade="mild", to_grade="severe", weight=2.5, is_critical=True),
        BoundaryPairConfig(from_grade="severe", to_grade="mild", weight=2.5, is_critical=True),
    ]

    breast_boundaries = [
        BoundaryPairConfig(from_grade="0", to_grade="3", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="3", to_grade="0", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="0", to_grade="4", weight=2.0, is_critical=True),
        BoundaryPairConfig(from_grade="4", to_grade="0", weight=2.0, is_critical=True),
        BoundaryPairConfig(from_grade="0", to_grade="5", weight=3.0, is_critical=True),
        BoundaryPairConfig(from_grade="5", to_grade="0", weight=3.0, is_critical=True),
        BoundaryPairConfig(from_grade="3", to_grade="4", weight=1.0, is_critical=True),
        BoundaryPairConfig(from_grade="4", to_grade="3", weight=1.0, is_critical=True),
        BoundaryPairConfig(from_grade="3", to_grade="4a", weight=1.0, is_critical=True),
        BoundaryPairConfig(from_grade="4a", to_grade="3", weight=1.0, is_critical=True),
        BoundaryPairConfig(from_grade="3", to_grade="4b", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="4b", to_grade="3", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="3", to_grade="4c", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="4c", to_grade="3", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="4a", to_grade="4b", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="4b", to_grade="4a", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="4a", to_grade="4c", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="4c", to_grade="4a", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="4b", to_grade="4c", weight=1.0, is_critical=False),
        BoundaryPairConfig(from_grade="4c", to_grade="4b", weight=1.0, is_critical=False),
        BoundaryPairConfig(from_grade="4", to_grade="5", weight=2.0, is_critical=True),
        BoundaryPairConfig(from_grade="5", to_grade="4", weight=2.0, is_critical=True),
        BoundaryPairConfig(from_grade="4a", to_grade="5", weight=2.0, is_critical=True),
        BoundaryPairConfig(from_grade="5", to_grade="4a", weight=2.0, is_critical=True),
        BoundaryPairConfig(from_grade="4b", to_grade="5", weight=2.0, is_critical=True),
        BoundaryPairConfig(from_grade="5", to_grade="4b", weight=2.0, is_critical=True),
        BoundaryPairConfig(from_grade="4c", to_grade="5", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="5", to_grade="4c", weight=1.5, is_critical=True),
        BoundaryPairConfig(from_grade="3", to_grade="5", weight=3.0, is_critical=True),
        BoundaryPairConfig(from_grade="5", to_grade="3", weight=3.0, is_critical=True),
    ]

    return BoundaryConfig(
        version="1.0",
        organ_boundaries={
            "thyroid": thyroid_boundaries,
            "liver": liver_boundaries,
            "cardiac": cardiac_boundaries,
            "breast": breast_boundaries,
        }
    )


def get_default_forbidden_terms_config() -> ForbiddenTermsConfig:
    """获取默认禁忌词配置"""
    thyroid_terms = [
        ForbiddenTermConfig(
            term="肺癌",
            variants=["肺恶性肿瘤", "肺腺癌", "肺鳞癌", "小细胞肺癌"],
            severity=2,
            description="与甲状腺检查无关的肺部肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="胃癌",
            variants=["胃恶性肿瘤", "胃腺癌"],
            severity=2,
            description="与甲状腺检查无关的胃部肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="肠癌",
            variants=["结直肠癌", "结肠癌", "直肠癌", "肠恶性肿瘤"],
            severity=2,
            description="与甲状腺检查无关的肠道肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="骨折",
            variants=["骨裂", "粉碎性骨折"],
            severity=1,
            description="与甲状腺检查无关的骨科术语"
        ),
        ForbiddenTermConfig(
            term="脑梗",
            variants=["脑梗死", "脑卒中", "脑血管意外"],
            severity=2,
            description="与甲状腺检查无关的脑血管术语"
        ),
        ForbiddenTermConfig(
            term="肾癌",
            variants=["肾恶性肿瘤", "肾细胞癌"],
            severity=2,
            description="与甲状腺检查无关的肾脏肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="胰腺癌",
            variants=["胰腺恶性肿瘤", "胰腺导管腺癌"],
            severity=2,
            description="与甲状腺检查无关的胰腺肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="骨转移",
            variants=["骨转移瘤", "骨继发恶性肿瘤"],
            severity=3,
            description="骨转移术语（甲状腺癌骨转移为已知转移途径）",
            allow_if_context="甲状腺",
        ),
        ForbiddenTermConfig(
            term="肝转移",
            variants=["肝转移瘤", "肝继发恶性肿瘤"],
            severity=3,
            description="肝转移术语（甲状腺癌肝转移虽少见但存在）",
            allow_if_context="甲状腺",
        ),
        ForbiddenTermConfig(
            term="脑转移",
            variants=["脑转移瘤", "脑继发恶性肿瘤"],
            severity=3,
            description="脑转移术语（甲状腺癌脑转移虽少见但存在）",
            allow_if_context="甲状腺",
        ),
        ForbiddenTermConfig(
            term="前列腺",
            variants=["前列腺癌", "前列腺增生"],
            severity=1,
            description="与甲状腺检查无关的前列腺术语"
        ),
        ForbiddenTermConfig(
            term="卵巢",
            variants=["卵巢癌", "卵巢囊肿"],
            severity=1,
            description="与甲状腺检查无关的卵巢术语"
        ),
        ForbiddenTermConfig(
            term="子宫",
            variants=["子宫癌", "子宫肌瘤"],
            severity=1,
            description="与甲状腺检查无关的子宫术语"
        ),
        ForbiddenTermConfig(
            term="黑色素瘤",
            variants=["恶性黑色素瘤", "皮肤黑色素瘤"],
            severity=2,
            description="与甲状腺检查无关的皮肤科肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="白血病",
            variants=["急性白血病", "慢性白血病", "急性髓系白血病", "急性淋巴细胞白血病"],
            severity=2,
            description="与甲状腺检查无关的血液科术语"
        ),
        ForbiddenTermConfig(
            term="淋巴瘤",
            variants=["霍奇金淋巴瘤", "非霍奇金淋巴瘤"],
            severity=2,
            description="与甲状腺检查无关的血液科术语"
        ),
        ForbiddenTermConfig(
            term="宫颈癌",
            variants=["子宫颈癌", "宫颈恶性肿瘤"],
            severity=2,
            description="与甲状腺检查无关的妇科肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="膀胱癌",
            variants=["膀胱恶性肿瘤", "膀胱尿路上皮癌"],
            severity=2,
            description="与甲状腺检查无关的泌尿科肿瘤术语"
        ),
    ]

    liver_terms = [
        ForbiddenTermConfig(
            term="肺癌",
            variants=["肺恶性肿瘤", "肺腺癌", "肺鳞癌"],
            severity=2,
            description="与肝脏检查无关的肺部肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="甲状腺癌",
            variants=["甲状腺恶性肿瘤"],
            severity=2,
            description="与肝脏检查无关的甲状腺肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="骨折",
            variants=["骨裂"],
            severity=1,
            description="与肝脏检查无关的骨科术语"
        ),
        ForbiddenTermConfig(
            term="脑梗",
            variants=["脑梗死", "脑卒中"],
            severity=2,
            description="与肝脏检查无关的脑血管术语"
        ),
        ForbiddenTermConfig(
            term="肾癌",
            variants=["肾恶性肿瘤"],
            severity=2,
            description="与肝脏检查无关的肾脏肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="骨转移",
            variants=["骨转移瘤"],
            severity=3,
            description="骨转移术语（肝癌骨转移常见）",
            allow_if_context="肝脏",
        ),
        ForbiddenTermConfig(
            term="脑转移",
            variants=["脑转移瘤"],
            severity=3,
            description="与肝脏检查无关的脑转移术语"
        ),
        ForbiddenTermConfig(
            term="前列腺",
            variants=["前列腺癌"],
            severity=1,
            description="与肝脏检查无关的前列腺术语"
        ),
        ForbiddenTermConfig(
            term="卵巢",
            variants=["卵巢癌"],
            severity=1,
            description="与肝脏检查无关的卵巢术语"
        ),
        ForbiddenTermConfig(
            term="白血病",
            variants=["急性白血病", "慢性白血病"],
            severity=2,
            description="与肝脏检查无关的血液科术语"
        ),
        ForbiddenTermConfig(
            term="淋巴瘤",
            variants=["霍奇金淋巴瘤", "非霍奇金淋巴瘤"],
            severity=2,
            description="与肝脏检查无关的血液科术语"
        ),
        ForbiddenTermConfig(
            term="黑色素瘤",
            variants=["恶性黑色素瘤"],
            severity=2,
            description="与肝脏检查无关的皮肤科肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="宫颈癌",
            variants=["子宫颈癌"],
            severity=2,
            description="与肝脏检查无关的妇科肿瘤术语"
        ),
    ]

    cardiac_terms = [
        ForbiddenTermConfig(
            term="肺癌",
            variants=["肺恶性肿瘤", "肺腺癌"],
            severity=2,
            description="与心脏检查无关的肺部肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="肝癌",
            variants=["肝恶性肿瘤", "肝细胞癌"],
            severity=2,
            description="与心脏检查无关的肝脏肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="骨折",
            variants=["骨裂"],
            severity=1,
            description="与心脏检查无关的骨科术语"
        ),
        ForbiddenTermConfig(
            term="胃穿孔",
            variants=["胃破裂"],
            severity=2,
            description="与心脏检查无关的胃部急症术语"
        ),
        ForbiddenTermConfig(
            term="肠癌",
            variants=["结直肠癌"],
            severity=2,
            description="与心脏检查无关的肠道肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="骨转移",
            variants=["骨转移瘤"],
            severity=3,
            description="与心脏检查无关的骨转移术语"
        ),
        ForbiddenTermConfig(
            term="肝转移",
            variants=["肝转移瘤"],
            severity=3,
            description="与心脏检查无关的肝转移术语"
        ),
        ForbiddenTermConfig(
            term="甲状腺癌",
            variants=["甲状腺恶性肿瘤"],
            severity=2,
            description="与心脏检查无关的甲状腺肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="白血病",
            variants=["急性白血病", "慢性白血病"],
            severity=2,
            description="与心脏检查无关的血液科术语"
        ),
        ForbiddenTermConfig(
            term="黑色素瘤",
            variants=["恶性黑色素瘤"],
            severity=2,
            description="与心脏检查无关的皮肤科肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="胰腺炎",
            variants=["急性胰腺炎", "慢性胰腺炎"],
            severity=2,
            description="与心脏检查无关的消化科术语"
        ),
        ForbiddenTermConfig(
            term="胆囊炎",
            variants=["急性胆囊炎", "慢性胆囊炎"],
            severity=1,
            description="与心脏检查无关的消化科术语"
        ),
    ]

    breast_terms = [
        ForbiddenTermConfig(
            term="肺癌",
            variants=["肺恶性肿瘤", "肺腺癌"],
            severity=2,
            description="与乳腺检查无关的肺部肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="肝癌",
            variants=["肝恶性肿瘤", "肝细胞癌"],
            severity=2,
            description="与乳腺检查无关的肝脏肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="胃癌",
            variants=["胃恶性肿瘤", "胃腺癌"],
            severity=2,
            description="与乳腺检查无关的胃部肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="骨折",
            variants=["骨裂", "粉碎性骨折"],
            severity=1,
            description="与乳腺检查无关的骨科术语"
        ),
        ForbiddenTermConfig(
            term="脑梗",
            variants=["脑梗死", "脑卒中"],
            severity=2,
            description="与乳腺检查无关的脑血管术语"
        ),
        ForbiddenTermConfig(
            term="肾癌",
            variants=["肾恶性肿瘤"],
            severity=2,
            description="与乳腺检查无关的肾脏肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="骨转移",
            variants=["骨转移瘤"],
            severity=3,
            description="与乳腺检查无关的骨转移术语（注：乳腺骨转移需特殊处理）",
            allow_if_context="乳腺"
        ),
        ForbiddenTermConfig(
            term="前列腺",
            variants=["前列腺癌", "前列腺增生"],
            severity=1,
            description="与乳腺检查无关的前列腺术语"
        ),
        ForbiddenTermConfig(
            term="白血病",
            variants=["急性白血病", "慢性白血病"],
            severity=2,
            description="与乳腺检查无关的血液科术语"
        ),
        ForbiddenTermConfig(
            term="淋巴瘤",
            variants=["霍奇金淋巴瘤", "非霍奇金淋巴瘤"],
            severity=2,
            description="与乳腺检查无关的血液科术语"
        ),
        ForbiddenTermConfig(
            term="黑色素瘤",
            variants=["恶性黑色素瘤"],
            severity=2,
            description="与乳腺检查无关的皮肤科肿瘤术语"
        ),
        ForbiddenTermConfig(
            term="胰腺癌",
            variants=["胰腺恶性肿瘤"],
            severity=2,
            description="与乳腺检查无关的胰腺肿瘤术语"
        ),
    ]

    return ForbiddenTermsConfig(
        version="1.0",
        organ_terms={
            "thyroid": thyroid_terms,
            "liver": liver_terms,
            "cardiac": cardiac_terms,
            "breast": breast_terms,
        }
    )


def get_default_numerical_validation_config() -> NumericalValidationConfig:
    """获取默认数值验证配置"""
    thyroid_ranges = [
        NumericalRangeConfig(
            parameter="结节大小",
            min_value=0.1,
            max_value=10.0,
            unit="cm",
            description="甲状腺结节大小的合理范围"
        ),
    ]

    liver_ranges = [
        NumericalRangeConfig(
            parameter="占位大小",
            min_value=0.1,
            max_value=20.0,
            unit="cm",
            description="肝脏占位大小的合理范围"
        ),
    ]

    cardiac_ranges = [
        NumericalRangeConfig(
            parameter="LVEF",
            min_value=20.0,
            max_value=85.0,
            unit="%",
            description="左心室射血分数的合理范围"
        ),
    ]

    breast_ranges = [
        NumericalRangeConfig(
            parameter="结节大小",
            min_value=0.1,
            max_value=10.0,
            unit="cm",
            description="乳腺结节大小的合理范围"
        ),
    ]

    return NumericalValidationConfig(
        version="1.0",
        organ_ranges={
            "thyroid": thyroid_ranges,
            "liver": liver_ranges,
            "cardiac": cardiac_ranges,
            "breast": breast_ranges,
        }
    )


def get_default_verification_config() -> VerificationConfig:
    """获取默认验证配置"""
    return VerificationConfig(
        version="1.0",
        boundary_config=get_default_boundary_config(),
        forbidden_terms_config=get_default_forbidden_terms_config(),
        strategy_weights=StrategyWeightConfig(),
        numerical_validation_config=get_default_numerical_validation_config(),
        test_data_version="2.0",
        adaptive_thresholds=[
            AdaptiveThresholdConfig(
                metric_name="grading_consistency_rate",
                base_threshold=0.95, min_threshold=0.90, max_threshold=0.99,
            ),
            AdaptiveThresholdConfig(
                metric_name="clinical_boundary_diff_rate",
                base_threshold=0.01, min_threshold=0.005, max_threshold=0.02,
            ),
            AdaptiveThresholdConfig(
                metric_name="hallucination_rate_diff",
                base_threshold=0.005, min_threshold=0.003, max_threshold=0.01,
            ),
        ],
    )


def load_config_from_json(file_path: str) -> VerificationConfig | None:
    """从JSON文件加载配置"""
    try:
        path = Path(file_path)
        if not path.exists():
            logger.warning("config_file_not_found", path=file_path)
            return None

        with open(path, encoding='utf-8') as f:
            data = json.load(f)

        config = VerificationConfig.model_validate(data)
        logger.info("config_loaded", path=file_path, version=config.version)
        return config
    except Exception as e:
        logger.error("config_load_failed", path=file_path, error=str(e))
        return None


def save_config_to_json(config: VerificationConfig, file_path: str) -> bool:
    """保存配置到JSON文件"""
    try:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(config.model_dump(), f, ensure_ascii=False, indent=2)

        logger.info("config_saved", path=file_path, version=config.version)
        return True
    except Exception as e:
        logger.error("config_save_failed", path=file_path, error=str(e))
        return False


def sync_default_config_to_json(
    config_path: str = str(Path(__file__).parent / "data" / "default_verification_config.json"),
) -> bool:
    """将代码默认配置同步到JSON文件，确保单一数据源"""
    config = get_default_verification_config()
    return save_config_to_json(config, config_path)


# 全局配置实例
_config: VerificationConfig | None = None
_config_lock = threading.Lock()


def get_verification_config(config_path: str | None = None) -> VerificationConfig:
    """获取验证配置（线程安全单例模式）"""
    global _config

    with _config_lock:
        if _config is None:
            if config_path:
                loaded_config = load_config_from_json(config_path)
                if loaded_config:
                    _config = loaded_config
                    return _config

            _config = get_default_verification_config()
            logger.info("using_default_config")

    return _config


def reset_verification_config() -> None:
    """重置配置为默认值"""
    global _config
    with _config_lock:
        _config = None
    logger.info("config_reset")
