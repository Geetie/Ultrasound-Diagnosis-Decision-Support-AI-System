"""CUSUM 运行时参数：优先消费**已审计的校准产物**（A.114 / G18）。

## 缺口

校准器能算 ARL0/ARL1、产出 `recommended_h` / `recommended_delta` 并落盘 + 写审计链；
但运行时 `create_cusum_monitor()` 只读 `settings.obs_cusum_target_rate/decision_threshold`，
`delta` 还是 `CUSUMConfig` 里的硬编码 `0.005`。也就是说：**校准产出从不回灌运行时**
（G18），管理员调完参数、看过报告，线上仍按旧值跑。

## 设计（为什么不直接读 settings）

- 校准值是**一次仿真产出的数据**，不是手写配置；它应当以**产物**形式存在、可追溯
  （`save_calibration_report` 已写审计链），并由运行时在启动时读取。这条路径与
  Prometheus 的 recording rule / 服务端读取离线标定文件同构。
- 产物缺失或字段非法时**回退 settings**，并留痕具体原因（不静默）：
  `source=settings` 与 `source=calibration_invalid:<reason>` 必须可区分。
- 严格校验（`h>0`、`delta>0`、`0<target_rate<1`、`recommended_*` 非 None）：
  校准报告是外部输入，脏值直接进熔断阈值会改变"什么时候停 AI 辅助"这一安全行为。

## 开关

`settings.obs_cusum_use_calibration`（默认 **True**）：产物存在且合法就用它；
置 False 则完全按 settings（用于故障时快速回到手写值）。路径由
`settings.obs_cusum_calibration_path` 指定（相对仓库根）。
"""

from __future__ import annotations

import json
import os
from typing import Any

import structlog

from app.config.settings import settings

from .monitor import CUSUMConfig

_logger = structlog.get_logger("app.observability.cusum")

DEFAULT_CALIBRATION_PATH = "data/cusum_calibration_report.json"


def _resolve_path(path: str | None) -> str:
    return path or getattr(settings, "obs_cusum_calibration_path", DEFAULT_CALIBRATION_PATH)


def load_runtime_config(path: str | None = None) -> tuple[CUSUMConfig, str]:
    """返回 (config, source)。``source`` 用于日志与看板对账：

    * ``settings``：未启用校准 / 产物不存在 / 已关闭开关；
    * ``calibration``：产物合法且已采用；
    * ``calibration_invalid:<reason>``：产物存在但不可用（格式/字段/取值），已回退。
    """
    base_target = float(settings.obs_cusum_target_rate)
    base_h = float(settings.obs_cusum_decision_threshold)
    base = CUSUMConfig(target_rate=base_target, decision_threshold=base_h)

    if not bool(getattr(settings, "obs_cusum_use_calibration", True)):
        return base, "settings"

    resolved = _resolve_path(path)
    if not os.path.exists(resolved):
        return base, "settings"

    try:
        with open(resolved, encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:  # silent-ok: 原因已写进 source（unreadable:<异常名>），调用方与日志可区分
        return base, f"calibration_invalid:unreadable:{type(exc).__name__}"

    if not isinstance(report, dict):
        return base, "calibration_invalid:not_a_dict"

    h = report.get("recommended_h")
    delta = report.get("recommended_delta")
    target = report.get("target_rate", base_target)
    if h is None or delta is None:
        return base, "calibration_invalid:no_recommendation"
    try:
        h_f, delta_f, target_f = float(h), float(delta), float(target)
    except (TypeError, ValueError):  # silent-ok: 原因已写进 source（non_numeric），不是吞掉
        return base, "calibration_invalid:non_numeric"
    if h_f <= 0 or delta_f <= 0:
        return base, "calibration_invalid:non_positive"
    if not (0.0 < target_f < 1.0):
        return base, "calibration_invalid:target_rate_out_of_range"

    cfg = CUSUMConfig(
        target_rate=target_f,
        decision_threshold=h_f,
        delta=delta_f,
        overall_hallucination_warning_rate=base.overall_hallucination_warning_rate,
        overall_hallucination_critical_rate=base.overall_hallucination_critical_rate,
    )
    _logger.info(
        "cusum_runtime_config_from_calibration",
        path=resolved,
        target_rate=target_f,
        decision_threshold=h_f,
        delta=delta_f,
        generated_at=report.get("generated_at"),
    )
    return cfg, "calibration"
