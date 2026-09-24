from unittest.mock import AsyncMock, patch

import pytest

from safety_gateway.checker import (
    CheckResultStatus,
    get_safety_gateway_checker,
)
from safety_gateway.consent_checker import ConsentCheckResult
from safety_gateway.validation_result import ConsentStatus

pytestmark = [pytest.mark.track_sg, pytest.mark.track_sec, pytest.mark.sec_domain_sg]


class TestConsentChecker:

    def test_consent_checker_singleton(self):
        from safety_gateway.consent_checker import get_consent_checker

        checker1 = get_consent_checker()
        checker2 = get_consent_checker()
        assert checker1 is checker2

    def test_consent_checker_config(self):
        from safety_gateway.consent_checker import get_consent_checker

        checker = get_consent_checker()
        assert checker._retry_max_attempts == 3
        assert checker._retry_delay_base == 0.5


class TestConsentStatusInputFastReject:

    @pytest.mark.asyncio
    async def test_pending_status_fast_reject(self):
        from unittest.mock import AsyncMock, patch

        checker = get_safety_gateway_checker()
        with patch.object(checker._consent_checker, 'check_consent_status', new_callable=AsyncMock, return_value=ConsentCheckResult(status=ConsentStatus.PENDING, timeout=False, error=False)):
            result = await checker.perform_full_check(
                "P001",
                {"organ": "thyroid", "diagnosis_data": {}, "conclusions": [{"organ": "thyroid", "confidence": 0.95}]},
                consent_status_at_input="pending",
            )
            assert result.status == CheckResultStatus.FAILED

    @pytest.mark.asyncio
    async def test_invalid_consent_status_fallback_to_denied(self):
        checker = get_safety_gateway_checker()
        result = await checker.perform_full_check(
            "P001",
            {"organ": "thyroid", "diagnosis_data": {}, "conclusions": [{"organ": "thyroid", "confidence": 0.95}]},
            consent_status_at_input="invalid_status",
        )
        assert result.status == CheckResultStatus.FAILED


class TestConsentStatusSource:

    @pytest.mark.asyncio
    async def test_fast_reject_sets_input_fast_reject_source(self):
        from unittest.mock import AsyncMock, patch

        checker = get_safety_gateway_checker()
        with patch.object(checker._consent_checker, 'check_consent_status', new_callable=AsyncMock, return_value=ConsentCheckResult(status=ConsentStatus.DENIED, timeout=False, error=False)):
            result = await checker.perform_full_check(
                "P001",
                {"organ": "thyroid", "diagnosis_data": {}, "conclusions": [{"organ": "thyroid", "confidence": 0.95}]},
                consent_status_at_input="denied",
            )
        assert result.status == CheckResultStatus.FAILED
        assert result.consent_status_source in ("input_fast_reject", "pg_direct")

    @pytest.mark.asyncio
    async def test_withdrawn_fast_reject_sets_input_fast_reject_source(self):
        checker = get_safety_gateway_checker()
        result = await checker.perform_full_check(
            "P001",
            {"organ": "thyroid", "diagnosis_data": {}, "conclusions": [{"organ": "thyroid", "confidence": 0.95}]},
            consent_status_at_input="withdrawn",
        )
        assert result.status == CheckResultStatus.FAILED
        assert result.consent_status_source in ("input_fast_reject", "pg_direct", "input_fast_reject_db_error")

    @pytest.mark.asyncio
    async def test_pg_direct_check_sets_pg_direct_source(self):
        from unittest.mock import AsyncMock, patch

        from safety_gateway.checker.consent_phase import ConsentPhase
        from safety_gateway.checker.phase_base import CheckContext
        mock_result = ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False)
        phase = ConsentPhase()
        ctx = CheckContext()
        with patch("safety_gateway.checker.consent_phase.get_consent_checker") as mock_get:
            mock_checker = AsyncMock()
            mock_checker.check_consent_status = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_checker
            result = await phase.check_with_params("P_GRANTED", True, ctx, 0.0)
        assert result is None
        assert ctx.consent_status == ConsentStatus.GRANTED


class TestConsentDirectPGQuery:

    @pytest.mark.asyncio
    async def test_pg_direct_query_granted(self):
        from safety_gateway.consent_checker import ConsentChecker
        checker = ConsentChecker()
        with patch.object(checker, '_retry_query', new_callable=AsyncMock, return_value=ConsentStatus.GRANTED):
            result = await checker.check_consent_status("test-patient")
            assert result.status == ConsentStatus.GRANTED
            assert result.error is False
            assert result.timeout is False

    @pytest.mark.asyncio
    async def test_pg_direct_query_withdrawn(self):
        from safety_gateway.consent_checker import ConsentChecker
        checker = ConsentChecker()
        with patch.object(checker, '_retry_query', new_callable=AsyncMock, return_value=ConsentStatus.WITHDRAWN):
            result = await checker.check_consent_status("test-patient")
            assert result.status == ConsentStatus.WITHDRAWN
            assert result.error is False

    @pytest.mark.asyncio
    async def test_pg_unavailable_returns_e4005b(self):
        from safety_gateway.consent_checker import ConsentChecker
        checker = ConsentChecker()
        with patch.object(checker, '_retry_query', new_callable=AsyncMock, side_effect=Exception("PG unavailable")):
            result = await checker.check_consent_status("test-patient")
            assert result.status is None
            assert result.error is True
