"""M8 报告模板库（按器官匹配 + 完整性校验）与 M3/M7 段落渲染单测。"""
from types import SimpleNamespace

import pytest

from app.services import report_service
from app.services.report_service import (
    ORGAN_TEMPLATES,
    TEMPLATE_DIR,
    build_report_content,
    check_completeness,
    load_template,
    render_report,
    select_template_name,
)

pytestmark = pytest.mark.track_api


def _result(**overrides):
    base = {
        "request_id": "req_1",
        "session_id": "sess_1",
        "organ": "thyroid",
        "findings": {"fusion_result": {"needs_review": False}},
        "recommendations": "[thyroid] TR4",
        "confidence": 0.72,
        "status": "completed",
        "clinical_text": "右叶低回声结节",
        "knowledge_ids": ["c1"],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _session():
    return SimpleNamespace(patient_id="pat_1", knowledge_base_version="kb_v1.0.0")


def _result_with_attributes(**overrides):
    """带 M3 测量 + M7 处置建议的结果（用于"报告完整"的用例）。"""
    findings = {
        "fusion_result": {"needs_review": False},
        "clinical_attributes": {
            "measurement": {"long_mm": 12.0, "short_mm": 8.0, "pixel_spacing_source": "dicom"},
            "clinical_actions": [{"action_type": "annual_followup"}],
        },
    }
    return _result(findings=findings, **overrides)


class TestTemplateSelection:
    def test_organ_templates_exist_on_disk(self):
        for organ, filename in ORGAN_TEMPLATES.items():
            assert (TEMPLATE_DIR / filename).exists(), f"{organ} 模板缺失：{filename}"

    @pytest.mark.parametrize("organ", ["thyroid", "liver", "cardiac"])
    def test_selects_organ_template(self, organ):
        assert select_template_name(organ=organ) == ORGAN_TEMPLATES[organ]

    def test_organ_lookup_case_insensitive(self):
        assert select_template_name(organ=" THYROID ") == ORGAN_TEMPLATES["thyroid"]

    def test_unknown_organ_falls_back_to_configured_template(self):
        assert select_template_name(organ="spleen") == report_service.settings.report_template_name

    def test_explicit_name_wins(self):
        assert select_template_name(organ="thyroid", name="default.md") == "default.md"

    def test_load_template_by_organ(self):
        template = load_template(organ="liver")
        assert "肝脏超声诊断报告" in template

    def test_missing_template_file_falls_back_to_builtin(self):
        template = load_template(name="no_such_template.md")
        assert "超声诊疗决策支持系统" in template


class TestCompleteness:
    def test_complete_report_passes(self):
        content = build_report_content(_result_with_attributes(), _session())
        result = check_completeness(content, organ="thyroid")
        assert result["passed"] is True
        assert result["missing"] == []
        assert result["template"] == ORGAN_TEMPLATES["thyroid"]

    def test_missing_measurement_flagged(self):
        """没有测量数据时甲状腺报告不完整——占位文本不能被当作已填写。"""
        content = build_report_content(_result(), _session())
        result = check_completeness(content, organ="thyroid")

        assert "measurement_summary" in result["missing"]
        assert result["passed"] is False

    def test_missing_actions_flagged(self):
        content = build_report_content(_result(), _session())
        assert "clinical_actions" in check_completeness(content, organ="thyroid")["missing"]

    def test_missing_common_field_flagged(self):
        result = check_completeness({"organ": "thyroid"}, organ="thyroid")
        assert "request_id" in result["missing"]
        assert "findings" in result["missing"]

    def test_empty_list_counts_as_missing(self):
        result = check_completeness({"request_id": "r", "organ": "thyroid"}, organ="thyroid")
        assert "findings" in result["missing"]

    def test_organ_from_content_when_not_passed(self):
        content = build_report_content(_result(organ="cardiac"), _session())
        assert check_completeness(content)["organ"] == "cardiac"

    def test_unknown_organ_checks_common_fields_only(self):
        content = build_report_content(_result(organ="spleen"), _session())
        result = check_completeness(content, organ="spleen")
        assert set(result["required"]) == set(report_service.REQUIRED_FIELDS_COMMON)


class TestContentSections:
    def test_measurement_summary_rendered_from_clinical_attributes(self):
        result = _result(
            findings={
                "fusion_result": {"needs_review": False},
                "clinical_attributes": {
                    "measurement": {
                        "long_mm": 12.0,
                        "short_mm": 8.0,
                        "aspect_ratio": 1.5,
                        "pixel_spacing_source": "dicom",
                    }
                },
            }
        )
        content = build_report_content(result, _session())
        assert "长径 12.0mm" in content["measurement_summary"]
        assert "纵横比 1.5" in content["measurement_summary"]
        assert "DICOM 像素间距" in content["measurement_summary"]

    def test_measurement_placeholder_when_absent(self):
        content = build_report_content(_result(), _session())
        assert content["measurement_summary"].startswith("（未提供测量数据")

    def test_actions_rendered_into_report(self):
        result = _result(
            findings={
                "fusion_result": {"needs_review": False},
                "clinical_attributes": {
                    "clinical_actions": [
                        {
                            "action_type": "fine_needle_aspiration",
                            "target_department": "甲状腺外科",
                            "urgency": "soon",
                            "applicable_level": "三甲",
                            "description": "建议行细针穿刺",
                        }
                    ]
                },
            }
        )
        content = build_report_content(result, _session())
        assert "fine_needle_aspiration" in content["clinical_actions"]
        assert "甲状腺外科" in content["clinical_actions"]
        assert "处置建议由规则表给出" in content["recommendation"]

    def test_recommendation_unchanged_without_actions(self):
        content = build_report_content(_result(), _session())
        assert content["recommendation"] == "建议结合临床与随访观察。"

    def test_review_required_recommendation_wins(self):
        result = _result(
            findings={
                "fusion_result": {"needs_review": True, "review_reason": "low_confidence"},
                "clinical_attributes": {
                    "clinical_actions": [{"action_type": "annual_followup"}]
                },
            }
        )
        content = build_report_content(result, _session())
        assert content["recommendation"] == "建议执业医师复核后再行签发。"


class TestPatientExplanationSection:
    """M10：患者解释段默认不入正式报告，医生显式开启才追加。"""

    def _content(self, explanation=None):
        findings = {
            "fusion_result": {},
            "clinical_attributes": {
                "measurement": {"long_mm": 12.0},
                "clinical_actions": [{"action_type": "annual_followup"}],
            },
        }
        if explanation is not None:
            findings["clinical_attributes"]["patient_explanation"] = explanation
        return build_report_content(_result(findings=findings), _session())

    def test_not_in_default_render(self):
        content = self._content(
            {"explanation": "结节多为良性", "reassurance": "不必过度担心"}
        )
        rendered = render_report(content)

        assert "患者解释" not in rendered
        assert "结节多为良性" not in rendered

    def test_appended_when_requested(self):
        content = self._content(
            {
                "explanation": "结节多为良性",
                "reassurance": "不必过度担心",
                "next_step": "6 个月后复查",
                "source_reference": "模型生成",
            }
        )
        rendered = render_report(content, include_patient_explanation=True)

        assert "患者解释" in rendered
        assert "非正式报告内容" in rendered
        assert "解释：结节多为良性" in rendered
        assert "下一步：6 个月后复查" in rendered

    def test_no_empty_section_without_explanation(self):
        content = self._content()
        rendered = render_report(content, include_patient_explanation=True)

        assert "患者解释" not in rendered

    def test_content_field_is_placeholder_when_absent(self):
        content = self._content()
        assert content["patient_explanation"].startswith("（本次未生成")

    def test_patient_explanation_not_required_for_completeness(self):
        """话术是可选内容，缺少它不应导致报告被判为不完整。"""
        result = check_completeness(self._content(), organ="thyroid")
        assert result["passed"] is True
        assert "patient_explanation" not in result["required"]


class TestRenderReport:
    def test_renders_organ_template(self):
        content = build_report_content(_result(organ="liver"), _session())
        rendered = render_report(content)

        assert "肝脏超声诊断报告" in rendered
        assert "{{" not in rendered  # 占位符必须全部替换
        assert content["request_id"] in rendered

    def test_renders_clinical_actions_section(self):
        content = build_report_content(
            _result(
                findings={
                    "fusion_result": {},
                    "clinical_attributes": {
                        "clinical_actions": [
                            {"action_type": "thyroid_function_test", "description": "查甲功"}
                        ]
                    },
                }
            ),
            _session(),
        )
        rendered = render_report(content)
        assert "处置建议" in rendered
        assert "thyroid_function_test" in rendered
