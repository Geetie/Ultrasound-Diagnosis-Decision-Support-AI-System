"""A.114 / G18：校准产物必须能回灌运行时（否则校准等于白算）。

缺口：`CUSUMCalibrator` 算出 `recommended_h` / `recommended_delta` 并落盘 + 写审计链，
但运行时 `create_cusum_monitor()` 只读 settings，`delta` 还是硬编码 0.005。

口径（本文件逐条钉住）：
1) 产物合法 → 采用（source=calibration，三个值全带过来）；
2) 产物缺失 → 回退 settings（source=settings）；
3) 产物非法（缺推荐值 / 非数值 / 非正 / target_rate 越界 / 坏 JSON）→ 回退并**留具体原因**
   （`calibration_invalid:<reason>`），不许静默；
4) 开关关闭 → 只按 settings；
5) 工厂函数真的用它（不是只有函数没人调）。
"""

from __future__ import annotations

import json

import pytest

from app.config.settings import settings
from app.observability.cusum import runtime_config
from app.observability.cusum.create import create_cusum_monitor


def _write(tmp_path, payload) -> str:
    p = tmp_path / "report.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


def test_valid_calibration_is_adopted(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "obs_cusum_use_calibration", True)
    path = _write(
        tmp_path,
        {"target_rate": 0.008, "recommended_h": 4.5, "recommended_delta": 0.02, "generated_at": "x"},
    )
    cfg, source = runtime_config.load_runtime_config(path)
    assert source == "calibration"
    assert (cfg.target_rate, cfg.decision_threshold, cfg.delta) == (0.008, 4.5, 0.02)


def test_missing_file_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "obs_cusum_use_calibration", True)
    cfg, source = runtime_config.load_runtime_config(str(tmp_path / "nope.json"))
    assert source == "settings"
    assert cfg.target_rate == settings.obs_cusum_target_rate
    assert cfg.decision_threshold == settings.obs_cusum_decision_threshold


def test_switch_off_ignores_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "obs_cusum_use_calibration", False)
    path = _write(tmp_path, {"target_rate": 0.008, "recommended_h": 4.5, "recommended_delta": 0.02})
    _, source = runtime_config.load_runtime_config(path)
    assert source == "settings"


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"target_rate": 0.005}, "no_recommendation"),
        ({"recommended_h": "x", "recommended_delta": 0.02}, "non_numeric"),
        ({"recommended_h": 0, "recommended_delta": 0.02}, "non_positive"),
        ({"recommended_h": 3, "recommended_delta": 0.02, "target_rate": 2.0}, "target_rate_out_of_range"),
    ],
)
def test_invalid_payload_reports_reason(tmp_path, monkeypatch, payload, reason):
    monkeypatch.setattr(settings, "obs_cusum_use_calibration", True)
    path = _write(tmp_path, payload)
    _, source = runtime_config.load_runtime_config(path)
    assert source == f"calibration_invalid:{reason}"


def test_broken_json_reports_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "obs_cusum_use_calibration", True)
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    _, source = runtime_config.load_runtime_config(str(p))
    assert source.startswith("calibration_invalid:unreadable:")


def test_factory_uses_calibration_and_records_source(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "obs_cusum_use_calibration", True)
    monkeypatch.setattr(
        settings, "obs_cusum_calibration_path", _write(
            tmp_path, {"target_rate": 0.01, "recommended_h": 5.0, "recommended_delta": 0.03}
        )
    )
    monitor = create_cusum_monitor()
    assert monitor._config.decision_threshold == 5.0
    assert monitor._config.delta == 0.03
    assert monitor._config_source == "calibration"


def test_calibrate_endpoint_saves_to_runtime_path_and_reports_adoption(monkeypatch, tmp_path):
    """端点必须存到运行时读的路径，并回告"下次启动采用什么参数"。"""
    import asyncio
    import inspect

    from app.api.v1 import alert_admin

    src = inspect.getsource(alert_admin.run_cusum_calibration)
    assert "save_calibration_report(report, path=path)" in src
    assert "load_runtime_config(path)" in src
    assert '"applies": "next_startup"' in src or "'applies': 'next_startup'" in src

    # 行为层面：把路径指到 tmp，真跑一遍（校准用极小仿真规模，避免慢）
    monkeypatch.setattr(settings, "obs_cusum_use_calibration", True)
    monkeypatch.setattr(settings, "obs_cusum_calibration_path", str(tmp_path / "r.json"))
    monkeypatch.setattr(settings, "obs_cusum_calibration_samples", 50)
    monkeypatch.setattr(settings, "obs_cusum_calibration_simulations", 20)

    class _U:
        pass

    out = asyncio.run(
        alert_admin.run_cusum_calibration(
            target_rate=0.005, daily_cases=10, simulation_days=5, current_user=_U()
        )
    )
    assert out["runtime"]["path"].endswith("r.json")
    assert out["runtime"]["applies"] == "next_startup"
    assert (tmp_path / "r.json").exists()
