from app.inference.grading_definitions import extract_grading

# 幻觉检测器实体位于 app.inference.hallucination_detector，但一致性校验的
# 调用方（tests/integration/llm/test_dual_engine_consistency.py 等）按
# ``from app.inference.consistency import HallucinationDetector`` 引用，
# 与 app/inference/__init__.py 的导出保持一致，这里做同名再导出。
from app.inference.hallucination_detector import (
    DetectionIssue,
    DetectionResult,
    DetectionStrategy,
    HallucinationDetector,
    get_default_detector,
)

from .failure_analysis import generate_failure_analysis, suggest_for_trace
from .organ_breakdown import compute_organ_breakdown
from .quantization_verifier import QuantizationHallucinationVerifier, QuantizationResult
from .report import (
    ConsistencyMetric,
    ConsistencyPair,
    ConsistencyReport,
    CrossValidationResult,
    FalseStats,
    GateStatus,
    GradingResult,
    detect_hallucination,
)
from .serialization import report_to_dict, report_to_json
from .statistics import (
    _check_boundary_match,
    _grades_cross_clinical_boundary,
    _levenshtein_distance,
    _numeric_fallback_match,
    grading_semantic_equivalent,
    mcnemar_test,
    text_consistency_with_grading,
    text_similarity,
    wilson_confidence_interval,
)
from .verifier import ConsistencyVerifier
