"""守卫：指标服务器必须绑定到**配置的地址**，默认只绑回环。

## 背景（2026-09-29 真机暴露）

`prometheus_client.start_http_server(port, registry=...)` 不传 `addr` 时默认 `0.0.0.0`。
共享 GPU 服务器上实测 `ss -ltn` 看到：

    LISTEN 0 5 0.0.0.0:9090 0.0.0.0:*

其余端口（18000 API / 9001 SG / 8001-8005 模型服务）都规规矩矩绑在 `127.0.0.1`，
只有它把 `/metrics` 暴露给整个校园网 —— 与"端口只绑回环、访问一律走 ssh -L"的红线冲突。

修法是"可配置 + 安全默认"：新增 `OBS_METRICS_HOST`（默认 `127.0.0.1`）。
需要跨主机抓取时显式设 `0.0.0.0`（仓库内当前编排**没有**这种用法）。
"""
from __future__ import annotations

import pathlib
from unittest.mock import patch

from app.config.settings import settings
from app.observability.metrics_collector.collector import MetricsCollector

COLLECTOR_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "app"
    / "observability"
    / "metrics_collector"
    / "collector.py"
)
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class TestMetricsServerBinding:
    def test_默认只绑回环(self):
        assert settings.obs_metrics_host in LOOPBACK, (
            "默认绑 0.0.0.0 会把 /metrics 暴露到整个网络（真机实测过这条）"
        )

    def test_启动时把配置地址传给prometheus(self):
        collector = MetricsCollector.get_instance()
        collector._server_started = False  # 允许再次进入启动分支

        with patch(
            "app.observability.metrics_collector.collector.start_http_server"
        ) as fake_start:
            collector.start_metrics_server(port=9999)

        assert fake_start.call_count == 1
        kwargs = fake_start.call_args.kwargs
        assert kwargs.get("addr") == settings.obs_metrics_host, (
            "不传 addr 时 prometheus_client 默认 0.0.0.0，必须显式传配置值"
        )
        assert kwargs["addr"] in LOOPBACK
        assert fake_start.call_args.args[0] == 9999

    def test_已启动则不重复起监听(self):
        collector = MetricsCollector.get_instance()
        collector._server_started = True

        with patch(
            "app.observability.metrics_collector.collector.start_http_server"
        ) as fake_start:
            collector.start_metrics_server(port=9999)

        assert fake_start.call_count == 0


class TestNoWideBindRegression:
    def test_不得回退成不传addr的写法(self):
        text = COLLECTOR_PATH.read_text(encoding="utf-8")

        assert "start_http_server(p, addr=" in text
        assert "start_http_server(p, registry=" not in text, (
            "不传 addr 的写法会退回 0.0.0.0（/metrics 暴露到整个网络）"
        )
