"""边缘端服务配置（环境变量驱动）。

与主服务一样走"显式配置 + 启动期校验"：边缘端部署在设备旁，出问题现场
排查成本高，因此宁可启动时就报清楚缺什么，也不要跑到一半才失败。

环境变量前缀统一为 ``EDGE_``：

    EDGE_MAIN_SERVICE_URL   http://backend:8000
    EDGE_WS_URL             ws://backend:8000
    EDGE_DEVICE_ID          dev_edge_001
    EDGE_API_KEY            <注册时下发的 API Key>
    EDGE_AE_TITLE           ULTRASOUND_EDGE
    EDGE_DICOM_PORT         11112
    EDGE_ENGINE_PATH        /opt/models/nav.plan
    EDGE_MODEL_LABELS       organ_name,boundary,echogenicity
    EDGE_DOWNLOAD_DIR       /var/lib/ultrasound/ota
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple

DEFAULT_MODEL_LABELS: Tuple[str, ...] = (
    "organ_name",
    "anatomical_location",
    "echogenicity",
    "boundary",
    "morphology",
    "vascularity",
    "calcification",
)

DEFAULT_OTA_RATE_LIMIT_BPS = 1024 * 1024  # 2 GiB / 34 min ≈ 1.048 MiB/s


class EdgeConfigError(Exception):
    """边缘端配置缺失或非法。"""


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _as_float(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_labels(value: Any, default: Tuple[str, ...]) -> Tuple[str, ...]:
    if value is None or value == "":
        return default
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return tuple(part.strip() for part in str(value).split(",") if part.strip())


@dataclass(frozen=True)
class EdgeSettings:
    """边缘端运行配置。"""

    main_service_url: str = "http://localhost:8000"
    ws_url: str = "ws://localhost:8000"
    device_id: str = ""
    api_key: str = ""

    ae_title: str = "ULTRASOUND_EDGE"
    dicom_port: int = 11112
    enable_dicom_scp: bool = True
    enable_ota: bool = True

    engine_path: Optional[str] = None
    model_labels: Tuple[str, ...] = DEFAULT_MODEL_LABELS
    nav_target_fps: float = 15.0

    download_dir: str = "./edge_downloads"
    ota_rate_limit_bytes_per_second: int = DEFAULT_OTA_RATE_LIMIT_BPS

    heartbeat_interval_seconds: float = 15.0
    health_check_interval_seconds: float = 1.0
    failure_threshold: int = 3
    reconnect_interval_seconds: float = 5.0

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "EdgeSettings":
        """从环境变量构建配置（未知键忽略，非法数值回退默认并保留原值语义）。"""
        source: Mapping[str, str] = os.environ if env is None else env

        def get(name: str) -> Optional[str]:
            value = source.get(f"EDGE_{name}")
            return value if value is not None else None

        return cls(
            main_service_url=get("MAIN_SERVICE_URL") or "http://localhost:8000",
            ws_url=get("WS_URL") or "ws://localhost:8000",
            device_id=get("DEVICE_ID") or "",
            api_key=get("API_KEY") or "",
            ae_title=get("AE_TITLE") or "ULTRASOUND_EDGE",
            dicom_port=_as_int(get("DICOM_PORT"), 11112),
            enable_dicom_scp=_as_bool(get("ENABLE_DICOM_SCP"), True),
            enable_ota=_as_bool(get("ENABLE_OTA"), True),
            engine_path=get("ENGINE_PATH") or None,
            model_labels=_as_labels(get("MODEL_LABELS"), DEFAULT_MODEL_LABELS),
            nav_target_fps=_as_float(get("NAV_TARGET_FPS"), 15.0),
            download_dir=get("DOWNLOAD_DIR") or "./edge_downloads",
            ota_rate_limit_bytes_per_second=_as_int(
                get("OTA_RATE_LIMIT_BPS"), DEFAULT_OTA_RATE_LIMIT_BPS
            ),
            heartbeat_interval_seconds=_as_float(
                get("HEARTBEAT_INTERVAL_SECONDS"), 15.0
            ),
            health_check_interval_seconds=_as_float(
                get("HEALTH_CHECK_INTERVAL_SECONDS"), 1.0
            ),
            failure_threshold=_as_int(get("FAILURE_THRESHOLD"), 3),
            reconnect_interval_seconds=_as_float(
                get("RECONNECT_INTERVAL_SECONDS"), 5.0
            ),
        )

    def validate(self) -> None:
        """启动期校验：缺凭据/端口非法直接报错，不要带病运行。

        Raises:
            EdgeConfigError: 配置不满足启动条件。
        """
        problems = []
        if not self.device_id:
            problems.append("EDGE_DEVICE_ID 未配置（设备注册与认证必需）")
        if not self.api_key:
            problems.append("EDGE_API_KEY 未配置（设备认证必需）")
        if self.enable_dicom_scp and not (1 <= self.dicom_port <= 65535):
            problems.append(f"EDGE_DICOM_PORT 非法: {self.dicom_port}")
        if self.nav_target_fps <= 0:
            problems.append(f"EDGE_NAV_TARGET_FPS 必须为正数: {self.nav_target_fps}")
        if self.ota_rate_limit_bytes_per_second < 0:
            problems.append(
                "EDGE_OTA_RATE_LIMIT_BPS 不能为负: "
                f"{self.ota_rate_limit_bytes_per_second}"
            )
        if self.health_check_interval_seconds <= 0:
            problems.append(
                f"EDGE_HEALTH_CHECK_INTERVAL_SECONDS 必须为正数: "
                f"{self.health_check_interval_seconds}"
            )
        if problems:
            raise EdgeConfigError("边缘端配置校验失败: " + "; ".join(problems))

    def as_dict(self) -> dict:
        """脱敏后的配置快照（API Key 不落日志）。"""
        return {
            "main_service_url": self.main_service_url,
            "ws_url": self.ws_url,
            "device_id": self.device_id,
            "api_key_configured": bool(self.api_key),
            "ae_title": self.ae_title,
            "dicom_port": self.dicom_port,
            "enable_dicom_scp": self.enable_dicom_scp,
            "enable_ota": self.enable_ota,
            "engine_path": self.engine_path,
            "model_labels": list(self.model_labels),
            "nav_target_fps": self.nav_target_fps,
            "download_dir": self.download_dir,
            "ota_rate_limit_bytes_per_second": self.ota_rate_limit_bytes_per_second,
            "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
            "health_check_interval_seconds": self.health_check_interval_seconds,
            "failure_threshold": self.failure_threshold,
        }


__all__ = [
    "DEFAULT_MODEL_LABELS",
    "DEFAULT_OTA_RATE_LIMIT_BPS",
    "EdgeConfigError",
    "EdgeSettings",
]
