"""T-GB-EDG-02 边缘端设备管理（注册心跳 + 健康探测 + 隔离恢复）。

规格（`开发计划.md` T-GB-EDG-02）：隔离 → 恢复全流程可用；
15s 设备注册（心跳周期）；1s 导航健康检查。

状态机：

    REGISTERED ──注册成功──> ONLINE
    ONLINE ──连续失败>=阈值──> ISOLATED（停止上行，本地只读/只做本地校验）
    ISOLATED ──探活成功──> RECOVERING（半开：继续探，仍不上行）
    RECOVERING ──再次成功──> ONLINE（恢复上行）
    ONLINE/… ──偶发失败──> DEGRADED（降级但不禁用）

隔离期的设计取舍：**宁可不上行，也不让脏数据进入后端**。导航是实时流，
隔离期间继续上行只会把过期帧喂给前端；本地仍保留 DICOM 采集与本地安全
校验能力，医生可继续操作设备，只是没有 AI 叠加。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional, Protocol

import structlog

from edge.device_auth import EdgeDeviceAuth

logger = structlog.get_logger(__name__)

DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 15.0
DEFAULT_HEALTH_CHECK_INTERVAL_SECONDS = 1.0
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_RECOVERY_PROBE_INTERVAL_SECONDS = 5.0


class DeviceState(str, Enum):
    """设备状态。"""

    REGISTERED = "registered"
    ONLINE = "online"
    DEGRADED = "degraded"
    ISOLATED = "isolated"
    RECOVERING = "recovering"


class DeviceTransport(Protocol):
    """与主服务通信的最小契约（可注入，便于测试）。"""

    async def post(self, path: str, payload: Dict[str, Any]) -> Any:
        ...


@dataclass
class DeviceHealth:
    """设备健康快照。"""

    state: DeviceState
    consecutive_failures: int
    last_error: Optional[str] = None
    isolated_reason: Optional[str] = None

    @property
    def should_uplink(self) -> bool:
        """是否允许向上行推送数据（隔离/恢复中一律不允许）。"""
        return self.state in (DeviceState.ONLINE, DeviceState.DEGRADED)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "isolated_reason": self.isolated_reason,
            "should_uplink": self.should_uplink,
        }


class EdgeDeviceManager:
    """边缘端设备生命周期与健康状态管理。"""

    def __init__(
        self,
        auth: EdgeDeviceAuth,
        transport: DeviceTransport,
        *,
        heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        health_check_interval_seconds: float = DEFAULT_HEALTH_CHECK_INTERVAL_SECONDS,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        recovery_probe_interval_seconds: float = DEFAULT_RECOVERY_PROBE_INTERVAL_SECONDS,
        clock: Callable[[], float] = None,
    ) -> None:
        import time as _time

        if failure_threshold < 1:
            raise ValueError("failure_threshold 至少为 1")

        self._auth = auth
        self._transport = transport
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.health_check_interval_seconds = health_check_interval_seconds
        self.recovery_probe_interval_seconds = recovery_probe_interval_seconds
        self._failure_threshold = failure_threshold
        self._clock = clock or _time.monotonic

        self._state = DeviceState.REGISTERED
        self._consecutive_failures = 0
        self._last_error: Optional[str] = None
        self._isolated_reason: Optional[str] = None
        self._last_heartbeat_at: Optional[float] = None
        self._last_health_check_at: Optional[float] = None

    # ---- 状态查询 ----
    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def health(self) -> DeviceHealth:
        return DeviceHealth(
            state=self._state,
            consecutive_failures=self._consecutive_failures,
            last_error=self._last_error,
            isolated_reason=self._isolated_reason,
        )

    @property
    def should_uplink(self) -> bool:
        return self.health.should_uplink

    @property
    def is_isolated(self) -> bool:
        return self._state == DeviceState.ISOLATED

    # ---- 注册与心跳 ----
    async def register(self) -> bool:
        """设备自通告（失败不影响本地可用，保持 REGISTERED 待重试）。

        供给语义（分配 device_id/api_key）属管理员的 POST /register；
        设备侧此处只做 check-in 声明上线（wiring-audit P0-4：原路径
        /register 与后端 DeviceRegisterRequest 契约错配，必 422/401/403）。
        """
        try:
            await self._transport.post(
                "/api/v1/admin/device/checkin",
                {"device_id": self._auth.device_id},
            )
        except Exception as e:
            self.record_failure(f"register_failed:{e}")
            return False
        self.record_success()
        logger.info("edge_device_registered", device_id=self._auth.device_id)
        return True

    async def send_heartbeat(self) -> bool:
        """发送一次心跳（按 15s 间隔由调用方节流，见 maybe_heartbeat）。"""
        try:
            await self._transport.post(
                "/api/v1/admin/device/heartbeat",
                {
                    "device_id": self._auth.device_id,
                    "state": self._state.value,
                },
            )
        except Exception as e:
            self.record_failure(f"heartbeat_failed:{e}")
            return False

        self._last_heartbeat_at = self._clock()
        self.record_success()
        return True

    async def maybe_heartbeat(self) -> bool:
        """按心跳周期决定是否真的要发（返回是否发送）。"""
        now = self._clock()
        if (
            self._last_heartbeat_at is not None
            and (now - self._last_heartbeat_at) < self.heartbeat_interval_seconds
        ):
            return False
        await self.send_heartbeat()
        return True

    # ---- 健康探测 ----
    async def health_probe(self) -> bool:
        """导航健康探测（1s 周期；隔离期用于判断能否恢复）。"""
        self._last_health_check_at = self._clock()
        try:
            await self._transport.post(
                "/api/v1/admin/device/health",
                {"device_id": self._auth.device_id},
            )
        except Exception as e:
            self.record_failure(f"health_probe_failed:{e}")
            return False
        self.record_success()
        return True

    # ---- 状态转移 ----
    def record_failure(self, reason: str) -> DeviceState:
        """记录一次失败；连续失败达阈值即隔离。"""
        self._consecutive_failures += 1
        self._last_error = reason

        if self._state == DeviceState.RECOVERING:
            # 半开态：探活失败立即回到隔离。恢复中的设备仍不允许上行，
            # 因此不能像 ONLINE 那样累积到阈值才隔离（等阈值等于把
            # 尚未确认健康的设备当成健康设备用）。
            self._state = DeviceState.ISOLATED
            self._isolated_reason = reason
            logger.warning(
                "edge_device_recovery_failed_reisolated",
                device_id=self._auth.device_id,
                reason=reason,
            )
            return self._state

        if self._consecutive_failures >= self._failure_threshold:
            if self._state != DeviceState.ISOLATED:
                self._state = DeviceState.ISOLATED
                self._isolated_reason = reason
                logger.error(
                    "edge_device_isolated",
                    device_id=self._auth.device_id,
                    consecutive_failures=self._consecutive_failures,
                    reason=reason,
                )
        else:
            self._state = DeviceState.DEGRADED

        return self._state

    def record_success(self) -> DeviceState:
        """记录一次成功；隔离态需连续成功才逐步恢复。"""
        previous = self._state
        self._consecutive_failures = 0
        self._last_error = None

        if previous == DeviceState.ISOLATED:
            # 半开：先进入 RECOVERING，仍不上行，等下一次成功再恢复
            self._state = DeviceState.RECOVERING
        elif previous == DeviceState.RECOVERING:
            self._state = DeviceState.ONLINE
            self._isolated_reason = None
        else:
            self._state = DeviceState.ONLINE
            self._isolated_reason = None

        if previous != self._state:
            logger.info(
                "edge_device_state_changed",
                device_id=self._auth.device_id,
                previous=previous.value,
                current=self._state.value,
            )
        return self._state

    def reset(self) -> None:
        """清空状态（设备侧重启/人工恢复后调用）。"""
        self._state = DeviceState.REGISTERED
        self._consecutive_failures = 0
        self._last_error = None
        self._isolated_reason = None
        self._last_heartbeat_at = None
        self._last_health_check_at = None


__all__ = [
    "DEFAULT_FAILURE_THRESHOLD",
    "DEFAULT_HEALTH_CHECK_INTERVAL_SECONDS",
    "DEFAULT_HEARTBEAT_INTERVAL_SECONDS",
    "DEFAULT_RECOVERY_PROBE_INTERVAL_SECONDS",
    "DeviceHealth",
    "DeviceState",
    "DeviceTransport",
    "EdgeDeviceManager",
]
