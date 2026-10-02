"""T-GB-EDG-04 边缘端 GPU 温度四级策略单测。"""
import pytest

from edge.gpu_thermal import (
    FRAME_SKIP_FPS,
    NORMAL_FPS,
    THROTTLED_FPS,
    GPUThermalPolicy,
    ThermalLevel,
)

pytestmark = pytest.mark.track_edg


class TestThermalLevels:
    def test_normal_below_throttle(self):
        policy = GPUThermalPolicy()
        decision = policy.decide(60.0, now=0.0)

        assert decision.level == ThermalLevel.NORMAL
        assert decision.target_fps == NORMAL_FPS
        assert decision.nav_inference_allowed is True
        assert decision.diagnosis_inference_allowed is True
        assert decision.frame_skip == 0

    @pytest.mark.parametrize("celsius", [75.0, 79.9])
    def test_throttled_75_to_80(self, celsius):
        decision = GPUThermalPolicy().decide(celsius, now=0.0)

        assert decision.level == ThermalLevel.THROTTLED
        assert decision.target_fps == THROTTLED_FPS
        assert decision.nav_inference_allowed is True

    @pytest.mark.parametrize("celsius", [80.0, 84.9])
    def test_frame_skip_80_to_85(self, celsius):
        decision = GPUThermalPolicy().decide(celsius, now=0.0)

        assert decision.level == ThermalLevel.FRAME_SKIP
        assert decision.target_fps == FRAME_SKIP_FPS
        # 隔帧推理：每 2 帧推理 1 帧
        assert decision.frame_skip == 1
        assert decision.nav_inference_allowed is True

    @pytest.mark.parametrize("celsius", [85.0, 89.9])
    def test_nav_paused_85_to_90(self, celsius):
        decision = GPUThermalPolicy().decide(celsius, now=0.0)

        assert decision.level == ThermalLevel.NAV_PAUSED
        assert decision.nav_inference_allowed is False
        # 诊断推理仍可继续，仅暂停导航
        assert decision.diagnosis_inference_allowed is True
        assert decision.target_fps == 0.0


class TestEmergencyEscalation:
    def test_above_90_not_sustained_is_nav_paused(self):
        policy = GPUThermalPolicy(overheat_duration_seconds=60.0)

        first = policy.decide(95.0, now=100.0)
        assert first.level == ThermalLevel.NAV_PAUSED

        still_pending = policy.decide(95.0, now=159.9)
        assert still_pending.level == ThermalLevel.NAV_PAUSED
        assert still_pending.inference_paused is False

    def test_above_90_sustained_60s_escalates_to_emergency(self):
        policy = GPUThermalPolicy(overheat_duration_seconds=60.0)

        policy.decide(95.0, now=100.0)
        emergency = policy.decide(95.0, now=160.0)

        assert emergency.level == ThermalLevel.EMERGENCY
        assert emergency.diagnosis_inference_allowed is False
        assert emergency.nav_inference_allowed is False
        assert emergency.inference_paused is True
        assert emergency.target_fps == 0.0

    def test_checkpoint_requested_once_by_default(self):
        policy = GPUThermalPolicy(overheat_duration_seconds=60.0)

        policy.decide(95.0, now=0.0)
        first_emergency = policy.decide(95.0, now=60.0)
        second_emergency = policy.decide(95.0, now=61.0)

        assert first_emergency.require_checkpoint is True
        assert second_emergency.require_checkpoint is False

    def test_checkpoint_repeats_when_interval_configured(self):
        policy = GPUThermalPolicy(
            overheat_duration_seconds=60.0, checkpoint_interval_seconds=30.0
        )

        policy.decide(95.0, now=0.0)
        policy.decide(95.0, now=60.0)  # 首次
        at_75 = policy.decide(95.0, now=75.0)
        at_95 = policy.decide(95.0, now=95.0)

        assert at_75.require_checkpoint is False
        assert at_95.require_checkpoint is True

    def test_temperature_drop_resets_emergency_timer(self):
        policy = GPUThermalPolicy(overheat_duration_seconds=60.0)

        policy.decide(95.0, now=0.0)
        policy.decide(70.0, now=30.0)  # 回落 → 复位

        assert policy.current_level == ThermalLevel.NORMAL
        assert policy.emergency_seconds == 0.0

        # 重新累计：30s 不足 60s，不应进入紧急降级
        again = policy.decide(95.0, now=100.0)
        assert again.level == ThermalLevel.NAV_PAUSED


class TestRecovery:
    def test_level_recovers_gradually(self):
        policy = GPUThermalPolicy()

        assert policy.decide(95.0, now=0.0).level == ThermalLevel.NAV_PAUSED
        assert policy.decide(82.0, now=1.0).level == ThermalLevel.FRAME_SKIP
        assert policy.decide(77.0, now=2.0).level == ThermalLevel.THROTTLED
        assert policy.decide(60.0, now=3.0).level == ThermalLevel.NORMAL
        assert policy.current_level == ThermalLevel.NORMAL
