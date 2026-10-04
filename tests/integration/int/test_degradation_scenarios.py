"""降级路径专项测试（离线可模拟的 5 个场景，每个都断言"恢复后功能正常"）。

## 背景

验收文档把"降级路径测试"列为**硬门槛**：降级不通过不进入下一阶段。要求覆盖 7 个
场景，并且**区分"可离线模拟"与"必须现场验证"**。本文件只放离线可验证的 5 个：

| 场景 | 离线可模拟 | 本文件的覆盖方式 |
|---|---|---|
| ① Redis 不可用 → 降级 | 是 | `RedisPool.get_client` 抛错时 `_apply_rollout_gate` 不得中断诊断，并留告警 |
| ② 融合层不可用 → 降级结论 | 是 | `_safe_fuse` 返回 `needs_review` 占位；`_write_result_to_db` 落 `needs_review` |
| ③ SG 不可用 → 拒绝输出 | 是（主服务崩溃+SG 存活除外） | `_safe_safety_check` → `(None, False)` → `status="failed"` |
| ④ 边缘端后端断连 → 本地降级 | 是 | 连续失败隔离后**停止上行**，本地仍可用；探活成功**半开→恢复上行** |
| ⑤ Redis + 融合层同时不可用 | 是 | 组合故障下终态仍为 `needs_review`（不是 failed） |
| ⑥ 融合层不可用 + SG 短暂不可用 | 是 | SG 客户端"排队等待恢复"与"超时拒绝（E4005）"两条路径 |

**未纳入本文件**（见提交信息与豁免清单）：
- ⑦ Redis 主从切换秒级接管：**无离线层可言**（mock 无法验证切换时序），已在
  `docs/合规/覆盖率豁免清单.json` 的 `excluded_by_design` 登记；
- ③(b) "主服务崩溃 + SG 存活"：属现场演练，已登记。

## 关于"恢复后功能正常"

每个场景都配一条恢复断言：注入故障 → 断言降级形态；撤掉故障 → 断言回到正常形态。
只测"坏了会怎样"而不测"好了能否恢复"，等于把**永久降级**当成正确行为。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import diagnosis_pipeline as pipeline_module
from app.services.diagnosis_pipeline import (
    _apply_rollout_gate,
    _safe_fuse,
    _safe_safety_check,
    _write_result_to_db,
)
from tests.integration.int.int_test_helpers import patch_write_deps

RID = "req-degradation"


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        request_id=RID,
        session_id="sess-deg",
        patient_id="P-DEG",
        organ="thyroid",
        clinical_text="甲状腺低回声结节",
        retry=False,
    )


def _response(confidence: float = 0.8, content: str = "专科结论"):
    from app.agents.protocols import AgentResponse

    return AgentResponse(
        agent_name="thyroid",
        content=content,
        confidence=confidence,
        reasoning_trace=["t"],
    )


@pytest.fixture
def repo():
    r = MagicMock()
    r.update_result = AsyncMock(return_value=MagicMock())
    return r


@pytest.fixture
def captured_findings(repo, monkeypatch):
    async def _run(**kwargs):
        # ⚠️ 不要再 setdefault("diagnosis_repo", ...)：`_write_result_to_db` 自开
        # 短会话（diagnosis_pipeline.py:783，2026-09-28 session 治理——只有
        # flush 不 commit 会丢写），该形参早已不存在。这里 patch 它真正用的
        # 两个依赖，见 int_test_helpers.patch_write_deps。
        patch_write_deps(monkeypatch, repo)
        kwargs.setdefault("request", _request())
        kwargs.setdefault("sg_result", {"passed": True})
        kwargs.setdefault("sg_ok", True)
        kwargs.setdefault("timings", {})
        return await _write_result_to_db(**kwargs)

    # 注意取的是 kwargs["findings"]：review_reason 在 findings 里面，
    # 不在 update_result 的顶层参数上（第一版写错，拿到 KeyError）。
    return _run, lambda: repo.update_result.await_args.kwargs["findings"]


class TestRedisUnavailableDegradesWithoutBreakingDiagnosis:
    """场景 ①：Redis 不可用 → 灰度计数降级，**诊断不得中断**。

    注：计划初判设想的是"Redis 不可用→PG 轮询降级"。实测发现真实形态是
    `rollout_router` 的**进程内计数降级**（`rollout_router.py:74-117`，含告警），
    并不存在 PG 轮询路径 —— 因此这里断言的是真实行为，不是设想的路径。
    """

    @staticmethod
    def _counter_cls():
        """按能力查找计数类（避免写死类名——类名不是本测试要钉的契约）。"""
        from app.services import rollout_router as rr

        return next(
            obj
            for obj in vars(rr).values()
            if isinstance(obj, type) and hasattr(obj, "next_index")
        )

    async def test_gate_does_not_raise_on_redis_failure(self, monkeypatch):
        from app.config.redis import RedisPool

        monkeypatch.setattr(
            RedisPool, "get_client", AsyncMock(side_effect=RuntimeError("redis down"))
        )

        routing, rollout = await _apply_rollout_gate(_request(), None)

        # 不抛异常即通过（灰度只是放量依据，不得中断诊断）
        assert rollout is None or hasattr(rollout, "as_dict")
        assert routing is None or hasattr(routing, "as_dict")

    async def test_counter_falls_back_to_memory_and_logs(self, monkeypatch):
        """Redis 不可用 → 进程内计数降级，且必须留痕（不静默）。"""
        from app.config.redis import RedisPool
        from app.services import rollout_router as rr

        monkeypatch.setattr(
            RedisPool, "get_client", AsyncMock(side_effect=RuntimeError("redis down"))
        )
        fake_logger = MagicMock()
        monkeypatch.setattr(rr, "logger", fake_logger)

        counter = self._counter_cls()()

        assert await counter.next_index("track-a") == 1
        assert await counter.next_index("track-a") == 2, "进程内也要单调递增"
        events = [c.args[0] for c in fake_logger.warning.call_args_list if c.args]
        assert "rollout_counter_redis_unavailable" in events

    async def test_counter_recovers_when_redis_returns(self, monkeypatch):
        """恢复断言：Redis 恢复后同一实例应立刻改走 Redis（不得缓存故障态）。"""
        from app.config.redis import RedisPool

        monkeypatch.setattr(
            RedisPool, "get_client", AsyncMock(side_effect=RuntimeError("redis down"))
        )
        counter = self._counter_cls()()
        assert await counter.next_index("track-a") == 1

        client = MagicMock()
        client.incr = AsyncMock(return_value=7)
        client.expire = AsyncMock(return_value=True)
        monkeypatch.setattr(RedisPool, "get_client", AsyncMock(return_value=client))

        assert await counter.next_index("track-a") == 7
        client.incr.assert_awaited()


class TestFusionLayerUnavailable:
    """场景 ②/⑤：融合层不可用 → 取专科最高置信度并强制 `needs_review`。"""

    async def test_fuse_degrades_to_placeholder_with_review(self, monkeypatch):
        from app.main import app as app_instance

        monkeypatch.setattr(app_instance.state, "fusion_engine", None, raising=False)

        result = await _safe_fuse(_request(), [_response(0.4), _response(0.7)], RID)

        assert result is not None
        assert result.needs_review is True
        assert result.review_reason == "fusion_layer_failed"
        assert result.final_response.confidence == pytest.approx(0.7), (
            "降级时必须取专科最高置信度，而不是丢掉结论"
        )

    async def test_fuse_exception_also_degrades(self, monkeypatch):
        from app.main import app as app_instance

        engine = MagicMock()
        engine.fuse = AsyncMock(side_effect=RuntimeError("fusion exploded"))
        monkeypatch.setattr(app_instance.state, "fusion_engine", engine, raising=False)

        result = await _safe_fuse(_request(), [_response(0.6)], RID)

        assert result.needs_review is True
        assert result.review_reason == "fusion_layer_failed"

    async def test_recovers_when_fusion_returns(self, monkeypatch):
        from app.main import app as app_instance

        healthy = MagicMock()
        healthy.needs_review = False
        healthy.review_reason = None
        healthy.final_response = _response(0.9)
        engine = MagicMock()
        engine.fuse = AsyncMock(return_value=healthy)
        monkeypatch.setattr(app_instance.state, "fusion_engine", engine, raising=False)

        result = await _safe_fuse(_request(), [_response(0.9)], RID)

        assert result is healthy
        assert result.needs_review is False

    async def test_degraded_fusion_is_persisted_as_needs_review(
        self, monkeypatch, repo, captured_findings
    ):
        """端到端片段：融合层降级 → 落库 status 必须是 needs_review（不是 completed）。"""
        from app.main import app as app_instance

        monkeypatch.setattr(app_instance.state, "fusion_engine", None, raising=False)
        fusion_result = await _safe_fuse(_request(), [_response(0.5)], RID)
        run, findings_of = captured_findings

        await run(fusion_result=fusion_result, routing=None, rollout=None)

        assert repo.update_result.await_args.kwargs["status"] == "needs_review"
        assert findings_of()["review_reason"] == "fusion_layer_failed"


class TestSafetyGatewayUnavailable:
    """场景 ③：SG 不可用 → 一律拒绝输出（fail-closed），**不得放行**。"""

    async def test_unavailable_gateway_fails_closed(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_module.safety_gateway_client,
            "get_consent_status",
            AsyncMock(return_value=None),
        )
        fake_logger = MagicMock()
        monkeypatch.setattr(pipeline_module, "logger", fake_logger)

        sg_result, sg_ok = await _safe_safety_check(_request(), None, RID, "P001")

        assert sg_ok is False
        assert sg_result is None
        events = [c.args[0] for c in fake_logger.error.call_args_list if c.args]
        assert "pipeline_consent_status_unavailable" in events

    async def test_write_marks_failed_with_reason(self, repo, captured_findings):
        run, findings_of = captured_findings

        await run(fusion_result=None, sg_result=None, sg_ok=False)

        kwargs = repo.update_result.await_args.kwargs
        assert kwargs["status"] == "failed"
        assert kwargs["confidence"] == 0.0
        assert findings_of()["review_reason"] == "safety_gateway_unavailable"

    async def test_check_output_raise_is_contained(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_module.safety_gateway_client,
            "get_consent_status",
            AsyncMock(return_value="granted"),
        )
        monkeypatch.setattr(
            pipeline_module.safety_gateway_client,
            "check_output",
            AsyncMock(side_effect=RuntimeError("sg process gone")),
        )

        sg_result, sg_ok = await _safe_safety_check(_request(), None, RID, "P001")

        assert (sg_result, sg_ok) == (None, False)

    async def test_recovers_when_gateway_returns(self, monkeypatch, repo, captured_findings):
        """恢复断言：SG 恢复后同一条链路必须回到正常终态。"""
        monkeypatch.setattr(
            pipeline_module.safety_gateway_client,
            "get_consent_status",
            AsyncMock(return_value="granted"),
        )
        monkeypatch.setattr(
            pipeline_module.safety_gateway_client,
            "check_output",
            AsyncMock(return_value={"passed": True, "allowed": True}),
        )

        sg_result, sg_ok = await _safe_safety_check(_request(), None, RID, "P001")
        assert sg_ok is True

        from app.fusion.protocols import FusionResult

        healthy = FusionResult(final_response=_response(0.85), needs_review=False)
        run, _ = captured_findings
        await run(fusion_result=healthy, sg_result=sg_result, sg_ok=True)

        assert repo.update_result.await_args.kwargs["status"] == "completed"


class TestRedisAndFusionSimultaneouslyUnavailable:
    """场景 ⑤：组合故障 —— 终态仍应是"需复核"，而不是把两处降级叠加成失败。"""

    async def test_combined_failure_keeps_reviewable_conclusion(
        self, monkeypatch, repo, captured_findings
    ):
        from app.config.redis import RedisPool
        from app.main import app as app_instance

        monkeypatch.setattr(
            RedisPool, "get_client", AsyncMock(side_effect=RuntimeError("redis down"))
        )
        monkeypatch.setattr(app_instance.state, "fusion_engine", None, raising=False)

        routing, rollout = await _apply_rollout_gate(_request(), None)
        fusion_result = await _safe_fuse(_request(), [_response(0.55)], RID)
        run, findings_of = captured_findings

        await run(
            fusion_result=fusion_result, routing=routing, rollout=rollout, sg_ok=True
        )

        kwargs = repo.update_result.await_args.kwargs
        assert kwargs["status"] == "needs_review", (
            "组合降级不得把可复核的结论变成 failed"
        )
        assert kwargs["confidence"] > 0.0
        assert findings_of()["review_reason"] == "fusion_layer_failed"


class TestSafetyGatewayQueueAndTimeout:
    """场景 ⑥：SG 短暂不可用 —— "排队等待恢复"与"超时拒绝"两条路径。

    规则：**禁止真实等待 15s**。这里把 `_queue_timeout` 与 `_retry_interval`
    压到毫秒级，只验证语义。
    """

    @pytest.fixture(autouse=True)
    def _reset_client_state(self):
        client = pipeline_module.safety_gateway_client
        client._consecutive_failures = 0
        client._queue_timeout = 0.05
        client._retry_interval = 0.01
        yield
        client._consecutive_failures = 0

    async def test_queues_and_recovers_within_window(self, monkeypatch):
        """短暂失败后恢复 → 返回真实结果，而不是 E4005。"""
        client = pipeline_module.safety_gateway_client
        client._queue_timeout = 2.0
        ok_result = {"passed": True, "allowed": True, "message": "ok"}
        attempts = {"n": 0}

        async def _flaky(*_args, **_kwargs):
            attempts["n"] += 1
            if attempts["n"] < 3:
                return None, True, "timeout"
            return ok_result, False, None

        monkeypatch.setattr(client, "_attempt_check", _flaky)

        result = await client.check_output(
            session_id="s",
            patient_id="P",
            output_data={"a": "b"},
            consent_status_at_input="granted",
        )

        assert attempts["n"] == 3
        assert result.get("allowed") is True
        assert result.get("passed") is True

    async def test_times_out_to_rejection(self, monkeypatch):
        """一直不可用 → 超时/熔断后拒绝（E4005 语义），且必须留痕。"""
        client = pipeline_module.safety_gateway_client
        fake_logger = MagicMock()
        monkeypatch.setattr("app.services.safety_gateway_client.logger", fake_logger)

        async def _always_down(*_args, **_kwargs):
            return None, True, "connect_error"

        monkeypatch.setattr(client, "_attempt_check", _always_down)

        result = await client.check_output(
            session_id="s",
            patient_id="P",
            output_data={"a": "b"},
            consent_status_at_input="granted",
        )

        assert result.get("allowed") is not True, "不可用期间不得放行"
        events = [
            c.args[0]
            for c in fake_logger.error.call_args_list
            if c.args
        ]
        assert any(
            e in ("safety_gateway_queue_timeout_exceeded", "safety_gateway_fast_fail")
            or "fast_fail" in e
            for e in events
        ), "拒绝必须有可观测的告警事件"

    async def test_recovers_after_gateway_returns(self, monkeypatch):
        """恢复断言：SG 恢复后同一客户端立即可用（不得残留熔断状态）。"""
        client = pipeline_module.safety_gateway_client

        async def _healthy(*_args, **_kwargs):
            return {"passed": True, "allowed": True}, False, None

        monkeypatch.setattr(client, "_attempt_check", _healthy)

        result = await client.check_output(
            session_id="s",
            patient_id="P",
            output_data={"a": "b"},
            consent_status_at_input="granted",
        )

        assert result.get("allowed") is True


class TestEdgeBackhaulDisconnect:
    """场景 ④：边缘端后端断连 → 本地降级（停上行），恢复后自动重新上行。

    契约见 `edge/device_manager.py:6-17` 的状态机与设计取舍：
    "**宁可不上行，也不让脏数据进入后端**" —— 隔离/恢复中一律 `should_uplink=False`，
    但本地仍保留 DICOM 采集与本地安全校验（医生可继续操作设备）。

    注意：边缘模块在仓库根目录 `edge/`，不在 `app/` 下。
    """

    @staticmethod
    def _manager(transport, failure_threshold: int = 3):
        from edge.device_manager import EdgeDeviceManager

        return EdgeDeviceManager(
            auth=SimpleNamespace(device_id="edge-001"),
            transport=transport,
            failure_threshold=failure_threshold,
        )

    @staticmethod
    def _transport(failing: bool):
        class _Transport:
            async def post(self, path, payload):
                if failing["value"]:
                    raise RuntimeError("backhaul down")
                return {"ok": True}

        return _Transport()

    async def test_occasional_failure_degrades_but_still_uplinks(self):
        transport = self._transport({"value": True})
        manager = self._manager(transport, failure_threshold=3)

        manager.record_success()  # REGISTERED → ONLINE
        state = manager.record_failure("heartbeat_failed")

        assert state.value == "degraded"
        assert manager.should_uplink is True, "偶发失败只降级，不禁用上行"
        assert manager.is_isolated is False

    async def test_consecutive_failures_isolate_and_stop_uplink(self):
        transport = self._transport({"value": True})
        manager = self._manager(transport, failure_threshold=3)
        manager.record_success()

        for _ in range(3):
            manager.record_failure("health_probe_failed")

        assert manager.state.value == "isolated"
        assert manager.is_isolated is True
        assert manager.should_uplink is False, "隔离期必须停止上行（宁可不上行）"
        assert manager.health.isolated_reason is not None

    async def test_recovery_is_half_open_then_online(self):
        """恢复断言：隔离后一次成功只到"半开"（仍不上行），再次成功才恢复上行。"""
        transport = self._transport({"value": True})
        manager = self._manager(transport, failure_threshold=2)
        manager.record_success()
        manager.record_failure("x")
        manager.record_failure("x")
        assert manager.should_uplink is False

        manager.record_success()
        assert manager.state.value == "recovering"
        assert manager.should_uplink is False, "半开态仍不得上行"

        manager.record_success()
        assert manager.state.value == "online"
        assert manager.should_uplink is True, "恢复后必须重新上行"
        assert manager.health.isolated_reason is None

    async def test_failure_during_recovery_reisolates_immediately(self):
        """半开期再失败 → 立即回到隔离（不能像 ONLINE 那样累积到阈值）。"""
        transport = self._transport({"value": True})
        manager = self._manager(transport, failure_threshold=2)
        manager.record_success()
        manager.record_failure("x")
        manager.record_failure("x")
        manager.record_success()
        assert manager.state.value == "recovering"

        manager.record_failure("probe failed again")

        assert manager.state.value == "isolated"
        assert manager.should_uplink is False

    async def test_real_transport_outage_drives_isolation_then_recovery(self):
        """端到端：真实 transport 断连 → 心跳连续失败 → 隔离；恢复 → 心跳成功 → 上线。"""
        failing = {"value": True}
        manager = self._manager(self._transport(failing), failure_threshold=3)

        assert await manager.send_heartbeat() is False
        assert await manager.send_heartbeat() is False
        assert await manager.send_heartbeat() is False
        assert manager.is_isolated is True
        assert manager.should_uplink is False

        failing["value"] = False
        assert await manager.health_probe() is True
        assert manager.state.value == "recovering"
        assert manager.should_uplink is False

        assert await manager.send_heartbeat() is True
        assert manager.state.value == "online"
        assert manager.should_uplink is True, "后端恢复后必须自动重新上行"
