"""`HSMManager` 兼容转发层与环境探测分支补测。

## 为什么补这里

全量口径下 `app/security/hsm/manager.py` 只有 76.2%（266 语句 / 缺 58 行），
缺口**不在密码学核心**，而是三类"边缘但会被现场触发"的代码：

1. **HSM 可用性探测**（`_is_pkcs11_available` / `_is_cloud_hsm_available` /
   `_check_hsm_availability`）—— 决定"到底走真机 HSM 还是降级到 SoftHSM"，
   判错会直接改变安全属性，**必须可测**；
2. **兼容转发 shim**（`_check_jwt_key_rotation` / `_flush_pending_persistence` /
   `_serialize_key_pair` / `cleanup_pending_tasks` / `_pending_tasks` 等）——
   老调用方仍走这些入口，断了就是隐性回归；
3. **私有访问器与定时清理闭包**（`_key_cleanup_timer` 等属性、`_start_key_cleanup_timer`
   内部的 `_cleanup_and_reschedule`、降级阈值属性、`get_degradation_info`）。

## 构造方式

沿用 `tests/unit/sec/conftest.py:358` 的 `_make_lightweight_hsm()` 思路：
`HSMManager.__new__(HSMManager)` 后手工挂替身，**不触发真实 HSM 初始化**
（真机会去连 SoftHSM/PKCS#11，测试里既慢又不可靠）。

## ⚠️ 本目录的 session 级打桩（本会话踩过第 N 次）

`tests/unit/sec/conftest.py:335` 有一个 **session 级 autouse** fixture 把
`HSMManager._safe_run_async_task` 与 `PersistenceManager.safe_run_async_task`
都打成了 no-op（原意是屏蔽 HSM 的 Redis 持久化）。因此**在本目录内无法验证
`_safe_run_async_task` 的真实转发**（调用会落到替身上，计数恒为 0）。
该转发自身是单行委托、且其行为已被持久化层的既有用例覆盖，故此处**不做重复断言**，
只把这条陷阱写下来，避免下一个人再花时间定位"为什么调用次数是 0"。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.settings import settings
from app.security.hsm import HSMManager, KeyManager


def _manager() -> HSMManager:
    """构造挂了替身子管理器的轻量实例（不跑真实 __init__）。"""
    manager = HSMManager.__new__(HSMManager)
    manager._soft_hsm_manager = MagicMock()
    manager._key_manager = MagicMock()
    manager._degradation_manager = MagicMock()
    manager._persistence_manager = MagicMock()
    manager._task_scheduler = MagicMock()
    manager._hsm_available = False
    return manager


class TestPkcs11Detection:
    """`_is_pkcs11_available` 是 staticmethod，可直接调用。"""

    def test_library_present_returns_true(self, monkeypatch):
        monkeypatch.setattr(os.path, "exists", lambda path: path.endswith("pkcs11.so"))

        assert HSMManager._is_pkcs11_available() is True

    def test_nothing_present_returns_false(self, monkeypatch):
        monkeypatch.setattr(os.path, "exists", lambda path: False)
        monkeypatch.delenv("SOFT_HSM_CONF", raising=False)

        assert HSMManager._is_pkcs11_available() is False

    def test_soft_hsm_conf_directory_detected(self, monkeypatch):
        # 原实现判的是配置文件的**目录**（dirname）是否存在
        monkeypatch.setattr(os.path, "exists", lambda path: path == "/etc/softhsm")
        monkeypatch.setenv("SOFT_HSM_CONF", "/etc/softhsm/softhsm2.conf")

        assert HSMManager._is_pkcs11_available() is True

    def test_probe_exception_is_contained(self, monkeypatch):
        def _boom(_path):
            raise OSError("permission denied")

        monkeypatch.setattr(os.path, "exists", _boom)

        assert HSMManager._is_pkcs11_available() is False


class TestCloudHsmDetection:
    def test_env_var_detected(self, monkeypatch):
        for name in ("AWS_CLOUDHSM_AWS_REGION", "AZURE_HSM_URI", "GCP_CLOUDHSM_PROJECT"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("AZURE_HSM_URI", "https://hsm.example")

        assert HSMManager._is_cloud_hsm_available() is True

    def test_no_env_returns_false(self, monkeypatch):
        for name in ("AWS_CLOUDHSM_AWS_REGION", "AZURE_HSM_URI", "GCP_CLOUDHSM_PROJECT"):
            monkeypatch.delenv(name, raising=False)

        assert HSMManager._is_cloud_hsm_available() is False


class TestCheckHsmAvailability:
    def test_soft_hsm_enabled_short_circuits(self, monkeypatch):
        manager = _manager()
        manager._soft_hsm_manager.is_enabled = True
        monkeypatch.setattr(
            HSMManager, "_is_pkcs11_available", staticmethod(lambda: False)
        )

        assert manager._check_hsm_availability() is True

    def test_pkcs11_path_returns_true(self, monkeypatch):
        manager = _manager()
        manager._soft_hsm_manager.is_enabled = False
        monkeypatch.setattr(
            HSMManager, "_is_pkcs11_available", staticmethod(lambda: True)
        )

        assert manager._check_hsm_availability() is True

    def test_cloud_path_returns_true(self, monkeypatch):
        manager = _manager()
        manager._soft_hsm_manager.is_enabled = False
        monkeypatch.setattr(
            HSMManager, "_is_pkcs11_available", staticmethod(lambda: False)
        )
        monkeypatch.setattr(
            HSMManager, "_is_cloud_hsm_available", staticmethod(lambda: True)
        )

        assert manager._check_hsm_availability() is True

    def test_nothing_available_returns_false(self, monkeypatch):
        manager = _manager()
        manager._soft_hsm_manager.is_enabled = False
        monkeypatch.setattr(
            HSMManager, "_is_pkcs11_available", staticmethod(lambda: False)
        )
        monkeypatch.setattr(
            HSMManager, "_is_cloud_hsm_available", staticmethod(lambda: False)
        )

        assert manager._check_hsm_availability() is False

    def test_probe_exception_returns_false(self, monkeypatch):
        """探测本身抛错时必须判为"不可用"（fail-closed），而不是让异常冒出去。"""

        def _boom():
            raise RuntimeError("probe exploded")

        manager = _manager()
        manager._soft_hsm_manager.is_enabled = False
        monkeypatch.setattr(HSMManager, "_is_pkcs11_available", staticmethod(_boom))

        assert manager._check_hsm_availability() is False


class TestDelegationShims:
    """老调用方仍走这些入口，断了就是隐性回归。"""

    def test_key_rotation_checks_delegate(self):
        manager = _manager()

        manager._check_jwt_key_rotation(30)
        manager._check_desensitization_key_rotation(90)

        manager._key_manager.check_jwt_key_rotation.assert_called_once_with(30)
        manager._key_manager.check_desensitization_key_rotation.assert_called_once_with(90)

    def test_flush_pending_persistence_delegates(self):
        manager = _manager()

        manager._flush_pending_persistence()

        manager._persistence_manager.flush_pending_persistence.assert_called_once_with()

    def test_serialize_key_pair_delegates(self, monkeypatch):
        monkeypatch.setattr(
            KeyManager, "_serialize_key_pair", staticmethod(lambda priv, pub: "serialized")
        )
        manager = _manager()

        assert manager._serialize_key_pair("priv", "pub") == "serialized"

    async def test_cleanup_pending_tasks_awaits_persistence_layer(self):
        manager = _manager()
        manager._persistence_manager.cleanup_pending_tasks = AsyncMock()

        await manager.cleanup_pending_tasks()

        manager._persistence_manager.cleanup_pending_tasks.assert_awaited_once_with()

    def test_pending_tasks_reads_through(self):
        manager = _manager()

        assert manager._pending_tasks is manager._persistence_manager._pending_tasks


class TestPrivateAccessorsAndDegradationState:
    def test_timer_accessors_default_to_none_and_roundtrip(self):
        manager = _manager()

        assert manager._key_cleanup_timer is None
        assert manager._soft_hsm_check_timer is None
        assert manager._periodic_check_timer is None
        assert manager._loop is None

        manager._soft_hsm_check_timer = "timer-1"
        assert manager._soft_hsm_check_timer == "timer-1"

    def test_is_soft_hsm_reads_submanager(self):
        manager = _manager()
        manager._soft_hsm_manager.is_enabled = True

        assert manager._is_soft_hsm is True

    def test_is_degraded_reads_submanager(self):
        manager = _manager()
        manager._degradation_manager.is_degraded = True

        assert manager._is_degraded is True

    def test_degradation_thresholds_come_from_settings(self):
        manager = _manager()

        phase_1: Any = manager.DEGRADED_PHASE_1_THRESHOLD
        phase_2: Any = manager.DEGRADED_PHASE_2_THRESHOLD
        if callable(phase_1):  # 兼容 static/class property 形式
            phase_1 = phase_1()
        if callable(phase_2):
            phase_2 = phase_2()

        assert phase_1 == timedelta(minutes=settings.hsm_degraded_phase_1_threshold_minutes)
        assert phase_2 == timedelta(minutes=settings.hsm_degraded_phase_2_threshold_minutes)


class TestGetDegradationInfo:
    def test_returns_submanager_info(self):
        manager = _manager()
        manager._degradation_manager.get_degradation_info.return_value = {"is_degraded": True}
        manager._soft_hsm_manager.enabled_at = None

        assert manager.get_degradation_info() == {"is_degraded": True}

    def test_adds_soft_hsm_enabled_at_when_present(self):
        manager = _manager()
        manager._degradation_manager.get_degradation_info.return_value = {"is_degraded": True}
        stamp = datetime(2026, 9, 28, 12, 0, 0)
        manager._soft_hsm_manager.enabled_at = stamp

        info = manager.get_degradation_info()

        assert info["soft_hsm_enabled_at"] == stamp.isoformat()


class TestKeyCleanupTimer:
    def test_schedules_periodic_task_with_expected_interval(self):
        manager = _manager()

        manager._start_key_cleanup_timer()

        kwargs = manager._task_scheduler.schedule_periodic_task.call_args.kwargs
        assert kwargs["task_name"] == "key_cleanup_and_rotation"
        assert kwargs["interval_seconds"] == KeyManager.KEY_CLEANUP_INTERVAL_HOURS * 3600

    def test_scheduled_callback_cleans_and_checks_rotation(self):
        """回调体内三件事（清理过期密钥 + 两类轮换检查）都要真的被调用。"""
        manager = _manager()
        manager._key_manager.cleanup_expired_keys.return_value = 3
        manager._start_key_cleanup_timer()
        callback = manager._task_scheduler.schedule_periodic_task.call_args.kwargs["task"]

        callback()

        manager._key_manager.cleanup_expired_keys.assert_called_once_with()
        manager._key_manager.check_jwt_key_rotation.assert_called_once_with(
            settings.jwt_key_rotation_days
        )
        manager._key_manager.check_desensitization_key_rotation.assert_called_once_with(
            settings.desensitization_key_rotation_days
        )

    def test_callback_with_zero_expired_still_checks_rotation(self):
        """清理数为 0 时仍必须检查轮换 —— 否则只清理、不轮换。"""
        manager = _manager()
        manager._key_manager.cleanup_expired_keys.return_value = 0
        manager._start_key_cleanup_timer()
        callback = manager._task_scheduler.schedule_periodic_task.call_args.kwargs["task"]

        callback()

        manager._key_manager.check_jwt_key_rotation.assert_called_once_with(
            settings.jwt_key_rotation_days
        )


@pytest.mark.parametrize("value", [True, False])
def test_is_hsm_available_reflects_flag(value: bool):
    """`is_hsm_available` 的取值来源是构造期探测结果，不应被其它状态覆盖。"""
    manager = _manager()
    manager._hsm_available = value
    result = manager.is_hsm_available

    assert (result() if callable(result) else result) is value
