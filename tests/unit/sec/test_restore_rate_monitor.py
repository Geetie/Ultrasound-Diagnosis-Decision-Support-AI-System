"""T-G1-SEC-02 脱敏还原频率监控测试。

覆盖：
- 阈值口径（compact 100 / full 200，含 settings 的档位校验行为）；
- 超限才告警，未超限不产生告警；
- 同一小时同档位只告警一次（防审计洪泛），下一小时可再次告警；
- 审计写入成功/失败两条路径（失败不抛异常）；
- 通知通道（默认日志 / webhook）与通知失败不影响业务；
- Redis 去重不可用时退化为进程内去重；
- 与 DesensitizationRestoreService 的接线：超限即触发告警。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.security.restore_rate_monitor import (
    AUDIT_ACTION,
    LoggingSecurityOfficerNotifier,
    RestoreRateMonitor,
    WebhookSecurityOfficerNotifier,
    get_restore_rate_monitor,
    reset_restore_rate_monitor,
)


class _RecordingNotifier:
    name = "recording"

    def __init__(self, ok: bool = True, raises: bool = False) -> None:
        self.ok = ok
        self.raises = raises
        self.alerts = []

    async def notify(self, alert) -> bool:
        if self.raises:
            raise RuntimeError("notify channel down")
        self.alerts.append(alert)
        return self.ok


@pytest.fixture(autouse=True)
def _reset_monitor_singleton():
    """模块单例跨用例残留时，会带着上一批的 notifier 与**已占位的去重集合**，
    使"首次必然告警"的断言时好时坏。逐条清掉，让每个用例从干净状态开始。"""
    reset_restore_rate_monitor()
    yield
    reset_restore_rate_monitor()


def _patch_audit(success: bool = True):
    """把审计写入替换为可控替身（monitor 内部用独立会话）。"""
    service = MagicMock()
    service.log_action = AsyncMock()
    return patch.multiple(
        "app.config.database",
        AsyncSessionLocal=_session_factory(),
    ), patch("app.services.audit_service.AuditService", MagicMock(return_value=service)), service


def _session_factory():
    db = MagicMock()
    db.commit = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=db)


class TestThresholds:
    def test_compact_and_full_limits_from_settings(self):
        from app.config.settings import Settings

        compact = Settings(deployment_profile="compact")
        full = Settings(deployment_profile="full")

        assert compact.desensitization_restore_rate_limit == 100
        assert compact.desensitization_restore_rate_limit_full == 200
        # full 档位使用更高的阈值（settings 校验器会把生效值提升为 full 限额）
        assert full.desensitization_restore_rate_limit == 200

    def test_monitor_exposes_thresholds(self):
        monitor = RestoreRateMonitor(profile="compact", hour_limit=100)
        thresholds = monitor.thresholds()
        assert thresholds["profile"] == "compact"
        assert thresholds["effective_hour_limit"] == 100
        assert thresholds["full_hour_limit"] == 200

    def test_is_breaching_is_strictly_greater(self):
        monitor = RestoreRateMonitor(hour_limit=100)
        assert monitor.is_breaching(100) is False
        assert monitor.is_breaching(101) is True


class TestAlerting:
    @pytest.mark.asyncio
    async def test_no_alert_below_limit(self):
        notifier = _RecordingNotifier()
        monitor = RestoreRateMonitor(hour_limit=100, notifier=notifier)

        assert await monitor.evaluate(count=100) is None
        assert notifier.alerts == []
        assert monitor.stats()["alerts_total"] == 0

    @pytest.mark.asyncio
    async def test_alert_on_exceed_with_audit_and_notify(self):
        notifier = _RecordingNotifier()
        monitor = RestoreRateMonitor(hour_limit=100, notifier=notifier)

        session_ctx, audit_patch, service = _patch_audit()
        with session_ctx, audit_patch:
            alert = await monitor.evaluate(
                count=150, hour_key="2026092410", actor="doc_1", session_id="sess_1"
            )

        assert alert is not None
        assert alert.count == 150 and alert.limit == 100
        assert alert.audit_logged is True
        assert alert.notified is True
        assert notifier.alerts and notifier.alerts[0].count == 150
        # 审计动作名与安全域一致
        assert service.log_action.await_args.kwargs["action"] == AUDIT_ACTION
        assert service.log_action.await_args.kwargs["log_type"] == "security"

    @pytest.mark.asyncio
    async def test_dedup_same_hour(self):
        notifier = _RecordingNotifier()
        monitor = RestoreRateMonitor(hour_limit=100, notifier=notifier)
        # hour_key 每次唯一：避免与同一 worker 更早的用例在去重存储里撞键。
        hour_key = f"{id(monitor) % 100000000:08d}"
        next_hour_key = f"{(id(monitor) + 1) % 100000000:08d}"

        session_ctx, audit_patch, _service = _patch_audit()
        # **必须显式切断 Redis 去重**（与下面 test_dedup_falls_back_to_memory 同一手法）。
        #
        # 真因（我先前判断错了两次，记在这里）：`_acquire_alert_slot` 优先走
        # `SET NX`，而同一 worker 里更早的用例可能留下 Redis 替身、或替身的 nx
        # 语义实现不正确 —— 那样"每次都拿到名额"，去重整体失效，`second` 不再是
        # None。表现就是**单文件跑 19 passed、全量跑必红**。
        # 唯一 hour_key 只挡住了"存储里撞键"这一种机制，挡不住替身这一种。
        with patch("app.config.redis.RedisPool") as pool, session_ctx, audit_patch:
            pool.get_client = AsyncMock(side_effect=RuntimeError("redis down"))
            first = await monitor.evaluate(count=120, hour_key=hour_key)
            second = await monitor.evaluate(count=180, hour_key=hour_key)
            next_hour = await monitor.evaluate(count=130, hour_key=next_hour_key)

        assert first is not None
        assert second is None, "同一小时重复超限不应重复告警（防审计洪泛）"
        assert next_hour is not None
        assert len(notifier.alerts) == 2

    @pytest.mark.asyncio
    async def test_dedup_falls_back_to_memory_without_redis(self):
        monitor = RestoreRateMonitor(hour_limit=100)

        with patch("app.config.redis.RedisPool") as pool:
            pool.get_client = AsyncMock(side_effect=RuntimeError("redis down"))
            first = await monitor.evaluate(count=101, hour_key="2026092413")
            second = await monitor.evaluate(count=101, hour_key="2026092413")

        assert first is not None and second is None

    @pytest.mark.asyncio
    async def test_audit_failure_does_not_raise(self):
        notifier = _RecordingNotifier()
        monitor = RestoreRateMonitor(hour_limit=100, notifier=notifier)

        with patch(
            "app.config.database.AsyncSessionLocal",
            MagicMock(side_effect=RuntimeError("db down")),
        ):
            alert = await monitor.evaluate(count=200, hour_key="2026092414")

        assert alert is not None
        assert alert.audit_logged is False
        assert alert.notified is True  # 审计失败仍要通知

    @pytest.mark.asyncio
    async def test_notifier_failure_does_not_raise(self):
        monitor = RestoreRateMonitor(
            hour_limit=100, notifier=_RecordingNotifier(raises=True)
        )
        session_ctx, audit_patch, _service = _patch_audit()
        with session_ctx, audit_patch:
            alert = await monitor.evaluate(count=101, hour_key="2026092415")

        assert alert is not None
        assert alert.notified is False

    @pytest.mark.asyncio
    async def test_notifier_returning_false_marked(self):
        monitor = RestoreRateMonitor(
            hour_limit=100, notifier=_RecordingNotifier(ok=False)
        )
        session_ctx, audit_patch, _service = _patch_audit()
        with session_ctx, audit_patch:
            alert = await monitor.evaluate(count=101, hour_key="2026092416")
        assert alert.notified is False

    @pytest.mark.asyncio
    async def test_webhook_notifier_posts_alert(self, monkeypatch):
        calls = []

        class _Response:
            status_code = 202

        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, json=None):
                calls.append((url, json))
                return _Response()

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", _Client)
        notifier = WebhookSecurityOfficerNotifier("https://soc.local/alert")
        monitor = RestoreRateMonitor(hour_limit=100, notifier=notifier)

        session_ctx, audit_patch, _service = _patch_audit()
        with session_ctx, audit_patch:
            alert = await monitor.evaluate(count=150, hour_key="2026092417")

        assert alert.notified is True
        assert calls and calls[0][0] == "https://soc.local/alert"
        assert calls[0][1]["type"] == AUDIT_ACTION

    @pytest.mark.asyncio
    async def test_webhook_notifier_requires_url(self):
        with pytest.raises(ValueError):
            WebhookSecurityOfficerNotifier("")

    @pytest.mark.asyncio
    async def test_default_notifier_is_logging(self, monkeypatch):
        from app.config.settings import settings

        monkeypatch.setattr(settings, "security_officer_webhook", "", raising=False)
        monitor = RestoreRateMonitor(hour_limit=100)
        assert isinstance(monitor._build_notifier(), LoggingSecurityOfficerNotifier)

    @pytest.mark.asyncio
    async def test_webhook_configured_by_settings(self, monkeypatch):
        from app.config.settings import settings

        monkeypatch.setattr(
            settings, "security_officer_webhook", "https://soc.local/x", raising=False
        )
        monitor = RestoreRateMonitor(hour_limit=100)
        assert isinstance(monitor._build_notifier(), WebhookSecurityOfficerNotifier)


class TestRestoreServiceWiring:
    @pytest.mark.asyncio
    async def test_exceeding_limit_warns_and_alerts(self):
        """超限处理必须"留痕 + 告警"（此前只有一条 warning 日志）。

        说明：conftest 会把 ``_check_rate_limit_persistent`` 整体打桩（测试期
        不希望限流生效），故这里直接覆盖从它抽出的 ``_handle_rate_limit_exceeded``。
        """
        from app.security.desensitization_restore import DesensitizationRestoreService

        service = DesensitizationRestoreService()
        warn_calls = []

        def _capture_warning(event, **kwargs):
            warn_calls.append((event, kwargs))

        with patch.object(
            service, "_raise_rate_limit_alert", AsyncMock()
        ) as alert, patch(
            "app.security.desensitization_restore.logger"
        ) as logger_mock:
            logger_mock.warning = MagicMock(side_effect=_capture_warning)
            await service._handle_rate_limit_exceeded(
                count=9999, limit=100, hour_key="2026092421"
            )

        assert any("rate_limit_exceeded" in event for event, _ in warn_calls)
        alert.assert_awaited_once()
        assert alert.await_args.kwargs == {
            "count": 9999,
            "limit": 100,
            "hour_key": "2026092421",
        }

    @pytest.mark.asyncio
    async def test_alert_dispatch_failure_is_contained(self):
        """告警链路自身异常不得影响"拒绝还原"这一安全动作。"""
        from app.security.desensitization_restore import DesensitizationRestoreService

        service = DesensitizationRestoreService()
        with patch(
            "app.security.restore_rate_monitor.get_restore_rate_monitor",
            MagicMock(side_effect=RuntimeError("monitor down")),
        ):
            await service._raise_rate_limit_alert(count=200, limit=100, hour_key="2026092419")

    @pytest.mark.asyncio
    async def test_monitor_evaluate_is_called_with_limit(self):
        """端到端接线：_raise_rate_limit_alert 会把计数交给监控单例。"""
        from app.security.desensitization_restore import DesensitizationRestoreService

        service = DesensitizationRestoreService()
        monitor = MagicMock()
        monitor.evaluate = AsyncMock()
        with patch(
            "app.security.restore_rate_monitor.get_restore_rate_monitor",
            MagicMock(return_value=monitor),
        ):
            await service._raise_rate_limit_alert(count=321, limit=100, hour_key="2026092420")

        monitor.evaluate.assert_awaited_once()
        assert monitor.evaluate.await_args.kwargs == {
            "count": 321,
            "limit": 100,
            "hour_key": "2026092420",
        }


class TestSingletonAndStats:
    def teardown_method(self):
        reset_restore_rate_monitor()

    def test_singleton(self):
        assert get_restore_rate_monitor() is get_restore_rate_monitor()

    @pytest.mark.asyncio
    async def test_stats_shape(self):
        notifier = _RecordingNotifier()
        monitor = RestoreRateMonitor(hour_limit=100, notifier=notifier)
        session_ctx, audit_patch, _service = _patch_audit()
        with session_ctx, audit_patch:
            await monitor.evaluate(count=120, hour_key="2026092418")

        stats = monitor.stats()
        assert stats["alerts_total"] == 1
        assert stats["last_alert"]["count"] == 120
        assert stats["notifier"] == "recording"
