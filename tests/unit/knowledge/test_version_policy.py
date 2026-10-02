"""Tests for app.knowledge.version_policy — KB 版本管理 2 层决策机制。"""
from __future__ import annotations

import pytest

from app.knowledge.version import parse_kb_version
from app.knowledge.version_policy import KBVersionPolicy, UpdateSeverity

# ------------------------------------------------------------------
# 4 步判定链测试
# ------------------------------------------------------------------

class TestResolveVersion4StepChain:
    """Test the 4-step cache read judgment chain."""

    @pytest.fixture
    def policy(self) -> KBVersionPolicy:
        return KBVersionPolicy(current_version="kb_v1.0.0")

    def test_step1_requested_version_takes_priority(self, policy: KBVersionPolicy) -> None:
        """Step 1: 请求指定版本 → 最高优先级。"""
        result = policy.resolve_version(requested_version="kb_v2.0.0")
        assert result == parse_kb_version("kb_v2.0.0")

    def test_step2_session_locked_version(self, policy: KBVersionPolicy) -> None:
        """Step 2: 会话锁定版本 → 请求未指定时使用锁定版本。"""
        policy.register_session("sess_1")
        # 版本锁定为 kb_v1.0.0
        result = policy.resolve_version(session_id="sess_1")
        assert result == parse_kb_version("kb_v1.0.0")

    def test_step2_session_lock_disabled(self) -> None:
        """Step 2: 会话锁定禁用时不使用锁定版本。"""
        policy = KBVersionPolicy(current_version="kb_v1.0.0", session_version_lock=False)
        policy.register_session("sess_1")
        # 仍应 fallback 到 step 4
        result = policy.resolve_version(session_id="sess_1")
        assert result == parse_kb_version("kb_v1.0.0")

    def test_step3_safety_critical_version(self, policy: KBVersionPolicy) -> None:
        """Step 3: 最新 safety_critical 版本 → 无会话锁定时即时切换。"""
        policy.on_kb_update("kb_v1.1.0", severity=UpdateSeverity.SAFETY_CRITICAL)
        result = policy.resolve_version()
        assert result == parse_kb_version("kb_v1.1.0")

    def test_step4_current_default_version(self, policy: KBVersionPolicy) -> None:
        """Step 4: 当前默认版本 → fallback。"""
        result = policy.resolve_version()
        assert result == parse_kb_version("kb_v1.0.0")

    def test_step1_overrides_step2(self, policy: KBVersionPolicy) -> None:
        """Step 1 优先于 Step 2。"""
        policy.register_session("sess_1")
        result = policy.resolve_version(requested_version="kb_v2.0.0", session_id="sess_1")
        assert result == parse_kb_version("kb_v2.0.0")

    def test_step2_overrides_step3(self, policy: KBVersionPolicy) -> None:
        """Step 2 优先于 Step 3。"""
        policy.register_session("sess_1")
        policy.on_kb_update("kb_v1.1.0", severity=UpdateSeverity.SAFETY_CRITICAL)
        # sess_1 被 safety_critical 终止了，重新注册
        policy.register_session("sess_1")
        result = policy.resolve_version(session_id="sess_1")
        # 锁定版本是 safety_critical 后的新版本 kb_v1.1.0
        assert result == parse_kb_version("kb_v1.1.0")


# ------------------------------------------------------------------
# safety_critical 即时失效测试
# ------------------------------------------------------------------

class TestSafetyCriticalInvalidation:
    """Test safety_critical update terminates all active sessions."""

    def test_safety_critical_terminates_all_sessions(self) -> None:
        policy = KBVersionPolicy(current_version="kb_v1.0.0")
        policy.register_session("sess_1")
        policy.register_session("sess_2")
        policy.register_session("sess_3")
        assert policy.active_session_count == 3

        terminated = policy.on_kb_update("kb_v1.1.0", severity=UpdateSeverity.SAFETY_CRITICAL)
        assert terminated == {"sess_1", "sess_2", "sess_3"}
        assert policy.active_session_count == 0

    def test_normal_update_does_not_terminate_sessions(self) -> None:
        policy = KBVersionPolicy(current_version="kb_v1.0.0")
        policy.register_session("sess_1")
        policy.register_session("sess_2")

        terminated = policy.on_kb_update("kb_v1.1.0", severity=UpdateSeverity.NORMAL)
        assert terminated == set()
        assert policy.active_session_count == 2

    def test_safety_critical_clears_locked_sessions(self) -> None:
        policy = KBVersionPolicy(current_version="kb_v1.0.0")
        policy.register_session("sess_1")

        policy.on_kb_update("kb_v1.1.0", severity=UpdateSeverity.SAFETY_CRITICAL)
        # After safety_critical, session lock is cleared
        result = policy.resolve_version(session_id="sess_1")
        # Should fall through to step 3 (safety_critical version)
        assert result == parse_kb_version("kb_v1.1.0")

    def test_safety_critical_updates_latest(self) -> None:
        policy = KBVersionPolicy(current_version="kb_v1.0.0")
        policy.on_kb_update("kb_v1.1.0", severity=UpdateSeverity.SAFETY_CRITICAL)
        result = policy.resolve_version()
        assert result == parse_kb_version("kb_v1.1.0")


# ------------------------------------------------------------------
# 会话管理测试
# ------------------------------------------------------------------

class TestSessionManagement:
    """Test session registration and unregistration."""

    def test_register_and_unregister(self) -> None:
        policy = KBVersionPolicy(current_version="kb_v1.0.0")
        policy.register_session("sess_1")
        assert policy.active_session_count == 1
        policy.unregister_session("sess_1")
        assert policy.active_session_count == 0

    def test_unregister_nonexistent_session_is_noop(self) -> None:
        policy = KBVersionPolicy(current_version="kb_v1.0.0")
        policy.unregister_session("nonexistent")  # Should not raise
        assert policy.active_session_count == 0

    def test_register_duplicate_session_is_idempotent(self) -> None:
        policy = KBVersionPolicy(current_version="kb_v1.0.0")
        policy.register_session("sess_1")
        policy.register_session("sess_1")  # Duplicate
        assert policy.active_session_count == 1

    def test_session_version_lock_tracks_version(self) -> None:
        policy = KBVersionPolicy(current_version="kb_v1.0.0")
        policy.register_session("sess_1")
        # Update to new version
        policy.on_kb_update("kb_v1.1.0", severity=UpdateSeverity.NORMAL)
        policy.register_session("sess_2")
        # sess_1 should still be locked to kb_v1.0.0 (normal update doesn't terminate)
        result_1 = policy.resolve_version(session_id="sess_1")
        assert result_1 == parse_kb_version("kb_v1.0.0")
        # sess_2 should be locked to kb_v1.1.0
        result_2 = policy.resolve_version(session_id="sess_2")
        assert result_2 == parse_kb_version("kb_v1.1.0")


# ------------------------------------------------------------------
# current_version property
# ------------------------------------------------------------------

class TestCurrentVersion:
    def test_current_version_reflects_updates(self) -> None:
        policy = KBVersionPolicy(current_version="kb_v1.0.0")
        assert policy.current_version == parse_kb_version("kb_v1.0.0")
        policy.on_kb_update("kb_v1.1.0")
        assert policy.current_version == parse_kb_version("kb_v1.1.0")
