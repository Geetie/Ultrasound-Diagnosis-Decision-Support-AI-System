"""04-3 Phase A 守卫：条件字段 + settings 开关 + 判读后写回 findings 留痕。

验收判据（04 号 §04-3 ⑤ / 用户拍板）：
1. 开关关（默认）→ _evaluate_grading_floor 返回 None → findings 不写键（逐字节不变）；
2. 开关开 + 空规则表 → 每例都有 reason ∈ {no_rule, ...}，且不改正文；
3. 注入条件匹配规则 → applied=True、from/to/rule_reference 可查，正文对象不被改动；
4. 空 condition 的规则被拒（invalid_rule）；条件不满足不抬（condition_unmet）。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.config.settings import settings
from app.knowledge import grading_floor as floor_module
from app.knowledge.grading_floor import (
    GradingFloorRule,
    apply_grading_floor,
    match_rule,
)
from app.services import diagnosis_pipeline as dp

_RULE = GradingFloorRule(
    organ="thyroid",
    min_grade="4",
    reason="测试规则",
    reference="测试用（非临床规则）",
    condition="pathology_proven",
)


class TestConditionField:
    def test_empty_condition_rule_is_invalid(self, monkeypatch):
        monkeypatch.setattr(
            floor_module,
            "GRADING_FLOOR_RULES",
            (
                GradingFloorRule(
                    organ="thyroid", min_grade="4", reason="r", reference="t", condition=""
                ),
            ),
        )
        out = apply_grading_floor(organ="thyroid", grading="2", enabled=True)
        assert out.applied is False
        assert out.reason == "invalid_rule"

    def test_condition_unmet_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(floor_module, "GRADING_FLOOR_RULES", (_RULE,))
        out = apply_grading_floor(
            organ="thyroid", grading="2", enabled=True, conditions=frozenset()
        )
        assert out.applied is False
        assert out.grading == "2"
        assert out.reason == "condition_unmet"

    def test_condition_met_raises_to_floor(self, monkeypatch):
        monkeypatch.setattr(floor_module, "GRADING_FLOOR_RULES", (_RULE,))
        out = apply_grading_floor(
            organ="thyroid",
            grading="2",
            enabled=True,
            conditions=frozenset({"pathology_proven"}),
        )
        assert out.applied is True
        assert out.grading == "4"
        assert out.original_grading == "2"

    def test_match_rule_none_for_other_organ(self, monkeypatch):
        monkeypatch.setattr(floor_module, "GRADING_FLOOR_RULES", (_RULE,))
        assert match_rule("liver", {"pathology_proven"}) is None

    def test_production_rule_table_stays_empty(self):
        """规则表保持空表：内容必须由医学专家给，找不到依据不填。"""
        assert floor_module.GRADING_FLOOR_RULES == ()


class TestSettingsSwitch:
    def test_default_off(self):
        """读**字段默认值**而非实例值 —— 实例会被部署 .env 覆盖（A.99 开启）。"""
        from app.config.settings import Settings

        assert Settings.model_fields["grading_floor_enabled"].default is False

    def test_enabled_none_reads_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "grading_floor_enabled", True)
        monkeypatch.setattr(floor_module, "GRADING_FLOOR_RULES", ())
        out = apply_grading_floor(organ="thyroid", grading="2")
        assert out.reason == "no_rule"  # 开了但空表：只留痕，不抬级

    def test_settings_off_disables_even_with_rules(self, monkeypatch):
        monkeypatch.setattr(settings, "grading_floor_enabled", False)
        monkeypatch.setattr(floor_module, "GRADING_FLOOR_RULES", (_RULE,))
        out = apply_grading_floor(
            organ="thyroid",
            grading="2",
            conditions=frozenset({"pathology_proven"}),
        )
        assert out.reason == "disabled"
        assert out.applied is False


class TestFindingsWriteBack:
    def _req(self) -> SimpleNamespace:
        return SimpleNamespace(organ="thyroid", request_id="r1")

    async def test_disabled_writes_nothing(self, monkeypatch):
        monkeypatch.setattr(settings, "grading_floor_enabled", False)
        assert dp._evaluate_grading_floor(self._req(), {"findings": "TR2"}) is None

    async def test_enabled_empty_rules_writes_no_rule_audit(self, monkeypatch):
        """判据 2：开关开 + 空规则表 → 每例都有 reason，不改正文。"""
        monkeypatch.setattr(settings, "grading_floor_enabled", True)
        monkeypatch.setattr(floor_module, "GRADING_FLOOR_RULES", ())
        payload = {"findings": "……"}
        audit = dp._evaluate_grading_floor(self._req(), payload)
        assert audit is not None
        assert audit["applied"] is False
        assert audit["reason"] == "no_rule"
        assert payload == {"findings": "……"}, "Phase A 不得改动结论载体"

    async def test_enabled_matching_rule_writes_raised_audit(self, monkeypatch):
        """判据 3：条件匹配 → applied=True、from/to/rule_reference 可查，正文不变。"""
        monkeypatch.setattr(settings, "grading_floor_enabled", True)
        monkeypatch.setattr(floor_module, "GRADING_FLOOR_RULES", (_RULE,))
        monkeypatch.setattr(
            dp, "_grading_conditions_for", lambda request: frozenset({"pathology_proven"})
        )
        monkeypatch.setattr(dp, "extract_findings_text", lambda x: "超声提示：TR2，建议复查")
        payload = {"findings": "超声提示：TR2，建议复查"}
        audit = dp._evaluate_grading_floor(self._req(), payload)
        assert audit["applied"] is True
        assert audit["to"] == "4"
        assert audit["rule_reference"] == "测试用（非临床规则）"
        assert audit["condition"] == "pathology_proven"
        assert payload == {"findings": "超声提示：TR2，建议复查"}, "结论正文必须字节级不变"

    async def test_evaluation_failure_leaves_trace_not_break(self, monkeypatch):
        monkeypatch.setattr(settings, "grading_floor_enabled", True)

        def _boom(*a, **kw):
            raise RuntimeError("floor exploded")

        monkeypatch.setattr(floor_module, "apply_grading_floor", _boom)
        audit = dp._evaluate_grading_floor(self._req(), {"findings": "x"})
        assert audit is not None
        assert audit["reason"] == "evaluation_failed"

    async def test_conditions_provider_is_empty_by_default(self):
        """Phase A 契约：生产路径恒返回空集 ⇒ 条件规则永不生效（宁可不抬）。"""
        assert dp._grading_conditions_for(self._req()) == frozenset()
