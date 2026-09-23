"""HSM SoftHSM 管理器。

负责 SoftHSM 的生命周期管理，包括启用时间跟踪、过期检查和定期巡检。
从 HSMManager 中提取，实现单一职责原则。
"""

import os
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta

import structlog

from app.security.hsm.task_scheduler import TaskScheduler

logger = structlog.get_logger()


class SoftHSMManager:
    """SoftHSM 生命周期管理器。

    管理 SoftHSM 的启用状态、过期检查和定期巡检。

    SoftHSM 是硬件安全模块的软件模拟，有过期时间限制：
    - EXPIRY_DAYS (90天): 超过后禁止签名操作
    - CRITICAL_DAYS (120天): 超过后记录 CRITICAL 级别告警

    Args:
        task_scheduler: 任务调度器，用于定期巡检
    """

    SOFT_HSM_EXPIRY_DAYS = 90
    SOFT_HSM_CRITICAL_DAYS = 120

    def __init__(self, task_scheduler: Optional[TaskScheduler] = None) -> None:
        self._use_soft_hsm: bool = os.environ.get("USE_SOFT_HSM", "false").lower() == "true"
        self._soft_hsm_enabled_at: Optional[datetime] = None
        self._task_scheduler = task_scheduler

        self._load_timestamp()

        if self._use_soft_hsm:
            self._check_expiry()

    @property
    def is_enabled(self) -> bool:
        """是否启用 SoftHSM 模式。"""
        return self._use_soft_hsm

    @property
    def enabled_at(self) -> Optional[datetime]:
        """SoftHSM 启用时间。"""
        return self._soft_hsm_enabled_at

    def is_expired(self) -> bool:
        """检查 SoftHSM 是否已过期。

        Returns:
            bool: 如果已过期返回 True
        """
        return self._check_expiry()

    def start_periodic_check(self) -> None:
        """启动 SoftHSM 定期巡检。

        每24小时检查一次 SoftHSM 过期状态。
        需要 TaskScheduler 支持周期性任务调度。
        """
        if not self._use_soft_hsm or self._task_scheduler is None:
            return

        interval_hours = 24
        interval_seconds = interval_hours * 3600

        self._task_scheduler.schedule_periodic_task(
            task=self._periodic_check,
            interval_seconds=interval_seconds,
            task_name="soft_hsm_expiry_check",
        )

    def get_status(self) -> Dict[str, Any]:
        """获取 SoftHSM 状态信息。

        Returns:
            dict: 包含启用状态、运行天数、过期状态和过期级别的字典
        """
        if not self._use_soft_hsm or self._soft_hsm_enabled_at is None:
            return {
                "is_soft_hsm": self._use_soft_hsm,
                "days_running": 0,
                "is_expired": False,
                "expiry_level": "none",
            }

        now = datetime.now(timezone.utc)
        time_since_enabled = now - self._soft_hsm_enabled_at
        is_expired = time_since_enabled > timedelta(days=self.SOFT_HSM_EXPIRY_DAYS)
        days_running = time_since_enabled.days

        if days_running > self.SOFT_HSM_CRITICAL_DAYS:
            expiry_level = "critical"
        elif is_expired:
            expiry_level = "warning"
        elif days_running > self.SOFT_HSM_EXPIRY_DAYS - 7:
            expiry_level = "approaching"
        else:
            expiry_level = "none"

        return {
            "is_soft_hsm": True,
            "days_running": days_running,
            "is_expired": is_expired,
            "expiry_level": expiry_level,
            "enabled_at": self._soft_hsm_enabled_at.isoformat(),
        }

    def _periodic_check(self) -> None:
        """定期巡检回调。"""
        if not self._use_soft_hsm:
            return
        status = self.get_status()
        if status["is_expired"]:
            if status["days_running"] > self.SOFT_HSM_CRITICAL_DAYS:
                logger.critical(
                    "soft_hsm_critical_overdue",
                    days_running=status["days_running"],
                    days_over_critical=status["days_running"] - self.SOFT_HSM_CRITICAL_DAYS,
                )
            else:
                logger.error(
                    "soft_hsm_periodic_expiry_warning",
                    days_running=status["days_running"],
                )

    def _check_expiry(self) -> bool:
        """检查 SoftHSM 是否过期。

        Returns:
            bool: 如果过期返回 True
        """
        if self._soft_hsm_enabled_at is None:
            return False

        expiry_threshold = timedelta(days=self.SOFT_HSM_EXPIRY_DAYS)
        now = datetime.now(timezone.utc)
        time_since_enabled = now - self._soft_hsm_enabled_at

        if time_since_enabled > expiry_threshold:
            days_overdue = (time_since_enabled - expiry_threshold).days
            logger.error(
                "soft_hsm_expired",
                days_overdue=days_overdue,
                enabled_at=self._soft_hsm_enabled_at.isoformat()
            )
            return True

        days_remaining = (expiry_threshold - time_since_enabled).days
        if days_remaining <= 7:
            logger.warning(
                "soft_hsm_approaching_expiry",
                days_remaining=days_remaining
            )

        return False

    def _load_timestamp(self) -> None:
        """从文件加载 SoftHSM 启用时间戳。"""
        timestamp_path = os.environ.get("SOFT_HSM_TIMESTAMP_PATH", "soft_hsm_enabled_at.txt")
        if os.path.exists(timestamp_path):
            try:
                with open(timestamp_path, "r") as f:
                    timestamp_str = f.read().strip()
                    self._soft_hsm_enabled_at = datetime.fromisoformat(timestamp_str)
                    logger.info("soft_hsm_timestamp_loaded", enabled_at=self._soft_hsm_enabled_at)
            except Exception as e:
                logger.error("failed_to_load_soft_hsm_timestamp", error=str(e))
        else:
            if self._use_soft_hsm:
                self._soft_hsm_enabled_at = datetime.now(timezone.utc)
                self._save_timestamp()

    def _save_timestamp(self) -> None:
        """将 SoftHSM 启用时间戳保存到文件。"""
        timestamp_path = os.environ.get("SOFT_HSM_TIMESTAMP_PATH", "soft_hsm_enabled_at.txt")
        try:
            with open(timestamp_path, "w") as f:
                f.write(self._soft_hsm_enabled_at.isoformat())
            logger.info("soft_hsm_timestamp_saved", enabled_at=self._soft_hsm_enabled_at)
        except Exception as e:
            logger.error("failed_to_save_soft_hsm_timestamp", error=str(e))
