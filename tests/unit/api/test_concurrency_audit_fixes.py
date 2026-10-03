"""并发与性能审查回归（2026-10-03，app/api + app/config 侧）。

钉住本轮「零语义风险」修复的行为契约，每条都对应审查报告里的一条发现：

- ``nav.ConnectionManager._deliver_local``：原实现**持 session 锁 await
  ``ws.send_json()``**，一个弱网客户端会把同 session 所有其它设备的下行消息
  一起阻塞（导航 P99 < 200ms 验收直接击穿）。现改为锁内只做连接快照。
- ``nav.ConnectionManager.disconnect``：锁回收原先在临界区内 ``del`` 锁字典
  条目 → 后续协程新建锁与仍在等待旧锁的协程并发进入，互斥失效。
- ``nav.ConnectionManager`` 的 ``_patient_id_cache``：原先只有 60s TTL 软失效、
  无上界、无清扫，且断开不清理 → 条目数 = 历史 session 总数单调增长。
- ``nav.ConnectionManager.connect``：超限时 ``websocket.close()`` 在锁内 await。
- ``auth.authenticate_user`` / ``auth._issue_access_token``：bcrypt 校验与 HSM
  签名是同步 CPU，必须 offload，否则并发登录/发码会把事件循环整段卡住。
- ``dicom_storage`` / ``imaging``：pydicom 解析与大 base64 解码必须 offload。
- ``rate_limit.RATE_LIMITS``：多器官联合诊断原先完全无限流（雪崩最短路径）。
"""
import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1 import auth as auth_api
from app.api.websocket import nav as nav_module
from app.api.websocket.nav import ConnectionManager
from app.config import rate_limit as rate_limit_module
from app.services import dicom_storage as dicom_module

pytestmark = pytest.mark.asyncio


class _GateWS:
    """``block_first=True`` 时第一次 ``send_json`` 阻塞到 ``release`` 被 set。"""

    def __init__(self, block_first: bool = True) -> None:
        self.calls = 0
        self.block_first = block_first
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.closed_with: int | None = None

    async def send_json(self, message):
        self.calls += 1
        if self.block_first and self.calls == 1:
            self.entered.set()
            await self.release.wait()

    async def close(self, code=None):
        self.closed_with = code

    async def accept(self):
        return None


def _bind(manager: ConnectionManager, session_id: str, device_id: str, ws) -> None:
    key = (session_id, device_id)
    manager.active_connections[key] = ws
    manager.session_index.setdefault(session_id, []).append(key)
    manager.last_heartbeat[key] = time.time()


class TestDeliverLocalDoesNotHoldLock:
    async def test_slow_socket_does_not_block_other_deliveries(self):
        """慢客户端发送期间，同 session 的另一次投递必须能完成。"""
        manager = ConnectionManager()
        # 只让 slow 阻塞，才能验证"锁已释放、另一次投递不被拖住"
        slow, fast = _GateWS(block_first=True), _GateWS(block_first=False)
        _bind(manager, "s1", "d1", slow)
        _bind(manager, "s1", "d2", fast)

        first = asyncio.create_task(manager._deliver_local({"m": 1}, "s1"))
        await asyncio.wait_for(slow.entered.wait(), timeout=1.0)

        # 旧实现在这里超时（session 锁被慢 send 持住）
        await asyncio.wait_for(manager._deliver_local({"m": 2}, "s1"), timeout=1.0)

        slow.release.set()
        await asyncio.wait_for(first, timeout=1.0)
        # 两个 socket 都收到了两条消息，且第二条是在慢发送**未结束**时投的
        assert slow.calls == 2
        assert fast.calls == 2

    async def test_unknown_session_is_noop(self):
        manager = ConnectionManager()
        await manager._deliver_local({"m": 1}, "missing")


class TestSessionLockReclamation:
    async def test_held_lock_is_not_deleted(self):
        """锁正被持有时不得从字典里删（否则互斥失效）。"""
        manager = ConnectionManager()
        lock = await manager._get_session_lock("s1")
        await lock.acquire()
        try:
            manager._cleanup_session_lock("s1")
            assert "s1" in manager.auth_manager._session_locks
        finally:
            lock.release()
        manager._cleanup_session_lock("s1")
        assert "s1" not in manager.auth_manager._session_locks

    async def test_lock_registry_is_bounded(self, monkeypatch):
        """字典有 LRU 上界，长跑进程不会因为 session 增多而无界增长。"""
        monkeypatch.setattr(nav_module, "_MAX_SESSION_LOCKS", 4)
        manager = ConnectionManager()
        for i in range(20):
            await manager._get_session_lock(f"s{i}")
        assert len(manager.auth_manager._session_locks) <= 5

    async def test_session_with_active_connections_is_kept(self):
        manager = ConnectionManager()
        await manager._get_session_lock("s1")
        manager.session_index["s1"] = [("s1", "d1")]
        manager._cleanup_session_lock("s1")
        assert "s1" in manager.auth_manager._session_locks


class TestPatientIdCacheIsBounded:
    async def test_disconnect_clears_patient_id_cache(self):
        manager = ConnectionManager()
        _bind(manager, "s1", "d1", _GateWS())
        manager._patient_id_cache["s1"] = ("P1", time.time())
        await manager.disconnect("s1", "d1")
        assert "s1" not in manager._patient_id_cache

    async def test_cache_size_is_capped(self, monkeypatch):
        monkeypatch.setattr(nav_module, "_PATIENT_ID_CACHE_MAX", 3)
        manager = ConnectionManager()
        now = time.time()
        for i in range(10):
            manager._patient_id_cache[f"s{i}"] = (f"P{i}", now - i)
        manager._evict_patient_id_cache_if_needed("s_new")
        manager._patient_id_cache["s_new"] = ("P_new", now)
        assert len(manager._patient_id_cache) <= 4

    async def test_expired_entries_are_evicted_first(self, monkeypatch):
        monkeypatch.setattr(nav_module, "_PATIENT_ID_CACHE_MAX", 2)
        manager = ConnectionManager()
        now = time.time()
        manager._patient_id_cache["old"] = ("P", now - 3600)
        manager._patient_id_cache["fresh"] = ("P", now)
        manager._evict_patient_id_cache_if_needed("new")
        assert "old" not in manager._patient_id_cache
        assert "fresh" in manager._patient_id_cache


class TestConnectRejectsOutsideLock:
    async def test_over_limit_closes_and_does_not_register(self, monkeypatch):
        monkeypatch.setattr(nav_module.settings, "nav_max_connections_per_session", 1)
        manager = ConnectionManager()
        _bind(manager, "s1", "d1", _GateWS())
        ws = _GateWS()
        assert await manager.connect(ws, "s1", "d2") is False
        assert ws.closed_with is not None
        assert ("s1", "d2") not in manager.active_connections

    async def test_connect_registers_and_disconnect_removes(self):
        manager = ConnectionManager()
        ws = _GateWS()
        assert await manager.connect(ws, "s1", "d1") is True
        assert manager.session_index["s1"] == [("s1", "d1")]
        await manager.disconnect("s1", "d1")
        assert "s1" not in manager.session_index


class TestAuthOffloadsBlockingCrypto:
    async def test_verify_password_runs_off_event_loop(self, monkeypatch):
        main_thread = threading.get_ident()
        seen = {}

        def _fake_verify(password, hashed):
            seen["thread"] = threading.get_ident()
            return True

        monkeypatch.setattr(auth_api, "verify_password", _fake_verify)
        monkeypatch.setattr(
            auth_api,
            "get_user_by_username",
            AsyncMock(return_value=SimpleNamespace(hashed_password="h")),
        )
        user = await auth_api.authenticate_user("doctor", "pw")
        assert user is not None
        assert seen["thread"] != main_thread

    async def test_wrong_password_rejected(self, monkeypatch):
        monkeypatch.setattr(auth_api, "verify_password", lambda p, h: False)
        monkeypatch.setattr(
            auth_api,
            "get_user_by_username",
            AsyncMock(return_value=SimpleNamespace(hashed_password="h")),
        )
        assert await auth_api.authenticate_user("doctor", "bad") is False

    async def test_issue_access_token_offloads_hsm_signing(self, monkeypatch):
        main_thread = threading.get_ident()
        seen = {}

        def _fake_create_token(data, expires_delta=None):
            seen["thread"] = threading.get_ident()
            seen["data"] = data
            return "SIGNED_TOKEN"

        monkeypatch.setattr(auth_api, "create_access_token", _fake_create_token)
        token = await auth_api._issue_access_token("doctor", {"role": "doctor"})
        assert token == "SIGNED_TOKEN"
        assert seen["thread"] != main_thread
        assert seen["data"]["sub"] == "doctor"


class TestDicomParsingOffloaded:
    async def test_strip_dicom_phi_runs_off_event_loop(self, monkeypatch, tmp_path):
        main_thread = threading.get_ident()
        seen = {}
        storage = dicom_module.DicomStorageService.__new__(dicom_module.DicomStorageService)
        storage._backend = "local"
        storage._base_path = str(tmp_path)
        storage._max_file_size = 10 * 1024 * 1024
        storage._minio_client = None
        storage_id = "img_20260101_abcdef12"
        def _fake_strip(content, patient_id):
            seen["thread"] = threading.get_ident()
            return b"OK", False

        monkeypatch.setattr(dicom_module, "strip_dicom_phi", _fake_strip)
        path = await storage.save_by_id(b"RAW", storage_id, "P1")
        assert path.endswith(".dcm")
        assert seen["thread"] != main_thread

    async def test_oversize_rejected_before_parsing(self, tmp_path):
        storage = dicom_module.DicomStorageService.__new__(dicom_module.DicomStorageService)
        storage._backend = "local"
        storage._base_path = str(tmp_path)
        storage._max_file_size = 4
        storage._minio_client = None
        with pytest.raises(ValueError):
            await storage.save_by_id(b"TOO-LONG", "img_20260101_abcdef12", "P1")


class TestMultiOrganRateLimit:
    def test_path_is_registered(self):
        assert "/api/v1/diagnosis/multi-organ" in rate_limit_module.RATE_LIMITS

    def test_registration_is_not_endpoint_managed(self):
        """必须由中间件统一限流（端点内并没有 check_rate_limit 兜底）。"""
        entry = rate_limit_module.RATE_LIMITS["/api/v1/diagnosis/multi-organ"]
        assert not entry.get("endpoint_managed")
        assert entry["capacity"] > 0 and entry["refill_rate"] > 0

    async def test_middleware_rejects_over_capacity(self, monkeypatch):
        """容量打满后必须返回 429 而不是放行。"""
        from fastapi import Request

        captured = {}

        async def _fake_check(path, client_ip, user_id, is_emergency):
            from app.api.v1.errors import RateLimitExceededException

            captured["path"] = path
            return RateLimitExceededException()

        monkeypatch.setattr(rate_limit_module, "check_rate_limit", _fake_check)

        async def _call_next(request):
            return "next"

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/diagnosis/multi-organ",
            "headers": [],
            "client": ("127.0.0.1", 1234),
        }
        mw = rate_limit_module.RateLimitMiddleware(AsyncMock())
        with patch.object(rate_limit_module.BaseHTTPMiddleware, "__call__"):
            response = await mw.dispatch(Request(scope), _call_next)
        assert response != "next"
        assert captured["path"] == "/api/v1/diagnosis/multi-organ"
