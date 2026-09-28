"""可观测性导出链路的**接线**层。

## 为什么需要这个模块

2026-09-28 的接线审计发现：以下能力此前**只定义、从未接线**——

- `MetricsCollector.start_metrics_server()` 只被测试调用 → 运行期**没有** `/metrics` 监听，
  Prometheus 抓不到任何指标；
- `VictoriaMetricsPusher` 在 `app/` 内没有任何实例化点 → VictoriaMetrics 收不到推送。

后果是：**看板上的 p99 / 幻觉率 / 缓存等面板即使算法完全正确也永远没有数据**。
注意这与"p99 恒为 None"是**两个独立缺陷**：前者是算法错（已修），后者是链路没接。

把这些启动/停止动作集中到本模块，并写成**可单测的协程**（而不是散落在 `lifespan` 里），
这样"有没有接线"能被测试锁住（见 `tests/unit/obs/test_obs_export_wiring.py`），
而不是靠人肉巡检——本项目已经出现过多次"测试全绿但功能未生效"。
"""
from __future__ import annotations

import structlog

from app.config.settings import settings

__all__ = ["start_obs_export", "stop_obs_export"]

_logger = structlog.get_logger("app.observability.wiring")


async def start_obs_export(app) -> None:
    """启动指标暴露与推送。

    **逐环节 best-effort**：任一环失败只记日志，不抛出、不阻塞主服务启动
    （可观测性不是主服务的可用性前提）。因此调用方无需再包 try/except，
    但仍建议只做日志级别的兜底。
    """
    from app.observability.metrics_collector import MetricsCollector
    from app.observability.metrics_collector.pusher import VictoriaMetricsPusher

    collector = MetricsCollector.get_instance()
    app.state.metrics_collector = collector

    if settings.obs_metrics_server_enabled:
        try:
            collector.start_metrics_server()
            _logger.info("obs_metrics_server_started", port=settings.obs_metrics_port)
        except Exception as exc:
            _logger.warning(
                "obs_metrics_server_start_failed",
                port=settings.obs_metrics_port,
                error=str(exc),
                error_type=type(exc).__name__,
            )
    else:
        _logger.info("obs_metrics_server_disabled")

    pusher = None
    if settings.obs_vm_push_enabled and settings.obs_victoriametrics_url:
        try:
            pusher = VictoriaMetricsPusher()
            await pusher.start()
        except Exception as exc:
            _logger.warning(
                "obs_vm_pusher_start_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            pusher = None
    else:
        _logger.info(
            "obs_vm_push_skipped",
            enabled=settings.obs_vm_push_enabled,
            has_url=bool(settings.obs_victoriametrics_url),
        )
    app.state.vm_pusher = pusher


async def stop_obs_export(app) -> None:
    """停止推送器。

    指标 HTTP server 由进程退出回收，不做单独关闭（prometheus_client 未提供
    稳定的关闭接口，强行关闭会引入不必要的失败面）。
    """
    pusher = getattr(app.state, "vm_pusher", None)
    if pusher is not None:
        try:
            await pusher.stop()
        except Exception as exc:
            _logger.warning(
                "obs_vm_pusher_stop_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
    app.state.vm_pusher = None
