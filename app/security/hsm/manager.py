"""HSM 管理器（门面模式）。

重构后的 HSMManager 作为门面协调器，组合各专职子管理器，
对外保持与原 HSMManager 完全兼容的公共 API。

架构：
    HSMManager (Facade)
    ├── DegradationManager   - 降级状态管理
    ├── TaskScheduler        - 定时任务调度
    ├── SoftHSMManager       - SoftHSM 生命周期
    ├── PersistenceManager   - Redis 持久化
    ├── KeyManager           - 密钥生命周期
    └── EncryptionService    - 加密操作
"""

import os
import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone, timedelta

import structlog

from app.security.hsm.enums import (
    DegradedPhase, KeySlot, DegradedBehavior,
    DEGRADED_BEHAVIORS, get_key_slot,
)
from app.security.hsm.degradation_manager import DegradationManager
from app.security.hsm.task_scheduler import TaskScheduler
from app.security.hsm.soft_hsm_manager import SoftHSMManager
from app.security.hsm.persistence_manager import PersistenceManager
from app.security.hsm.key_manager import KeyManager
from app.security.hsm.encryption_service import EncryptionService

logger = structlog.get_logger()


class HSMManager:
    """HSM 管理器（门面模式）。

    作为系统安全模块的核心入口，组合各专职子管理器，
    提供与原 HSMManager 完全兼容的公共 API。

    职责：
    - 协调各子管理器的初始化和生命周期
    - 委托公共 API 调用到对应的子管理器
    - 维护 HSM 可用性检测

    注意：本类仅作为协调层，不包含业务逻辑。
    所有具体实现都委托给对应的子管理器。
    """

    # ---- 向后兼容类常量（委托给子管理器） ----
    SOFT_HSM_EXPIRY_DAYS = SoftHSMManager.SOFT_HSM_EXPIRY_DAYS
    SOFT_HSM_CRITICAL_DAYS = SoftHSMManager.SOFT_HSM_CRITICAL_DAYS
    KEY_CLEANUP_INTERVAL_HOURS = KeyManager.KEY_CLEANUP_INTERVAL_HOURS

    # 子管理器属性名（供 __getattr__ 惰性补齐）
    _SUBMANAGER_NAMES = frozenset({
        "_persistence_manager",
        "_degradation_manager",
        "_task_scheduler",
        "_soft_hsm_manager",
        "_key_manager",
        "_encryption_service",
    })

    def _init_submanagers(self) -> None:
        """构造子管理器（幂等）。

        与 ``__init__`` 解耦：``__new__`` 构造的轻量实例（测试常用
        ``HSMManager.__new__(HSMManager)`` 绕过 ``__init__`` 后直接读写
        扁平属性）在首次访问子管理器时经 ``__getattr__`` 延迟补齐。
        本方法不触碰 Redis、不启动定时任务。
        """
        if "_persistence_manager" in self.__dict__:
            return
        self._persistence_manager = PersistenceManager()
        self._degradation_manager = DegradationManager()
        self._task_scheduler = TaskScheduler()
        self._soft_hsm_manager = SoftHSMManager(task_scheduler=self._task_scheduler)
        self._key_manager = KeyManager(self._persistence_manager)
        self._encryption_service = EncryptionService(
            key_manager=self._key_manager,
            degradation_manager=self._degradation_manager,
            soft_hsm_manager=self._soft_hsm_manager,
        )

    def __getattr__(self, name: str):
        """惰性补齐子管理器，保持与原模块一致的属性访问契约。

        重构后``_key_slots`` / ``_is_degraded`` / ``_use_soft_hsm`` 等
        都变成了委托子管理器的 property；若实例未经 ``__init__``
        （``__new__`` 构造），子管理器缺失会导致 AttributeError。
        """
        if name in HSMManager._SUBMANAGER_NAMES:
            self._init_submanagers()
            return self.__dict__[name]
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}"
        )

    def __init__(self) -> None:
        # ---- 初始化子管理器 ----
        self._init_submanagers()

        # ---- HSM 可用性检测 ----
        self._hsm_available: bool = self._check_hsm_availability()

        # ---- 检测测试环境 ----
        import sys
        is_test = 'pytest' in sys.modules or 'PYTEST_CURRENT_TEST' in os.environ

        # ---- 从 Redis 恢复状态（非测试环境）----
        if not is_test:
            self._persistence_manager.restore_degraded_state(
                callback=self._degradation_manager.restore_state,
            )
            self._persistence_manager.restore_keys(
                key_slots=self._key_manager.key_slots,
                lock=self._key_manager.lock,
                encryption_key_getter=KeyManager._get_encryption_key,
                key_encryption_password_getter=KeyManager._get_key_encryption_password,
            )
            self._persistence_manager.migrate_legacy_keys()
            KeyManager._get_encryption_key()

        # ---- 启动定时任务 ----
        if self._soft_hsm_manager.is_enabled and not is_test:
            self._soft_hsm_manager.start_periodic_check()
        if not is_test:
            self._start_key_cleanup_timer()

    # ================================================================
    #  属性委托（支持测试中的内部属性访问）
    # ================================================================

    @property
    def DEGRADED_PHASE_1_THRESHOLD(self) -> timedelta:
        from app.config.settings import settings
        return timedelta(minutes=settings.hsm_degraded_phase_1_threshold_minutes)

    @property
    def DEGRADED_PHASE_2_THRESHOLD(self) -> timedelta:
        from app.config.settings import settings
        return timedelta(minutes=settings.hsm_degraded_phase_2_threshold_minutes)

    @property
    def _key_slots(self) -> Dict[KeySlot, Dict[str, Dict[str, Any]]]:
        """密钥槽位（向后兼容测试访问）。"""
        return self._key_manager.key_slots

    @_key_slots.setter
    def _key_slots(self, value: Dict[KeySlot, Dict[str, Dict[str, Any]]]) -> None:
        """密钥槽位设置器（向后兼容测试访问）。"""
        self._key_manager._key_slots = value

    @property
    def _lock(self):
        """线程锁（向后兼容测试访问）。"""
        return self._key_manager.lock

    @_lock.setter
    def _lock(self, value) -> None:
        """线程锁设置器（向后兼容测试构造轻量实例）。"""
        self._key_manager._lock = value

    @property
    def _use_soft_hsm(self) -> bool:
        """SoftHSM 启用标志（向后兼容测试访问）。"""
        return self._soft_hsm_manager.is_enabled

    @_use_soft_hsm.setter
    def _use_soft_hsm(self, value: bool) -> None:
        """SoftHSM 启用标志设置器（向后兼容测试访问）。"""
        self._soft_hsm_manager._use_soft_hsm = value

    @property
    def _is_soft_hsm(self) -> bool:
        """SoftHSM 启用标志别名（向后兼容测试访问）。"""
        return self._soft_hsm_manager.is_enabled

    @property
    def _degraded_at(self) -> Optional[datetime]:
        """降级时间（向后兼容测试访问）。"""
        return self._degradation_manager.degraded_at

    @_degraded_at.setter
    def _degraded_at(self, value: Optional[datetime]) -> None:
        """降级时间设置器（向后兼容测试访问）。"""
        self._degradation_manager._degraded_at = value

    @_degraded_at.deleter
    def _degraded_at(self) -> None:
        """降级时间删除器（向后兼容测试 patch 清理）。"""
        self._degradation_manager._degraded_at = None

    @property
    def _is_degraded(self) -> bool:
        """降级标志（向后兼容测试访问）。"""
        return self._degradation_manager.is_degraded

    @_is_degraded.setter
    def _is_degraded(self, value: bool) -> None:
        """降级标志设置器（向后兼容测试访问）。"""
        self._degradation_manager._is_degraded = value

    @_is_degraded.deleter
    def _is_degraded(self) -> None:
        """降级标志删除器（向后兼容测试 patch 清理）。"""
        self._degradation_manager._is_degraded = False

    @property
    def _degraded_reason(self) -> Optional[str]:
        """降级原因（向后兼容测试访问）。"""
        return self._degradation_manager.degraded_reason

    @_degraded_reason.setter
    def _degraded_reason(self, value: Optional[str]) -> None:
        """降级原因设置器（向后兼容测试访问）。"""
        self._degradation_manager._degraded_reason = value

    @_degraded_reason.deleter
    def _degraded_reason(self) -> None:
        """降级原因删除器（向后兼容测试 patch 清理）。"""
        self._degradation_manager._degraded_reason = None

    @property
    def _soft_hsm_enabled_at(self) -> Optional[datetime]:
        """SoftHSM 启用时间（向后兼容测试访问）。"""
        return self._soft_hsm_manager.enabled_at

    @_soft_hsm_enabled_at.setter
    def _soft_hsm_enabled_at(self, value: Optional[datetime]) -> None:
        """SoftHSM 启用时间设置器（向后兼容测试访问）。"""
        self._soft_hsm_manager._soft_hsm_enabled_at = value

    # 以下四个为重构前的扁平属性占位：定时器已迁入 TaskScheduler、
    # 事件循环已迁入 PersistenceManager，这里保留可读可写的兼容语义
    # （赋值存入实例 __dict__，供测试构造轻量实例时直接写入）。

    @property
    def _key_cleanup_timer(self):
        """密钥清理定时器占位（向后兼容测试访问）。"""
        return self.__dict__.get("_key_cleanup_timer")

    @_key_cleanup_timer.setter
    def _key_cleanup_timer(self, value) -> None:
        self.__dict__["_key_cleanup_timer"] = value

    @property
    def _soft_hsm_check_timer(self):
        """SoftHSM检查定时器占位（向后兼容测试访问）。"""
        return self.__dict__.get("_soft_hsm_check_timer")

    @_soft_hsm_check_timer.setter
    def _soft_hsm_check_timer(self, value) -> None:
        self.__dict__["_soft_hsm_check_timer"] = value

    @property
    def _periodic_check_timer(self):
        """定期检查定时器占位（向后兼容测试访问）。"""
        return self.__dict__.get("_periodic_check_timer")

    @_periodic_check_timer.setter
    def _periodic_check_timer(self, value) -> None:
        self.__dict__["_periodic_check_timer"] = value

    @property
    def _pending_tasks(self) -> set:
        """待完成任务集合（向后兼容测试访问）。"""
        return self._persistence_manager._pending_tasks

    @_pending_tasks.setter
    def _pending_tasks(self, value: set) -> None:
        self._persistence_manager._pending_tasks = value

    @property
    def _loop(self):
        """事件循环占位（向后兼容测试访问）。"""
        return self.__dict__.get("_loop")

    @_loop.setter
    def _loop(self, value) -> None:
        self.__dict__["_loop"] = value

    @staticmethod
    def _get_encryption_key() -> bytes:
        """获取加密密钥（向后兼容测试访问，委托给 KeyManager）。"""
        return KeyManager._get_encryption_key()

    def _check_desensitization_key_rotation(self, rotation_days: int) -> None:
        """检查脱敏密钥轮换（向后兼容测试访问，委托给 KeyManager）。"""
        self._key_manager.check_desensitization_key_rotation(rotation_days)

    def _check_jwt_key_rotation(self, rotation_days: int) -> None:
        """检查 JWT 密钥轮换（向后兼容测试访问，委托给 KeyManager）。"""
        self._key_manager.check_jwt_key_rotation(rotation_days)

    def _safe_run_async_task(self, coro):
        """安全运行异步任务（向后兼容测试 mock）。"""
        return self._persistence_manager.safe_run_async_task(coro)

    def _flush_pending_persistence(self) -> None:
        """刷新待持久化操作（向后兼容测试访问）。"""
        self._persistence_manager.flush_pending_persistence()

    def _check_soft_hsm_expiry(self) -> bool:
        """检查 SoftHSM 是否过期（向后兼容测试访问，委托给 SoftHSMManager）。"""
        return self._soft_hsm_manager.is_expired()

    def _find_key(self, key_name: str):
        """查找密钥（向后兼容测试访问，委托给 KeyManager）。"""
        return self._key_manager.find_key(key_name)

    @staticmethod
    def _serialize_key_pair(private_key, public_key) -> tuple:
        """序列化密钥对（向后兼容测试访问，委托给 KeyManager）。"""
        return KeyManager._serialize_key_pair(private_key, public_key)

    # ================================================================
    #  公共 API：密钥管理（委托给 KeyManager）
    # ================================================================

    def _get_key_slot(self, key_name: str) -> KeySlot:
        """获取密钥槽位（向后兼容）。"""
        return get_key_slot(key_name)

    def generate_key_pair(self, key_name: str, key_size: int = 2048,
                          slot: Optional[KeySlot] = None) -> Dict[str, Any]:
        """生成 RSA 密钥对。"""
        return self._key_manager.generate_key_pair(key_name, key_size, slot)

    def rotate_key(self, key_name: str, parallel_days: int = 7) -> Dict[str, Any]:
        """轮换密钥。"""
        return self._key_manager.rotate_key(key_name, parallel_days)

    def cleanup_expired_keys(self) -> int:
        """清理过期密钥。"""
        return self._key_manager.cleanup_expired_keys()

    def get_key_info(self, key_name: str) -> Optional[Dict[str, Any]]:
        """获取密钥信息。"""
        return self._key_manager.get_key_info(key_name)

    def list_keys(self) -> List[str]:
        """列出所有密钥。"""
        return self._key_manager.list_keys()

    def list_keys_by_slot(self, slot: KeySlot) -> List[str]:
        """列出指定槽位的密钥。"""
        return self._key_manager.list_keys_by_slot(slot)

    def get_public_key(self, key_name: str) -> Optional[str]:
        """获取公钥 PEM 字符串。"""
        return self._key_manager.get_public_key(key_name)

    def get_private_key(self, key_name: str) -> str:
        """禁止导出私钥。"""
        return EncryptionService.get_private_key(key_name)

    # ================================================================
    #  公共 API：加密操作（委托给 EncryptionService）
    # ================================================================

    def sign(self, key_name: str, data: bytes) -> bytes:
        """使用指定密钥签名数据（PSS+MGF1 填充）。"""
        return self._encryption_service.sign(key_name, data)

    def verify_signature(self, key_name: str, data: bytes, signature: bytes) -> bool:
        """验证签名（PSS+MGF1 填充）。"""
        return self._encryption_service.verify_signature(key_name, data, signature)

    def encrypt(self, key_name: str, data: bytes) -> bytes:
        """加密数据。"""
        return self._encryption_service.encrypt(key_name, data)

    def decrypt(self, key_name: str, ciphertext: bytes) -> bytes:
        """解密数据。"""
        return self._encryption_service.decrypt(key_name, ciphertext)

    def sign_jwt(self, key_name: str, header: Dict[str, Any],
                 payload: Dict[str, Any], algorithm: str = "RS256") -> str:
        """签名 JWT。"""
        return self._encryption_service.sign_jwt(key_name, header, payload, algorithm)

    # ================================================================
    #  公共 API：降级状态管理（委托给 DegradationManager）
    # ================================================================

    def is_degraded(self) -> bool:
        """是否处于降级状态。"""
        return self._degradation_manager.is_degraded

    def get_degraded_at(self) -> Optional[datetime]:
        """获取降级开始时间。"""
        return self._degradation_manager.degraded_at

    def get_degraded_phase(self) -> DegradedPhase:
        """获取当前降级阶段。"""
        return self._degradation_manager.get_degraded_phase()

    def set_degraded(self, reason: str) -> None:
        """设置降级状态。"""
        self._degradation_manager.set_degraded(reason)
        self._persist_degraded_state()

    def clear_degraded(self) -> None:
        """清除降级状态。"""
        self._degradation_manager.clear_degraded()
        self._persist_degraded_state()

    def check_degraded_behavior(self, operation: str) -> bool:
        """检查操作在当前降级阶段是否允许。"""
        return self._degradation_manager.check_degraded_behavior(operation)

    def get_degradation_info(self) -> dict:
        """获取降级状态信息。"""
        info = self._degradation_manager.get_degradation_info()
        if self._soft_hsm_manager.enabled_at:
            info["soft_hsm_enabled_at"] = self._soft_hsm_manager.enabled_at.isoformat()
        return info

    # ================================================================
    #  公共 API：SoftHSM 管理（委托给 SoftHSMManager）
    # ================================================================

    def get_soft_hsm_status(self) -> Dict[str, Any]:
        """获取 SoftHSM 状态信息。"""
        return self._soft_hsm_manager.get_status()

    def get_hsm_mode(self) -> str:
        """获取 HSM 模式。"""
        if self._soft_hsm_manager.is_enabled:
            return "soft_hsm"
        return "hardware_hsm"

    def is_hsm_available(self) -> bool:
        """HSM 是否可用。"""
        return self._hsm_available

    # ================================================================
    #  公共 API：异步任务管理（委托给 PersistenceManager）
    # ================================================================

    async def cleanup_pending_tasks(self) -> None:
        """清理待完成的异步任务。"""
        await self._persistence_manager.cleanup_pending_tasks()

    # ================================================================
    #  内部方法
    # ================================================================

    def _persist_degraded_state(self) -> None:
        """持久化降级状态到 Redis。"""
        self._persistence_manager.persist_degraded_state(
            is_degraded=self._degradation_manager.is_degraded,
            degraded_at=self._degradation_manager.degraded_at.isoformat()
            if self._degradation_manager.degraded_at else None,
            degraded_reason=self._degradation_manager.degraded_reason,
        )

    def _start_key_cleanup_timer(self) -> None:
        """启动密钥清理定时器。"""
        interval_seconds = KeyManager.KEY_CLEANUP_INTERVAL_HOURS * 3600

        def _cleanup_and_reschedule():
            from app.config.settings import settings
            count = self._key_manager.cleanup_expired_keys()
            if count > 0:
                logger.info("hsm_scheduled_key_cleanup_completed", expired_count=count)
            self._check_jwt_key_rotation(settings.jwt_key_rotation_days)
            self._check_desensitization_key_rotation(settings.desensitization_key_rotation_days)

        self._task_scheduler.schedule_periodic_task(
            task=_cleanup_and_reschedule,
            interval_seconds=interval_seconds,
            task_name="key_cleanup_and_rotation",
        )

    def _check_hsm_availability(self) -> bool:
        """检查 HSM 可用性。"""
        if self._soft_hsm_manager.is_enabled:
            logger.info("hsm_using_soft_hsm_mode")
            return True

        try:
            if self._is_pkcs11_available():
                return True

            if self._is_cloud_hsm_available():
                return True

            logger.warning("hsm_not_available_falling_back_to_soft_hsm")
            return False

        except Exception as e:
            logger.error("hsm_availability_check_failed", error=str(e))
            return False

    @staticmethod
    def _is_pkcs11_available() -> bool:
        """检查 PKCS#11 库是否可用。"""
        try:
            pkcs11_lib_paths = [
                "/usr/lib/opensc/pkcs11.so",
                "/usr/lib64/pkcs11/opensc-pkcs11.so",
                "C:\\Windows\\System32\\opensc-pkcs11.dll",
            ]

            for lib_path in pkcs11_lib_paths:
                if os.path.exists(lib_path):
                    logger.info("hsm_pkcs11_library_found", path=lib_path)
                    return True

            soft_hsm_env = os.environ.get("SOFT_HSM_CONF", "")
            if soft_hsm_env and os.path.exists(os.path.dirname(soft_hsm_env)):
                logger.info("hsm_soft_hsm_configured", config=soft_hsm_env)
                return True

            return False

        except Exception as e:
            logger.debug("hsm_pkcs11_check_failed", error=str(e))
            return False

    @staticmethod
    def _is_cloud_hsm_available() -> bool:
        """检查云 HSM 是否可用。"""
        try:
            cloud_hsm_env_vars = [
                "AWS_CLOUDHSM_AWS_REGION",
                "AZURE_HSM_URI",
                "GCP_CLOUDHSM_PROJECT",
            ]

            for env_var in cloud_hsm_env_vars:
                if os.environ.get(env_var):
                    logger.info("hsm_cloud_hsm_env_detected", env_var=env_var)
                    return True

            return False

        except Exception as e:
            logger.debug("hsm_cloud_hsm_check_failed", error=str(e))
            return False
