"""T-G1-DATA-02：模型版本 × 知识库版本兼容性强制校验。

覆盖三处接线：
1. ``parse_model_version`` 能解析带器官/骨干前缀的真实配置写法；
2. ``validate_version_compatibility`` 在启动校验链路中生效（不兼容即拒绝启动）；
3. 诊断结果契约字段 ``version_compatibility`` 由兼容矩阵真实计算得出。
"""
import inspect
from pathlib import Path

import pytest

from app.config import validator as validator_module
from app.config.settings import settings
from app.config.validator import (
    VERSION_COMPATIBLE,
    VERSION_INCOMPATIBLE,
    check_version_compatibility,
    validate_config,
    validate_version_compatibility,
)
from app.knowledge.version import is_compatible, parse_model_version


class TestParseModelVersion:
    def test_plain_versions(self):
        assert parse_model_version("v1.0") == (1, 0)
        assert parse_model_version("1.2.3") == (1, 2)
        assert parse_model_version("v2.0.0") == (2, 0)

    def test_prefixed_versions(self):
        """真实配置是 organ_v1.0.0 形式，直接按点号切分会解析失败。"""
        assert parse_model_version("thyroid_v1.0.0") == (1, 0)
        assert parse_model_version("liver_v2.1.3") == (2, 1)
        assert parse_model_version("resnet50_v2.0") == (2, 0)

    def test_invalid(self):
        assert parse_model_version("") is None
        assert parse_model_version("no_version_here") is None


class TestIsCompatibleWithRealConfig:
    def test_matrix_unchanged(self):
        assert is_compatible("v1.0", "kb_v1.2.3") is True
        assert is_compatible("v1.0", "kb_v2.0.0") is True
        assert is_compatible("v2.0", "kb_v2.0.0") is True
        assert is_compatible("v2.0", "kb_v1.0.0") is False

    def test_real_prefixed_versions_are_judged_compatible(self):
        """回归：宽容解析前，默认配置会被误判为不兼容并导致进程拒绝启动。"""
        assert is_compatible("thyroid_v1.0.0", "kb_v1.0.0") is True

    def test_unparseable_is_incompatible(self):
        assert is_compatible("", "kb_v1.0.0") is False
        assert is_compatible("v1.0", "not-a-kb-version") is False


class TestValidateVersionCompatibility:
    def test_compatible_pair_has_no_error(self):
        assert validate_version_compatibility("v1.0.0", "kb_v1.2.3") == []

    def test_incompatible_pair_reports_error(self):
        errors = validate_version_compatibility("v2.0.0", "kb_v1.0.0")

        assert len(errors) == 1
        assert "VERSION_COMPATIBILITY" in errors[0]
        assert "v2.0.0" in errors[0]
        assert "kb_v1.0.0" in errors[0]

    def test_upgrade_order_message_mentions_kb_first(self):
        """升级顺序是 KB 先于模型——错误信息必须把这条要求带出来。"""
        errors = validate_version_compatibility("v2.0.0", "kb_v1.0.0")

        assert "KB before model" in errors[0]

    def test_defaults_read_settings(self):
        """不传参时按当前配置判定（启动路径的行为）。"""
        errors = validate_version_compatibility()

        assert errors == validate_version_compatibility(
            settings.phase0a_mock_model_version, settings.phase0a_mock_kb_version
        )

    def test_default_config_does_not_block_startup(self):
        """默认配置必须能通过启动校验，否则 lifespan 直接抛错、服务起不来。"""
        assert validate_version_compatibility() == []
        assert "VERSION_COMPATIBILITY" not in " ".join(validate_config())


class TestCheckVersionCompatibility:
    def test_returns_contract_values(self):
        assert check_version_compatibility("v1.0.0", "kb_v1.0.0") == VERSION_COMPATIBLE
        assert check_version_compatibility("v2.0.0", "kb_v1.0.0") == VERSION_INCOMPATIBLE

    def test_value_matches_frontend_union_type(self):
        """契约取值必须落在前端 types/api.ts 的 'compatible' | 'incompatible' 内。"""
        assert {VERSION_COMPATIBLE, VERSION_INCOMPATIBLE} == {"compatible", "incompatible"}

    def test_missing_model_name_is_incompatible_not_crash(self):
        assert check_version_compatibility("", "") == VERSION_INCOMPATIBLE


class TestStartupHookWiring:
    def test_validate_config_includes_version_check(self):
        """启动校验必须真的调用版本校验（否则又回到"定义了没人调"）。"""
        assert "VERSION_COMPATIBILITY" in " ".join(
            validate_version_compatibility("v2.0.0", "kb_v1.0.0")
        )
        source = inspect.getsource(validator_module.validate_config)
        assert "validate_version_compatibility()" in source

    def test_main_lifespan_raises_on_validation_errors(self):
        """lifespan 已有 raise 分支，版本不兼容会经由 validate_config 一并阻断启动。"""
        main_source = (Path(validator_module.__file__).parents[2] / "app" / "main.py").read_text(
            encoding="utf-8"
        )
        assert "errors = validate_config()" in main_source
        assert "raise ConfigValidationError(errors)" in main_source


class TestDiagnosisResponseWiring:
    def test_response_no_longer_hardcodes_compatible(self):
        """防回退：响应组装处必须是计算结果，不能是恒为 compatible 的字面量。"""
        diagnosis_source = (
            Path(validator_module.__file__).parents[1] / "api" / "v1" / "diagnosis.py"
        ).read_text(encoding="utf-8")

        assert 'version_compatibility="compatible"' not in diagnosis_source
        assert "check_version_compatibility(" in diagnosis_source


@pytest.mark.parametrize(
    "model_version,kb_version,expected",
    [
        ("thyroid_v1.0.0", "kb_v1.0.0", VERSION_COMPATIBLE),
        ("thyroid_v1.0.0", "kb_v2.0.0", VERSION_COMPATIBLE),
        ("thyroid_v2.0.0", "kb_v2.0.0", VERSION_COMPATIBLE),
        ("thyroid_v2.0.0", "kb_v1.0.0", VERSION_INCOMPATIBLE),
    ],
)
def test_contract_field_follows_matrix(model_version, kb_version, expected):
    assert check_version_compatibility(model_version, kb_version) == expected
