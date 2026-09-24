from .statistics import (
    wilson_confidence_interval,
    mcnemar_test,
    grading_semantic_equivalent,
    text_similarity,
    text_consistency_with_grading,
    _grades_cross_clinical_boundary,
    _check_boundary_match,
    _numeric_fallback_match,
    _levenshtein_distance,
)
from .report import (
    ConsistencyMetric,
    GateStatus,
    GradingResult,
    ConsistencyPair,
    FalseStats,
    CrossValidationResult,
    ConsistencyReport,
    detect_hallucination,
)
from .verifier import ConsistencyVerifier
from .organ_breakdown import compute_organ_breakdown
from .failure_analysis import generate_failure_analysis, suggest_for_trace
from .serialization import report_to_dict, report_to_json
from .quantization_verifier import QuantizationHallucinationVerifier, QuantizationResult

from app.inference.grading_definitions import extract_grading
# 幻觉检测器实体位于 app.inference.hallucination_detector，但一致性校验的
# 调用方（tests/integration/llm/test_dual_engine_consistency.py 等）按
# ``from app.inference.consistency import HallucinationDetector`` 引用，
# 与 app/inference/__init__.py 的导出保持一致，这里做同名再导出。
from app.inference.hallucination_detector import (
    HallucinationDetector,
    DetectionStrategy,
    DetectionResult,
    DetectionIssue,
    get_default_detector,
)
