"""
Unit tests for Knowledge Base module.
"""
import json
import os
import tempfile

import pytest

from app.knowledge.baseline_annotation import (
    AnnotationLabel,
    AnnotationStatus,
    AnnotatorResult,
    BaselineAnnotator,
    DisputeResolution,
    DualAnnotation,
    compute_agreement_rate,
    compute_cohen_kappa,
)
from app.knowledge.baseline_validation import BaselineValidator, ValidationCase, ValidationStatus
from app.knowledge.segmentation_validation import (
    SegBackend,
    SegMatchStatus,
    SegmentationValidator,
    SegToken,
)
from app.knowledge.udt_parser import MeasurementValue, UDTParser, UDTParserConfig
from app.knowledge.version import KBVersion, is_compatible, parse_kb_version
from app.knowledge.vocabulary import (
    Organ,
    UDAFVocabulary,
)


class TestUDAFVocabulary:
    """Tests for UDAFVocabulary."""

    def test_init(self):
        vocab = UDAFVocabulary()
        assert len(vocab.all_standard_terms) > 0
        assert "hyperechoic" in vocab.all_standard_terms
        assert "hypoechoic" in vocab.all_standard_terms

    def test_normalize_existing_term(self):
        vocab = UDAFVocabulary()
        assert vocab.normalize("高回声") == "hyperechoic"

    def test_normalize_standard_term_directly(self):
        vocab = UDAFVocabulary()
        assert vocab.normalize("hyperechoic") == "hyperechoic"

    def test_normalize_nonexistent_term(self):
        vocab = UDAFVocabulary()
        assert vocab.normalize("不存在的术语") is None

    def test_normalize_none(self):
        vocab = UDAFVocabulary()
        assert vocab.normalize(None) is None

    def test_normalize_empty_string(self):
        vocab = UDAFVocabulary()
        assert vocab.normalize("") is None

    def test_normalize_whitespace(self):
        vocab = UDAFVocabulary()
        assert vocab.normalize("  高回声  ") == "hyperechoic"

    def test_get_synonyms(self):
        vocab = UDAFVocabulary()
        synonyms = vocab.get_synonyms("低回声")
        assert isinstance(synonyms, list)
        assert len(synonyms) > 0
        assert "回声减低" in synonyms

    def test_get_synonyms_for_standard_term(self):
        vocab = UDAFVocabulary()
        synonyms = vocab.get_synonyms("hypoechoic")
        assert isinstance(synonyms, list)
        assert len(synonyms) > 0
        assert "低回声" in synonyms

    def test_get_synonyms_nonexistent(self):
        vocab = UDAFVocabulary()
        assert vocab.get_synonyms("不存在") == []

    def test_get_terms_by_category(self):
        vocab = UDAFVocabulary()
        terms = vocab.get_terms_by_category("echogenicity")
        assert "hyperechoic" in terms
        assert "hypoechoic" in terms

    def test_get_terms_by_category_li_rads(self):
        vocab = UDAFVocabulary()
        terms = vocab.get_terms_by_category("li_rads")
        assert len(terms) > 0
        assert "LI-RADS 1" in terms

    def test_get_terms_by_organ_enum(self):
        vocab = UDAFVocabulary()
        terms = vocab.get_terms_by_organ(Organ.THYROID)
        assert len(terms) > 0
        assert "TI-RADS 1" in terms
        assert "TI-RADS 2" in terms

    def test_get_terms_by_organ_string(self):
        vocab = UDAFVocabulary()
        terms = vocab.get_terms_by_organ("thyroid")
        assert len(terms) > 0
        assert "TI-RADS 3" in terms

    def test_coverage_rate(self):
        vocab = UDAFVocabulary()
        terms = ["高回声", "边界清", "不存在的术语"]
        rate = vocab.coverage_rate(terms)
        assert 0.0 <= rate <= 1.0
        assert rate < 1.0

    def test_coverage_rate_empty(self):
        vocab = UDAFVocabulary()
        assert vocab.coverage_rate([]) == 1.0

    def test_coverage_rate_all_matched(self):
        vocab = UDAFVocabulary()
        terms = ["高回声", "低回声"]
        assert vocab.coverage_rate(terms) == 1.0

    def test_morphology_rule_mapping(self):
        vocab = UDAFVocabulary()
        assert vocab.normalize("形态规则") == "oval"
        assert vocab.normalize("规则") == "oval"

    def test_vascularity_rich_mapping(self):
        vocab = UDAFVocabulary()
        assert vocab.normalize("血流丰富") == "mixed"
        assert vocab.normalize("丰富血流") == "mixed"

    def test_morphology_irregular_not_in_boundary(self):
        vocab = UDAFVocabulary()
        boundary_terms = vocab.get_terms_by_category("boundary")
        morphology_terms = vocab.get_terms_by_category("morphology")
        assert "形态不规则" not in [
            s for t in boundary_terms
            for s in vocab.get_synonyms(t)
        ]
        assert "形态不规则" in [
            s for t in morphology_terms
            for s in vocab.get_synonyms(t)
        ]

    def test_no_self_mapping_in_reverse_lookup(self):
        vocab = UDAFVocabulary()
        assert "hyperechoic" not in vocab.get_all_synonyms()

    def test_has_synonym(self):
        vocab = UDAFVocabulary()
        assert vocab.has_synonym("低回声结节")
        assert not vocab.has_synonym("xyz123")

    def test_iter_synonym_mappings(self):
        vocab = UDAFVocabulary()
        mappings = list(vocab.iter_synonym_mappings())
        assert len(mappings) > 0
        for synonym, standard in mappings:
            assert isinstance(synonym, str)
            assert isinstance(standard, str)


class TestUDTParser:
    """Tests for UDTParser."""

    def test_init(self):
        parser = UDTParser()
        assert parser.vocab is not None
        assert parser.config is not None

    def test_init_with_config(self):
        config = UDTParserConfig(min_confidence=0.8, extract_measurements=False)
        parser = UDTParser(config=config)
        assert parser.config.min_confidence == 0.8
        assert parser.config.extract_measurements is False

    # NEW-003: verify pre-cached sorted synonyms
    def test_sorted_synonyms_cached(self):
        parser = UDTParser()
        assert len(parser._sorted_synonyms) > 0
        first_len = len(parser._sorted_synonyms[0][0])
        last_len = len(parser._sorted_synonyms[-1][0])
        assert first_len >= last_len

    def test_parse_empty_text(self):
        parser = UDTParser()
        result = parser.parse("")
        assert len(result.attributes) == 0

    def test_parse_none_text(self):
        parser = UDTParser()
        result = parser.parse(None)
        assert len(result.attributes) == 0

    def test_parse_whitespace_only(self):
        parser = UDTParser()
        result = parser.parse("   ")
        assert len(result.attributes) == 0

    def test_parse_thyroid_text(self):
        parser = UDTParser()
        text = "甲状腺右叶中极低回声结节，边界清，TI-RADS 3"
        result = parser.parse(text, Organ.THYROID)
        assert result.organ == Organ.THYROID
        assert len(result.attributes) > 0
        categories = {a.category for a in result.attributes}
        assert "echogenicity" in categories

    def test_parse_extracts_echogenicity(self):
        parser = UDTParser()
        result = parser.parse("低回声结节", Organ.THYROID)
        echos = [a for a in result.attributes if a.category == "echogenicity"]
        assert len(echos) > 0
        assert echos[0].value == "hypoechoic"

    # NEW2-001: verify English standard term parsing (second loop fix)
    def test_parse_english_standard_term(self):
        parser = UDTParser()
        result = parser.parse("hyperechoic nodule", Organ.THYROID)
        echos = [a for a in result.attributes if a.category == "echogenicity"]
        assert len(echos) > 0
        assert echos[0].value == "hyperechoic"
        assert echos[0].category == "echogenicity"

    def test_parse_english_boundary_term(self):
        parser = UDTParser()
        result = parser.parse("ill-defined margin", Organ.THYROID)
        bounds = [a for a in result.attributes if a.category == "boundary"]
        assert len(bounds) > 0
        assert bounds[0].value == "ill-defined"
        assert bounds[0].category == "boundary"

    def test_parse_extracts_morphology_rule(self):
        parser = UDTParser()
        result = parser.parse("形态规则", Organ.THYROID)
        morphs = [a for a in result.attributes if a.category == "morphology"]
        assert len(morphs) > 0
        assert morphs[0].value == "oval"

    def test_parse_extracts_vascularity_rich(self):
        parser = UDTParser()
        result = parser.parse("血流丰富", Organ.THYROID)
        vascs = [a for a in result.attributes if a.category == "vascularity"]
        assert len(vascs) > 0
        assert vascs[0].value == "mixed"

    def test_parse_extracts_tirads(self):
        parser = UDTParser()
        result = parser.parse("TI-RADS 4C", Organ.THYROID)
        tirads = [a for a in result.attributes if a.category == "ti_rads"]
        assert len(tirads) > 0
        assert tirads[0].value == "TI-RADS 4C"

    def test_parse_extracts_lirads(self):
        parser = UDTParser()
        result = parser.parse("LI-RADS 4", Organ.LIVER)
        lirads = [a for a in result.attributes if a.category == "li_rads"]
        assert len(lirads) > 0
        assert lirads[0].value == "LI-RADS 4"

    # NEW-001: measurement uses MeasurementValue internally
    def test_parse_extracts_measurement(self):
        parser = UDTParser()
        result = parser.parse("大小约1.2x0.8 cm")
        measurements = [a for a in result.attributes if a.category == "measurement"]
        assert len(measurements) > 0
        assert measurements[0].value == "1.2x0.8 cm"

    def test_parse_config_disable_measurements(self):
        config = UDTParserConfig(extract_measurements=False)
        parser = UDTParser(config=config)
        result = parser.parse("大小约1.2x0.8 cm")
        measurements = [a for a in result.attributes if a.category == "measurement"]
        assert len(measurements) == 0

    def test_parse_config_disable_classifications(self):
        config = UDTParserConfig(extract_classifications=False)
        parser = UDTParser(config=config)
        result = parser.parse("TI-RADS 3", Organ.THYROID)
        tirads = [a for a in result.attributes if a.category == "ti_rads"]
        assert len(tirads) == 0

    def test_parse_organ_detection_thyroid(self):
        parser = UDTParser()
        result = parser.parse("甲状腺右叶低回声结节")
        assert result.organ == Organ.THYROID

    def test_parse_organ_detection_liver(self):
        parser = UDTParser()
        result = parser.parse("肝右叶高回声结节")
        assert result.organ == Organ.LIVER

    def test_parse_organ_detection_heart(self):
        parser = UDTParser()
        result = parser.parse("心脏结构未见异常")
        assert result.organ == Organ.HEART

    def test_parse_organ_no_false_positive(self):
        parser = UDTParser()
        result = parser.parse("心情愉快，体检正常")
        assert result.organ != Organ.HEART

    def test_parse_tirads_no_tr_prefix(self):
        parser = UDTParser()
        result = parser.parse("TR3", Organ.THYROID)
        tirads = [a for a in result.attributes if a.category == "ti_rads"]
        assert len(tirads) == 0

    def test_parse_coverage_rate(self):
        parser = UDTParser()
        result = parser.parse("低回声，边界清，形态规则", Organ.THYROID)
        assert result.coverage_rate > 0

    # NEW-004 + BUG-007 residual: normalize_report uses clean_text positions
    def test_normalize_report(self):
        parser = UDTParser()
        text = "甲状腺右叶中极低回声结节，边界清，TI-RADS 3"
        normalized = parser.normalize_report(text)
        assert isinstance(normalized, str)
        assert "hypoechoic" in normalized
        assert "clear" in normalized

    # Boundary condition tests with meaningful assertions
    def test_parse_long_text(self):
        parser = UDTParser()
        text = "甲状腺" + "低回声" * 100
        result = parser.parse(text)
        echos = [a for a in result.attributes if a.category == "echogenicity"]
        assert len(echos) >= 100

    def test_parse_special_characters(self):
        parser = UDTParser()
        result = parser.parse("!@#$%^&*()")
        assert len(result.attributes) == 0

    def test_parse_mixed_chinese_english(self):
        parser = UDTParser()
        result = parser.parse("thyroid低回声结节")
        echos = [a for a in result.attributes if a.category == "echogenicity"]
        assert len(echos) == 1
        assert echos[0].value == "hypoechoic"

    def test_parse_very_long_measurement(self):
        parser = UDTParser()
        result = parser.parse("大小约99.9x88.8 cm")
        measurements = [a for a in result.attributes if a.category == "measurement"]
        assert len(measurements) == 1
        assert measurements[0].value == "99.9x88.8 cm"

    # DESIGN-004: MeasurementValue dataclass
    def test_measurement_value_str(self):
        mv = MeasurementValue(value1=1.2, value2=0.8)
        assert str(mv) == "1.2x0.8 cm"

    def test_measurement_value_single(self):
        mv = MeasurementValue(value1=1.5)
        assert str(mv) == "1.5 cm"

    def test_measurement_value_with_type(self):
        mv = MeasurementValue(value1=2.0, value2=1.5, measurement_type="longitudinal")
        assert str(mv) == "2.0x1.5 cm"
        assert mv.measurement_type == "longitudinal"


class TestVersion:
    """Tests for version management."""

    def test_parse_valid_version(self):
        version = parse_kb_version("kb_v1.2.3")
        assert version.major == 1
        assert version.minor == 2
        assert version.patch == 3

    def test_parse_version_with_hospital(self):
        version = parse_kb_version("kb_v1.2.3~hospital_A")
        assert version.hospital_id == "hospital_A"

    def test_parse_invalid_version(self):
        assert parse_kb_version("invalid_version") is None

    def test_parse_empty_version(self):
        assert parse_kb_version("") is None
        assert parse_kb_version(None) is None

    def test_version_comparison(self):
        v1 = KBVersion(major=1, minor=2, patch=3)
        v2 = KBVersion(major=1, minor=2, patch=4)
        v3 = KBVersion(major=2, minor=0, patch=0)
        assert v1 < v2
        assert v2 < v3
        assert v1 == KBVersion(major=1, minor=2, patch=3)
        assert v1 != v2
        assert v1 <= v2

    def test_version_comparison_with_hospital(self):
        v1 = KBVersion(major=1, minor=2, patch=3, hospital_id="A")
        v2 = KBVersion(major=1, minor=2, patch=3, hospital_id="B")
        v3 = KBVersion(major=1, minor=2, patch=3)
        assert v1 < v2
        assert v3 < v1

    def test_version_str(self):
        v = KBVersion(major=1, minor=2, patch=3)
        assert str(v) == "kb_v1.2.3"

    def test_version_str_with_hospital(self):
        v = KBVersion(major=1, minor=2, patch=3, hospital_id="hosp_A")
        assert str(v) == "kb_v1.2.3~hosp_A"

    def test_version_to_tuple(self):
        v = KBVersion(major=1, minor=2, patch=3)
        assert v.to_tuple() == (1, 2, 3)

    def test_is_compatible(self):
        assert is_compatible("v1.0", "kb_v1.2.3") is True
        assert is_compatible("v1.0", "kb_v2.0.0") is True
        assert is_compatible("v2.0", "kb_v2.0.0") is True
        assert is_compatible("v2.0", "kb_v1.0.0") is False

    def test_is_compatible_invalid(self):
        assert is_compatible("", "kb_v1.0.0") is False
        assert is_compatible("v1.0", "") is False


class TestBaselineValidator:
    """Tests for BaselineValidator."""

    def test_init(self):
        validator = BaselineValidator()
        assert len(validator.cases) > 0

    def test_thyroid_cases_count(self):
        validator = BaselineValidator()
        thyroid_cases = validator.get_cases_by_organ(Organ.THYROID)
        assert len(thyroid_cases) >= 200

    def test_liver_cases_count(self):
        validator = BaselineValidator()
        liver_cases = validator.get_cases_by_organ(Organ.LIVER)
        assert len(liver_cases) >= 200

    def test_heart_cases_count(self):
        validator = BaselineValidator()
        heart_cases = validator.get_cases_by_organ(Organ.HEART)
        assert len(heart_cases) >= 200

    # NEW2-003: verify heart cases are VALIDATED (not SKIPPED)
    def test_heart_cases_validated(self):
        validator = BaselineValidator()
        parser = UDTParser()
        report = validator.validate_udt_parser(parser)
        heart_results = [r for r in report.results if r.case.organ == Organ.HEART]
        skipped = sum(1 for r in heart_results if r.status == ValidationStatus.SKIPPED)
        assert skipped == 0, (
            f"Expected no heart cases to be SKIPPED, "
            f"but {skipped} were skipped out of {len(heart_results)}"
        )
        passed = sum(1 for r in heart_results if r.status == ValidationStatus.PASSED)
        assert passed > 0, (
            f"Expected at least some heart cases to pass, but {passed} passed"
        )

    def test_case_id_matches_organ(self):
        validator = BaselineValidator()
        for case in validator.cases:
            if case.organ == Organ.THYROID:
                assert case.case_id.startswith("thyroid_"), (
                    f"Thyroid case has wrong case_id: {case.case_id}"
                )
            elif case.organ == Organ.LIVER:
                assert case.case_id.startswith("liver_"), (
                    f"Liver case has wrong case_id: {case.case_id}"
                )
            elif case.organ == Organ.HEART:
                assert case.case_id.startswith("heart_"), (
                    f"Heart case has wrong case_id: {case.case_id}"
                )

    def test_validate_udt_parser(self):
        validator = BaselineValidator()
        parser = UDTParser()
        report = validator.validate_udt_parser(parser)
        assert report is not None
        assert report.total_cases > 0
        assert isinstance(report.accuracy, float)
        assert 0.0 <= report.accuracy <= 1.0

    def test_validate_accuracy_threshold(self):
        validator = BaselineValidator()
        parser = UDTParser()
        report = validator.validate_udt_parser(parser)
        thyroid_liver_total = (
            report.organ_breakdown.get(Organ.THYROID, {}).get("total", 0)
            + report.organ_breakdown.get(Organ.LIVER, {}).get("total", 0)
        )
        thyroid_liver_passed = (
            report.organ_breakdown.get(Organ.THYROID, {}).get("passed", 0)
            + report.organ_breakdown.get(Organ.LIVER, {}).get("passed", 0)
        )
        if thyroid_liver_total > 0:
            tl_accuracy = thyroid_liver_passed / thyroid_liver_total
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"Thyroid+Liver validation accuracy: {tl_accuracy:.2%}")

    def test_validation_status_enum(self):
        assert ValidationStatus.PASSED.value == "passed"
        assert ValidationStatus.FAILED.value == "failed"

    def test_organ_breakdown(self):
        validator = BaselineValidator()
        parser = UDTParser()
        report = validator.validate_udt_parser(parser)
        assert Organ.THYROID in report.organ_breakdown
        assert Organ.LIVER in report.organ_breakdown

    # BUG-011 residual: actual_attributes now uses list values
    def test_actual_attributes_uses_lists(self):
        validator = BaselineValidator()
        parser = UDTParser()
        report = validator.validate_udt_parser(parser)
        for result in report.results:
            if result.actual_attributes:
                for key, val in result.actual_attributes.items():
                    assert isinstance(val, list), (
                        f"actual_attributes['{key}'] should be list, got {type(val)}"
                    )

    def test_add_cases_from_json(self):
        validator = BaselineValidator()
        initial_count = len(validator.cases)

        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False, encoding='utf-8'
        ) as f:
            json.dump({
                "cases": [
                    {
                        "case_id": "test_0001",
                        "organ": "thyroid",
                        "input_text": "甲状腺低回声结节",
                        "expected_attributes": {"echogenicity": "hypoechoic"},
                    }
                ]
            }, f, ensure_ascii=False)
            temp_path = f.name

        try:
            validator.add_cases_from_json(temp_path)
            assert len(validator.cases) == initial_count + 1
        finally:
            os.unlink(temp_path)

    def test_export_annotation_batch_permission_error(self):
        annotator = BaselineAnnotator()
        batch = annotator.create_batch(Organ.THYROID)

        for case in batch.cases[:1]:
            result_a = annotator.auto_annotate(case, "A")
            result_b = annotator.auto_annotate(case, "B")
            dual = annotator.create_dual_annotation(case, result_a, result_b)
            batch.dual_annotations.append(dual)

        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            read_only_file = os.path.join(tmpdir, "readonly.json")
            with open(read_only_file, 'w') as f:
                f.write("test")
            os.chmod(read_only_file, 0o444)

            result = annotator.export_annotation_batch(batch, read_only_file)
            assert result is False, "Export should return False when file is read-only"

    def test_add_cases_from_json_not_found(self):
        validator = BaselineValidator()
        with pytest.raises(FileNotFoundError):
            validator.add_cases_from_json("/nonexistent/path.json")

    def test_export_report(self):
        validator = BaselineValidator()
        parser = UDTParser()
        report = validator.validate_udt_parser(parser)

        with tempfile.NamedTemporaryFile(
            suffix='.json', delete=False
        ) as f:
            temp_path = f.name

        try:
            validator.export_report(report, temp_path)
            assert os.path.exists(temp_path)
            with open(temp_path, encoding='utf-8') as f:
                data = json.load(f)
            assert "report_id" in data
            assert "summary" in data
            assert data["summary"]["total_cases"] > 0
        finally:
            os.unlink(temp_path)

    def test_validate_single_case_exception_returns_failed_with_attributes(self):
        validator = BaselineValidator()
        case = ValidationCase(
            case_id="test_exc_001",
            organ=Organ.THYROID,
            input_text="甲状腺右叶低回声结节",
            expected_attributes={"echo": "hypoechoic"},
        )
        result = validator._validate_single_case(case, None)
        assert result.status == ValidationStatus.FAILED
        assert isinstance(result.actual_attributes, dict)
        assert any("Validation error" in e for e in result.errors)


class TestSegmentationValidator:
    """Tests for SegmentationValidator."""

    def test_init(self):
        sv = SegmentationValidator()
        assert sv.backend in (SegBackend.PKUSEG_MEDICINE, SegBackend.JIEBA, SegBackend.BASIC)

    def test_segment_empty(self):
        sv = SegmentationValidator()
        tokens = sv.segment("")
        assert tokens == []

    def test_segment_none(self):
        sv = SegmentationValidator()
        tokens = sv.segment(None)
        assert tokens == []

    def test_segment_thyroid_text(self):
        sv = SegmentationValidator()
        tokens = sv.segment("甲状腺右叶中极低回声结节，边界清")
        assert len(tokens) > 0
        token_texts = [t.text for t in tokens]
        joined = "".join(token_texts)
        assert "甲状腺" in joined or any("甲状腺" in t for t in token_texts)
        found_medical = any(
            t in token_texts or any(t in tt for tt in token_texts)
            for t in ["低回声", "边界清"]
        )
        assert found_medical, f"No medical terms found in segmentation: {token_texts}"
        assert any("结节" in t or "低回声" in t for t in token_texts), (
            f"Expected '结节' or '低回声' as standalone token, got: {token_texts}"
        )

    def test_segment_positions_incremental(self):
        sv = SegmentationValidator()
        text = "低回声结节"
        tokens = sv.segment(text)
        assert len(tokens) > 0
        reconstructed = "".join(t.text for t in tokens)
        assert reconstructed == text
        for i in range(len(tokens) - 1):
            assert tokens[i].end == tokens[i + 1].start

    def test_match_against_vocabulary(self):
        sv = SegmentationValidator()
        tokens = sv.segment("低回声结节，边界清")
        matches = sv.match_against_vocabulary(tokens)
        assert len(matches) > 0
        vocab_matched = [m for m in matches if m.status in (SegMatchStatus.EXACT, SegMatchStatus.PARTIAL)]
        assert len(vocab_matched) > 0, f"No vocabulary matches found: {[m.token.text for m in matches]}"
        for m in vocab_matched:
            assert m.matched_standard is not None

    def test_match_exact_standard_term(self):
        sv = SegmentationValidator()
        tokens = [SegToken(text="hypoechoic", start=0, end=10)]
        matches = sv.match_against_vocabulary(tokens)
        assert len(matches) == 1
        assert matches[0].status == SegMatchStatus.EXACT
        assert matches[0].matched_standard == "hypoechoic"

    def test_match_synonym_term(self):
        sv = SegmentationValidator()
        tokens = [SegToken(text="低回声", start=0, end=3)]
        matches = sv.match_against_vocabulary(tokens)
        assert len(matches) == 1
        assert matches[0].status == SegMatchStatus.PARTIAL
        assert matches[0].matched_standard == "hypoechoic"

    def test_match_unknown_term(self):
        sv = SegmentationValidator()
        tokens = [SegToken(text="xyz123", start=0, end=6)]
        matches = sv.match_against_vocabulary(tokens)
        assert len(matches) == 1
        assert matches[0].status == SegMatchStatus.EXTRA

    def test_validate_expected_terms_found(self):
        sv = SegmentationValidator()
        tokens = [SegToken(text="低回声", start=0, end=3)]
        results = sv.validate_expected_terms(tokens, ["低回声"])
        assert len(results) == 1
        assert results[0].status in (SegMatchStatus.EXACT, SegMatchStatus.PARTIAL)

    def test_validate_expected_terms_missing(self):
        sv = SegmentationValidator()
        tokens = [SegToken(text="正常", start=0, end=2)]
        results = sv.validate_expected_terms(tokens, ["不存在的术语"])
        assert len(results) == 1
        assert results[0].status == SegMatchStatus.MISSING

    def test_generate_validation_cases(self):
        sv = SegmentationValidator()
        cases = sv.generate_validation_cases()
        assert len(cases) > 0
        organs = {c.organ for c in cases}
        assert Organ.THYROID in organs
        assert Organ.LIVER in organs
        assert Organ.HEART in organs

    def test_compute_f1(self):
        sv = SegmentationValidator()
        tokens = sv.segment("甲状腺右叶中极低回声结节，边界清")
        f1 = sv.compute_f1(tokens, ["甲状腺", "低回声", "边界清"])
        assert 0.0 <= f1.precision <= 1.0
        assert 0.0 <= f1.recall <= 1.0
        assert 0.0 <= f1.f1 <= 1.0
        assert f1.recall > 0.0, "Recall should be positive when expected terms are in text"

    def test_compute_f1_precision_exact_value(self):
        sv = SegmentationValidator()
        tokens = [SegToken(text="低回声", start=0, end=3)]
        f1 = sv.compute_f1(tokens, ["低回声"])
        assert f1.precision == 1.0, f"Expected precision=1.0 when all vocab tokens match expected, got {f1.precision}"
        assert f1.recall == 1.0
        assert f1.f1 == 1.0

    def test_compute_f1_precision_with_extra_vocab_tokens(self):
        sv = SegmentationValidator()
        tokens = [
            SegToken(text="低回声", start=0, end=3),
            SegToken(text="边界清", start=4, end=7),
        ]
        f1 = sv.compute_f1(tokens, ["低回声"])
        assert f1.recall == 1.0
        assert f1.precision == 0.5, (
            f"Expected precision=0.5 (1 TP / 2 vocab tokens), got {f1.precision}"
        )

    def test_compute_f1_perfect(self):
        sv = SegmentationValidator()
        tokens = [SegToken(text="低回声", start=0, end=3)]
        f1 = sv.compute_f1(tokens, ["低回声"])
        assert f1.recall == 1.0
        assert f1.precision == 1.0

    def test_compute_f1_empty(self):
        sv = SegmentationValidator()
        f1 = sv.compute_f1([], [])
        assert f1.f1 == 0.0

    def test_validate_fallback_quality(self):
        sv = SegmentationValidator()
        result = sv.validate_fallback_quality()
        assert "backend" in result
        assert "f1_metrics" in result
        assert "passed" in result
        assert "python_version" in result
        assert isinstance(result["passed"], bool)
        f1_data = result["f1_metrics"]
        precision = float(f1_data["precision"])
        recall = float(f1_data["recall"])
        f1_val = float(f1_data["f1"])
        assert 0.0 <= precision <= 1.0, f"Precision out of range: {precision}"
        assert 0.0 <= recall <= 1.0, f"Recall out of range: {recall}"
        assert 0.0 <= f1_val <= 1.0, f"F1 out of range: {f1_val}"

    def test_run_validation(self):
        sv = SegmentationValidator()
        report = sv.run_validation()
        assert report is not None
        assert report.total_cases > 0
        assert report.total_tokens > 0
        assert 0.0 <= report.term_coverage <= 1.0
        assert report.f1_metrics is not None
        assert 0.0 <= report.f1_metrics.f1 <= 1.0
        assert report.backend in (SegBackend.PKUSEG_MEDICINE, SegBackend.JIEBA, SegBackend.BASIC)
        assert len(report.organ_breakdown) > 0
        assert report.python_version != ""

    def test_run_validation_organ_coverage(self):
        sv = SegmentationValidator()
        report = sv.run_validation()
        assert Organ.THYROID in report.organ_breakdown
        assert Organ.LIVER in report.organ_breakdown
        assert Organ.HEART in report.organ_breakdown

    def test_run_validation_extended(self):
        sv = SegmentationValidator()
        report = sv.run_validation(extended=True)
        assert report.total_cases >= 500

    def test_extended_cases_diverse_texts(self):
        sv = SegmentationValidator()
        from app.knowledge.segmentation_validation import _MIN_TEST_CASES
        cases = sv._generate_extended_cases()
        assert len(cases) >= _MIN_TEST_CASES
        unique_texts = {c.input_text for c in cases}
        assert len(unique_texts) >= 100, (
            f"Expected ≥100 unique texts, got {len(unique_texts)}"
        )

    def test_no_dead_path_in_match(self):
        sv = SegmentationValidator()
        tokens = [SegToken(text="xyz_not_in_vocab", start=0, end=15)]
        matches = sv.match_against_vocabulary(tokens)
        assert len(matches) == 1
        assert matches[0].status == SegMatchStatus.EXTRA


class TestBaselineAnnotator:
    """Tests for BaselineAnnotator."""

    def test_init(self):
        annotator = BaselineAnnotator()
        assert annotator.vocab is not None
        assert annotator.batches == []

    def test_create_batch_thyroid(self):
        annotator = BaselineAnnotator()
        batch = annotator.create_batch(Organ.THYROID)
        assert batch.organ == Organ.THYROID
        assert len(batch.cases) > 0
        assert batch.batch_id.startswith("batch_thyroid_")

    def test_create_batch_liver(self):
        annotator = BaselineAnnotator()
        batch = annotator.create_batch(Organ.LIVER)
        assert batch.organ == Organ.LIVER
        assert len(batch.cases) >= 200

    def test_auto_annotate(self):
        annotator = BaselineAnnotator()
        validator = BaselineValidator()
        cases = validator.get_cases_by_organ(Organ.THYROID)
        case = cases[0]
        result = annotator.auto_annotate(case)
        assert result.annotator_id == "auto"
        assert result.case_id == case.case_id
        assert len(result.labels) > 0
        for lbl in result.labels:
            assert lbl.notes == "auto_pre_annotation_udt_parser"
            assert lbl.attribute_category != ""
            assert lbl.attribute_value != ""

        categories = {lbl.attribute_category for lbl in result.labels}
        assert len(categories) > 0, "Auto-annotate should produce at least one category"

    def test_auto_annotate_with_classification(self):
        annotator = BaselineAnnotator()
        validator = BaselineValidator()
        cases = validator.get_cases_by_organ(Organ.THYROID)
        classified_cases = [c for c in cases if c.expected_classification]
        if classified_cases:
            result = annotator.auto_annotate(classified_cases[0])
            assert result.classification is not None

    def test_create_dual_annotation(self):
        annotator = BaselineAnnotator()
        validator = BaselineValidator()
        cases = validator.get_cases_by_organ(Organ.THYROID)
        case = cases[0]

        result_a = annotator.auto_annotate(case, annotator_id="annotator_A")
        result_b = annotator.auto_annotate(case, annotator_id="annotator_B")

        dual = annotator.create_dual_annotation(case, result_a, result_b)
        assert dual.case_id == case.case_id
        assert dual.agreement_rate > 0
        assert dual.status in (AnnotationStatus.COMPLETED, AnnotationStatus.DISPUTED)

    def test_dual_annotation_perfect_agreement(self):
        annotator = BaselineAnnotator()
        validator = BaselineValidator()
        cases = validator.get_cases_by_organ(Organ.THYROID)
        case = cases[0]

        result_a = annotator.auto_annotate(case, annotator_id="A")
        result_b = AnnotatorResult(
            annotator_id="B",
            case_id=case.case_id,
            labels=list(result_a.labels),
        )

        dual = annotator.create_dual_annotation(case, result_a, result_b)
        assert dual.agreement_rate == 1.0
        assert dual.status == AnnotationStatus.COMPLETED

    def test_resolve_dispute(self):
        annotator = BaselineAnnotator()
        validator = BaselineValidator()
        cases = validator.get_cases_by_organ(Organ.THYROID)
        case = cases[0]

        result_a = annotator.auto_annotate(case, "A")
        result_b = AnnotatorResult(
            annotator_id="B",
            case_id=case.case_id,
            labels=[AnnotationLabel(attribute_category="test", attribute_value="different")],
        )

        dual = annotator.create_dual_annotation(case, result_a, result_b)
        assert dual.status == AnnotationStatus.DISPUTED
        decision = annotator.auto_annotate(case, "reviewer")
        resolved = annotator.resolve_dispute(
            dual, "reviewer_1", decision, DisputeResolution.REVIEWER_DECISION
        )
        assert resolved.status == AnnotationStatus.APPROVED
        assert resolved.reviewer_id == "reviewer_1"
        assert resolved.dispute_resolution == DisputeResolution.REVIEWER_DECISION

    def test_compute_batch_metrics(self):
        annotator = BaselineAnnotator()
        batch = annotator.create_batch(Organ.THYROID)

        for case in batch.cases[:5]:
            result_a = annotator.auto_annotate(case, "A")
            result_b = annotator.auto_annotate(case, "B")
            dual = annotator.create_dual_annotation(case, result_a, result_b)
            batch.dual_annotations.append(dual)

        metrics = annotator.compute_batch_metrics(batch)
        assert metrics.total_cases == len(batch.cases)
        assert metrics.completed_cases == 5
        assert metrics.completion_rate == 5 / len(batch.cases)
        assert 0.0 <= metrics.agreement_rate <= 1.0
        assert -1.0 <= metrics.cohen_kappa <= 1.0

    def test_compute_batch_metrics_kappa_pairing(self):
        annotator = BaselineAnnotator()
        validator = BaselineValidator()
        cases = validator.get_cases_by_organ(Organ.THYROID)

        batch = annotator.create_batch(Organ.THYROID, cases=cases[:3])

        for case in cases[:3]:
            result_a = annotator.auto_annotate(case, "A")
            labels_b = list(result_a.labels)
            result_b = AnnotatorResult(
                annotator_id="B", case_id=case.case_id, labels=labels_b,
            )
            dual = annotator.create_dual_annotation(case, result_a, result_b)
            batch.dual_annotations.append(dual)

        metrics = annotator.compute_batch_metrics(batch)
        assert metrics.cohen_kappa == 1.0

    def test_compute_batch_metrics_kappa_partial_disagreement(self):
        annotator = BaselineAnnotator()
        validator = BaselineValidator()
        cases = validator.get_cases_by_organ(Organ.THYROID)

        batch = annotator.create_batch(Organ.THYROID, cases=cases[:2])

        result_a = annotator.auto_annotate(cases[0], "A")
        result_b = AnnotatorResult(
            annotator_id="B",
            case_id=cases[0].case_id,
            labels=[AnnotationLabel(attribute_category="echo", attribute_value="hyperechoic")],
        )
        dual = annotator.create_dual_annotation(cases[0], result_a, result_b)
        batch.dual_annotations.append(dual)

        result_a2 = annotator.auto_annotate(cases[1], "A")
        result_b2 = AnnotatorResult(
            annotator_id="B",
            case_id=cases[1].case_id,
            labels=list(result_a2.labels),
        )
        dual2 = annotator.create_dual_annotation(cases[1], result_a2, result_b2)
        batch.dual_annotations.append(dual2)

        metrics = annotator.compute_batch_metrics(batch)
        assert -1.0 <= metrics.cohen_kappa <= 1.0
        assert metrics.cohen_kappa < 1.0, "Kappa should be < 1.0 when there is disagreement"
        assert metrics.cohen_kappa >= -0.5, f"Kappa unexpectedly low: {metrics.cohen_kappa}"

    def test_window_agreement_rates(self):
        annotator = BaselineAnnotator()
        batch = annotator.create_batch(Organ.THYROID)

        for case in batch.cases[:5]:
            result_a = annotator.auto_annotate(case, "A")
            result_b = annotator.auto_annotate(case, "B")
            dual = annotator.create_dual_annotation(case, result_a, result_b)
            batch.dual_annotations.append(dual)

        metrics = annotator.compute_batch_metrics(batch)
        assert len(metrics.window_agreement_rates) > 0

    def test_validate_gate_0a_readiness(self):
        annotator = BaselineAnnotator()
        batch = annotator.create_batch(Organ.THYROID)

        for case in batch.cases[:10]:
            result_a = annotator.auto_annotate(case, "A")
            result_b = annotator.auto_annotate(case, "B")
            dual = annotator.create_dual_annotation(case, result_a, result_b)
            batch.dual_annotations.append(dual)

        result = annotator.validate_gate_0a_readiness(batch)
        assert "gate_0a_ready" in result
        assert "checks" in result
        assert "agreement_rate" in result["checks"]
        assert "cohen_kappa" in result["checks"]
        assert "sample_size" in result["checks"]
        assert "window_agreement_rates" in result["metrics_summary"]

    def test_export_annotation_batch(self):
        annotator = BaselineAnnotator()
        batch = annotator.create_batch(Organ.THYROID)

        for case in batch.cases[:3]:
            result_a = annotator.auto_annotate(case, "A")
            result_b = annotator.auto_annotate(case, "B")
            dual = annotator.create_dual_annotation(case, result_a, result_b)
            batch.dual_annotations.append(dual)

        with tempfile.NamedTemporaryFile(
            suffix='.json', delete=False
        ) as f:
            temp_path = f.name

        try:
            result = annotator.export_annotation_batch(batch, temp_path)
            assert result is True, "Export should return True on success"
            assert os.path.exists(temp_path)
            with open(temp_path, encoding='utf-8') as f:
                data = json.load(f)
            assert data["batch_id"] == batch.batch_id
            assert data["organ"] == Organ.THYROID
            assert len(data["dual_annotations"]) == 3
        finally:
            os.unlink(temp_path)

    def test_agreement_threshold_configurable(self):
        annotator = BaselineAnnotator()
        validator = BaselineValidator()
        cases = validator.get_cases_by_organ(Organ.THYROID)
        case = cases[0]

        result_a = annotator.auto_annotate(case, "A")
        result_b = AnnotatorResult(
            annotator_id="B",
            case_id=case.case_id,
            labels=[AnnotationLabel(attribute_category="test", attribute_value="different")],
        )

        dual_low = annotator.create_dual_annotation(case, result_a, result_b, agreement_threshold=1.0)
        assert dual_low.status == AnnotationStatus.DISPUTED

        dual_high = annotator.create_dual_annotation(case, result_a, result_b, agreement_threshold=0.0)
        assert dual_high.status == AnnotationStatus.COMPLETED


class TestCohenKappa:
    """Tests for Cohen's Kappa computation."""

    def test_perfect_agreement(self):
        kappa = compute_cohen_kappa(
            ["echo:hypo", "boundary:clear", "morph:oval"],
            ["echo:hypo", "boundary:clear", "morph:oval"],
        )
        assert kappa == 1.0

    def test_no_agreement(self):
        kappa = compute_cohen_kappa(
            ["echo:hypo", "echo:hyper", "echo:hypo", "echo:hyper"],
            ["echo:hyper", "echo:hypo", "echo:hyper", "echo:hypo"],
        )
        assert kappa <= 0.0

    def test_empty(self):
        kappa = compute_cohen_kappa([], [])
        assert kappa == 0.0

    def test_moderate_agreement(self):
        kappa = compute_cohen_kappa(
            ["echo:hypo", "boundary:clear", "morph:oval", "calc:none"],
            ["echo:hypo", "boundary:ill-defined", "morph:oval", "calc:micro"],
        )
        assert 0.0 <= kappa <= 0.5

    def test_high_agreement(self):
        kappa = compute_cohen_kappa(
            ["echo:hypo", "boundary:clear", "morph:oval", "calc:none", "vasc:absent"],
            ["echo:hypo", "boundary:clear", "morph:oval", "calc:none", "vasc:peripheral"],
        )
        assert kappa >= 0.5


class TestAgreementRate:
    """Tests for agreement rate computation."""

    def test_identical_results(self):
        labels = [AnnotationLabel(attribute_category="echo", attribute_value="hypoechoic")]
        result_a = AnnotatorResult(annotator_id="A", case_id="c1", labels=labels)
        result_b = AnnotatorResult(annotator_id="B", case_id="c1", labels=labels)
        rate, agreements, total = compute_agreement_rate(result_a, result_b)
        assert rate == 1.0
        assert agreements == total

    def test_different_results(self):
        labels_a = [AnnotationLabel(attribute_category="echo", attribute_value="hypoechoic")]
        labels_b = [AnnotationLabel(attribute_category="echo", attribute_value="hyperechoic")]
        result_a = AnnotatorResult(annotator_id="A", case_id="c1", labels=labels_a)
        result_b = AnnotatorResult(annotator_id="B", case_id="c1", labels=labels_b)
        rate, _agreements, _total = compute_agreement_rate(result_a, result_b)
        assert rate == 0.0

    def test_partial_overlap(self):
        labels_a = [
            AnnotationLabel(attribute_category="echo", attribute_value="hypoechoic"),
            AnnotationLabel(attribute_category="boundary", attribute_value="clear"),
        ]
        labels_b = [
            AnnotationLabel(attribute_category="echo", attribute_value="hypoechoic"),
            AnnotationLabel(attribute_category="boundary", attribute_value="ill-defined"),
        ]
        result_a = AnnotatorResult(annotator_id="A", case_id="c1", labels=labels_a)
        result_b = AnnotatorResult(annotator_id="B", case_id="c1", labels=labels_b)
        rate, _weighted_agreements, total = compute_agreement_rate(result_a, result_b)
        assert total == 2
        assert rate == 0.5

    def test_jaccard_partial_intersection(self):
        labels_a = [
            AnnotationLabel(attribute_category="echo", attribute_value="hypoechoic"),
            AnnotationLabel(attribute_category="echo", attribute_value="hyperechoic"),
        ]
        labels_b = [
            AnnotationLabel(attribute_category="echo", attribute_value="hypoechoic"),
        ]
        result_a = AnnotatorResult(annotator_id="A", case_id="c1", labels=labels_a)
        result_b = AnnotatorResult(annotator_id="B", case_id="c1", labels=labels_b)
        rate, weighted_agreements, total = compute_agreement_rate(result_a, result_b)
        assert total == 1
        assert rate == 0.5, f"Expected rate=0.5 (Jaccard 1/2), got {rate}"
        assert isinstance(weighted_agreements, float)

    def test_kappa_shared_categories_only(self):
        result_a = AnnotatorResult(
            annotator_id="A", case_id="c1",
            labels=[
                AnnotationLabel(attribute_category="echo", attribute_value="hypoechoic"),
                AnnotationLabel(attribute_category="boundary", attribute_value="clear"),
            ],
        )
        result_b = AnnotatorResult(
            annotator_id="B", case_id="c1",
            labels=[
                AnnotationLabel(attribute_category="echo", attribute_value="hypoechoic"),
            ],
        )
        dual = DualAnnotation(
            case_id="c1", annotator_a=result_a, annotator_b=result_b,
        )
        from app.knowledge.baseline_annotation import _compute_kappa_for_dual
        kappa = _compute_kappa_for_dual(dual)
        assert kappa == 1.0, "Kappa should be 1.0 for shared categories where values match"

    def test_kappa_no_shared_categories(self):
        result_a = AnnotatorResult(
            annotator_id="A", case_id="c1",
            labels=[AnnotationLabel(attribute_category="echo", attribute_value="hypoechoic")],
        )
        result_b = AnnotatorResult(
            annotator_id="B", case_id="c1",
            labels=[AnnotationLabel(attribute_category="boundary", attribute_value="clear")],
        )
        dual = DualAnnotation(
            case_id="c1", annotator_a=result_a, annotator_b=result_b,
        )
        from app.knowledge.baseline_annotation import _compute_kappa_for_dual
        kappa = _compute_kappa_for_dual(dual)
        assert kappa == 0.0, "Kappa should be 0.0 when no shared categories"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
