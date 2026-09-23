"""HSM 降级状态管理器。

负责 HSM 降级状态的检测、切换和行为控制。
从 HSMManager 中提取，实现单一职责原则。
"""

import threading
from typing import Optional, Dict, Any
from datetime import datetime, timezone, timedelta

import structlog

from app.security.hsm.enums import DegradedPhase, DegradedBehavior, DEGRADED_BEHAVIORS

logger = structlog.get_logger()


class DegradationManager:
    """HSM 降级状态管理器。

    管理 HSM 的降级状态，包括：
    - 降级状态的设置和清除
    - 降级阶段的自动计算（基于降级持续时间）
    - 操作权限检查

    降级阶段时间线：
    - NORMAL: 正常状态，所有操作允许
    - CACHED: 降级后 phase_1_threshold 内，大部分操作允许
    - RESTRICTED: 降级超过 phase_1_threshold，JWT签名禁止
    - EMERGENCY: 降级超过 phase_2_threshold，仅紧急签名允许

    Args:
        phase_1_threshold: 从降级到 RESTRICTED 阶段的时间阈值
        phase_2_threshold: 从降级到 EMERGENCY 阶段的时间阈值
    """

    def __init__(
        self,
        phase_1_threshold: timedelta = timedelta(minutes=5),
        phase_2_threshold: timedelta = timedelta(minutes=30),
    ) -> None:
        self._is_degraded: bool = False
        self._degraded_at: Optional[datetime] = None
        self._degraded_reason: Optional[str] = None
        self._lock = threading.RLock()
        self._phase_1_threshold = phase_1_threshold
        self._phase_2_threshold = phase_2_threshold

    @property
    def is_degraded(self) -> bool:
        """是否处于降级状态。"""
        with self._lock:
            return self._is_degraded

    @property
    def degraded_at(self) -> Optional[datetime]:
        """降级开始时间。"""
        with self._lock:
            return self._degraded_at

    @property
    def degraded_reason(self) -> Optional[str]:
        """降级原因。"""
        with self._lock:
            return self._degraded_reason

    def set_degraded(self, reason: str) -> None:
        """设置降级状态。

        Args:
            reason: 降级原因
        """
        with self._lock:
            if not self._is_degraded:
                self._is_degraded = True
                self._degraded_at = datetime.now(timezone.utc)
                self._degraded_reason = reason
                logger.error(
                    "hsm_entered_degraded_mode",
                    reason=reason,
                    degraded_at=self._degraded_at.isoformat()
                )

    def clear_degraded(self) -> None:
        """清除降级状态，恢复正常。"""
        with self._lock:
            if self._is_degraded:
                logger.info(
                    "hsm_exited_degraded_mode",
                    duration=(datetime.now(timezone.utc) - self._degraded_at).total_seconds() if self._degraded_at else 0
                )
                self._is_degraded = False
                self._degraded_at = None
                self._degraded_reason = None

    def get_degraded_phase(self) -> DegradedPhase:
        """获取当前降级阶段。

        根据降级持续时间自动计算：
        - 未降级: NORMAL
        - 降级 ≤ phase_1_threshold: CACHED
        - phase_1_threshold < 降级 ≤ phase_2_threshold: RESTRICTED
        - 降级 > phase_2_threshold: EMERGENCY

        Returns:
            DegradedPhase: 当前降级阶段
        """
        with self._lock:
            if not self._is_degraded or self._degraded_at is None:
                return DegradedPhase.NORMAL

            now = datetime.now(timezone.utc)
            time_since_degraded = now - self._degraded_at

            if time_since_degraded <= self._phase_1_threshold:
                return DegradedPhase.CACHED
            elif time_since_degraded <= self._phase_2_threshold:
                return DegradedPhase.RESTRICTED
            else:
                return DegradedPhase.EMERGENCY

    def check_degraded_behavior(self, operation: str) -> bool:
        """检查指定操作在当前降级阶段是否允许。

        Args:
            operation: 操作名称，可选值：
                - jwt_signing: JWT签名
                - data_encryption: 数据加密
                - desensitization_restore: 脱敏还原
                - emergency_signing: 紧急签名

        Returns:
            bool: 操作是否允许
        """
        phase = self.get_degraded_phase()
        behavior = DEGRADED_BEHAVIORS[phase]
        op_map = {
            "jwt_signing": behavior.allow_jwt_signing,
            "data_encryption": behavior.allow_data_encryption,
            "desensitization_restore": behavior.allow_desensitization_restore,
            "emergency_signing": behavior.allow_emergency_signing,
        }
        return op_map.get(operation, False)

    def get_degradation_info(self) -> Dict[str, Any]:
        """获取降级状态信息。

        Returns:
            dict: 包含降级时间和原因的字典
        """
        info: Dict[str, Any] = {}
        if self._degraded_at:
            info["degraded_at"] = self._degraded_at.isoformat()
        if self._degraded_reason:
            info["degraded_reason"] = self._degraded_reason
        return info

    def restore_state(self, is_degraded: bool, degraded_at: Optional[datetime],
                      degraded_reason: Optional[str]) -> None:
        """从持久化存储恢复降级状态。

        Args:
            is_degraded: 是否降级
            degraded_at: 降级开始时间
            degraded_reason: 降级原因
        """
        with self._lock:
            self._is_degraded = is_degraded
            self._degraded_at = degraded_at
            self._degraded_reason = degraded_reason
            if is_degraded:
                logger.warning(
                    "hsm_degraded_state_restored",
                    degraded_at=degraded_at,
                    reason=degraded_reason
                )
