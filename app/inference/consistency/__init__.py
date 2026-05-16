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
