"""规则引擎抽象层（二期 §11）。

不变量：
1. 条件匹配语义与既有禁忌引擎**逐字一致**，尤其"缺数据兜底命中"这条安全兜底；
2. 作用域正确：global 规则对任何器官生效，器官规则只对本人；
3. 等价性验证必须真的能发现差异——否则它只是装饰（用故意走样的引擎反证）。
"""
import importlib.util
from pathlib import Path

from app.knowledge.rule_engine import (
    CONDITION_ALIASES,
    GLOBAL_SCOPE,
    Rule,
    RuleMatch,
    SimpleRuleEngine,
    get_rule_engine,
    match_condition,
    set_rule_engine,
    verify_equivalence,
)

SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "rule_engine_consistency.py"
)


def _load_script_module():
    spec = importlib.util.spec_from_file_location("rule_engine_consistency", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestConditionMatching:
    def test_alias_match(self):
        assert match_condition("pregnancy", {"patient_conditions": ["pregnant"]}) is True

    def test_non_matching_condition(self):
        assert (
            match_condition("pregnancy", {"patient_conditions": ["diabetes"]}) is False
        )

    def test_clinical_context_branch(self):
        facts = {"clinical_context": {"conditions": ["coagulopathy"]}}

        assert match_condition("coagulopathy", facts) is True
        assert match_condition("pregnancy", facts) is False

    def test_patient_conditions_takes_precedence(self):
        """两个字段同时存在时以 patient_conditions 为准（与既有实现一致）。"""
        facts = {
            "patient_conditions": ["diabetes"],
            "clinical_context": {"conditions": ["pregnancy"]},
        }

        assert match_condition("pregnancy", facts) is False

    def test_missing_data_falls_back_to_match(self):
        """安全兜底：没有条件数据时判命中，宁可多提示也不漏。"""
        assert match_condition("pregnancy", {}) is True

    def test_non_list_patient_conditions_falls_through(self):
        """类型不对时不能崩，继续走 clinical_context / 兜底分支。"""
        assert match_condition("pregnancy", {"patient_conditions": "pregnant"}) is True
        assert (
            match_condition(
                "pregnancy",
                {
                    "patient_conditions": "pregnant",
                    "clinical_context": {"conditions": ["pregnancy"]},
                },
            )
            is True
        )

    def test_aliases_cover_legacy_vocabulary(self):
        for condition, aliases in CONDITION_ALIASES.items():
            for alias in aliases:
                assert match_condition(condition, {"patient_conditions": [alias]}) is True


class TestSimpleRuleEngine:
    def _rules(self):
        return [
            Rule(rule_id="g1", condition="pregnancy", organ=GLOBAL_SCOPE),
            Rule(rule_id="t1", condition="pregnancy", organ="thyroid"),
            Rule(rule_id="l1", condition="pregnancy", organ="liver"),
        ]

    def test_global_rules_apply_to_every_organ(self):
        engine = SimpleRuleEngine(self._rules())

        matched = [match.rule_id for match in engine.evaluate({"patient_conditions": ["pregnant"]}, organ="liver")]

        assert matched == ["g1", "l1"]

    def test_organ_rules_are_scoped(self):
        engine = SimpleRuleEngine(self._rules())

        matched = [match.rule_id for match in engine.evaluate({"patient_conditions": ["pregnant"]}, organ="thyroid")]

        assert "l1" not in matched

    def test_no_organ_means_global_only(self):
        engine = SimpleRuleEngine(self._rules())

        matched = [match.rule_id for match in engine.evaluate({"patient_conditions": ["pregnant"]})]

        assert matched == ["g1"]

    def test_disabled_rule_is_skipped(self):
        engine = SimpleRuleEngine(
            [Rule(rule_id="g1", condition="pregnancy", enabled=False)]
        )

        assert engine.evaluate({"patient_conditions": ["pregnant"]}) == []

    def test_match_carries_description_and_severity(self):
        engine = SimpleRuleEngine(
            [
                Rule(
                    rule_id="g1",
                    condition="pregnancy",
                    severity="medium",
                    description="孕期慎用",
                )
            ]
        )

        match = engine.evaluate({"patient_conditions": ["pregnant"]})[0]

        assert isinstance(match, RuleMatch)
        assert match.severity == "medium"
        assert match.description == "孕期慎用"

    def test_reload_replaces_rules(self):
        engine = SimpleRuleEngine(self._rules())
        engine.load([Rule(rule_id="x", condition="pregnancy")])

        assert [rule.rule_id for rule in engine.rules()] == ["x"]


class TestAdapterEquivalence:
    def test_new_engine_matches_legacy_on_shared_corpus(self):
        from app.knowledge.rule_engine import ContraindicationRuleEngineAdapter
        from safety_gateway.contraindication import ContraindicationEngine

        rules = [
            Rule(rule_id="g1", condition="pregnancy", organ=GLOBAL_SCOPE),
            Rule(rule_id="g2", condition="coagulopathy", organ=GLOBAL_SCOPE),
            Rule(rule_id="t1", condition="pregnancy", organ="thyroid", enabled=False),
        ]
        inputs = [
            {"patient_conditions": ["pregnant"]},
            {"patient_conditions": ["coagulopathy"]},
            {"clinical_context": {"conditions": ["pregnancy"]}},
            {"note": "no data"},
        ]

        report = verify_equivalence(
            SimpleRuleEngine(),
            ContraindicationRuleEngineAdapter(ContraindicationEngine()),
            rules,
            inputs,
            organs=["thyroid", "liver"],
        )

        assert report.equivalent is True
        assert report.total_inputs == 4

    def test_verification_detects_divergence(self):
        """故意造一个语义走样的引擎：验证必须报不一致，否则等于没验证。"""
        from app.knowledge.rule_engine import ContraindicationRuleEngineAdapter
        from safety_gateway.contraindication import ContraindicationEngine

        class _NeverMatches(SimpleRuleEngine):
            def evaluate(self, facts, *, organ=None):
                return []

        report = verify_equivalence(
            _NeverMatches(),
            ContraindicationRuleEngineAdapter(ContraindicationEngine()),
            [Rule(rule_id="g1", condition="pregnancy")],
            [{"patient_conditions": ["pregnant"]}],
        )

        assert report.equivalent is False
        assert report.mismatches[0]["expected"] == ["g1"]
        assert report.mismatches[0]["actual"] == []

    def test_report_shape(self):
        report = verify_equivalence(
            SimpleRuleEngine(), SimpleRuleEngine(), [], [{}]
        )

        payload = report.as_dict()

        assert set(payload) == {
            "total_inputs",
            "matched_inputs",
            "equivalent",
            "mismatch_rate",
            "mismatch_count",
            "mismatches",
        }


class TestSingleton:
    def test_set_and_get(self):
        original = get_rule_engine()
        replacement = SimpleRuleEngine()
        try:
            set_rule_engine(replacement)
            assert get_rule_engine() is replacement
        finally:
            set_rule_engine(original)


class TestConsistencyScript:
    def test_corpus_meets_minimum_and_covers_scopes(self):
        module = _load_script_module()

        rules = module.build_rule_corpus()
        scopes = {rule.scope for rule in rules}

        assert len(rules) >= module.MIN_RULES
        assert GLOBAL_SCOPE in scopes
        assert len(scopes) > 1, "只有 global 规则时无法覆盖作用域过滤逻辑"
        assert any(not rule.enabled for rule in rules), "禁用规则分支未被覆盖"

    def test_rule_ids_unique(self):
        module = _load_script_module()

        ids = [rule.rule_id for rule in module.build_rule_corpus()]

        assert len(ids) == len(set(ids))

    def test_inputs_are_deterministic(self):
        module = _load_script_module()

        assert module.build_inputs(40, seed=7) == module.build_inputs(40, seed=7)
        assert module.build_inputs(40, seed=7) != module.build_inputs(40, seed=8)

    def test_inputs_cover_all_branches(self):
        module = _load_script_module()

        inputs = module.build_inputs(40)
        has_patient_only = any(
            "patient_conditions" in item and "clinical_context" not in item
            for item in inputs
        )
        has_context_only = any(
            "clinical_context" in item and "patient_conditions" not in item
            for item in inputs
        )
        has_both = any(
            "clinical_context" in item and "patient_conditions" in item
            for item in inputs
        )
        has_neither = any(
            "patient_conditions" not in item and "clinical_context" not in item
            for item in inputs
        )

        assert has_patient_only and has_context_only and has_both and has_neither

    def test_run_passes_at_scale(self):
        module = _load_script_module()

        assert module.run(rules_count_min=200, inputs_count=1000, seed=20260924) == 0

    def test_run_rejects_insufficient_scale(self):
        """规则/输入不足量时返回 2（验证不成立），而不是"通过"。"""
        module = _load_script_module()

        assert module.run(rules_count_min=100000, inputs_count=10, seed=1) == 2
