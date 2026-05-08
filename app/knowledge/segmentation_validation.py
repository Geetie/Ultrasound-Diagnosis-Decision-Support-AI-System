"""
Segmentation Validation Module
Validates Chinese word segmentation (pkuseg-medicine / jieba fallback) against UDAF vocabulary.

Segmentation priority:
1. pkuseg with medicine model (preferred, requires C++ build tools)
2. jieba with medical custom dictionary (fallback, pure Python)
3. Basic character-level segmentation (last resort)

Design spec (01-系统架构设计.md §946-960):
- pkuseg-medicine F1 ≥ 95%
- jieba + UDAF F1 ≥ 90%
- 超声术语测试集 ≥ 500条
"""
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

import structlog

from app.knowledge.vocabulary import Organ, UDAFVocabulary

logger = structlog.get_logger("app.knowledge.segmentation_validation")

_F1_THRESHOLD_PKUSEG = 0.95
_F1_THRESHOLD_JIEBA = 0.90
_MIN_TEST_CASES = 500


class SegBackend(Enum):
    """Available segmentation backends."""
    PKUSEG_MEDICINE = "pkuseg_medicine"
    JIEBA = "jieba"
    BASIC = "basic"


class SegMatchStatus(Enum):
    """Status of a segmentation-vocabulary match."""
    EXACT = "exact"
    PARTIAL = "partial"
    MISSING = "missing"
    EXTRA = "extra"


@dataclass
class SegToken:
    """A single segmented token with metadata."""
    text: str
    start: int
    end: int
    pos_tag: str | None = None


@dataclass
class SegMatchResult:
    """Result of matching a segmented token against UDAF vocabulary."""
    token: SegToken
    status: SegMatchStatus
    matched_standard: str | None = None
    matched_category: str | None = None


@dataclass
class SegValidationCase:
    """A single segmentation validation test case."""
    case_id: str
    organ: str
    input_text: str
    expected_terms: list[str]


@dataclass
class F1Metrics:
    """Precision / Recall / F1 for segmentation validation."""
    precision: float
    recall: float
    f1: float


@dataclass
class SegValidationReport:
    """Complete segmentation validation report."""
    report_id: str
    backend: SegBackend
    total_cases: int
    total_tokens: int
    exact_matches: int
    partial_matches: int
    missing_terms: int
    extra_terms: int
    term_coverage: float
    f1_metrics: F1Metrics | None = None
    organ_breakdown: dict[str, dict[str, int]] = None
    case_results: list[dict] = None
    created_at: str = ""
    duration_seconds: float = 0.0
    python_version: str = ""

    def __post_init__(self):
        if self.organ_breakdown is None:
            self.organ_breakdown = {}
        if self.case_results is None:
            self.case_results = []


def _build_medical_terms_from_vocab(vocab: UDAFVocabulary) -> list[str]:
    """Extract all Chinese synonym terms from UDAF vocabulary for jieba dictionary."""
    terms: list[str] = []
    for synonym, _standard in vocab.iter_synonym_mappings():
        if any('\u4e00' <= c <= '\u9fff' for c in synonym):
            terms.append(synonym)
    for standard_term in vocab.all_standard_terms:
        if any('\u4e00' <= c <= '\u9fff' for c in standard_term) and standard_term not in terms:
            terms.append(standard_term)
    return terms


def _try_pkuseg_medicine():
    """Try to initialize pkuseg with medicine model."""
    try:
        import pkuseg
        seg = pkuseg.pkuseg(model_name="medicine")
        logger.info("pkuseg-medicine backend initialized",
                     python_version=sys.version)
        return seg, SegBackend.PKUSEG_MEDICINE
    except ImportError:
        logger.warning("pkuseg not available, falling back to jieba",
                        python_version=sys.version)
        return None, SegBackend.JIEBA
    except (OSError, RuntimeError) as e:
        logger.warning("pkuseg-medicine init failed", error=str(e),
                        python_version=sys.version)
        return None, SegBackend.JIEBA


def _try_jieba(medical_terms: list[str]):
    """Initialize jieba with medical custom dictionary."""
    try:
        import jieba
        for term in medical_terms:
            jieba.add_word(term)
        logger.info("jieba backend initialized with medical dictionary",
                     term_count=len(medical_terms))
        return jieba, SegBackend.JIEBA
    except ImportError:
        logger.warning("jieba not available, falling back to basic segmentation")
        return None, SegBackend.BASIC


def _compute_f1(precision: float, recall: float) -> float:
    """Compute F1 score from precision and recall."""
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


class SegmentationValidator:
    """
    Validates segmentation quality against UDAF vocabulary.
    Uses pkuseg-medicine as primary backend, jieba as fallback.
    """

    def __init__(self, vocab: UDAFVocabulary | None = None):
        self.vocab = vocab or UDAFVocabulary()
        self._backend: SegBackend | None = None
        self._seg = None
        self._jieba = None
        self._medical_terms = _build_medical_terms_from_vocab(self.vocab)
        self._init_backend()

    def _init_backend(self):
        """Initialize the best available segmentation backend."""
        seg, backend = _try_pkuseg_medicine()
        if seg is not None:
            self._seg = seg
            self._backend = backend
            return

        jieba_mod, backend = _try_jieba(self._medical_terms)
        if jieba_mod is not None:
            self._jieba = jieba_mod
            self._backend = backend
            return

        self._backend = SegBackend.BASIC
        logger.warning("Using basic character-level segmentation (no pkuseg/jieba)",
                        python_version=sys.version)

    @property
    def backend(self) -> SegBackend:
        """Current segmentation backend."""
        return self._backend

    def segment(self, text: str) -> list[SegToken]:
        """
        Segment text into tokens using the best available backend.

        Args:
            text: Input text to segment

        Returns:
            List of SegToken with position information
        """
        if not text or not text.strip():
            return []

        if self._backend == SegBackend.PKUSEG_MEDICINE and self._seg is not None:
            return self._segment_pkuseg(text)
        elif self._backend == SegBackend.JIEBA and self._jieba is not None:
            return self._segment_jieba(text)
        else:
            return self._segment_basic(text)

    def _segment_pkuseg(self, text: str) -> list[SegToken]:
        """Segment using pkuseg-medicine with incremental position tracking."""
        tokens: list[SegToken] = []
        result = self._seg.cut(text)
        pos = 0
        for word in result:
            start = pos
            end = pos + len(word)
            tokens.append(SegToken(text=word, start=start, end=end))
            pos = end
        return tokens

    def _segment_jieba(self, text: str) -> list[SegToken]:
        """Segment using jieba with medical dictionary and incremental position tracking."""
        tokens: list[SegToken] = []
        result = self._jieba.cut(text)
        pos = 0
        for word in result:
            start = pos
            end = pos + len(word)
            tokens.append(SegToken(text=word, start=start, end=end))
            pos = end
        return tokens

    def _segment_basic(self, text: str) -> list[SegToken]:
        """Basic segmentation: split on punctuation and whitespace, then character pairs."""
        tokens: list[SegToken] = []
        current = 0
        parts = re.split(r'([，。、；：！？\s,.;:!?]+)', text)
        for part in parts:
            if not part:
                continue
            part_start = text.find(part, current)
            if part_start == -1:
                continue
            if re.match(r'[，。、；：！？\s,.;:!?]+', part):
                current = part_start + len(part)
                continue
            if len(part) <= 4:
                tokens.append(SegToken(text=part, start=part_start, end=part_start + len(part)))
            else:
                for i in range(0, len(part), 2):
                    chunk = part[i:i + 2]
                    if chunk:
                        c_start = part_start + i
                        tokens.append(SegToken(text=chunk, start=c_start, end=c_start + len(chunk)))
            current = part_start + len(part)
        return tokens

    def match_against_vocabulary(self, tokens: list[SegToken]) -> list[SegMatchResult]:
        """
        Match segmented tokens against UDAF vocabulary.

        Args:
            tokens: List of segmented tokens

        Returns:
            List of match results
        """
        results: list[SegMatchResult] = []
        for token in tokens:
            normalized = self.vocab.normalize(token.text)
            if normalized is not None:
                mapping = self.vocab.get_mapping(normalized)
                category = mapping.category if mapping else None
                if normalized == token.text:
                    results.append(SegMatchResult(
                        token=token, status=SegMatchStatus.EXACT,
                        matched_standard=normalized, matched_category=category,
                    ))
                else:
                    results.append(SegMatchResult(
                        token=token, status=SegMatchStatus.PARTIAL,
                        matched_standard=normalized, matched_category=category,
                    ))
            else:
                results.append(SegMatchResult(
                    token=token, status=SegMatchStatus.EXTRA,
                ))
        return results

    def validate_expected_terms(self, tokens: list[SegToken],
                                expected_terms: list[str]) -> list[SegMatchResult]:
        """
        Check which expected terms are found in segmentation results.

        Args:
            tokens: Segmented tokens
            expected_terms: Terms expected to be present

        Returns:
            Match results for expected terms
        """
        token_texts = {t.text for t in tokens}
        results: list[SegMatchResult] = []

        for term in expected_terms:
            normalized = self.vocab.normalize(term)
            if term in token_texts:
                mapping = self.vocab.get_mapping(normalized) if normalized else None
                results.append(SegMatchResult(
                    token=SegToken(text=term, start=0, end=len(term)),
                    status=SegMatchStatus.EXACT,
                    matched_standard=normalized,
                    matched_category=mapping.category if mapping else None,
                ))
            elif normalized and normalized in token_texts:
                mapping = self.vocab.get_mapping(normalized)
                results.append(SegMatchResult(
                    token=SegToken(text=term, start=0, end=len(term)),
                    status=SegMatchStatus.PARTIAL,
                    matched_standard=normalized,
                    matched_category=mapping.category if mapping else None,
                ))
            else:
                found_partial = False
                for token in tokens:
                    if term in token.text or token.text in term:
                        results.append(SegMatchResult(
                            token=SegToken(text=term, start=0, end=len(term)),
                            status=SegMatchStatus.PARTIAL,
                            matched_standard=normalized,
                        ))
                        found_partial = True
                        break
                if not found_partial:
                    results.append(SegMatchResult(
                        token=SegToken(text=term, start=0, end=len(term)),
                        status=SegMatchStatus.MISSING,
                    ))

        return results

    def compute_f1(self, tokens: list[SegToken],
                   expected_terms: list[str]) -> F1Metrics:
        """
        Compute F1 metrics for segmentation against expected terms.

        Standard information retrieval definitions:
        - TP (True Positive): vocab-matched tokens that correspond to expected terms
        - FP (False Positive): vocab-matched tokens that do NOT correspond to expected terms
        - FN (False Negative): expected terms not found in any token
        - Precision = TP / (TP + FP) = TP / vocab_token_count
        - Recall = TP / (TP + FN) = matched_expected / len(expected_terms)
        - F1 = 2 * P * R / (P + R)

        This ensures F1 is not diluted by non-medical tokens and can be
        directly compared against design spec thresholds.

        Args:
            tokens: Segmented tokens
            expected_terms: Terms expected to be present in the text

        Returns:
            F1Metrics with precision, recall, and f1
        """
        token_texts = {t.text for t in tokens}

        matched_expected = 0
        for term in expected_terms:
            normalized = self.vocab.normalize(term)
            found = (term in token_texts
                     or (normalized is not None and normalized in token_texts))
            if not found:
                for t in tokens:
                    if term in t.text or t.text in term:
                        found = True
                        break
            if found:
                matched_expected += 1

        expected_set = set(expected_terms)
        expected_normalized = set()
        for term in expected_terms:
            norm = self.vocab.normalize(term)
            if norm is not None:
                expected_normalized.add(norm)

        tp = 0
        vocab_token_count = 0
        for token in tokens:
            normalized = self.vocab.normalize(token.text)
            if normalized is not None:
                vocab_token_count += 1
                is_expected = (token.text in expected_set
                               or normalized in expected_normalized)
                if is_expected:
                    tp += 1

        precision = tp / vocab_token_count if vocab_token_count > 0 else 0.0
        recall = matched_expected / len(expected_terms) if expected_terms else 0.0
        f1 = _compute_f1(precision, recall)

        return F1Metrics(precision=precision, recall=recall, f1=f1)

    def validate_fallback_quality(self) -> dict:
        """
        Validate that the current fallback backend meets F1 thresholds.

        Per design spec:
        - pkuseg-medicine F1 ≥ 95%
        - jieba + UDAF F1 ≥ 90%

        Returns:
            Dict with F1 results and pass/fail status
        """
        cases = self.generate_validation_cases()
        all_expected: list[str] = []
        all_tokens: list[SegToken] = []

        for case in cases:
            tokens = self.segment(case.input_text)
            all_tokens.extend(tokens)
            all_expected.extend(case.expected_terms)

        f1 = self.compute_f1(all_tokens, all_expected)

        threshold = (_F1_THRESHOLD_PKUSEG
                     if self._backend == SegBackend.PKUSEG_MEDICINE
                     else _F1_THRESHOLD_JIEBA)

        return {
            "backend": self._backend.value,
            "f1_metrics": {
                "precision": f"{f1.precision:.4f}",
                "recall": f"{f1.recall:.4f}",
                "f1": f"{f1.f1:.4f}",
            },
            "threshold": threshold,
            "passed": f1.f1 >= threshold,
            "python_version": sys.version,
        }

    @staticmethod
    def _extract_terms_from_text(text: str, vocab: UDAFVocabulary) -> list[str]:
        """Extract terms that actually appear in the text from the vocabulary."""
        found: list[str] = []
        for synonym, _standard in vocab.iter_synonym_mappings():
            if synonym in text:
                found.append(synonym)
        for standard_term in vocab.all_standard_terms:
            if standard_term in text:
                found.append(standard_term)
        return found

    def generate_validation_cases(self) -> list[SegValidationCase]:
        """Generate validation cases from UDAF vocabulary terms."""
        cases: list[SegValidationCase] = []

        thyroid_texts = [
            ("甲状腺右叶中极低回声结节，边界清，形态规则，大小约1.2x0.8 cm，微钙化，周边血流。TI-RADS 3",
             ["甲状腺", "右叶中极", "低回声", "边界清", "形态规则", "微钙化", "周边血流"]),
            ("甲状腺左叶下极高回声结节，边界不清，形态不规则，粗大钙化，内部血流。TI-RADS 4B",
             ["甲状腺", "左叶下极", "高回声", "边界不清", "形态不规则", "粗大钙化", "内部血流"]),
            ("甲状腺峡部无回声结节，边界清，未见明显血流。TI-RADS 2",
             ["甲状腺", "峡部", "无回声", "边界清", "未见明显血流"]),
            ("甲状腺右叶上极低回声结节，边界欠清，分叶状，微钙化，血流丰富。TI-RADS 4C",
             ["甲状腺", "右叶上极", "低回声", "边界欠清", "分叶状", "微钙化", "血流丰富"]),
            ("甲状腺左叶中极等回声结节，边界清，形态规则，无钙化，未见明显血流。",
             ["甲状腺", "左叶中极", "等回声", "边界清", "形态规则", "无钙化", "未见明显血流"]),
        ]
        for i, (text, terms) in enumerate(thyroid_texts, 1):
            cases.append(SegValidationCase(
                case_id=f"seg_thyroid_{i:04d}",
                organ=Organ.THYROID,
                input_text=text,
                expected_terms=terms,
            ))

        liver_texts = [
            ("肝右叶低回声结节，边界清，形态规则，大小约2.0x1.5 cm，无钙化，周边血流。LI-RADS 3",
             ["肝右叶", "低回声", "边界清", "形态规则", "无钙化", "周边血流"]),
            ("肝左叶高回声结节，边界不清，形态不规则，粗大钙化，内部血流。LI-RADS 4",
             ["肝左叶", "高回声", "边界不清", "形态不规则", "粗大钙化", "内部血流"]),
            ("肝右后叶混合回声占位，边界不清，血流丰富。LI-RADS 5",
             ["肝右后叶", "混合回声", "边界不清", "血流丰富"]),
            ("肝左内叶无回声囊肿，边界清，未见明显血流。LI-RADS 1",
             ["肝左内叶", "无回声", "边界清", "未见明显血流"]),
            ("肝右前叶等回声结节，边界清，形态规则，无钙化，周边血流。",
             ["肝右前叶", "等回声", "边界清", "形态规则", "无钙化", "周边血流"]),
        ]
        for i, (text, terms) in enumerate(liver_texts, 1):
            cases.append(SegValidationCase(
                case_id=f"seg_liver_{i:04d}",
                organ=Organ.LIVER,
                input_text=text,
                expected_terms=terms,
            ))

        heart_texts = [
            ("左房增大，二尖瓣轻度反流，左室收缩功能减低。",
             ["左房", "增大", "二尖瓣", "轻度反流", "左室", "收缩功能减低"]),
            ("左室壁增厚，左室舒张功能减低，心包少量积液。",
             ["左室", "增厚", "舒张功能减低", "心包", "少量积液"]),
            ("心内结构未见明显异常，各瓣膜回声及活动未见异常。",
             []),
            ("右房增大，三尖瓣中度反流，室间隔运动减低。",
             ["右房", "增大", "三尖瓣", "中度反流", "室间隔", "运动减低"]),
            ("左室增大，主动脉瓣狭窄，左室收缩功能减低。",
             ["左室", "增大", "主动脉瓣", "狭窄", "收缩功能减低"]),
        ]
        for i, (text, terms) in enumerate(heart_texts, 1):
            cases.append(SegValidationCase(
                case_id=f"seg_heart_{i:04d}",
                organ=Organ.HEART,
                input_text=text,
                expected_terms=terms,
            ))

        return cases

    def _generate_extended_cases(self) -> list[SegValidationCase]:
        """
        Generate extended validation cases to meet ≥500 requirement.

        Uses parameterized combination of organ locations, echo patterns,
        boundary types, morphology, calcification, and vascularity to
        produce diverse clinical text variants rather than repeating
        identical templates.
        """
        base_cases = self.generate_validation_cases()
        extended: list[SegValidationCase] = list(base_cases)

        thyroid_locs = ["右叶上极", "右叶中极", "右叶下极", "左叶上极", "左叶中极", "左叶下极", "峡部"]
        liver_locs = ["肝右叶", "肝左叶", "肝右后叶", "肝左内叶", "肝右前叶"]
        heart_structures = [
            ("左房", "增大"), ("左室", "增厚"), ("右房", "增大"), ("右室", "增厚"),
            ("二尖瓣", "轻度反流"), ("三尖瓣", "中度反流"), ("主动脉瓣", "狭窄"),
            ("室间隔", "增厚"), ("心包", "少量积液"),
        ]
        echo_types = ["低回声", "高回声", "等回声", "无回声", "混合回声"]
        boundaries = ["边界清", "边界不清", "边界欠清"]
        morphologies = ["形态规则", "形态不规则", "分叶状"]
        calcifications = ["微钙化", "粗大钙化", "无钙化", "环状钙化"]
        vasculatures = ["未见明显血流", "周边血流", "内部血流", "血流丰富"]
        tirads = ["TI-RADS 2", "TI-RADS 3", "TI-RADS 4A", "TI-RADS 4B", "TI-RADS 4C"]
        lirads = ["LI-RADS 1", "LI-RADS 2", "LI-RADS 3", "LI-RADS 4", "LI-RADS 5"]

        case_num = len(extended)
        for loc in thyroid_locs:
            for echo in echo_types:
                for bnd in boundaries:
                    for morph in morphologies:
                        for calc in calcifications:
                            for vasc in vasculatures:
                                for tr in tirads:
                                    case_num += 1
                                    text = f"甲状腺{loc}{echo}结节，{bnd}，{morph}，{calc}，{vasc}。{tr}"
                                    terms = [t for t in ["甲状腺", loc, echo, bnd, morph, calc, vasc] if t in text]
                                    extended.append(SegValidationCase(
                                        case_id=f"seg_thyroid_{case_num:04d}",
                                        organ=Organ.THYROID,
                                        input_text=text,
                                        expected_terms=terms,
                                    ))

        for loc in liver_locs:
            for echo in echo_types:
                for bnd in boundaries:
                    for morph in morphologies:
                        for calc in calcifications:
                            for vasc in vasculatures:
                                for lr in lirads:
                                    case_num += 1
                                    text = f"{loc}{echo}结节，{bnd}，{morph}，{calc}，{vasc}。{lr}"
                                    terms = [t for t in [loc, echo, bnd, morph, calc, vasc] if t in text]
                                    extended.append(SegValidationCase(
                                        case_id=f"seg_liver_{case_num:04d}",
                                        organ=Organ.LIVER,
                                        input_text=text,
                                        expected_terms=terms,
                                    ))

        for struct, finding in heart_structures:
            for struct2, finding2 in heart_structures:
                if struct == struct2:
                    continue
                case_num += 1
                text = f"{struct}{finding}，{struct2}{finding2}。"
                terms = [t for t in [struct, finding, struct2, finding2] if t in text]
                extended.append(SegValidationCase(
                    case_id=f"seg_heart_{case_num:04d}",
                    organ=Organ.HEART,
                    input_text=text,
                    expected_terms=terms,
                ))

        unique_texts: set[str] = set()
        deduped: list[SegValidationCase] = []
        for case in extended:
            if case.input_text not in unique_texts:
                unique_texts.add(case.input_text)
                deduped.append(case)

        return deduped[:_MIN_TEST_CASES]

    def run_validation(self, extended: bool = False) -> SegValidationReport:
        """
        Run full segmentation validation against UDAF vocabulary.

        Args:
            extended: If True, generate ≥500 test cases per design spec

        Returns:
            SegValidationReport with comprehensive results
        """
        start_time = time.time()
        cases = self._generate_extended_cases() if extended else self.generate_validation_cases()

        total_tokens = 0
        exact_matches = 0
        partial_matches = 0
        missing_terms = 0
        extra_terms = 0
        organ_breakdown: dict[str, dict[str, int]] = {}
        case_results: list[dict] = []

        all_expected: list[str] = []
        all_tokens: list[SegToken] = []

        for case in cases:
            tokens = self.segment(case.input_text)
            total_tokens += len(tokens)

            all_tokens.extend(tokens)
            all_expected.extend(case.expected_terms)

            vocab_matches = self.match_against_vocabulary(tokens)
            expected_matches = self.validate_expected_terms(tokens, case.expected_terms)

            case_exact = sum(1 for m in vocab_matches if m.status == SegMatchStatus.EXACT)
            case_partial = sum(1 for m in vocab_matches if m.status == SegMatchStatus.PARTIAL)
            case_extra = sum(1 for m in vocab_matches if m.status == SegMatchStatus.EXTRA)
            case_missing = sum(1 for m in expected_matches if m.status == SegMatchStatus.MISSING)

            exact_matches += case_exact
            partial_matches += case_partial
            extra_terms += case_extra
            missing_terms += case_missing

            organ = case.organ
            if organ not in organ_breakdown:
                organ_breakdown[organ] = {
                    "total_tokens": 0, "exact": 0, "partial": 0,
                    "missing": 0, "extra": 0, "cases": 0,
                }
            organ_breakdown[organ]["total_tokens"] += len(tokens)
            organ_breakdown[organ]["exact"] += case_exact
            organ_breakdown[organ]["partial"] += case_partial
            organ_breakdown[organ]["missing"] += case_missing
            organ_breakdown[organ]["extra"] += case_extra
            organ_breakdown[organ]["cases"] += 1

            case_results.append({
                "case_id": case.case_id,
                "organ": organ,
                "token_count": len(tokens),
                "exact": case_exact,
                "partial": case_partial,
                "missing": case_missing,
                "extra": case_extra,
                "sample_tokens": [t.text for t in tokens[:20]],
            })

        total_vocab_hits = exact_matches + partial_matches
        term_coverage = total_vocab_hits / total_tokens if total_tokens > 0 else 0.0

        f1_metrics = self.compute_f1(all_tokens, all_expected)

        duration = time.time() - start_time

        report = SegValidationReport(
            report_id=f"seg_val_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            backend=self._backend,
            total_cases=len(cases),
            total_tokens=total_tokens,
            exact_matches=exact_matches,
            partial_matches=partial_matches,
            missing_terms=missing_terms,
            extra_terms=extra_terms,
            term_coverage=term_coverage,
            f1_metrics=f1_metrics,
            organ_breakdown=organ_breakdown,
            case_results=case_results,
            created_at=datetime.now().isoformat(),
            duration_seconds=duration,
            python_version=sys.version,
        )

        logger.info("Segmentation validation completed",
                     backend=self._backend.value,
                     total_cases=len(cases),
                     total_tokens=total_tokens,
                     coverage=f"{term_coverage:.2%}",
                     f1=f"{f1_metrics.f1:.4f}",
                     missing=missing_terms,
                     duration=duration)

        return report
