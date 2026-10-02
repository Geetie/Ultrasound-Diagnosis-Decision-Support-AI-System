from unittest.mock import AsyncMock, MagicMock

import pytest

from app.observability.audit_chain import (
    AuditChainVerifier,
    CriticalOperationAudit,
    is_critical_operation,
)


@pytest.mark.track_obs
class TestIsCriticalOperation:
    def test_threshold_adjustment_is_critical(self):
        assert is_critical_operation("threshold_adjustment") is True

    def test_model_version_change_is_critical(self):
        assert is_critical_operation("model_version_change") is True

    def test_kb_update_is_critical(self):
        assert is_critical_operation("kb_update") is True

    def test_desensitization_rule_update_is_critical(self):
        assert is_critical_operation("desensitization_rule_update") is True

    def test_normal_operation_not_critical(self):
        assert is_critical_operation("diagnosis_request") is False

    def test_empty_string_not_critical(self):
        assert is_critical_operation("") is False


@pytest.mark.track_obs
class TestCriticalOperationAudit:
    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        return db

    def _make_scalar_result(self, value):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = value
        return mock_result

    @pytest.mark.asyncio
    async def test_log_critical_operation_first(self, mock_db):
        mock_db.execute = AsyncMock(return_value=self._make_scalar_result(None))

        audit = CriticalOperationAudit(mock_db)
        log = await audit.log_critical_operation(
            operation="threshold_adjustment",
            operator="admin",
            before_value="0.85",
            after_value="0.90",
        )

        assert log.log_type == "critical"
        assert log.action == "threshold_adjustment"
        assert log.actor == "admin"
        assert log.target_type == "critical_operation"
        assert log.detail["is_critical_operation"] is True
        assert log.detail["before_value"] == "0.85"
        assert log.detail["after_value"] == "0.90"
        assert log.hash_prev is None
        assert log.hash_self is not None

    @pytest.mark.asyncio
    async def test_log_critical_operation_chain(self, mock_db):
        prev_log = MagicMock()
        prev_log.hash_self = "abc123"

        mock_db.execute = AsyncMock(return_value=self._make_scalar_result(prev_log))

        audit = CriticalOperationAudit(mock_db)
        log = await audit.log_critical_operation(
            operation="threshold_adjustment",
            operator="admin",
            before_value="0.90",
            after_value="0.85",
        )

        assert log.hash_prev == "abc123"
        assert log.hash_self is not None
        assert log.hash_self != "abc123"

    @pytest.mark.asyncio
    async def test_log_with_session_id(self, mock_db):
        mock_db.execute = AsyncMock(return_value=self._make_scalar_result(None))

        audit = CriticalOperationAudit(mock_db)
        log = await audit.log_critical_operation(
            operation="kb_update",
            operator="system",
            session_id="session-123",
        )

        assert log.session_id == "session-123"


@pytest.mark.track_obs
class TestAuditChainVerifier:
    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    def _make_scalars_result(self, items):
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = items
        mock_result.scalars.return_value = mock_scalars
        return mock_result

    @pytest.mark.asyncio
    async def test_verify_session_chain_valid(self, mock_db):
        log1 = MagicMock()
        log1.hash_prev = None
        log1.hash_self = "hash1"
        log2 = MagicMock()
        log2.hash_prev = "hash1"
        log2.hash_self = "hash2"

        mock_db.execute = AsyncMock(return_value=self._make_scalars_result([log1, log2]))

        verifier = AuditChainVerifier(mock_db)
        valid, broken_id, expected, actual = await verifier.verify_session_chain("session-1")
        assert valid is True

    @pytest.mark.asyncio
    async def test_verify_session_chain_broken(self, mock_db):
        log1 = MagicMock()
        log1.hash_prev = None
        log1.hash_self = "hash1"
        log2 = MagicMock()
        log2.hash_prev = "tampered"
        log2.hash_self = "hash2"

        mock_db.execute = AsyncMock(return_value=self._make_scalars_result([log1, log2]))

        verifier = AuditChainVerifier(mock_db)
        valid, broken_id, expected, actual = await verifier.verify_session_chain("session-1")
        assert valid is False
        assert expected == "hash1"
        assert actual == "tampered"

    @pytest.mark.asyncio
    async def test_verify_session_chain_first_has_prev(self, mock_db):
        log1 = MagicMock()
        log1.hash_prev = "should_be_none"
        log1.hash_self = "hash1"

        mock_db.execute = AsyncMock(return_value=self._make_scalars_result([log1]))

        verifier = AuditChainVerifier(mock_db)
        valid, broken_id, expected, actual = await verifier.verify_session_chain("session-1")
        assert valid is False

    @pytest.mark.asyncio
    async def test_verify_all(self, mock_db):
        mock_db.execute = AsyncMock(return_value=self._make_scalars_result([]))

        verifier = AuditChainVerifier(mock_db)
        results = await verifier.verify_all()
        assert results["all_valid"] is True
        assert results["session_chains"]["total"] == 0
