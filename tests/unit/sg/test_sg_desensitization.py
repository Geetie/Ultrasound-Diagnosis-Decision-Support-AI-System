import pytest
from unittest.mock import patch

from safety_gateway.desensitization import (
    SafetyGatewayDesensitizationEngine,
    DesensitizationRule,
    sg_desensitization_engine,
)
from safety_gateway.sg_config import sg_settings as settings

pytestmark = [pytest.mark.track_sg, pytest.mark.track_sec, pytest.mark.sec_domain_sg]


class TestDesensitizationEngine:

    def setup_method(self):
        self.engine = SafetyGatewayDesensitizationEngine()

    def test_id_card_desensitize(self):
        text = "身份证号：110101199001011234"
        result = self.engine.desensitize(text)
        assert "110101199001011234" not in result
        assert "[REDACTED:ID_CARD]" in result

    def test_id_card_no_match(self):
        text = "这是一段普通文本"
        result = self.engine.desensitize(text)
        assert result == text

    def test_phone_desensitize(self):
        text = "联系电话：13812345678"
        result = self.engine.desensitize(text)
        assert "13812345678" not in result
        assert "[REDACTED:PHONE]" in result

    def test_phone_no_match(self):
        text = "数字1234567不够长"
        result = self.engine.desensitize(text)
        assert result == text

    def test_patient_name_desensitize(self):
        text = "张主任患者前来就诊"
        result = self.engine.desensitize(text)
        assert "张主任" not in result

    def test_birth_date_desensitize(self):
        text = "出生日期：1990年01月01日"
        result = self.engine.desensitize(text)
        assert "1990" not in result
        assert "[REDACTED:BIRTH_DATE]" in result

    def test_address_desensitize(self):
        text = "住址：北京市朝阳区某某路123号"
        result = self.engine.desensitize(text)
        assert "北京市朝阳区某某路123号" not in result

    def test_medical_record_number_desensitize(self):
        text = "病历号：MR20230101001"
        result = self.engine.desensitize(text)
        assert "MR20230101001" not in result

    def test_dicom_uid_desensitize(self):
        text = "StudyInstanceUID：1.2.840.113619.2.55.3.1253934931.169.1234567890.123"
        result = self.engine.desensitize(text)
        assert "1.2.840.113619" not in result

    def test_email_desensitize(self):
        text = "邮箱：test@example.com"
        result = self.engine.desensitize(text)
        assert "test@example.com" not in result
        assert "[REDACTED:EMAIL]" in result

    def test_desensitize_none(self):
        result = self.engine.desensitize(None)
        assert result is None

    def test_desensitize_empty_string(self):
        result = self.engine.desensitize("")
        assert result == ""

    def test_desensitize_dict(self):
        data = {
            "name": "张三患者",
            "phone": "13812345678",
            "safe_field": "正常内容",
        }
        result = self.engine.desensitize_dict(data)
        assert "张三患者" not in result["name"]
        assert "13812345678" not in result["phone"]
        assert result["safe_field"] == "正常内容"

    def test_desensitize_dict_nested(self):
        data = {
            "patient": {
                "name": "张三患者",
                "phone": "13812345678",
            }
        }
        result = self.engine.desensitize_dict(data)
        assert "张三患者" not in result["patient"]["name"]

    def test_desensitize_dict_with_list(self):
        data = {
            "contacts": ["13812345678", "13987654321"],
        }
        result = self.engine.desensitize_dict(data)
        assert "13812345678" not in result["contacts"][0]

    def test_desensitize_dict_preserves_non_string(self):
        data = {"age": 30, "active": True, "score": 0.95}
        result = self.engine.desensitize_dict(data)
        assert result["age"] == 30
        assert result["active"] is True
        assert result["score"] == 0.95

    def test_check_sensitive_data_id_card(self):
        text = "身份证号：110101199001011234"
        detected = self.engine.check_sensitive_data(text)
        assert "sg_id_card" in detected

    def test_check_sensitive_data_phone(self):
        text = "电话：13812345678"
        detected = self.engine.check_sensitive_data(text)
        assert "sg_phone" in detected

    def test_check_sensitive_data_email(self):
        text = "邮箱：test@example.com"
        detected = self.engine.check_sensitive_data(text)
        assert "sg_email" in detected

    def test_check_sensitive_data_no_match(self):
        text = "这是一段普通文本"
        detected = self.engine.check_sensitive_data(text)
        assert len(detected) == 0

    def test_check_sensitive_data_multiple(self):
        text = "身份证号：110101199001011234，电话：13812345678"
        detected = self.engine.check_sensitive_data(text)
        assert "sg_id_card" in detected
        assert "sg_phone" in detected

    def test_get_rules(self):
        rules = self.engine.get_rules()
        assert len(rules) > 0
        assert all(isinstance(r, DesensitizationRule) for r in rules)

    def test_person_name_suffix_matching(self):
        text = "张医生建议手术"
        result = self.engine.desensitize(text)
        assert "张医生" not in result

    def test_person_name_dict_matching(self):
        text = "王主任查房记录"
        result = self.engine.desensitize(text)
        assert "王主任" not in result

    def test_desensitize_already_masked(self):
        text = "身份证号：[REDACTED:ID_CARD]"
        result = self.engine.desensitize(text)
        assert result == text

    def test_multiple_sensitive_in_one_string(self):
        text = "张三患者，电话13812345678，邮箱test@example.com"
        result = self.engine.desensitize(text)
        assert "张三患者" not in result
        assert "13812345678" not in result
        assert "test@example.com" not in result


class TestDesensitizationRuleModel:

    def test_rule_creation(self):
        rule = DesensitizationRule(
            name="test_rule",
            pattern=r"\d+",
            replacement="***",
            description="测试规则",
            priority=1,
        )
        assert rule.name == "test_rule"
        assert rule.enabled is True
        assert rule.priority == 1
        assert rule.is_patch is False

    def test_rule_patch_flag(self):
        rule = DesensitizationRule(
            name="patch_rule",
            pattern=r"\d+",
            replacement="***",
            is_patch=True,
        )
        assert rule.is_patch is True


class TestDesensitizationRuleValidation:

    def setup_method(self):
        self.engine = SafetyGatewayDesensitizationEngine()

    def test_validate_rules_empty_name(self):
        rules = [DesensitizationRule(name="", pattern=r"\d+", replacement="***")]
        errors = self.engine._validate_rules(rules)
        assert len(errors) > 0
        assert "empty name" in errors[0]

    def test_validate_rules_duplicate_names(self):
        rules = [
            DesensitizationRule(name="test", pattern=r"\d+", replacement="***"),
            DesensitizationRule(name="test", pattern=r"\w+", replacement="###"),
        ]
        errors = self.engine._validate_rules(rules)
        assert len(errors) > 0
        assert "Duplicate rule name" in errors[0]

    def test_validate_rules_invalid_pattern(self):
        rules = [DesensitizationRule(name="test", pattern=r"[\d+", replacement="***")]
        errors = self.engine._validate_rules(rules)
        assert len(errors) > 0
        assert "invalid regex" in errors[0]

    def test_validate_rules_invalid_priority(self):
        rules = [DesensitizationRule(name="test", pattern=r"\d+", replacement="***", priority=101)]
        errors = self.engine._validate_rules(rules)
        assert len(errors) > 0
        assert "invalid priority" in errors[0]

    def test_validate_rules_valid(self):
        rules = [DesensitizationRule(name="test", pattern=r"\d+", replacement="***", priority=10)]
        errors = self.engine._validate_rules(rules)
        assert len(errors) == 0


class TestDesensitizationRuleReloadWithRollback:

    def setup_method(self):
        self.engine = SafetyGatewayDesensitizationEngine()
        self.original_rules = self.engine._rules.copy()
        self.original_patterns = {k: v for k, v in self.engine._compiled_patterns.items()}

    def test_reload_rules_rollback_on_validation_error(self, tmp_path):
        bad_rules_file = tmp_path / "bad_rules.yaml"
        bad_rules_file.write_text("""
rules:
  - name: test_rule
    pattern: "[invalid"
    replacement: "***"
""")

        with patch.object(settings, 'desensitization_rules_safety_path', str(bad_rules_file)):
            result = self.engine.reload_rules()
            assert result is False

        assert len(self.engine._rules) == len(self.original_rules)

    def test_reload_rules_success(self, tmp_path):
        good_rules_file = tmp_path / "good_rules.yaml"
        good_rules_file.write_text("""
rules:
  - name: test_custom_rule
    pattern: "CUSTOM_[0-9]+"
    replacement: "CUSTOM_REPLACED"
    description: "Test custom rule"
    priority: 5
""")

        with patch.object(settings, 'desensitization_rules_safety_path', str(good_rules_file)):
            result = self.engine.reload_rules()
            assert result is True

        rule_names = {r.name for r in self.engine._rules}
        assert "test_custom_rule" in rule_names

    def test_reload_rules_preserves_on_error(self, tmp_path):
        good_rules_file = tmp_path / "good_rules.yaml"
        good_rules_file.write_text("""
rules:
  - name: valid_rule
    pattern: "VALID_[0-9]+"
    replacement: "***"
""")

        with patch.object(settings, 'desensitization_rules_safety_path', str(good_rules_file)):
            result = self.engine.reload_rules()
            assert result is True

        original_rule_count = len(self.engine._rules)

        bad_rules_file = tmp_path / "bad_rules.yaml"
        bad_rules_file.write_text("""
rules:
  - name: ""
    pattern: "test"
    replacement: "***"
""")

        with patch.object(settings, 'desensitization_rules_safety_path', str(bad_rules_file)):
            result = self.engine.reload_rules()
            assert result is False

        assert len(self.engine._rules) == original_rule_count


class TestEmailDesensitizationHeterogeneity:

    def setup_method(self):
        self.engine = SafetyGatewayDesensitizationEngine()

    def test_sg_email_rule_chinese_username(self):
        text = "邮箱：张三@hospital.com"
        detected = self.engine.check_sensitive_data(text)
        assert "sg_email" in detected

    def test_sg_email_rule_multiple_tld(self):
        text = "test@sub.domain.com.cn"
        detected = self.engine.check_sensitive_data(text)
        assert "sg_email" in detected

    def test_sg_email_rule_desensitize_chinese(self):
        text = "联系邮箱：李四@example.org"
        result = self.engine.desensitize(text)
        assert "李四@example.org" not in result
        assert "[REDACTED:EMAIL]" in result

    def test_sg_email_rule_regular_email(self):
        text = "user.name@company.com"
        detected = self.engine.check_sensitive_data(text)
        assert "sg_email" in detected


class TestTypedDesensitizationFormat:

    def test_phone_replacement_format(self):
        engine = SafetyGatewayDesensitizationEngine()
        text = "联系电话：13912345678"
        result = engine.desensitize(text)
        assert "[REDACTED:PHONE]" in result

    def test_id_card_replacement_format(self):
        engine = SafetyGatewayDesensitizationEngine()
        text = "身份证号：110101199001011234"
        result = engine.desensitize(text)
        assert "[REDACTED:ID_CARD]" in result

    def test_email_replacement_format(self):
        engine = SafetyGatewayDesensitizationEngine()
        text = "邮箱：test@example.com"
        result = engine.desensitize(text)
        assert "[REDACTED:EMAIL]" in result


class TestRuleLoadingFailureDegradation:

    def test_default_rules_used_when_yaml_missing(self):
        engine = SafetyGatewayDesensitizationEngine()
        with patch.object(engine, '_get_default_rules') as mock_default:
            mock_rules = [DesensitizationRule(
                name="sg_phone",
                pattern=r"(?<!\d)(1[3-9]\d{9})(?!\d)",
                replacement="[REDACTED:PHONE]",
                description="手机号脱敏",
                priority=2,
            )]
            mock_default.return_value = mock_rules
            engine._rules = mock_rules
            engine._compile_patterns()
            result = engine.desensitize("电话：13912345678")
            assert "[REDACTED:PHONE]" in result

    def test_malformed_yaml_falls_back_to_default(self):
        engine = SafetyGatewayDesensitizationEngine()
        with patch('builtins.open', side_effect=FileNotFoundError):
            rules = engine._get_default_rules()
            assert len(rules) > 0


class TestDesensitizationConcurrency:

    def test_concurrent_desensitize(self):
        import concurrent.futures

        engine = SafetyGatewayDesensitizationEngine()
        text = "身份证号：110101199001011234，电话：13912345678"

        def desensitize_task():
            return engine.desensitize(text)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(desensitize_task) for _ in range(50)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        for result in results:
            assert "110101199001011234" not in result
            assert "13912345678" not in result

    def test_concurrent_check_sensitive_data(self):
        import concurrent.futures

        engine = SafetyGatewayDesensitizationEngine()
        text = "身份证号：110101199001011234"

        def check_task():
            return engine.check_sensitive_data(text)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(check_task) for _ in range(50)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        for result in results:
            assert "sg_id_card" in result
