"""
Baseline Validation Annotation Module
Supports the annotation workflow for baseline accuracy validation.

Implements:
- Dual annotation (two independent annotators)
- Consistency checking (Cohen's Kappa)
- Annotation quality control
- Structured output for annotation review

Per 07-数据标注与模型评估.md:
- 双标注一致率 ≥ 85% (Cohen's Kappa ≥ 0.80)
- 审核抽检通过率 ≥ 95%
- 标注完成率 ≥ 98%

TODO(Phase0A): DESIGN-A1 - Add "标注医师修正" step after pre-annotation
TODO(Phase0A): DESIGN-A1 - Add "审核医师抽检" step before final approval
TODO(Phase0A): DESIGN-A1 - Add "入库" step for approved annotations
"""
import contextlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

import structlog

from app.knowledge.baseline_validation import (
    BaselineValidator,
    ValidationCase,
)
from app.knowledge.vocabulary import UDAFVocabulary

logger = structlog.get_logger("app.knowledge.baseline_annotation")

_AGREEMENT_THRESHOLD = 0.85
_KAPPA_THRESHOLD = 0.80
_REVIEW_PASS_THRESHOLD = 0.95
_COMPLETION_THRESHOLD = 0.98
_SAMPLE_SIZE_THRESHOLD = 200
_WINDOW_SIZE = 100


class AnnotationStatus(Enum):
    """Status of an annotation task."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DISPUTED = "disputed"
    APPROVED = "approved"
    REJECTED = "rejected"


class DisputeResolution(Enum):
    """How a disputed annotation was resolved."""
    CONSENSUS = "consensus"
    REVIEWER_DECISION = "reviewer_decision"
    ESCALATION = "escalation"


@dataclass
class AnnotationLabel:
    """A single annotation label from an annotator."""
    attribute_category: str
    attribute_value: str
    confidence: float = 1.0
    notes: str | None = None


@dataclass
class AnnotatorResult:
    """Annotation result from a single annotator."""
    annotator_id: str
    case_id: str
    labels: list[AnnotationLabel]
    organ: str | None = None
    classification: str | None = None
    timestamp: str | None = None
    duration_seconds: float = 0.0


@dataclass
class DualAnnotation:
    """Dual annotation result with consistency check."""
    case_id: str
    annotator_a: AnnotatorResult
    annotator_b: AnnotatorResult
    agreement_rate: float = 0.0
    status: AnnotationStatus = AnnotationStatus.PENDING
    dispute_resolution: DisputeResolution | None = None
    reviewer_id: str | None = None
    reviewer_decision: AnnotatorResult | None = None


@dataclass
class AnnotationQualityMetrics:
    """Quality metrics for annotation batch."""
    total_cases: int
    completed_cases: int
    disputed_cases: int
    approved_cases: int
    rejected_cases: int
    completion_rate: float
    agreement_rate: float
    cohen_kappa: float
    review_pass_rate: float
    window_agreement_rates: list[float] = field(default_factory=list)


@dataclass
class AnnotationBatch:
    """A batch of annotation tasks."""
    batch_id: str
    organ: str
    cases: list[ValidationCase]
    dual_annotations: list[DualAnnotation] = field(default_factory=list)
    created_at: str = ""
    status: AnnotationStatus = AnnotationStatus.PENDING


def compute_cohen_kappa(labels_a: list[str], labels_b: list[str]) -> float:
    """
    Compute Cohen's Kappa for dual annotation agreement.

    Kappa = (Po - Pe) / (1 - Pe)
    where Po = observed agreement, Pe = expected agreement by chance.

    Uses the standard multi-class formulation with a confusion matrix
    built from paired label assignments.

    Args:
        labels_a: Labels from annotator A
        labels_b: Labels from annotator B (same length as labels_a)

    Returns:
        Cohen's Kappa value (-1 to 1)
    """
    n = len(labels_a)
    if n == 0 or len(labels_b) != n:
        return 0.0

    all_labels = sorted(set(labels_a) | set(labels_b))
    label_idx = {label: i for i, label in enumerate(all_labels)}
    k = len(all_labels)

    matrix = [[0] * k for _ in range(k)]
    for a, b in zip(labels_a, labels_b, strict=True):
        i = label_idx.get(a, 0)
        j = label_idx.get(b, 0)
        matrix[i][j] += 1

    po = sum(matrix[i][i] for i in range(k)) / n

    row_sums = [sum(matrix[i]) for i in range(k)]
    col_sums = [sum(matrix[i][j] for i in range(k)) for j in range(k)]
    pe = sum(row_sums[i] * col_sums[i] for i in range(k)) / (n * n)

    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0

    return (po - pe) / (1 - pe)


def compute_agreement_rate(result_a: AnnotatorResult,
                           result_b: AnnotatorResult) -> tuple[float, float, int]:
    """
    Compute agreement rate between two annotators on attribute labels.

    Uses Jaccard similarity for multi-value categories: partial credit
    when annotators overlap but don't match exactly.

    Args:
        result_a: First annotator's result
        result_b: Second annotator's result

    Returns:
        Tuple of (agreement_rate, weighted_agreements, total_comparisons)
        where weighted_agreements is a float (may include Jaccard fractions)
    """
    labels_a = {(lbl.attribute_category, lbl.attribute_value) for lbl in result_a.labels}
    labels_b = {(lbl.attribute_category, lbl.attribute_value) for lbl in result_b.labels}

    all_keys = set()
    cat_a: dict[str, set] = {}
    for cat, val in labels_a:
        cat_a.setdefault(cat, set()).add(val)
        all_keys.add(cat)
    cat_b: dict[str, set] = {}
    for cat, val in labels_b:
        cat_b.setdefault(cat, set()).add(val)
        all_keys.add(cat)

    agreements: float = 0.0
    total = 0
    for cat in all_keys:
        vals_a = cat_a.get(cat, set())
        vals_b = cat_b.get(cat, set())
        if vals_a == vals_b:
            agreements += 1
        elif vals_a & vals_b:
            agreements += len(vals_a & vals_b) / len(vals_a | vals_b)
        total += 1

    rate = agreements / total if total > 0 else 0.0
    return rate, agreements, total


def _compute_kappa_for_dual(dual: DualAnnotation) -> float:
    """
    Compute Cohen's Kappa for a single dual annotation pair.

    Only considers categories where BOTH annotators provided labels.
    Categories present in only one annotator are excluded from Kappa
    calculation, because an omitted attribute (absence) is a different
    kind of error than a wrong value, and should not be conflated.

    Within shared categories, if one annotator has a value the other
    lacks, the missing side is marked as ``<absent>``. This means
    value-level omissions within a shared category ARE counted as
    disagreements in Kappa — this is intentional: when both annotators
    agree a category is relevant, the specific values should match.
    This differs from category-level absence (where one annotator
    omits the entire category), which is excluded from Kappa entirely.

    Args:
        dual: DualAnnotation with two annotator results

    Returns:
        Cohen's Kappa for this pair
    """
    cat_a: dict[str, set] = {}
    for lbl in dual.annotator_a.labels:
        cat_a.setdefault(lbl.attribute_category, set()).add(lbl.attribute_value)
    cat_b: dict[str, set] = {}
    for lbl in dual.annotator_b.labels:
        cat_b.setdefault(lbl.attribute_category, set()).add(lbl.attribute_value)

    shared_cats = sorted(set(cat_a) & set(cat_b))

    if not shared_cats:
        return 0.0

    labels_a: list[str] = []
    labels_b: list[str] = []
    for cat in shared_cats:
        vals_a = cat_a[cat]
        vals_b = cat_b[cat]
        for val in sorted(vals_a | vals_b):
            labels_a.append(f"{cat}:{val}" if val in vals_a else f"{cat}:<absent>")
            labels_b.append(f"{cat}:{val}" if val in vals_b else f"{cat}:<absent>")

    return compute_cohen_kappa(labels_a, labels_b)


class BaselineAnnotator:
    """
    Baseline validation annotation manager.
    Manages dual annotation workflow, consistency checking, and quality control.
    """

    def __init__(self, vocab: UDAFVocabulary | None = None):
        self.vocab = vocab or UDAFVocabulary()
        self.batches: list[AnnotationBatch] = []
        self._parser = None

    def create_batch(self, organ: str,
                     cases: list[ValidationCase] | None = None) -> AnnotationBatch:
        """
        Create an annotation batch for a specific organ.

        Args:
            organ: Target organ
            cases: Optional list of cases; if None, generates from BaselineValidator

        Returns:
            AnnotationBatch ready for annotation
        """
        if cases is None:
            validator = BaselineValidator(vocab=self.vocab)
            cases = validator.get_cases_by_organ(organ)

        organ_value = organ.value if isinstance(organ, Enum) else organ
        batch = AnnotationBatch(
            batch_id=f"batch_{organ_value}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            organ=organ,
            cases=cases,
            created_at=datetime.now().isoformat(),
        )
        self.batches.append(batch)
        logger.info("Annotation batch created",
                     batch_id=batch.batch_id,
                     organ=organ,
                     case_count=len(cases))
        return batch

    def auto_annotate(self, case: ValidationCase,
                      annotator_id: str = "auto") -> AnnotatorResult:
        """
        Generate automatic pre-annotation using UDT parser.
        Uses the parser's actual output (not expected_attributes) to simulate
        model-assisted pre-annotation, preserving annotator independence.

        Args:
            case: Validation case to annotate
            annotator_id: Annotator identifier

        Returns:
            AnnotatorResult with auto-generated labels
        """
        from app.knowledge.udt_parser import Organ, UDTParser

        if self._parser is None:
            self._parser = UDTParser()

        labels: list[AnnotationLabel] = []

        organ_enum = None
        with contextlib.suppress(ValueError):
            organ_enum = Organ(case.organ) if isinstance(case.organ, str) else case.organ

        result = self._parser.parse(case.input_text, organ_enum)

        for attr in result.attributes:
            labels.append(AnnotationLabel(
                attribute_category=attr.category,
                attribute_value=attr.value,
                confidence=attr.confidence,
                notes="auto_pre_annotation_udt_parser",
            ))

        classification = None
        for attr in result.attributes:
            if attr.category in ("ti_rads", "li_rads"):
                classification = attr.value
                labels.append(AnnotationLabel(
                    attribute_category="classification",
                    attribute_value=attr.value,
                    confidence=attr.confidence,
                    notes="auto_pre_annotation_udt_parser",
                ))
                break

        return AnnotatorResult(
            annotator_id=annotator_id,
            case_id=case.case_id,
            labels=labels,
            organ=case.organ,
            classification=classification,
            timestamp=datetime.now().isoformat(),
        )

    def create_dual_annotation(self, case: ValidationCase,
                               result_a: AnnotatorResult,
                               result_b: AnnotatorResult,
                               agreement_threshold: float = _AGREEMENT_THRESHOLD
                               ) -> DualAnnotation:
        """
        Create a dual annotation with consistency check.

        Args:
            case: Original validation case
            result_a: First annotator's result
            result_b: Second annotator's result
            agreement_threshold: Threshold for disputed status (default 0.85)

        Returns:
            DualAnnotation with agreement metrics
        """
        agreement_rate, _agreements, _total = compute_agreement_rate(result_a, result_b)

        status = AnnotationStatus.COMPLETED
        if agreement_rate < agreement_threshold:
            status = AnnotationStatus.DISPUTED

        return DualAnnotation(
            case_id=case.case_id,
            annotator_a=result_a,
            annotator_b=result_b,
            agreement_rate=agreement_rate,
            status=status,
        )

    def resolve_dispute(self, dual: DualAnnotation,
                        reviewer_id: str,
                        decision: AnnotatorResult,
                        resolution: DisputeResolution = DisputeResolution.REVIEWER_DECISION
                        ) -> DualAnnotation:
        """
        Resolve a disputed dual annotation.

        Args:
            dual: Disputed dual annotation
            reviewer_id: Reviewer who resolves the dispute
            decision: Final annotation decision
            resolution: Resolution method

        Returns:
            Updated DualAnnotation with resolution
        """
        dual.dispute_resolution = resolution
        dual.reviewer_id = reviewer_id
        dual.reviewer_decision = decision
        dual.status = AnnotationStatus.APPROVED
        return dual

    def compute_batch_metrics(self, batch: AnnotationBatch) -> AnnotationQualityMetrics:
        """
        Compute quality metrics for an annotation batch.

        Cohen's Kappa is computed per-case by pairing labels from both
        annotators on the same category, then aggregating across cases.

        Args:
            batch: Annotation batch to evaluate

        Returns:
            AnnotationQualityMetrics with computed metrics
        """
        total = len(batch.cases)
        completed = sum(1 for d in batch.dual_annotations
                        if d.status in (AnnotationStatus.COMPLETED,
                                        AnnotationStatus.APPROVED))
        disputed = sum(1 for d in batch.dual_annotations
                       if d.status == AnnotationStatus.DISPUTED)
        approved = sum(1 for d in batch.dual_annotations
                       if d.status == AnnotationStatus.APPROVED)
        rejected = sum(1 for d in batch.dual_annotations
                       if d.status == AnnotationStatus.REJECTED)

        completion_rate = completed / total if total > 0 else 0.0

        agreement_rates = [d.agreement_rate for d in batch.dual_annotations]
        avg_agreement = (sum(agreement_rates) / len(agreement_rates)
                         if agreement_rates else 0.0)

        case_kappas = [_compute_kappa_for_dual(d) for d in batch.dual_annotations]
        kappa = (sum(case_kappas) / len(case_kappas)) if case_kappas else 0.0

        window_agreement_rates = self._compute_window_agreement_rates(batch)

        reviewed = approved + rejected
        review_pass_rate = approved / reviewed if reviewed > 0 else 1.0

        return AnnotationQualityMetrics(
            total_cases=total,
            completed_cases=completed,
            disputed_cases=disputed,
            approved_cases=approved,
            rejected_cases=rejected,
            completion_rate=completion_rate,
            agreement_rate=avg_agreement,
            cohen_kappa=kappa,
            review_pass_rate=review_pass_rate,
            window_agreement_rates=window_agreement_rates,
        )

    def _compute_window_agreement_rates(self,
                                         batch: AnnotationBatch) -> list[float]:
        """
        Compute agreement rates per _WINDOW_SIZE cases (default 100).

        Per 07-数据标注与模型评估.md: 一致率按每100例统计。

        Args:
            batch: Annotation batch

        Returns:
            List of agreement rates per window
        """
        if not batch.dual_annotations:
            return []

        rates: list[float] = []
        for i in range(0, len(batch.dual_annotations), _WINDOW_SIZE):
            window = batch.dual_annotations[i:i + _WINDOW_SIZE]
            window_rate = sum(d.agreement_rate for d in window) / len(window)
            rates.append(window_rate)
        return rates

    def validate_gate_0a_readiness(self, batch: AnnotationBatch) -> dict:
        """
        Validate whether annotation batch meets Gate 0A readiness criteria.

        Per 07-数据标注与模型评估.md:
        - 双标注一致率 ≥ 85% (Cohen's Kappa ≥ 0.80)
        - 审核抽检通过率 ≥ 95%
        - 标注完成率 ≥ 98%
        - 各器官最低样本量 ≥ 200 例

        Args:
            batch: Annotation batch to validate

        Returns:
            Dict with readiness status and detailed checks
        """
        metrics = self.compute_batch_metrics(batch)

        checks = {
            "agreement_rate": {
                "value": metrics.agreement_rate,
                "threshold": _AGREEMENT_THRESHOLD,
                "passed": metrics.agreement_rate >= _AGREEMENT_THRESHOLD,
            },
            "cohen_kappa": {
                "value": metrics.cohen_kappa,
                "threshold": _KAPPA_THRESHOLD,
                "passed": metrics.cohen_kappa >= _KAPPA_THRESHOLD,
            },
            "review_pass_rate": {
                "value": metrics.review_pass_rate,
                "threshold": _REVIEW_PASS_THRESHOLD,
                "passed": metrics.review_pass_rate >= _REVIEW_PASS_THRESHOLD,
            },
            "completion_rate": {
                "value": metrics.completion_rate,
                "threshold": _COMPLETION_THRESHOLD,
                "passed": metrics.completion_rate >= _COMPLETION_THRESHOLD,
            },
            "sample_size": {
                "value": metrics.total_cases,
                "threshold": _SAMPLE_SIZE_THRESHOLD,
                "passed": metrics.total_cases >= _SAMPLE_SIZE_THRESHOLD,
            },
        }

        all_passed = all(c["passed"] for c in checks.values())

        return {
            "batch_id": batch.batch_id,
            "organ": batch.organ,
            "gate_0a_ready": all_passed,
            "checks": checks,
            "metrics_summary": {
                "total_cases": metrics.total_cases,
                "completed_cases": metrics.completed_cases,
                "disputed_cases": metrics.disputed_cases,
                "agreement_rate": f"{metrics.agreement_rate:.2%}",
                "cohen_kappa": f"{metrics.cohen_kappa:.4f}",
                "review_pass_rate": f"{metrics.review_pass_rate:.2%}",
                "completion_rate": f"{metrics.completion_rate:.2%}",
                "window_agreement_rates": [f"{r:.2%}" for r in metrics.window_agreement_rates],
            },
            "validated_at": datetime.now().isoformat(),
        }

    def export_annotation_batch(self, batch: AnnotationBatch,
                                output_path: str) -> bool:
        """
        Export annotation batch to JSON file.

        Returns:
            True if export succeeded, False if it failed.
        """
        data = {
            "batch_id": batch.batch_id,
            "organ": batch.organ,
            "created_at": batch.created_at,
            "status": batch.status.value,
            "total_cases": len(batch.cases),
            "dual_annotations": [],
        }

        for dual in batch.dual_annotations:
            dual_data = {
                "case_id": dual.case_id,
                "agreement_rate": dual.agreement_rate,
                "status": dual.status.value,
                "annotator_a": {
                    "annotator_id": dual.annotator_a.annotator_id,
                    "labels": [
                        {
                            "category": lbl.attribute_category,
                            "value": lbl.attribute_value,
                            "confidence": lbl.confidence,
                        }
                        for lbl in dual.annotator_a.labels
                    ],
                    "classification": dual.annotator_a.classification,
                },
                "annotator_b": {
                    "annotator_id": dual.annotator_b.annotator_id,
                    "labels": [
                        {
                            "category": lbl.attribute_category,
                            "value": lbl.attribute_value,
                            "confidence": lbl.confidence,
                        }
                        for lbl in dual.annotator_b.labels
                    ],
                    "classification": dual.annotator_b.classification,
                },
            }
            if dual.dispute_resolution:
                dual_data["dispute_resolution"] = dual.dispute_resolution.value
                dual_data["reviewer_id"] = dual.reviewer_id
            data["dual_annotations"].append(dual_data)

        path = Path(output_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info("Annotation batch exported", path=str(path))
            return True
        except OSError as e:
            logger.error("Annotation batch export failed",
                          path=str(path), error=str(e))
            return False
