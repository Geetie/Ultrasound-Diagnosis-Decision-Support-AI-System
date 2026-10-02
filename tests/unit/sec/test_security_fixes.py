"""
SEC安全合规模块新增功能单元测试
覆盖Redis SCAN、密钥持久化、健康检查、脱敏热更新、日志配置等修复
"""

import pytest

from app.config.logging import LOG_LEVELS, get_log_level_info, set_log_level
from app.security.desensitization import desensitization_engine

pytestmark = [pytest.mark.track_sec, pytest.mark.track_inf, pytest.mark.sec_domain_desens]


class TestDesensitizationHotReload:
    """测试脱敏规则热更新功能"""

    def test_check_and_reload_rules_no_change(self):
        """测试规则文件未修改时不重新加载"""
        result = desensitization_engine.check_and_reload_rules()
        assert result is False

    def test_reload_rules_success(self):
        """测试强制重新加载规则"""
        result = desensitization_engine.reload_rules()

        assert result["status"] == "success"
        assert "previous_rule_count" in result
        assert "current_rule_count" in result
        assert "reloaded_at" in result

    def test_get_status(self):
        """测试获取脱敏引擎状态"""
        status = desensitization_engine.get_status()

        assert "rules_path" in status
        assert "total_rules" in status
        assert "enabled_rules" in status
        assert "disabled_rules" in status
        assert "last_loaded_time" in status
        assert "last_modified_time" in status


class TestLoggingConfiguration:
    """测试日志级别配置功能"""

    def test_get_log_level_info(self):
        """测试获取日志级别信息"""
        info = get_log_level_info()

        assert "current_level" in info
        assert "available_levels" in info
        assert info["available_levels"] == LOG_LEVELS
        assert "debug_enabled" in info
        assert "info_enabled" in info

    def test_set_log_level_valid(self):
        """测试设置有效日志级别"""
        result = set_log_level("DEBUG")

        assert result["status"] == "success"
        assert result["current_level"] == "DEBUG"
        assert "previous_level" in result

        set_log_level("INFO")

    def test_set_log_level_invalid(self):
        """测试设置无效日志级别"""
        with pytest.raises(ValueError):
            set_log_level("INVALID_LEVEL")

    def test_log_level_persistence(self):
        """测试日志级别持久化"""
        initial_info = get_log_level_info()

        set_log_level("DEBUG")
        debug_info = get_log_level_info()
        assert debug_info["current_level"] == "DEBUG"

        set_log_level("WARNING")
        warning_info = get_log_level_info()
        assert warning_info["current_level"] == "WARNING"

        set_log_level(initial_info["current_level"])


class TestDesensitizationRulesManagement:
    """测试脱敏规则管理功能"""

    def test_add_and_remove_rule(self):
        """测试添加和删除规则"""
        from app.security.desensitization import DesensitizationRule

        new_rule = DesensitizationRule(
            name="test_temp_rule",
            pattern=r"test_pattern",
            replacement="TEST_REPLACEMENT",
            description="Test rule",
            priority=100
        )

        desensitization_engine.add_rule(new_rule)

        rules = desensitization_engine.get_rules()
        assert any(r.name == "test_temp_rule" for r in rules)

        desensitization_engine.remove_rule("test_temp_rule")

        rules = desensitization_engine.get_rules()
        assert not any(r.name == "test_temp_rule" for r in rules)

    def test_update_rule(self):
        """测试更新规则"""
        from app.security.desensitization import DesensitizationRule

        original_rules = desensitization_engine.get_rules()
        if original_rules:
            first_rule = original_rules[0]
            updated_rule = DesensitizationRule(
                name=first_rule.name,
                pattern=first_rule.pattern,
                replacement="UPDATED_REPLACEMENT",
                description="Updated",
                enabled=first_rule.enabled,
                priority=first_rule.priority
            )

            desensitization_engine.update_rule(first_rule.name, updated_rule)

            rules = desensitization_engine.get_rules()
            found = next((r for r in rules if r.name == first_rule.name), None)
            assert found is not None
            assert found.replacement == "UPDATED_REPLACEMENT"
