"""diagnosis_pipeline 三层脱敏四函数分支覆盖（④）。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.track_inf]

import app.services.diagnosis_pipeline as mod
from app.services.diagnosis_pipeline import (
    _clear_desens_run,
    _desensitize_input_text,
    _desensitize_output_payload,
    _mark_desens_audit,
    _register_l3_existing_sg_check,
)


def _manager(process_std=None, process_fusion=None, record=None):
    m = MagicMock()
    m.process_standardization_layer = AsyncMock(return_value=process_std or ({"clinical_text": "masked"}, {"modified": True}))
    m.process_fusion_layer = AsyncMock(return_value=process_fusion or ({"findings": "masked"}, {"modified": True}))
    m.record_safety_gateway_existing_check = record or MagicMock()
    return m


@pytest.fixture(autouse=True)
def _audit_ok(monkeypatch):
    monkeypatch.setattr(
        mod.RedisPool, "setex", AsyncMock(), raising=False
    )


@pytest.mark.anyio
class TestMarkDesensAudit:
    async def test_ok_and_redis_failure(self, monkeypatch):
        await _mark_desens_audit("r-audit", "l1")  # applied

        async def boom(*a, **kw):
            raise ConnectionError("redis")

        monkeypatch.setattr(mod.RedisPool, "setex", boom, raising=False)
        await _mark_desens_audit("r-audit", "l1", degraded=True)  # 吞异常


@pytest.mark.anyio
class TestL1InputText:
    async def test_applies_and_caches(self, monkeypatch):
        _clear_desens_run("r1")
        mgr = _manager()
        monkeypatch.setattr(
            mod, "get_three_layer_desensitization_manager", lambda: mgr
        )
        out = await _desensitize_input_text(
            request_id="r1", raw_text="患者张三", session_id="s1"
        )
        assert out == "masked"
        mgr.process_standardization_layer.assert_awaited_once()
        # 命中缓存：第二次不再调用引擎
        out2 = await _desensitize_input_text(request_id="r1", raw_text="患者张三")
        assert out2 == "masked"
        assert mgr.process_standardization_layer.await_count == 1

    async def test_engine_failure_passthrough(self, monkeypatch):
        _clear_desens_run("r2")
        mgr = _manager()
        mgr.process_standardization_layer = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(
            mod, "get_three_layer_desensitization_manager", lambda: mgr
        )
        out = await _desensitize_input_text(request_id="r2", raw_text="原文")
        assert out == "原文"  # 失败安全：原文透传

    async def test_audit_failure_does_not_break_apply(self, monkeypatch):
        _clear_desens_run("r3")
        mgr = _manager()
        monkeypatch.setattr(
            mod, "get_three_layer_desensitization_manager", lambda: mgr
        )

        async def boom(*a, **kw):
            raise ConnectionError("redis")

        monkeypatch.setattr(mod.RedisPool, "setex", boom, raising=False)
        out = await _desensitize_input_text(request_id="r3", raw_text="患者李四")
        assert out == "masked"


@pytest.mark.anyio
class TestL2OutputPayload:
    async def test_applies_and_caches(self, monkeypatch):
        _clear_desens_run("r4")
        mgr = _manager()
        monkeypatch.setattr(
            mod, "get_three_layer_desensitization_manager", lambda: mgr
        )
        payload = {"findings": "报告"}
        out = await _desensitize_output_payload(request_id="r4", payload=payload)
        assert out == {"findings": "masked"}
        out2 = await _desensitize_output_payload(request_id="r4", payload=payload)
        assert out2 == {"findings": "masked"}
        assert mgr.process_fusion_layer.await_count == 1

    async def test_engine_failure_passthrough(self, monkeypatch):
        _clear_desens_run("r5")
        mgr = _manager()
        mgr.process_fusion_layer = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(
            mod, "get_three_layer_desensitization_manager", lambda: mgr
        )
        payload = {"findings": "原样"}
        out = await _desensitize_output_payload(request_id="r5", payload=payload)
        assert out == payload


@pytest.mark.anyio
class TestL3Register:
    async def test_registers_once(self, monkeypatch):
        _clear_desens_run("r6")
        record = MagicMock()
        mgr = _manager(record=record)
        monkeypatch.setattr(
            mod, "get_three_layer_desensitization_manager", lambda: mgr
        )
        await _register_l3_existing_sg_check(
            request_id="r6", payload={"x": 1}, sg_result={"ok": True}
        )
        record.assert_called_once()
        # 幂等：第二次不再登记
        await _register_l3_existing_sg_check(
            request_id="r6", payload={"x": 1}, sg_result={"ok": True}
        )
        record.assert_called_once()

    async def test_register_failure_swallowed(self, monkeypatch):
        _clear_desens_run("r7")
        record = MagicMock(side_effect=RuntimeError("boom"))
        mgr = _manager(record=record)
        monkeypatch.setattr(
            mod, "get_three_layer_desensitization_manager", lambda: mgr
        )
        await _register_l3_existing_sg_check(
            request_id="r7", payload={}, sg_result={}
        )  # 不应抛
