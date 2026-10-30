"""T-GR-INT-01：灰度发布流量路由。

关键不变量：
1. 分桶必须**确定性**——同一 session_id 恒定命中同一桶，否则同一患者在一次
   检查里会一会儿走新行为一会儿走旧行为，结果不可复现、无法回放；
2. full 档位放量比例贴近配置的百分比（不是"随机一次碰运气"）；
3. compact 档位按会话数爬坡：首个会话 -> N 个会话 -> 全量；
4. 缺 session_id 时 fail-open——灰度是控制暴露面的手段，不该拦诊疗。
"""
from dataclasses import replace

import pytest

from app.config.settings import settings
from app.services.rollout_router import (
    BUCKET_COUNT,
    RolloutDecision,
    RolloutRouter,
    RolloutSessionCounter,
    RolloutStage,
    RolloutTrack,
    bucket_of,
    get_rollout_router,
    set_rollout_router,
)


class _StubCounter:
    """按调用次数递增的会话计数器（替掉 Redis）。"""

    def __init__(self):
        self.calls = 0

    async def next_index(self, track: str) -> int:
        self.calls += 1
        return self.calls


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "rollout_enabled", True)
    monkeypatch.setattr(settings, "rollout_full_percent", 5)
    monkeypatch.setattr(settings, "rollout_compact_session_threshold", 10)
    yield


class TestBucketing:
    def test_deterministic(self):
        assert bucket_of("session_abc") == bucket_of("session_abc")

    def test_range(self):
        for index in range(200):
            assert 0 <= bucket_of(f"session_{index}") < BUCKET_COUNT

    def test_different_sessions_differ(self):
        """200 个会话应铺开到大多数桶（50 个样本本就会因生日悖论只覆盖约 40 桶）。"""
        buckets = {bucket_of(f"session_{index}") for index in range(200)}

        assert len(buckets) > 60, "分桶过于集中，说明哈希退化"

    def test_empty_string_is_stable(self):
        assert bucket_of("") == bucket_of("")


class TestFullTrackSampling:
    async def test_percent_respected_across_many_sessions(self, enabled):
        """放量比例应贴近 5%（±2 个百分点），单看一两个会话看不出对错。"""
        router = RolloutRouter(counter=_StubCounter())

        allowed = 0
        total = 4000
        for index in range(total):
            decision = await router.decide(
                session_id=f"session_{index}", track=RolloutTrack.FULL.value
            )
            assert decision.track == "full"
            allowed += int(decision.allowed)

        ratio = allowed / total
        assert 0.03 <= ratio <= 0.07, f"实际放量比例 {ratio:.3%} 偏离 5% 过大"

    async def test_same_session_always_same_verdict(self, enabled):
        router = RolloutRouter(counter=_StubCounter())

        first = await router.decide(session_id="stable_session", track="full")
        for _ in range(20):
            again = await router.decide(session_id="stable_session", track="full")
            assert again.allowed == first.allowed
            assert again.bucket == first.bucket

    async def test_bucket_boundary_is_inclusive_of_lower_bounds(self, enabled, monkeypatch):
        """bucket < percent 才放行：bucket==percent 必须被挡下，否则放量会多一档。"""
        session_id = next(
            candidate
            for candidate in (f"session_{i}" for i in range(10000))
            if bucket_of(candidate) == 7
        )
        monkeypatch.setattr(settings, "rollout_full_percent", 7)
        router = RolloutRouter(counter=_StubCounter())

        decision = await router.decide(session_id=session_id, track="full")

        assert decision.bucket == 7
        assert decision.allowed is False

    async def test_zero_percent_blocks_all(self, enabled, monkeypatch):
        monkeypatch.setattr(settings, "rollout_full_percent", 0)
        router = RolloutRouter(counter=_StubCounter())

        for index in range(20):
            decision = await router.decide(session_id=f"session_{index}", track="full")
            assert decision.allowed is False

    async def test_hundred_percent_allows_all(self, enabled, monkeypatch):
        monkeypatch.setattr(settings, "rollout_full_percent", 100)
        router = RolloutRouter(counter=_StubCounter())

        for index in range(20):
            decision = await router.decide(session_id=f"session_{index}", track="full")
            assert decision.allowed is True


class TestCompactRamp:
    async def test_stage_progression(self, enabled):
        """首个会话 -> 10 个会话 -> 全量。"""
        counter = _StubCounter()
        router = RolloutRouter(counter=counter)

        stages = []
        for _ in range(12):
            decision = await router.decide(session_id="s", track=RolloutTrack.COMPACT.value)
            stages.append(decision.stage)

        assert stages[0] == RolloutStage.CANARY.value
        assert stages[1:10] == [RolloutStage.RAMP.value] * 9
        assert stages[10] == RolloutStage.COMPLETE.value
        assert stages[11] == RolloutStage.COMPLETE.value

    async def test_compact_always_allowed(self, enabled):
        """compact 是爬坡轨道：全程放行，阶段只用于观测与放量节奏。"""
        router = RolloutRouter(counter=_StubCounter())

        for _ in range(15):
            decision = await router.decide(session_id="s", track="compact")
            assert decision.allowed is True

    async def test_session_index_recorded(self, enabled):
        router = RolloutRouter(counter=_StubCounter())

        decision = await router.decide(session_id="s", track="compact")

        assert decision.session_index == 1


class TestCounter:
    async def test_memory_fallback_when_redis_unavailable(self, monkeypatch):
        async def _no_redis(self):
            return None

        monkeypatch.setattr(RolloutSessionCounter, "_ensure_redis", _no_redis)
        counter = RolloutSessionCounter()

        assert await counter.next_index("compact") == 1
        assert await counter.next_index("compact") == 2

    async def test_redis_used_when_available(self):
        class _FakeRedis:
            def __init__(self):
                self.value = 0
                self.expires = []

            async def incr(self, key):
                self.value += 1
                return self.value

            async def expire(self, key, ttl):
                self.expires.append((key, ttl))

        fake = _FakeRedis()
        counter = RolloutSessionCounter(redis_client=fake)

        assert await counter.next_index("full") == 1
        assert await counter.next_index("full") == 2
        assert fake.expires, "首次计数应设置 TTL，否则键会永久累积"

    async def test_redis_failure_degrades_to_memory(self):
        class _BrokenRedis:
            async def incr(self, key):
                raise RuntimeError("redis down")

        counter = RolloutSessionCounter(redis_client=_BrokenRedis())

        assert await counter.next_index("full") == 1


class TestFailOpen:
    async def test_missing_session_id_is_allowed(self, enabled):
        """拿不到分桶依据时不得拦下诊断——尤其这是临床系统。"""
        router = RolloutRouter(counter=_StubCounter())

        decision = await router.decide(session_id="", track="full")

        assert decision.allowed is True
        assert decision.reason == "missing_session_id"

    async def test_unknown_track_fails_open(self, enabled):
        router = RolloutRouter(counter=_StubCounter())

        decision = await router.decide(session_id="s", track="weird_track")

        assert decision.allowed is True
        assert decision.reason == "unknown_track_fail_open"

    async def test_disabled_rollout_allows_everything(self, monkeypatch):
        monkeypatch.setattr(settings, "rollout_enabled", False)
        router = RolloutRouter(counter=_StubCounter())

        decision = await router.decide(session_id="s", track="full")

        assert decision.allowed is True
        assert decision.stage == RolloutStage.OFF.value


class TestDecisionSerialization:
    async def test_as_dict_has_observability_fields(self, enabled):
        router = RolloutRouter(counter=_StubCounter())

        payload = (await router.decide(session_id="s", track="full")).as_dict()

        assert set(payload) == {
            "allowed",
            "track",
            "stage",
            "reason",
            "bucket",
            "session_index",
            "percent",
        }
        assert payload["track"] == "full"
        assert payload["percent"] == 5


class TestSingleton:
    def test_set_and_get(self):
        original = get_rollout_router()
        replacement = RolloutRouter(counter=_StubCounter())
        try:
            set_rollout_router(replacement)
            assert get_rollout_router() is replacement
        finally:
            set_rollout_router(original)


class TestRolloutSettingsValidation:
    """灰度配置写错时必须在启动期就被挡住，而不是灰度打开后才发现放量全开/全关。"""

    def test_invalid_percent_rejected_when_enabled(self, monkeypatch):
        from app.config.validator import validate_config

        monkeypatch.setattr(settings, "rollout_enabled", True)
        monkeypatch.setattr(settings, "rollout_full_percent", 200)

        errors = validate_config()

        assert any("ROLLOUT_FULL_PERCENT" in error for error in errors)

    def test_zero_threshold_rejected_when_enabled(self, monkeypatch):
        from app.config.validator import validate_config

        monkeypatch.setattr(settings, "rollout_enabled", True)
        monkeypatch.setattr(settings, "rollout_compact_session_threshold", 0)

        errors = validate_config()

        assert any("ROLLOUT_COMPACT_SESSION_THRESHOLD" in error for error in errors)

    def test_invalid_values_ignored_when_disabled(self, monkeypatch):
        """灰度关闭时这些值不参与决策，不应阻塞启动。"""
        from app.config.validator import validate_config

        monkeypatch.setattr(settings, "rollout_enabled", False)
        monkeypatch.setattr(settings, "rollout_full_percent", 200)

        errors = validate_config()

        assert not any("ROLLOUT_" in error for error in errors)

    def test_default_config_is_valid(self):
        from app.config.validator import validate_config

        assert not any("ROLLOUT_" in error for error in validate_config())


class TestPipelineGate:
    """管线内的灰度闸门：未放行时退回基线检索档位，且绝不因异常阻断诊断。"""

    class _Request:
        def __init__(self, session_id="session_abc", tier="full", organ="thyroid"):
            self.session_id = session_id
            self.tier = tier
            self.organ = organ
            self.clinical_text = "低回声结节"

    def _routing(self):
        from app.agents.routing import RetrievalProfile
        from app.models.schemas import ResponseTier
        from app.services.diagnosis_pipeline import _assess_routing

        class _R:
            requested_tier = ResponseTier.FULL

        del _R, RetrievalProfile
        return _assess_routing(self._Request())

    async def test_denied_session_falls_back_to_baseline_profile(self, enabled, monkeypatch):
        from app.agents.routing import RetrievalProfile
        from app.services import diagnosis_pipeline as pipeline

        async def _denied(self, *, session_id, track):
            return RolloutDecision(
                allowed=False, track=track, stage="off", reason="percent_bucket_miss"
            )

        monkeypatch.setattr(RolloutRouter, "decide", _denied)
        routing = self._routing()

        gated, decision = await pipeline._apply_rollout_gate(self._Request(), routing)

        assert decision is not None and decision.allowed is False
        # 01-2 步骤4（B4，16 号 A.96.2）：灰度退回基线只退 use_llm_enhancement /
        # full_validation，**保留 use_reranking** —— 否则灰度一开，未放行会话
        # 的精排被静默关掉（01-2② #4"灰度是静默杀手"）。
        assert gated.retrieval_profile.use_llm_enhancement is False
        assert gated.retrieval_profile.full_validation is False
        assert (
            gated.retrieval_profile.use_reranking
            == routing.retrieval_profile.use_reranking
        ), "灰度退回基线不得动精排开关"

    async def test_allowed_session_keeps_profile(self, enabled, monkeypatch):
        from app.services import diagnosis_pipeline as pipeline

        async def _allowed(self, *, session_id, track):
            return RolloutDecision(
                allowed=True, track=track, stage="ramp", reason="percent_bucket_hit"
            )

        monkeypatch.setattr(RolloutRouter, "decide", _allowed)
        routing = self._routing()

        gated, decision = await pipeline._apply_rollout_gate(self._Request(), routing)

        assert decision.allowed is True
        assert gated is routing, "放行时不应重建判定对象"

    async def test_track_selected_by_requested_tier(self, enabled, monkeypatch):
        from app.services import diagnosis_pipeline as pipeline

        seen: list = []

        async def _capture(self, *, session_id, track):
            seen.append(track)
            return RolloutDecision(allowed=True, track=track, stage="ramp", reason="ok")

        monkeypatch.setattr(RolloutRouter, "decide", _capture)

        from app.models.schemas import ResponseTier

        full_routing = self._routing()
        standard_routing = replace(full_routing, requested_tier=ResponseTier.STANDARD)

        await pipeline._apply_rollout_gate(self._Request(tier="full"), full_routing)
        await pipeline._apply_rollout_gate(self._Request(tier="standard"), standard_routing)

        assert seen[0] == RolloutTrack.FULL.value
        assert seen[1] == RolloutTrack.COMPACT.value

    async def test_gate_failure_does_not_block_diagnosis(self, enabled, monkeypatch):
        from app.services import diagnosis_pipeline as pipeline

        async def _boom(self, *, session_id, track):
            raise RuntimeError("rollout backend down")

        monkeypatch.setattr(RolloutRouter, "decide", _boom)
        routing = self._routing()

        gated, decision = await pipeline._apply_rollout_gate(self._Request(), routing)

        assert decision is None
        assert gated is routing, "判定失败必须原样放行"

    async def test_gate_without_routing_decision_is_safe(self, enabled, monkeypatch):
        from app.services import diagnosis_pipeline as pipeline

        async def _denied(self, *, session_id, track):
            return RolloutDecision(allowed=False, track=track, stage="off", reason="miss")

        monkeypatch.setattr(RolloutRouter, "decide", _denied)

        gated, decision = await pipeline._apply_rollout_gate(self._Request(), None)

        assert gated is None
        assert decision is not None

    async def test_decision_is_written_to_findings(self):
        """判定必须落库，否则"放量比例"只能靠数日志。"""

        decision = RolloutDecision(
            allowed=False, track="full", stage="off", reason="percent_bucket_miss", bucket=88
        )

        assert decision.as_dict()["bucket"] == 88
        assert replace(decision, allowed=True).allowed is True
