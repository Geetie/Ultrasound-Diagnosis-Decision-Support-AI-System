"""T-G1-INT-06：在途诊断登记表。

登记表本身逻辑不复杂，但两个不变量必须守住：
1. 摘牌彻底——漏摘牌会让后续撤回去 cancel 一个已结束的任务；
2. 事件循环不一致时**跳过而不是硬 cancel**——宁可漏取消（安全网关还有强阻断
   兜底），也不要跨循环 cancel 造成难以定位的挂死（本项目已修过 3 次同类问题）。
"""
import asyncio
import threading
from unittest.mock import MagicMock

import pytest

from app.services.inflight_diagnosis import (
    InFlightDiagnosisRegistry,
    get_inflight_registry,
    reset_inflight_registry,
)


@pytest.fixture
def registry():
    return InFlightDiagnosisRegistry()


async def _run_forever():
    await asyncio.sleep(30)


class TestRegistration:
    async def test_register_captures_caller_task(self, registry):
        async def _diagnosis():
            registry.register(patient_id="pat_1", request_id="req_1")
            return registry.in_flight_request_ids()

        assert await asyncio.create_task(_diagnosis()) == ["req_1"]

    def test_register_outside_event_loop_is_noop(self, registry):
        """无线程内事件循环时静默跳过（如在纯同步的初始化代码里被调用）。

        放在子线程里跑：pytest-asyncio 的测试本体可能已有运行中的循环，直接
        在主线程断言"没有循环"是环境依赖的。
        """
        result: dict = {}

        def _worker():
            registry.register(patient_id="pat_1", request_id="req_1")
            result["ids"] = registry.in_flight_request_ids()

        thread = threading.Thread(target=_worker)
        thread.start()
        thread.join()

        assert result["ids"] == []

    async def test_unregister_removes_patient_index(self, registry):
        async def _diagnosis():
            registry.register(patient_id="pat_1", request_id="req_1")
            registry.unregister("req_1")
            return registry.cancel_for_patient("pat_1")

        assert await asyncio.create_task(_diagnosis()) == []

    async def test_unregister_unknown_id_is_noop(self, registry):
        registry.unregister("never_registered")

        assert registry.in_flight_count() == 0

    async def test_multiple_requests_for_same_patient(self, registry):
        async def _diagnosis(request_id):
            registry.register(patient_id="pat_1", request_id=request_id)
            await asyncio.sleep(30)

        tasks = [
            asyncio.create_task(_diagnosis("req_1")),
            asyncio.create_task(_diagnosis("req_2")),
        ]
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        cancelled = registry.cancel_for_patient("pat_1")

        assert cancelled == ["req_1", "req_2"]
        for task in tasks:
            with pytest.raises(asyncio.CancelledError):
                await task


class TestCancellation:
    async def test_unknown_patient_returns_empty(self, registry):
        assert registry.cancel_for_patient("nobody") == []

    async def test_done_task_is_cleaned_not_cancelled(self, registry):
        async def _diagnosis():
            registry.register(patient_id="pat_1", request_id="req_1")

        task = asyncio.create_task(_diagnosis())
        await task

        cancelled = registry.cancel_for_patient("pat_1")

        assert cancelled == []
        assert registry.in_flight_count() == 0

    async def test_loop_mismatch_is_skipped_with_warning(self, registry, caplog):
        """登记在别的循环上的任务不得跨循环 cancel。"""
        from app.services.inflight_diagnosis import _Entry

        foreign_task = MagicMock()
        foreign_task.done.return_value = False
        registry._entries["req_1"] = _Entry(
            request_id="req_1",
            patient_id="pat_1",
            task=foreign_task,
            loop=object(),  # 与当前运行中的 loop 必然不同
        )
        registry._by_patient.setdefault("pat_1", set()).add("req_1")

        cancelled = registry.cancel_for_patient("pat_1")

        assert cancelled == []
        foreign_task.cancel.assert_not_called()

    async def test_reset_clears_everything(self, registry):
        async def _diagnosis():
            registry.register(patient_id="pat_1", request_id="req_1")
            await asyncio.sleep(30)

        task = asyncio.create_task(_diagnosis())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        registry.reset()

        assert registry.in_flight_count() == 0


class TestModuleSingleton:
    def test_get_returns_same_instance(self):
        assert get_inflight_registry() is get_inflight_registry()

    def test_reset_helper_clears_singleton(self):
        registry = get_inflight_registry()
        registry._entries["x"] = None  # type: ignore[assignment]

        reset_inflight_registry()

        assert registry.in_flight_request_ids() == []
