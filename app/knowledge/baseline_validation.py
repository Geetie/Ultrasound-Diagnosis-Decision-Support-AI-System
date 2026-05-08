"""
Baseline Accuracy Validation
Framework for validating baseline accuracy of diagnostic models.
"""
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import structlog

from app.knowledge.vocabulary import Organ, UDAFVocabulary

logger = structlog.get_logger("app.knowledge.baseline_validation")


class ValidationStatus(Enum):
    """Status of a validation case."""
    PASSED = "passed"
    FAILED = "failed"
    PENDING = "pending"
    SKIPPED = "skipped"


@dataclass
class ValidationCase:
    """A single validation test case."""
    case_id: str
    organ: str
    input_text: str
    expected_classification: str | None = None
    expected_attributes: dict[str, Any] | None = None
    reference_standard: str | None = None
    source: str | None = None


@dataclass
class ValidationResult:
    """Result of a single validation case."""
    case: ValidationCase
    status: ValidationStatus
    actual_classification: str | None = None
    actual_attributes: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    execution_time_ms: float = 0.0


@dataclass
class BaselineReport:
    """Complete baseline validation report."""
    report_id: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    skipped_cases: int
    accuracy: float
    organ_breakdown: dict[str, dict[str, int]]
    results: list[ValidationResult]
    created_at: str
    duration_seconds: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.total_cases == 0:
            return 0.0
        return self.passed_cases / self.total_cases


_THYROID_TEMPLATES = [
    {
        "locations": ["右叶上极", "右叶中极", "右叶下极", "左叶上极", "左叶中极", "左叶下极", "峡部"],
        "echoes": ["低回声", "等回声", "高回声", "混合回声", "无回声"],
        "boundaries": ["边界清", "边界不清", "边界欠清"],
        "morphologies": ["形态规则", "形态不规则", "分叶状"],
        "calcifications": ["无钙化", "微钙化", "粗大钙化"],
        "vascularities": ["未见明显血流", "周边血流", "内部血流", "血流丰富"],
    },
]

_LIVER_TEMPLATES = [
    {
        "locations": ["肝右叶", "肝左叶", "肝左内叶", "肝右前叶", "肝右后叶"],
        "echoes": ["低回声", "等回声", "高回声", "混合回声", "无回声"],
        "boundaries": ["边界清", "边界不清"],
        "morphologies": ["形态规则", "形态不规则"],
        "calcifications": ["无钙化", "粗大钙化"],
        "vascularities": ["未见明显血流", "周边血流", "内部血流", "血流丰富"],
    },
]

_HEART_STRUCTURES = [
    "左房", "左室", "右房", "右室",
    "二尖瓣", "三尖瓣", "主动脉瓣", "肺动脉瓣",
    "室间隔", "左室后壁", "左室侧壁", "右室游离壁",
    "心包", "升主动脉", "降主动脉",
]

_HEART_CONDITIONS = [
    "增大",
    "增厚",
    "轻度反流",
    "中度反流",
    "重度反流",
    "狭窄",
    "运动减低",
    "未见明显异常",
    "收缩功能减低",
    "舒张功能减低",
    "少量积液",
    "未见积液",
]

_HEART_NORMAL_FINDINGS = [
    ("心内结构未见明显异常", None),
    ("左室壁运动未见明显异常", None),
    ("各瓣膜回声及活动未见异常", None),
    ("心包腔未见积液", None),
    ("左室收缩功能正常", None),
    ("左室舒张功能正常", None),
]


# STYLE-003 fix: use vocabulary for mapping instead of separate _map_* functions
def _map_via_vocabulary(vocab: UDAFVocabulary, chinese_term: str) -> str:
    """Map a Chinese term to its standard form using the vocabulary."""
    normalized = vocab.normalize(chinese_term)
    return normalized if normalized else chinese_term


def _generate_thyroid_cases(vocab: UDAFVocabulary) -> list[ValidationCase]:
    """Generate thyroid validation cases programmatically."""
    cases: list[ValidationCase] = []
    tmpl = _THYROID_TEMPLATES[0]
    tirads_levels = [
        ("TI-RADS 2", "等回声", "边界清", "形态规则", "无钙化", "未见明显血流"),
        ("TI-RADS 3", "低回声", "边界清", "形态规则", "无钙化", "未见明显血流"),
        ("TI-RADS 4A", "低回声", "边界欠清", "形态规则", "无钙化", "周边血流"),
        ("TI-RADS 4B", "低回声", "边界不清", "形态不规则", "微钙化", "内部血流"),
        ("TI-RADS 4C", "低回声", "边界不清", "形态不规则", "微钙化", "血流丰富"),
        ("TI-RADS 5", "低回声", "边界不清", "形态不规则", "微钙化", "血流丰富"),
    ]

    idx = 0
    for location in tmpl["locations"]:
        for tirads, echo, boundary, morph, calc, vasc in tirads_levels:
            idx += 1
            size1 = round(0.5 + idx % 30 * 0.1, 1)
            size2 = round(0.3 + idx % 20 * 0.1, 1)
            text = (
                f"甲状腺{location}可见一{echo}结节，"
                f"{boundary}，{morph}，"
                f"大小约{size1}x{size2} cm，"
                f"{calc}，{vasc}。{tirads}"
            )
            expected_attrs = {
                "echogenicity": _map_via_vocabulary(vocab, echo),
                "boundary": _map_via_vocabulary(vocab, boundary),
                "morphology": _map_via_vocabulary(vocab, morph),
                "calcification": _map_via_vocabulary(vocab, calc),
                "vascularity": _map_via_vocabulary(vocab, vasc),
            }
            cases.append(ValidationCase(
                case_id=f"thyroid_{idx:04d}",
                organ=Organ.THYROID,
                input_text=text,
                expected_classification=tirads,
                expected_attributes=expected_attrs,
                source="generated",
            ))

    for location in tmpl["locations"]:
        for echo in tmpl["echoes"]:
            for boundary in tmpl["boundaries"]:
                idx += 1
                size1 = round(0.3 + idx % 25 * 0.1, 1)
                size2 = round(0.2 + idx % 15 * 0.1, 1)
                text = (
                    f"甲状腺{location}{echo}结节，"
                    f"{boundary}，大小约{size1}x{size2} cm。"
                )
                expected_attrs = {
                    "echogenicity": _map_via_vocabulary(vocab, echo),
                    "boundary": _map_via_vocabulary(vocab, boundary),
                }
                cases.append(ValidationCase(
                    case_id=f"thyroid_{idx:04d}",
                    organ=Organ.THYROID,
                    input_text=text,
                    expected_attributes=expected_attrs,
                    source="generated",
                ))

    for location in tmpl["locations"]:
        for echo in tmpl["echoes"]:
            for morph in tmpl["morphologies"]:
                idx += 1
                size1 = round(0.4 + idx % 20 * 0.1, 1)
                size2 = round(0.3 + idx % 15 * 0.1, 1)
                text = (
                    f"甲状腺{location}{echo}结节，"
                    f"{morph}，大小约{size1}x{size2} cm。"
                )
                expected_attrs = {
                    "echogenicity": _map_via_vocabulary(vocab, echo),
                    "morphology": _map_via_vocabulary(vocab, morph),
                }
                cases.append(ValidationCase(
                    case_id=f"thyroid_{idx:04d}",
                    organ=Organ.THYROID,
                    input_text=text,
                    expected_attributes=expected_attrs,
                    source="generated",
                ))

    # BUG-009 fix: case_id and organ corrected from liver_ to thyroid_
    # BUG-010 fix: added morphology to expected_attributes
    for location in tmpl["locations"]:
        for echo in tmpl["echoes"]:
            for vasc in tmpl["vascularities"]:
                idx += 1
                size1 = round(1.2 + idx % 20 * 0.1, 1)
                size2 = round(0.9 + idx % 15 * 0.1, 1)
                text = (
                    f"甲状腺{location}见一{echo}结节，"
                    f"边界清，形态规则，大小约{size1}x{size2} cm，"
                    f"{vasc}。"
                )
                expected_attrs = {
                    "echogenicity": _map_via_vocabulary(vocab, echo),
                    "boundary": "clear",
                    "morphology": "oval",
                    "vascularity": _map_via_vocabulary(vocab, vasc),
                }
                cases.append(ValidationCase(
                    case_id=f"thyroid_{idx:04d}",
                    organ=Organ.THYROID,
                    input_text=text,
                    expected_attributes=expected_attrs,
                    source="generated",
                ))

    return cases


def _generate_liver_cases(vocab: UDAFVocabulary) -> list[ValidationCase]:
    """Generate liver validation cases programmatically."""
    cases: list[ValidationCase] = []
    tmpl = _LIVER_TEMPLATES[0]
    lirads_levels = [
        ("LI-RADS 1", "高回声", "边界清", "形态规则", "无钙化", "未见明显血流"),
        ("LI-RADS 2", "高回声", "边界清", "形态规则", "无钙化", "未见明显血流"),
        ("LI-RADS 3", "低回声", "边界清", "形态规则", "无钙化", "周边血流"),
        ("LI-RADS 4", "低回声", "边界不清", "形态不规则", "无钙化", "内部血流"),
        ("LI-RADS 5", "低回声", "边界不清", "形态不规则", "无钙化", "血流丰富"),
    ]

    idx = 0
    for location in tmpl["locations"]:
        for lirads, echo, boundary, morph, calc, vasc in lirads_levels:
            idx += 1
            size1 = round(1.0 + idx % 40 * 0.1, 1)
            size2 = round(0.8 + idx % 30 * 0.1, 1)
            text = (
                f"{location}见一{echo}结节，"
                f"{boundary}，{morph}，"
                f"大小约{size1}x{size2} cm，"
                f"{calc}，{vasc}。{lirads}"
            )
            expected_attrs = {
                "echogenicity": _map_via_vocabulary(vocab, echo),
                "boundary": _map_via_vocabulary(vocab, boundary),
                "morphology": _map_via_vocabulary(vocab, morph),
                "calcification": _map_via_vocabulary(vocab, calc),
                "vascularity": _map_via_vocabulary(vocab, vasc),
            }
            cases.append(ValidationCase(
                case_id=f"liver_{idx:04d}",
                organ=Organ.LIVER,
                input_text=text,
                expected_classification=lirads,
                expected_attributes=expected_attrs,
                source="generated",
            ))

    for location in tmpl["locations"]:
        for echo in tmpl["echoes"]:
            for boundary in tmpl["boundaries"]:
                idx += 1
                size1 = round(0.5 + idx % 35 * 0.1, 1)
                size2 = round(0.4 + idx % 25 * 0.1, 1)
                text = (
                    f"{location}见一{echo}结节，"
                    f"{boundary}，大小约{size1}x{size2} cm。"
                )
                expected_attrs = {
                    "echogenicity": _map_via_vocabulary(vocab, echo),
                    "boundary": _map_via_vocabulary(vocab, boundary),
                }
                cases.append(ValidationCase(
                    case_id=f"liver_{idx:04d}",
                    organ=Organ.LIVER,
                    input_text=text,
                    expected_attributes=expected_attrs,
                    source="generated",
                ))

    for location in tmpl["locations"]:
        for echo in tmpl["echoes"]:
            for morph in tmpl["morphologies"]:
                idx += 1
                size1 = round(0.6 + idx % 30 * 0.1, 1)
                size2 = round(0.5 + idx % 20 * 0.1, 1)
                text = (
                    f"{location}见一{echo}结节，"
                    f"{morph}，大小约{size1}x{size2} cm。"
                )
                expected_attrs = {
                    "echogenicity": _map_via_vocabulary(vocab, echo),
                    "morphology": _map_via_vocabulary(vocab, morph),
                }
                cases.append(ValidationCase(
                    case_id=f"liver_{idx:04d}",
                    organ=Organ.LIVER,
                    input_text=text,
                    expected_attributes=expected_attrs,
                    source="generated",
                ))

    for location in tmpl["locations"]:
        for vasc in tmpl["vascularities"]:
            for calc in tmpl["calcifications"]:
                idx += 1
                size1 = round(1.0 + idx % 25 * 0.1, 1)
                size2 = round(0.8 + idx % 20 * 0.1, 1)
                text = (
                    f"{location}见一低回声结节，"
                    f"边界清，形态规则，大小约{size1}x{size2} cm，"
                    f"{calc}，{vasc}。"
                )
                expected_attrs = {
                    "echogenicity": "hypoechoic",
                    "boundary": "clear",
                    "morphology": "oval",
                    "calcification": _map_via_vocabulary(vocab, calc),
                    "vascularity": _map_via_vocabulary(vocab, vasc),
                }
                cases.append(ValidationCase(
                    case_id=f"liver_{idx:04d}",
                    organ=Organ.LIVER,
                    input_text=text,
                    expected_attributes=expected_attrs,
                    source="generated",
                ))

    for location in tmpl["locations"]:
        for echo in tmpl["echoes"]:
            for vasc in tmpl["vascularities"]:
                idx += 1
                size1 = round(1.2 + idx % 20 * 0.1, 1)
                size2 = round(0.9 + idx % 15 * 0.1, 1)
                text = (
                    f"{location}见一{echo}结节，"
                    f"边界清，大小约{size1}x{size2} cm，"
                    f"{vasc}。"
                )
                expected_attrs = {
                    "echogenicity": _map_via_vocabulary(vocab, echo),
                    "boundary": "clear",
                    "vascularity": _map_via_vocabulary(vocab, vasc),
                }
                cases.append(ValidationCase(
                    case_id=f"liver_{idx:04d}",
                    organ=Organ.LIVER,
                    input_text=text,
                    expected_attributes=expected_attrs,
                    source="generated",
                ))

    return cases


# TEST-007 fix: heart cases expanded to 200+ with expected_attributes
# Updated: all heart cases now have expected_attributes
def _generate_heart_cases(vocab: UDAFVocabulary) -> list[ValidationCase]:
    """Generate heart validation cases programmatically with expected_attributes."""
    cases: list[ValidationCase] = []
    idx = 0

    # Normal findings with expected attributes
    normal_findings = [
        ("心内结构未见明显异常", {"heart_condition": "no_abnormality"}),
        ("左室壁运动未见明显异常", {"heart_condition": "normal"}),
        ("各瓣膜回声及活动未见异常", {"heart_condition": "normal"}),
        ("心包腔未见积液", {"heart_condition": "no_effusion"}),
        ("左室收缩功能正常", {"heart_function": "ef_normal"}),
        ("左室舒张功能正常", {"heart_condition": "normal"}),
    ]
    for finding_text, expected_attrs in normal_findings:
        idx += 1
        cases.append(ValidationCase(
            case_id=f"heart_{idx:04d}",
            organ=Organ.HEART,
            input_text=finding_text + "。",
            expected_attributes=expected_attrs,
            source="generated",
        ))

    # Heart structure to code mapping
    structure_map = {
        "左房": "left_atrium",
        "左室": "left_ventricle",
        "右房": "right_atrium",
        "右室": "right_ventricle",
        "二尖瓣": "mitral_valve",
        "三尖瓣": "tricuspid_valve",
        "主动脉瓣": "aortic_valve",
        "肺动脉瓣": "pulmonary_valve",
        "室间隔": "interventricular_septum",
        "左室后壁": "left_ventricle_wall",
        "左室侧壁": "left_ventricle_wall",
        "右室游离壁": "right_ventricle",
        "心包": "pericardium",
        "升主动脉": "aorta",
        "降主动脉": "aorta",
    }

    # Condition to code mapping
    condition_map = {
        "增大": "enlarged",
        "增厚": "thickened",
        "轻度反流": "mild_regurgitation",
        "中度反流": "moderate_regurgitation",
        "重度反流": "severe_regurgitation",
        "狭窄": "stenosis",
        "运动减低": "hypokinesis",
        "未见明显异常": "no_abnormality",
        "收缩功能减低": "reduced_systolic_function",
        "舒张功能减低": "reduced_diastolic_function",
        "少量积液": "small_effusion",
        "未见积液": "no_effusion",
    }

    # Structure-condition combinations with expected attributes
    for structure in _HEART_STRUCTURES:
        for condition_name in _HEART_CONDITIONS:
            idx += 1
            text = f"{structure}{condition_name}。"
            struct_code = structure_map.get(structure)
            cond_code = condition_map.get(condition_name)
            expected_attrs = {}
            if struct_code:
                expected_attrs["heart_structure"] = struct_code
            if cond_code:
                expected_attrs["heart_condition"] = cond_code
            cases.append(ValidationCase(
                case_id=f"heart_{idx:04d}",
                organ=Organ.HEART,
                input_text=text,
                expected_attributes=expected_attrs if expected_attrs else None,
                source="generated",
            ))

    # Combination cases with expected attributes
    combination_cases = [
        ("左房增大，二尖瓣轻度反流", {"heart_structure": "left_atrium", "heart_condition": "enlarged"}),
        ("左室增大，左室收缩功能减低", {"heart_structure": "left_ventricle", "heart_condition": "enlarged"}),
        ("右房增大，三尖瓣轻度反流", {"heart_structure": "right_atrium", "heart_condition": "enlarged"}),
        ("左室壁增厚，左室舒张功能减低", {"heart_structure": "left_ventricle_wall", "heart_condition": "thickened"}),
        ("心包少量积液，心内结构未见明显异常", {"heart_condition": "small_effusion"}),
    ]
    for text, expected_attrs in combination_cases:
        idx += 1
        cases.append(ValidationCase(
            case_id=f"heart_{idx:04d}",
            organ=Organ.HEART,
            input_text=text + "。",
            expected_attributes=expected_attrs,
            source="generated",
        ))

    # Double structure normal findings
    for structure in _HEART_STRUCTURES[:5]:
        for s2 in _HEART_STRUCTURES[5:10]:
            idx += 1
            text = f"{structure}未见明显异常，{s2}未见明显异常。"
            struct_code1 = structure_map.get(structure)
            struct_code2 = structure_map.get(s2)
            expected_attrs = {"heart_condition": "no_abnormality"}
            if struct_code1:
                expected_attrs["heart_structure"] = struct_code1
            cases.append(ValidationCase(
                case_id=f"heart_{idx:04d}",
                organ=Organ.HEART,
                input_text=text,
                expected_attributes=expected_attrs,
                source="generated",
            ))

    # Additional function assessment cases
    function_cases = [
        ("左室收缩功能减低", {"heart_condition": "reduced_systolic_function"}),
        ("左室舒张功能减低", {"heart_condition": "reduced_diastolic_function"}),
        ("左室EF值正常", {"heart_function": "ef_normal"}),
        ("左室EF轻度减低", {"heart_function": "ef_mild_reduced"}),
        ("心包少量积液", {"heart_condition": "small_effusion"}),
    ]
    for text, expected_attrs in function_cases:
        idx += 1
        cases.append(ValidationCase(
            case_id=f"heart_{idx:04d}",
            organ=Organ.HEART,
            input_text=text + "。",
            expected_attributes=expected_attrs,
            source="generated",
        ))

    return cases


class BaselineValidator:
    """
    Baseline accuracy validator.
    Validates diagnostic models against a baseline dataset.
    """

    def __init__(self, vocab: UDAFVocabulary | None = None):
        self.vocab = vocab or UDAFVocabulary()
        self.cases: list[ValidationCase] = []
        self._load_cases()

    def _load_cases(self):
        """Load validation cases (generated to meet >=200/organ requirement)."""
        thyroid_cases = _generate_thyroid_cases(self.vocab)
        liver_cases = _generate_liver_cases(self.vocab)
        heart_cases = _generate_heart_cases(self.vocab)

        self.cases.extend(thyroid_cases)
        self.cases.extend(liver_cases)
        self.cases.extend(heart_cases)

        logger.info("Loaded validation cases",
                    thyroid=len(thyroid_cases),
                    liver=len(liver_cases),
                    heart=len(heart_cases),
                    total=len(self.cases))

    def add_case(self, case: ValidationCase) -> None:
        """Add a validation case."""
        self.cases.append(case)

    def add_cases_from_json(self, json_path: str) -> None:
        """Add validation cases from a JSON file."""
        path = Path(json_path)
        if not path.exists():
            raise FileNotFoundError(f"Validation cases file not found: {json_path}")

        with open(path, encoding='utf-8') as f:
            data = json.load(f)

        for case_data in data.get('cases', []):
            case = ValidationCase(
                case_id=case_data.get('case_id'),
                organ=case_data.get('organ'),
                input_text=case_data.get('input_text'),
                expected_classification=case_data.get('expected_classification'),
                expected_attributes=case_data.get('expected_attributes'),
                reference_standard=case_data.get('reference_standard'),
                source=case_data.get('source')
            )
            self.add_case(case)

    def validate_udt_parser(self, parser) -> BaselineReport:
        """
        Validate the UDT parser against baseline cases.

        Args:
            parser: UDTParser instance

        Returns:
            BaselineReport with validation results
        """
        start_time = time.time()
        results: list[ValidationResult] = []

        for case in self.cases:
            case_start = time.time()
            result = self._validate_single_case(case, parser)
            case_end = time.time()
            result.execution_time_ms = (case_end - case_start) * 1000
            results.append(result)

        total = len(results)
        passed = sum(1 for r in results if r.status == ValidationStatus.PASSED)
        failed = sum(1 for r in results if r.status == ValidationStatus.FAILED)
        skipped = sum(1 for r in results if r.status == ValidationStatus.SKIPPED)

        organ_breakdown: dict[str, dict[str, int]] = {}
        for result in results:
            organ = result.case.organ
            if organ not in organ_breakdown:
                organ_breakdown[organ] = {"total": 0, "passed": 0, "failed": 0}
            organ_breakdown[organ]["total"] += 1
            if result.status == ValidationStatus.PASSED:
                organ_breakdown[organ]["passed"] += 1
            elif result.status == ValidationStatus.FAILED:
                organ_breakdown[organ]["failed"] += 1

        duration = time.time() - start_time

        report = BaselineReport(
            report_id=f"baseline_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            total_cases=total,
            passed_cases=passed,
            failed_cases=failed,
            skipped_cases=skipped,
            accuracy=passed / (passed + failed) if (passed + failed) > 0 else 0.0,
            organ_breakdown=organ_breakdown,
            results=results,
            created_at=datetime.now().isoformat(),
            duration_seconds=duration,
        )

        logger.info("Baseline validation completed",
                    total=total, passed=passed, failed=failed,
                    accuracy=report.accuracy, duration=duration)

        return report

    def _validate_single_case(self, case: ValidationCase, parser) -> ValidationResult:
        """Validate a single case."""
        errors: list[str] = []
        warnings: list[str] = []

        try:
            parse_result = parser.parse(case.input_text, case.organ)

            actual_classification = None
            for attr in parse_result.attributes:
                if attr.category in ('ti_rads', 'li_rads'):
                    actual_classification = attr.value
                    break

            classification_ok = True
            if case.expected_classification and actual_classification != case.expected_classification:
                    errors.append(
                        f"Classification mismatch: expected '{case.expected_classification}', "
                        f"got '{actual_classification}'"
                    )
                    classification_ok = False

            # NEW2-002: skip attribute validation when expected_attributes is None
            if case.expected_attributes is None and case.expected_classification is None:
                return ValidationResult(
                    case=case,
                    status=ValidationStatus.SKIPPED,
                    actual_classification=actual_classification,
                    actual_attributes={},
                    errors=[],
                    warnings=["Skipped: no expected attributes or classification defined"],
                )

            attributes_ok = True
            if case.expected_attributes:
                # BUG-011 fix: use list values to avoid overwrite on duplicate categories
                actual_attrs: dict[str, list[str]] = {}
                for a in parse_result.attributes:
                    actual_attrs.setdefault(a.category, []).append(a.value)

                for category, expected_value in case.expected_attributes.items():
                    if category not in actual_attrs:
                        errors.append(f"Missing attribute: {category}")
                        attributes_ok = False
                    elif expected_value not in actual_attrs[category]:
                        errors.append(
                            f"Attribute mismatch for {category}: "
                            f"expected '{expected_value}', got '{actual_attrs[category]}'"
                        )
                        attributes_ok = False

            status = ValidationStatus.PASSED if (classification_ok and attributes_ok) else ValidationStatus.FAILED

            # BUG-011 residual fix: use list values in actual_attributes too
            actual_attrs_listed: dict[str, list[str]] = {}
            for a in parse_result.attributes:
                actual_attrs_listed.setdefault(a.category, []).append(a.value)

            return ValidationResult(
                case=case,
                status=status,
                actual_classification=actual_classification,
                actual_attributes=actual_attrs_listed,
                errors=errors,
                warnings=warnings,
            )

        except Exception as e:
            logger.error("Case validation error", case_id=case.case_id, error=str(e))
            errors.append(f"Validation error: {e!s}")
            return ValidationResult(
                case=case,
                status=ValidationStatus.FAILED,
                actual_attributes={},
                errors=errors,
                warnings=warnings,
            )

    def export_report(self, report: BaselineReport, output_path: str) -> None:
        """Export validation report to JSON file."""
        data = {
            "report_id": report.report_id,
            "summary": {
                "total_cases": report.total_cases,
                "passed_cases": report.passed_cases,
                "failed_cases": report.failed_cases,
                "skipped_cases": report.skipped_cases,
                "accuracy": report.accuracy,
                "organ_breakdown": report.organ_breakdown,
                "duration_seconds": report.duration_seconds,
            },
            "results": [
                {
                    "case_id": r.case.case_id,
                    "organ": r.case.organ,
                    "status": r.status.value,
                    "expected_classification": r.case.expected_classification,
                    "actual_classification": r.actual_classification,
                    "errors": r.errors,
                    "warnings": r.warnings,
                    "execution_time_ms": r.execution_time_ms,
                }
                for r in report.results
            ],
            "created_at": report.created_at,
        }

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_cases_by_organ(self, organ) -> list[ValidationCase]:
        """Get all validation cases for a specific organ."""
        # BUG-003 residual: normalize organ for comparison
        organ_value = organ.value if isinstance(organ, Enum) else organ
        return [c for c in self.cases if c.organ == organ_value]
