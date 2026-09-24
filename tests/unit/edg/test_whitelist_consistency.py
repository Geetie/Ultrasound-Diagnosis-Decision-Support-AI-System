"""白名单跨模块一致性守卫。

背景：导航标签/字段白名单在 **4 处**各有一份独立定义（边缘端 1 份、安全网关
3 份）。任一处的修改都必须同步其余三处，否则会出现"边缘端放行、网关却把它
当作降级字段剔除"的口径漂移——对安全防线而言这是**静默失效**，不会有任何
报错。历史上 ``edge/safety.py`` 的注释还指向了一个并不存在白名单常量的模块
（``app/fusion/desensitization.py``），说明单靠注释约束并不可靠。

因此这里用测试把 4 处口径锁死。新增 M1/M2/M3/M6/M9 所需字段时，必须四处同改，
否则本文件会失败。
"""
import pytest

from edge.safety import DEFAULT_ALLOWED_LABELS
from safety_gateway.checker.orchestrator import SafetyGatewayChecker
from safety_gateway.degraded_output import DEFAULT_WHITELIST_FIELDS
from safety_gateway.sg_config import SGSettings

pytestmark = pytest.mark.track_edg

# M1/M2/M3/M6/M9 新增的白名单字段（4 处必须一致包含）
M_SERIES_FIELDS = (
    # M1 切面标准性评估
    "plane_assessment",
    # M6 图像质量评估
    "image_quality",
    # M2 B+ 级高危标记与建议动作
    "risk_flag",
    "suggested_action",
    # M9 后方回声特征
    "posterior_acoustic",
    # M3 自动测量
    "measurement",
    "aspect_ratio",
    "distance_to_capsule",
    "distance_to_organ_edge",
    "distance_to_skin",
    "volume",
)

# 禁止出现在白名单里的分级结论类术语（与 SG 的 FORBIDDEN_GRADING_TERMS 同义）
FORBIDDEN_SUBSTRINGS = (
    "grading",
    "diagnosis",
    "conclusion",
    "malignant",
    "benign",
    "ti_rads",
    "tirads",
    "li_rads",
    "lirads",
    "suspicious",
)


def _whitelist_copies() -> dict:
    """收集 4 份白名单定义。

    ``SGSettings`` 用 ``model_fields[...].default`` 取**声明默认值**，
    不实例化，避免受环境变量影响。
    """
    return {
        "edge.safety.DEFAULT_ALLOWED_LABELS": list(DEFAULT_ALLOWED_LABELS),
        "safety_gateway.degraded_output.DEFAULT_WHITELIST_FIELDS": list(
            DEFAULT_WHITELIST_FIELDS
        ),
        "safety_gateway.checker.orchestrator.SafetyGatewayChecker.DEFAULT_WHITELIST_FIELDS": list(
            SafetyGatewayChecker.DEFAULT_WHITELIST_FIELDS
        ),
        "safety_gateway.sg_config.SGSettings.safety_gateway_whitelist_fields": list(
            SGSettings.model_fields["safety_gateway_whitelist_fields"].default
        ),
    }


class TestWhitelistConsistency:
    def test_copies_are_identical(self):
        copies = _whitelist_copies()
        reference_name, reference = next(iter(copies.items()))
        for name, value in copies.items():
            assert value == reference, (
                f"{name} 与 {reference_name} 白名单口径不一致："
                f"only_in_reference={sorted(set(reference) - set(value))} "
                f"only_in_this={sorted(set(value) - set(reference))}"
            )

    def test_m_series_fields_whitelisted_everywhere(self):
        for name, value in _whitelist_copies().items():
            missing = [f for f in M_SERIES_FIELDS if f not in value]
            assert not missing, f"{name} 缺少 M1~M9 字段：{missing}"

    def test_no_grading_terms_in_whitelist(self):
        for name, value in _whitelist_copies().items():
            for field in value:
                lowered = field.lower()
                for forbidden in FORBIDDEN_SUBSTRINGS:
                    assert forbidden not in lowered, (
                        f"{name} 的字段 {field!r} 含分级结论类术语 {forbidden!r}，"
                        "违反白名单语义（只允许解剖/影像征象类字段上行）"
                    )

    def test_whitelist_fields_unique(self):
        for name, value in _whitelist_copies().items():
            assert len(value) == len(set(value)), f"{name} 存在重复字段"

    def test_orchestrator_uses_settings_when_provided(self):
        """orchestrator 的非空 settings 值优先于类常量（既有行为）。"""
        orch = SafetyGatewayChecker()
        assert set(orch._whitelist_fields) == set(DEFAULT_ALLOWED_LABELS)
