import logging
import math
import re

from app.inference.grading_definitions import (
    _CLINICALLY_SIGNIFICANT_SUBCLASS_PAIRS,
    _GRADE_HIERARCHY,
)
from app.inference.verification_config import (
    VerificationConfig,
    get_verification_config,
)

logger = logging.getLogger(__name__)


def wilson_confidence_interval(successes: int, total: int, confidence: float = 0.95) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    p_hat = successes / total
    z = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}.get(confidence, 1.96)
    n = total
    denominator = 1 + z ** 2 / n
    center = (p_hat + z ** 2 / (2 * n)) / denominator
    margin = z * math.sqrt(p_hat * (1 - p_hat) / n + z ** 2 / (4 * n ** 2)) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))


def mcnemar_test(engine_a_results: list[bool], engine_b_results: list[bool]) -> tuple[float, float]:
    b = sum(1 for a, bb in zip(engine_a_results, engine_b_results) if a and not bb)
    c = sum(1 for a, bb in zip(engine_a_results, engine_b_results) if not a and bb)
    if b + c == 0:
        return (0.0, 1.0)
    if b + c < 25:
        from math import comb
        n = b + c
        k = min(b, c)
        p_value = sum(comb(n, i) * (0.5 ** n) for i in range(k + 1)) * 2
        p_value = min(p_value, 1.0)
        statistic = float(n)
        return (statistic, p_value)
    statistic = (abs(b - c) - 1) ** 2 / (b + c)
    try:
        from scipy.stats import chi2
        p_value = 1.0 - chi2.cdf(statistic, df=1)
    except ImportError:
        logger.warning("mcnemar_test: 未预期异常，按原逻辑降级", exc_info=True)
        p_value = math.exp(-statistic / 2.0)
    return (statistic, p_value)


def grading_semantic_equivalent(grade_a: str | None, grade_b: str | None, organ: str) -> bool:
    if grade_a is None or grade_b is None:
        return False
    if grade_a == grade_b:
        return True
    hierarchy = _GRADE_HIERARCHY.get(organ, {})
    level_a = hierarchy.get(grade_a)
    level_b = hierarchy.get(grade_b)
    if level_a is not None and level_b is not None:
        if level_a[0] == level_b[0]:
            if abs(level_a[1] - level_b[1]) <= 1:
                significant_pairs = _CLINICALLY_SIGNIFICANT_SUBCLASS_PAIRS.get(organ, set())
                pair_key = frozenset({grade_a, grade_b})
                if pair_key in significant_pairs:
                    return False
                return True
        return False
    return False


def grading_clinically_equivalent(grade_a: str | None, grade_b: str | None, organ: str) -> bool:
    if grade_a is None or grade_b is None:
        return False
    if grade_a == grade_b:
        return True
    hierarchy = _GRADE_HIERARCHY.get(organ, {})
    level_a = hierarchy.get(grade_a)
    level_b = hierarchy.get(grade_b)
    if level_a is not None and level_b is not None:
        return level_a[0] == level_b[0]
    return False


def _smart_truncate(text: str, head: int = 300, tail: int = 200) -> str:
    if len(text) <= head + tail:
        return text
    return text[:head] + text[-tail:]


def text_similarity(text_a: str, text_b: str, max_chars: int = 500,
                    trigram_weight: float = 0.4, edit_ratio_weight: float = 0.4,
                    bigram_weight: float = 0.2, head: int = 300, tail: int = 200) -> float:
    if not text_a and not text_b:
        return 1.0
    if not text_a or not text_b:
        return 0.0
    trunc_a = _smart_truncate(text_a, head=head, tail=tail)
    trunc_b = _smart_truncate(text_b, head=head, tail=tail)
    def _bigrams(text: str) -> set[str]:
        return {text[i:i+2] for i in range(len(text) - 1)}
    def _trigrams(text: str) -> set[str]:
        return {text[i:i+3] for i in range(len(text) - 2)}
    set_a_bi = _bigrams(trunc_a)
    set_b_bi = _bigrams(trunc_b)
    intersection_bi = set_a_bi & set_b_bi
    union_bi = set_a_bi | set_b_bi
    bigram_jaccard = len(intersection_bi) / len(union_bi) if union_bi else 0.0

    set_a_tri = _trigrams(trunc_a)
    set_b_tri = _trigrams(trunc_b)
    intersection_tri = set_a_tri & set_b_tri
    union_tri = set_a_tri | set_b_tri
    trigram_jaccard = len(intersection_tri) / len(union_tri) if union_tri else 0.0

    max_len = max(len(trunc_a), len(trunc_b))
    if max_len == 0:
        return 1.0
    dist = _levenshtein_distance(trunc_a, trunc_b)
    edit_ratio = 1.0 - dist / max_len

    return trigram_weight * trigram_jaccard + edit_ratio_weight * edit_ratio + bigram_weight * bigram_jaccard


def _levenshtein_distance(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def text_consistency_with_grading(
    text_a: str, text_b: str,
    grading_a: str | None, grading_b: str | None,
    organ: str,
) -> bool:
    config = get_verification_config()
    sim_config = config.text_similarity_config
    if grading_a and grading_b and grading_semantic_equivalent(grading_a, grading_b, organ):
        return text_similarity(text_a, text_b) >= sim_config.semantic_equivalent_threshold
    return text_similarity(text_a, text_b) >= sim_config.non_equivalent_threshold


def _grades_cross_clinical_boundary(
    grade_a: str, grade_b: str, organ: str, config: VerificationConfig | None = None
) -> bool:
    if config is None:
        config = get_verification_config()
    organ_boundaries = config.boundary_config.organ_boundaries.get(organ, [])
    hierarchy = _GRADE_HIERARCHY.get(organ, {})
    level_a = hierarchy.get(grade_a)
    level_b = hierarchy.get(grade_b)
    for boundary in organ_boundaries:
        from_level = hierarchy.get(boundary.from_grade)
        to_level = hierarchy.get(boundary.to_grade)
        if _check_boundary_match(
            grade_a, grade_b, level_a, level_b,
            boundary.from_grade, boundary.to_grade,
            from_level, to_level,
        ):
            return True
        if not boundary.is_directional and _check_boundary_match(
            grade_a, grade_b, level_a, level_b,
            boundary.to_grade, boundary.from_grade,
            to_level, from_level,
        ):
            return True
    return False


def _check_boundary_match(
    grade_a: str, grade_b: str,
    level_a, level_b,
    from_grade: str, to_grade: str,
    from_level, to_level,
) -> bool:
    if level_a is not None and level_b is not None:
        if from_level is not None and to_level is not None:
            if from_level[0] == to_level[0]:
                return (from_grade == grade_a and to_grade == grade_b) or \
                       (from_grade == grade_b and to_grade == grade_a)
            else:
                return (level_a[0] == from_level[0] and level_b[0] == to_level[0]) or \
                       (level_a[0] == to_level[0] and level_b[0] == from_level[0])
    if (from_grade == grade_a and to_grade == grade_b) or \
       (from_grade == grade_b and to_grade == grade_a):
        return True
    if level_a is None or level_b is None:
        return _numeric_fallback_match(grade_a, grade_b, from_grade, to_grade)
    return False


def _numeric_fallback_match(
    grade_a: str, grade_b: str,
    from_grade: str, to_grade: str,
) -> bool:
    def _extract_num(grade: str) -> float | None:
        m = re.match(r'(\d+)', grade)
        return float(m.group(1)) if m else None
    num_a = _extract_num(grade_a)
    num_b = _extract_num(grade_b)
    from_num = _extract_num(from_grade)
    to_num = _extract_num(to_grade)
    if num_a is None or num_b is None or from_num is None or to_num is None:
        return False
    return ((num_a == from_num and num_b == to_num) or
            (num_a == to_num and num_b == from_num))
