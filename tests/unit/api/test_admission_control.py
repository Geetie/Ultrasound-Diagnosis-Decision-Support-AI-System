"""守卫（第二梯队② 生产容量验收，2026-10-12）：诊断准入控制 + 入队失败不再静默。

背景（16 号 A.53）：worker 池只有 ``int_worker_pool_size`` 个并发位，此前**无准入判断**，
队列可涨到 1000 才拒 —— 50 并发压测时结果接口集体超时（排队等待被算进 30s 结果超时），
排队者互相拖累、已入队者 P99 一起爆。本组测试钉住两条：

1. **准入闸门**：队列深度 ≥ 上限 → 提交前就 503 + ``Retry-After``（不落 processing 行）；
   探测不到深度 / 开关关闭 → 放行（不能因为 Redis 抖动把系统打死）。
2. **入队失败显式失败**：`enqueue_diagnosis` 返回 False 时，请求行必须被标记 failed
   （不留僵尸 processing 行），并对外抛 503 而不是返回 200。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.errors import DiagnosisQueueFullException, SystemQueueFullException
from app.config.settings import settings
from app.services import admission_control as ac


class TestAdmissionGate:
    @pytest.mark.asyncio
    async def test_below_limit_passes_and_returns_depth(self):
        with patch.object(ac, "current_pending_depth", AsyncMock(return_value=3)):
            depth = await ac.check_diagnosis_admission()
        assert depth == 3

    @pytest.mark.asyncio
    async def test_at_limit_rejects_with_retry_after(self):
        limit = settings.diagnosis_admission_queue_depth_limit
        with (
            patch.object(ac, "current_pending_depth", AsyncMock(return_value=limit)),
            pytest.raises(SystemQueueFullException) as exc,
        ):
            await ac.check_diagnosis_admission()
        err = exc.value
        assert err.status_code == 503
        # Retry-After 必须存在（前端据此排程重试，而不是立刻再撞一次）
        assert err.headers.get("Retry-After")
        # 报错要说清"现在多深、上限多少"——医生/运维据此判断系统确实忙
        assert str(limit) in err.error_message

    @pytest.mark.asyncio
    async def test_probe_unavailable_does_not_block(self):
        """Redis 抖动探测不到深度 → 放行（另有入队侧长度上限兜底，不能把系统打死）。"""
        with patch.object(ac, "current_pending_depth", AsyncMock(return_value=None)):
            assert await ac.check_diagnosis_admission() is None

    @pytest.mark.asyncio
    async def test_disabled_passes(self):
        with patch.object(settings, "diagnosis_admission_enabled", False):
            assert await ac.check_diagnosis_admission() is None

    def test_wait_seconds_monotonic_and_clamped(self):
        w_small = ac.admission_wait_seconds(1)
        w_big = ac.admission_wait_seconds(500)
        assert w_small <= w_big
        assert w_small >= settings.diagnosis_admission_retry_after_s  # 下限
        assert w_big <= settings.diagnosis_admission_retry_after_cap_s  # 上限


class TestEnqueueFailureIsExplicit:
    """入队失败 → 标记 failed + 503（不再 200 静默）。"""

    @pytest.mark.asyncio
    async def test_enqueue_false_marks_failed_and_raises_503(self):
        from app.api.v1 import diagnosis as diag_mod

        repo = MagicMock()
        repo.db = MagicMock()
        repo.db.commit = AsyncMock()
        repo.update_result = AsyncMock()
        repo.create_request = AsyncMock(return_value=MagicMock())

        request = MagicMock()
        request.session_id = "sess-x"
        request.idempotency_key = None
        request.retry = False
        request.retry_strategy = None
        request.target_organ = "thyroid"
        request.response_tier = MagicMock()
        request.clinical_text = ""
        request.patient_context = None
        request.image_ids = []
        request._magic_mock_child = None
        request.agent_type = None
        request.routing_decision = None

        session = MagicMock()
        session.patient_id = "p-1"
        session_repo = MagicMock()
        session_repo.get_session = AsyncMock(return_value=session)
        consent_repo = MagicMock()
        consent_repo.get_latest_consent = AsyncMock(return_value=MagicMock(status="granted"))
        fastapi_request = MagicMock()
        fastapi_request.client = MagicMock(host="127.0.0.1")
        fastapi_request.headers = {}

        with (
            patch.object(diag_mod, "check_rate_limit", AsyncMock(return_value=None)),
            patch.object(diag_mod, "check_consent_status", MagicMock()),
            patch.object(diag_mod, "check_diagnosis_admission", AsyncMock(return_value=0)),
            patch.object(diag_mod, "try_renew_session", AsyncMock()),
            patch.object(diag_mod, "enqueue_diagnosis", AsyncMock(return_value=False)),
            patch.object(diag_mod, "_mark_request_failed", AsyncMock()) as mark,
            pytest.raises(DiagnosisQueueFullException) as exc,
        ):
            await diag_mod.submit_diagnosis_request(
                request=request,
                fastapi_request=fastapi_request,
                diagnosis_repo=repo,
                consent_repo=consent_repo,
                session_repo=session_repo,
                audit_service=MagicMock(),
                current_user={"username": "t"},
            )

        assert exc.value.status_code == 503
        assert exc.value.headers.get("Retry-After")
        mark.assert_awaited()  # 僵尸 processing 行必须被标失败
