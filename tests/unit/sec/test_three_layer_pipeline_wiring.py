"""三层脱敏编排入管线接线守卫。

## 为什么有这条测试

2026-09-28 接线审计：``ThreeLayerDesensitizationManager`` 此前**只被测试引用**，
``run_diagnosis_pipeline`` 未调用任何 ``process_*`` —— 合规意义上的"三层纵深"从未
生效；更实际的是，``AgentContext.user_query`` 直接取 ``request.clinical_text`` 原文
（入口层并未脱敏），**临床原文进入了智能体与 RAG**。

## 钉住的三条不变量

1. **三层都有生产调用点**（源码断言），且 **L1 先于 L2**；
2. **不重复调用 SG**：第三层复用既有 ``check_output`` 结果登记，而不是走
   ``process_safety_gateway_layer``（否则同一请求会第二次调用 SG）；
3. **同一次运行内同层只生效一次**（按 request_id 缓存），且重复调用结果一致。
   注意：这里的幂等**不是** Redis 跳过门 —— 管线每次运行都重新读原文，用 Redis 标记
   跳过 L1 会让未脱敏原文进入智能体（安全回退）。Redis 只写审计留痕。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.redis_keys import RedisKey
from app.security.three_layer_desensitization import ThreeLayerDesensitizationManager
from app.services import diagnosis_pipeline as pipeline_module
from app.services.diagnosis_pipeline import (
    _clear_desens_run,
    _desensitize_input_text,
    _desensitize_output_payload,
    _register_l3_existing_sg_check,
)

PIPELINE_PY = Path(__file__).resolve().parents[3] / "app" / "services" / "diagnosis_pipeline.py"

# 命中脱敏规则的原文：手机号规则保留后 4 位（→ ********5678），
# 姓名规则把「患者张三」→「患者[REDACTED_NAME]」（见 desensitization.py:112-136）
PII_TEXT = "患者张三，手机13812345678，甲状腺低回声结节"
PII_PAYLOAD = {"diagnosis": PII_TEXT, "organ": "thyroid", "score": 0.87}

RID = "req-desens-wiring"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """清掉按运行缓存，并把 Redis 审计留痕换成 spy（避免真连 Redis 拖慢用例）。"""
    _clear_desens_run(RID)
    setex = AsyncMock()
    monkeypatch.setattr(pipeline_module.RedisPool, "setex", setex)
    yield setex
    _clear_desens_run(RID)


def _pipeline_source() -> str:
    return "\n".join(
        line
        for line in PIPELINE_PY.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    )


class TestPipelineWiresAllThreeLayers:
    def test_source_has_three_call_sites(self):
        code = _pipeline_source()

        assert code.count("await _desensitize_input_text(") >= 1, "L1 必须在管线里被调用"
        assert code.count("await _desensitize_output_payload(") >= 1, (
            "L2 必须在写库前被调用"
        )
        assert code.count("await _register_l3_existing_sg_check(") >= 1, (
            "L3 必须登记既有 SG 结果"
        )
        assert code.count("_clear_desens_run(request_id)") >= 1, "按运行缓存必须清账"

    def test_l1_runs_before_l2(self):
        code = _pipeline_source()
        assert code.index("await _desensitize_input_text(") < code.index(
            "await _desensitize_output_payload("
        )

    def test_no_second_safety_gateway_call(self):
        """第三层不得再走 process_safety_gateway_layer（那会第二次调用 SG）。"""
        code = _pipeline_source()
        assert "process_safety_gateway_layer(" not in code


class TestLayer1InputDesensitization:
    async def test_masks_pii_before_agents(self):
        masked = await _desensitize_input_text(
            request_id=RID, raw_text=PII_TEXT, session_id="sess-1"
        )

        assert "13812345678" not in masked
        # 姓名走哪条机制不作断言：规则替换为 [REDACTED_NAME]，而引擎的令牌化
        # 路径会产出 PATIENT_001 之类的稳定令牌（供 restore 服务还原）。
        # 断言"原文姓名不再出现"才是真正要钉住的不变量。
        assert "张三" not in masked

    async def test_marks_audit_with_layer_scoped_key(self, _clean_state):
        await _desensitize_input_text(request_id=RID, raw_text=PII_TEXT)

        setex = _clean_state
        assert setex.await_count == 1
        assert setex.await_args.args[0] == RedisKey.custom_idempotency(
            "desens", f"l1:{RID}"
        )

    async def test_same_run_is_idempotent_and_stable(self, monkeypatch):
        manager = MagicMock()
        manager.process_standardization_layer = AsyncMock(
            return_value=({"clinical_text": "已脱敏"}, {"modified": True})
        )
        monkeypatch.setattr(
            pipeline_module, "get_three_layer_desensitization_manager", lambda: manager
        )

        first = await _desensitize_input_text(request_id=RID, raw_text=PII_TEXT)
        second = await _desensitize_input_text(request_id=RID, raw_text=PII_TEXT)

        assert manager.process_standardization_layer.await_count == 1, (
            "同一次运行内同一层只应生效一次"
        )
        assert first == second == "已脱敏"

    async def test_failure_passes_through_but_logs(self, monkeypatch):
        manager = MagicMock()
        manager.process_standardization_layer = AsyncMock(
            side_effect=RuntimeError("engine down")
        )
        monkeypatch.setattr(
            pipeline_module, "get_three_layer_desensitization_manager", lambda: manager
        )
        fake_logger = MagicMock()
        monkeypatch.setattr(pipeline_module, "logger", fake_logger)

        masked = await _desensitize_input_text(request_id=RID, raw_text=PII_TEXT)

        assert masked == PII_TEXT
        events = [c.args[0] for c in fake_logger.error.call_args_list if c.args]
        assert "desens_l1_failed_passthrough" in events


class TestLayer2OutputDesensitization:
    async def test_masks_output_payload_before_persist(self):
        masked = await _desensitize_output_payload(request_id=RID, payload=PII_PAYLOAD)

        assert masked != PII_PAYLOAD
        assert "13812345678" not in str(masked)

    async def test_non_string_fields_survive(self):
        masked = await _desensitize_output_payload(request_id=RID, payload=PII_PAYLOAD)

        assert masked["organ"] == "thyroid"
        assert masked["score"] == pytest.approx(0.87)

    async def test_same_run_is_idempotent(self, monkeypatch):
        manager = MagicMock()
        manager.process_fusion_layer = AsyncMock(
            return_value=({"diagnosis": "已脱敏"}, {"modified": True})
        )
        monkeypatch.setattr(
            pipeline_module, "get_three_layer_desensitization_manager", lambda: manager
        )

        first = await _desensitize_output_payload(request_id=RID, payload=PII_PAYLOAD)
        second = await _desensitize_output_payload(request_id=RID, payload=PII_PAYLOAD)

        assert manager.process_fusion_layer.await_count == 1
        assert first == second

    async def test_failure_returns_original_payload(self, monkeypatch):
        manager = MagicMock()
        manager.process_fusion_layer = AsyncMock(side_effect=RuntimeError("engine down"))
        monkeypatch.setattr(
            pipeline_module, "get_three_layer_desensitization_manager", lambda: manager
        )
        fake_logger = MagicMock()
        monkeypatch.setattr(pipeline_module, "logger", fake_logger)

        masked = await _desensitize_output_payload(request_id=RID, payload=PII_PAYLOAD)

        assert masked == PII_PAYLOAD
        events = [c.args[0] for c in fake_logger.error.call_args_list if c.args]
        assert "desens_l2_failed_passthrough" in events


class TestLayer3ReusesExistingGatewayCheck:
    async def test_registers_result_without_new_gateway_call(self, monkeypatch):
        manager = MagicMock()
        manager.record_safety_gateway_existing_check = MagicMock(
            return_value={"layer": "safety_gateway"}
        )
        monkeypatch.setattr(
            pipeline_module, "get_three_layer_desensitization_manager", lambda: manager
        )
        check_output = AsyncMock()
        monkeypatch.setattr(
            pipeline_module.safety_gateway_client, "check_output", check_output
        )
        sg_result = {"passed": True, "degraded_mode": False}

        await _register_l3_existing_sg_check(
            request_id=RID, payload=PII_PAYLOAD, sg_result=sg_result
        )

        manager.record_safety_gateway_existing_check.assert_called_once_with(
            PII_PAYLOAD, sg_result
        )
        assert check_output.await_count == 0, "L3 不得再调用一次 SG"

    async def test_registration_is_idempotent(self, monkeypatch):
        manager = MagicMock()
        manager.record_safety_gateway_existing_check = MagicMock(return_value={})
        monkeypatch.setattr(
            pipeline_module, "get_three_layer_desensitization_manager", lambda: manager
        )

        await _register_l3_existing_sg_check(
            request_id=RID, payload=PII_PAYLOAD, sg_result={"passed": True}
        )
        await _register_l3_existing_sg_check(
            request_id=RID, payload=PII_PAYLOAD, sg_result={"passed": True}
        )

        assert manager.record_safety_gateway_existing_check.call_count == 1

    async def test_registration_failure_does_not_raise(self, monkeypatch):
        manager = MagicMock()
        manager.record_safety_gateway_existing_check = MagicMock(
            side_effect=RuntimeError("register failed")
        )
        monkeypatch.setattr(
            pipeline_module, "get_three_layer_desensitization_manager", lambda: manager
        )
        fake_logger = MagicMock()
        monkeypatch.setattr(pipeline_module, "logger", fake_logger)

        await _register_l3_existing_sg_check(
            request_id=RID, payload=PII_PAYLOAD, sg_result={"passed": True}
        )

        events = [c.args[0] for c in fake_logger.error.call_args_list if c.args]
        assert "desens_l3_register_failed" in events

    async def test_consent_none_is_fail_closed_contract(self):
        """管线依赖的既有契约：同意状态未知时第三层必须拒绝（fail-closed）。

        （管线自己在 ``_safe_safety_check`` 里已做"取不到同意状态即 failed"，
        此处钉住被复用的那一层本身也是 fail-closed 的。）
        """
        manager = ThreeLayerDesensitizationManager()

        with pytest.raises(ValueError, match="consent_status_at_input must not be None"):
            await manager.process_safety_gateway_layer(
                patient_id="P001", data={"a": "b"}, consent_status_at_input=None
            )


class TestCacheDoesNotLeakAcrossRuns:
    """回归守卫：同一 request_id 的**不同**载荷不得复用上一次的结果。

    这条缺陷由既有集成测试抓出（``tests/integration/int/test_routing_wiring.py``）：
    第一版缓存只按 ``request_id`` 存"脱敏后的结果"，于是同一 request_id 的第二次
    运行会拿到**上一次运行的载荷** —— L2 会把 A 次的输出当成 B 次的输出写库
    （实测症状：B 次的 findings 里出现了 A 次的 routing 标记）。修复办法是缓存
    存 ``(来源, 结果)`` 并**比对来源一致**才复用。
    """

    async def test_l2_does_not_reuse_stale_payload(self, monkeypatch):
        manager = MagicMock()
        manager.process_fusion_layer = AsyncMock(
            side_effect=lambda payload: ({"masked": True, **payload}, {"modified": True})
        )
        monkeypatch.setattr(
            pipeline_module, "get_three_layer_desensitization_manager", lambda: manager
        )
        first_payload = {"diagnosis": "A 的结论", "routing": {"tier": "standard"}}
        second_payload = {"diagnosis": "B 的结论"}

        await _desensitize_output_payload(request_id=RID, payload=first_payload)
        second = await _desensitize_output_payload(request_id=RID, payload=second_payload)

        assert manager.process_fusion_layer.await_count == 2, (
            "载荷不同必须重算，不得复用陈旧结果"
        )
        assert "routing" not in second
        assert second["diagnosis"] == "B 的结论"

    async def test_l1_does_not_reuse_stale_text(self, monkeypatch):
        manager = MagicMock()
        manager.process_standardization_layer = AsyncMock(
            side_effect=lambda data, session_id=None: (
                {"clinical_text": f"掩码:{data['clinical_text']}"},
                {"modified": True},
            )
        )
        monkeypatch.setattr(
            pipeline_module, "get_three_layer_desensitization_manager", lambda: manager
        )

        first = await _desensitize_input_text(request_id=RID, raw_text="原文甲")
        second = await _desensitize_input_text(request_id=RID, raw_text="原文乙")

        assert first == "掩码:原文甲"
        assert second == "掩码:原文乙"


class TestRunScopedCleanup:
    async def test_clear_run_resets_all_layer_caches(self):
        await _desensitize_input_text(request_id=RID, raw_text=PII_TEXT)
        await _desensitize_output_payload(request_id=RID, payload=PII_PAYLOAD)
        assert RID in pipeline_module._desens_l1_cache
        assert RID in pipeline_module._desens_l2_cache

        _clear_desens_run(RID)

        assert RID not in pipeline_module._desens_l1_cache
        assert RID not in pipeline_module._desens_l2_cache
        assert RID not in pipeline_module._desens_l3_done

    async def test_recorded_existing_check_matches_layer_semantics(self):
        """登记的第三层结果与 process_safety_gateway_layer 的语义字段一致。"""
        manager = ThreeLayerDesensitizationManager()

        result = manager.record_safety_gateway_existing_check(
            {"diagnosis": "原文"}, {"passed": True, "degraded_mode": True, "degraded_output": {"diagnosis": "降级文案"}}
        )

        assert result["layer"].value == "safety_gateway"
        assert result["degraded"] is True
        assert result["final"] == {"diagnosis": "降级文案"}
