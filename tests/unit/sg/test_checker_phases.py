from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from safety_gateway.checker.phase_base import CheckContext, CheckPhase
from safety_gateway.validation_result import CheckResult, CheckResultStatus, ConsentStatus


class TestCheckPhaseProtocol:

    def test_concrete_check_phase_instantiable(self):
        class ConcretePhase(CheckPhase):
            async def check(self, context):
                return None
        phase = ConcretePhase()
        assert phase is not None

    def test_base_check_phase_check_raises_not_implemented(self):
        phase = CheckPhase()
        with pytest.raises(NotImplementedError):
            # 用 asyncio.run（每次新建并关闭事件循环）。
            # 不要用 asyncio.get_event_loop()：Python 3.13 下没有当前事件循环时
            # 会抛 RuntimeError，被 pytest.raises(NotImplementedError) 判为失败，
            # 且是否触发取决于同进程内其他用例是否刚关闭过循环（顺序相关）。
            import asyncio
            asyncio.run(phase.check(CheckContext()))


class TestCheckContext:

    def test_default_values(self):
        ctx = CheckContext()
        assert ctx.consent_status is None
        assert ctx.is_timeout is False
        assert ctx.is_db_error is False
        assert ctx.threshold_results == []
        assert ctx.fusion_available is True
        assert ctx.desensitization_degraded is False
        assert ctx.edge_connected is True
        assert ctx.conclusion_suppressed is False

    def test_field_assignment(self):
        ctx = CheckContext()
        ctx.consent_status = ConsentStatus.GRANTED
        ctx.fusion_available = False
        assert ctx.consent_status == ConsentStatus.GRANTED
        assert ctx.fusion_available is False

    def test_cross_phase_state_accumulation(self):
        ctx = CheckContext()
        ctx.threshold_results.append({"organ": "thyroid", "passed": True})
        assert len(ctx.threshold_results) == 1


class TestConsistencyPhaseContradiction:

    def test_contradiction_detected(self):
        from safety_gateway.checker.consistency_phase import ConsistencyPhase
        phase = ConsistencyPhase()
        assert phase._has_contradiction_in_sentences("良性结节", "恶性病变")

    def test_no_contradiction(self):
        from safety_gateway.checker.consistency_phase import ConsistencyPhase
        phase = ConsistencyPhase()
        assert not phase._has_contradiction_in_sentences("良性结节", "良性表现")

    def test_negation_prevents_contradiction(self):
        from safety_gateway.checker.consistency_phase import ConsistencyPhase
        phase = ConsistencyPhase()
        assert not phase._has_contradiction_in_sentences("良性结节", "非恶性表现")

    def test_conclusion_evidence_contradiction(self):
        from safety_gateway.checker.consistency_phase import ConsistencyPhase
        phase = ConsistencyPhase()
        assert phase._check_conclusion_evidence_contradiction("良性病变", "恶性特征明显")

    def test_fusion_self_consistency_no_conflict(self):
        from safety_gateway.checker.consistency_phase import ConsistencyPhase
        phase = ConsistencyPhase()
        fusion_output = {
            "diagnostic_conclusion": {
                "conclusion": "良性结节",
                "evidence": "边界清晰，形态规则",
            }
        }
        conflicts = phase._check_fusion_self_consistency(fusion_output)
        assert len(conflicts) == 0

    def test_fusion_self_consistency_with_conflict(self):
        from safety_gateway.checker.consistency_phase import ConsistencyPhase
        phase = ConsistencyPhase()
        fusion_output = {
            "diagnostic_conclusion": {
                "conclusion": "良性病变",
                "evidence": "恶性特征明显",
            }
        }
        conflicts = phase._check_fusion_self_consistency(fusion_output)
        assert len(conflicts) == 1
        assert conflicts[0]["type"] == "fusion_self_contradiction"


class TestSafetyCheckPipelineShortCircuit:

    @pytest.mark.asyncio
    async def test_consent_denied_short_circuits(self):
        from safety_gateway.checker.orchestrator import SafetyGatewayChecker
        checker = SafetyGatewayChecker()
        with patch('safety_gateway.checker.consent_phase.get_consent_checker') as mock_get_cc:
            mock_cc = AsyncMock()
            mock_cc.check_consent_status = AsyncMock(
                return_value=MagicMock(status=ConsentStatus.DENIED, timeout=False, error=False)
            )
            mock_get_cc.return_value = mock_cc
            result = await checker.perform_full_check(
                "P001",
                {"organ": "thyroid", "diagnostic_conclusion": {"conclusion": "test"}},
            )
            assert result.status == CheckResultStatus.FAILED

    @pytest.mark.asyncio
    async def test_consent_granted_continues_to_desensitization(self):
        from safety_gateway.checker.orchestrator import SafetyGatewayChecker
        from safety_gateway.consent_checker import ConsentCheckResult
        checker = SafetyGatewayChecker()
        with patch('safety_gateway.checker.consent_phase.get_consent_checker') as mock_get_cc:
            mock_cc = AsyncMock()
            mock_cc.check_consent_status = AsyncMock(
                return_value=ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False)
            )
            mock_get_cc.return_value = mock_cc
            with patch.object(checker._desensitization_phase, 'check_with_params', new_callable=AsyncMock) as mock_desens:
                mock_desens.return_value = CheckResult.passed(
                    consent_status=ConsentStatus.GRANTED,
                    message="通过",
                    elapsed_ms=1.0,
                )
                result = await checker.perform_full_check(
                    "P001",
                    {"organ": "thyroid", "diagnostic_conclusion": {"conclusion": "test"}},
                )
                assert result.status == CheckResultStatus.PASSED
