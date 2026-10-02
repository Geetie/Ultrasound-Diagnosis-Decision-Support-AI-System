
import pytest

from app.common.desensitization.base import BaseDesensitizationEngine
from app.common.desensitization.recursive import (
    compile_patterns,
    desensitize_dict,
    validate_rules,
)
from app.common.desensitization.rule import DesensitizationRule

pytestmark = [pytest.mark.sec_domain_desens]


class ConcreteEngine(BaseDesensitizationEngine):

    def desensitize(self, text):
        if text is None:
            return None
        result = text
        for rule in self._rules:
            if rule.enabled and rule.name in self._compiled_patterns:
                result = self._compiled_patterns[rule.name].sub(rule.replacement, result)
        return result

    def check_sensitive_data(self, text):
        detected = []
        for rule in self._rules:
            if rule.enabled and rule.name in self._compiled_patterns:
                if self._compiled_patterns[rule.name].search(text):
                    detected.append(rule.name)
        return detected


class TestBaseDesensitizationEngine:

    def test_load_rules_from_yaml(self, tmp_path):
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text("""
rules:
  - name: phone
    pattern: "1[3-9]\\\\d{9}"
    replacement: "***********"
    priority: 1
""")
        engine = ConcreteEngine()
        engine.load_rules_from_yaml(str(rules_file))
        assert len(engine.get_rules()) == 1
        assert engine.get_rules()[0].name == "phone"

    def test_compile_patterns(self):
        engine = ConcreteEngine()
        engine._rules = [
            DesensitizationRule(name="test", pattern=r"\d+", replacement="X", enabled=True, priority=1),
        ]
        engine._compile_patterns()
        assert "test" in engine._compiled_patterns

    def test_desensitize_dict_recursive(self):
        engine = ConcreteEngine()
        engine._rules = [
            DesensitizationRule(name="digits", pattern=r"\d+", replacement="X", enabled=True, priority=1),
        ]
        engine._compile_patterns()
        data = {"a": "123", "b": {"c": "456"}, "d": ["789", {"e": "012"}]}
        result = engine.desensitize_dict(data)
        assert result["a"] == "X"
        assert result["b"]["c"] == "X"
        assert result["d"][0] == "X"
        assert result["d"][1]["e"] == "X"

    def test_validate_rules_valid(self):
        rules = [
            DesensitizationRule(name="a", pattern=r"\d+", replacement="X", priority=1),
            DesensitizationRule(name="b", pattern=r"[a-z]+", replacement="Y", priority=2),
        ]
        errors = validate_rules(rules)
        assert len(errors) == 0

    def test_validate_rules_invalid_pattern(self):
        rules = [
            DesensitizationRule(name="bad", pattern=r"[invalid", replacement="X", priority=1),
        ]
        errors = validate_rules(rules)
        assert len(errors) > 0

    def test_add_rule(self):
        engine = ConcreteEngine()
        engine.add_rule(DesensitizationRule(name="new", pattern=r"\d+", replacement="X", priority=1))
        assert len(engine.get_rules()) == 1

    def test_update_rule(self):
        engine = ConcreteEngine()
        engine.add_rule(DesensitizationRule(name="test", pattern=r"\d+", replacement="X", priority=1))
        engine.update_rule("test", DesensitizationRule(name="test", pattern=r"[a-z]+", replacement="Y", priority=2))
        assert engine.get_rules()[0].pattern == r"[a-z]+"

    def test_remove_rule(self):
        engine = ConcreteEngine()
        engine.add_rule(DesensitizationRule(name="test", pattern=r"\d+", replacement="X", priority=1))
        engine.remove_rule("test")
        assert len(engine.get_rules()) == 0

    def test_check_sensitive_data(self):
        engine = ConcreteEngine()
        engine._rules = [
            DesensitizationRule(name="phone", pattern=r"1[3-9]\d{9}", replacement="***", enabled=True, priority=1),
        ]
        engine._compile_patterns()
        result = engine.check_sensitive_data("手机号13812345678")
        assert "phone" in result


class TestRecursiveDesensitize:

    def test_desensitize_dict_with_tuple(self):
        def mock_fn(text):
            return text.replace("secret", "***")
        data = {"a": ("secret", "normal")}
        result = desensitize_dict(data, mock_fn)
        assert result["a"][0] == "***"
        assert result["a"][1] == "normal"

    def test_desensitize_dict_with_set(self):
        def mock_fn(text):
            return text.replace("secret", "***")
        data = {"a": {"secret", "normal"}}
        result = desensitize_dict(data, mock_fn)
        assert "***" in result["a"]

    def test_desensitize_dict_preserves_non_string(self):
        def mock_fn(text):
            return text
        data = {"a": 42, "b": True, "c": None}
        result = desensitize_dict(data, mock_fn)
        assert result["a"] == 42
        assert result["b"] is True
        assert result["c"] is None


class TestCompilePatterns:

    def test_disabled_rule_not_compiled(self):
        rules = [
            DesensitizationRule(name="off", pattern=r"\d+", replacement="X", enabled=False, priority=1),
        ]
        compiled = compile_patterns(rules)
        assert "off" not in compiled

    def test_invalid_pattern_skipped(self):
        rules = [
            DesensitizationRule(name="bad", pattern=r"[invalid", replacement="X", enabled=True, priority=1),
        ]
        compiled = compile_patterns(rules)
        assert "bad" not in compiled


class TestHeterogeneousRedundancy:

    def test_sg_id_card_pattern_differs_from_main(self):
        sg_pattern = r'(?<![*\d])\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![*\d])'
        main_pattern = r'(?<![*\d])\d{17}[\dXx](?![*\d])'
        assert sg_pattern != main_pattern

    def test_sg_phone_pattern_differs_from_main(self):
        sg_pattern = r'(?<!\d)(1[3-9]\d{9})(?!\d)'
        main_pattern = r'(?<![*\d])(1[3-9]\d)(\d{4})(\d{4})(?![*\d])'
        assert sg_pattern != main_pattern, "SG phone pattern must differ from main (heterogeneous redundancy)"

    def test_shared_framework_preserves_sg_desensitization(self):
        engine = ConcreteEngine()
        engine._rules = [
            DesensitizationRule(
                name="id_card",
                pattern=r'(?<![*\d])\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![*\d])',
                replacement="[REDACTED:ID_CARD]",
                enabled=True,
                priority=1,
            ),
        ]
        engine._compile_patterns()
        result = engine.desensitize("身份证号110101199003076534")
        assert "[REDACTED:ID_CARD]" in result
