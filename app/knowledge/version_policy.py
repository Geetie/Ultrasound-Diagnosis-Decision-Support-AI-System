"""KB 版本管理 2 层决策机制。

设计规格 §1.3:
1. safety_critical 更新: 即时失效，直接终止所有活跃会话
2. 非 safety_critical 更新: 会话级版本锁定，新会话使用新版本
3. 4 步缓存读取判定链:
   a. 检查请求是否指定 kb_version → 使用指定版本
   b. 检查会话是否锁定 kb_version → 使用锁定版本
   c. 检查是否有最新 safety_critical 版本 → 即时切换
   d. 使用当前默认版本

会话内版本锁定:
- SESSION_KB_VERSION_LOCK=true（一期默认 true）
- safety_critical 更新时直接终止所有活跃会话（E4006）
- major 版本不一致时拒绝处理（E4004）
"""
from __future__ import annotations

from enum import Enum

from app.knowledge.version import KBVersion, parse_kb_version


class UpdateSeverity(str, Enum):
    """KB 更新的严重级别。"""
    NORMAL = "normal"
    SAFETY_CRITICAL = "safety_critical"


class KBVersionPolicy:
    """KB 版本管理 2 层决策机制。

    Args:
        current_version: 当前默认 KB 版本字符串。
        session_version_lock: 是否启用会话级版本锁定（一期默认 True）。
    """

    def __init__(
        self,
        current_version: str,
        session_version_lock: bool = True,
    ) -> None:
        self._current = parse_kb_version(current_version)
        self._session_version_lock = session_version_lock
        self._locked_sessions: dict[str, KBVersion] = {}
        self._latest_safety_critical: KBVersion | None = None
        self._active_sessions: set[str] = set()

    @property
    def current_version(self) -> KBVersion | None:
        """当前默认版本。"""
        return self._current

    @property
    def active_session_count(self) -> int:
        """当前活跃会话数。"""
        return len(self._active_sessions)

    def resolve_version(
        self,
        requested_version: str | None = None,
        session_id: str | None = None,
    ) -> KBVersion | None:
        """4 步缓存读取判定链。

        Step 1: 请求指定版本 → 优先使用
        Step 2: 会话锁定版本 → 使用锁定版本
        Step 3: 最新 safety_critical 版本 → 即时切换
        Step 4: 当前默认版本 → fallback

        Args:
            requested_version: 请求中显式指定的 KB 版本。
            session_id: 当前会话 ID（用于版本锁定查找）。

        Returns:
            解析后的 KBVersion，或 None（当 current_version 无效时）。
        """
        # Step 1: 请求指定版本
        if requested_version:
            return parse_kb_version(requested_version)
        # Step 2: 会话锁定版本
        if session_id and self._session_version_lock:
            locked = self._locked_sessions.get(session_id)
            if locked:
                return locked
        # Step 3: 最新 safety_critical 版本
        if self._latest_safety_critical:
            return self._latest_safety_critical
        # Step 4: 当前默认版本
        return self._current

    def on_kb_update(
        self,
        new_version: str,
        severity: UpdateSeverity = UpdateSeverity.NORMAL,
    ) -> set[str]:
        """处理 KB 版本更新。

        Args:
            new_version: 新版本字符串。
            severity: 更新严重级别。

        Returns:
            被终止的会话 ID 集合（仅 safety_critical 更新时会终止会话）。
        """
        terminated: set[str] = set()
        new_ver = parse_kb_version(new_version)
        if severity == UpdateSeverity.SAFETY_CRITICAL:
            self._latest_safety_critical = new_ver
            terminated = set(self._active_sessions)
            self._active_sessions.clear()
            self._locked_sessions.clear()
        self._current = new_ver
        return terminated

    def register_session(self, session_id: str) -> None:
        """注册新会话并锁定当前版本。

        Args:
            session_id: 会话 ID。
        """
        self._active_sessions.add(session_id)
        if self._session_version_lock and self._current:
            self._locked_sessions[session_id] = self._current

    def unregister_session(self, session_id: str) -> None:
        """注销会话并移除版本锁定。

        Args:
            session_id: 会话 ID。
        """
        self._active_sessions.discard(session_id)
        self._locked_sessions.pop(session_id, None)


__all__ = [
    "KBVersionPolicy",
    "UpdateSeverity",
]
