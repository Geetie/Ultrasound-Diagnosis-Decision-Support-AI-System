from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.track_sec, pytest.mark.sec_domain_desens]

from app.security.three_layer_desensitization import (
    ThreeLayerDesensitizationManager,
    ThreeLayerDesensitizationResult,
    DesensitizationLayer,
)

PII_INPUT_DATA = {
    "patient_name": "患者张三",
    "id_card": "110101199001011234",
    "phone": "13812345678",
}

SG_PASS_RESULT = {
    "passed": True,
    "degraded_mode": False,
    "consent_status_at_output": "granted",
}

SG_FAIL_RESULT = {
    "passed": False,
    "degraded_mode": True,
    "error_code": "E4005A1",
    "consent_status_at_output": "unknown_blocked",
}

SG_DEGRADED_RESULT = {
    "passed": True,
    "degraded_mode": True,
    "consent_status_at_output": "granted",
}


class TestThreeLayerPipeline:

    @pytest.mark.asyncio
    async def test_full_pipeline_all_layers_pass(self):
        manager = ThreeLayerDesensitizationManager()

        with patch(
            "app.services.safety_gateway_client.safety_gateway_client.check_output",
            new_callable=AsyncMock,
            return_value=SG_PASS_RESULT,
        ):
            result = await manager.process_full_pipeline(
                patient_id="P001",
                input_data=PII_INPUT_DATA,
                consent_status_at_input="granted",
            )

        assert result.all_passed is True
        assert result.security_check_failed is False
        assert result.final_data is not None
        assert "110101199001011234" not in str(result.final_data.values())
        assert "13812345678" not in str(result.final_data.values())

    @pytest.mark.asyncio
    async def test_full_pipeline_consent_status_none_raises(self):
        manager = ThreeLayerDesensitizationManager()

        with patch(
            "app.services.safety_gateway_client.safety_gateway_client.check_output",
            new_callable=AsyncMock,
            return_value=SG_PASS_RESULT,
        ):
            with pytest.raises(ValueError, match="consent_status_at_input must not be None"):
                await manager.process_full_pipeline(
                    patient_id="P001",
                    input_data=PII_INPUT_DATA,
                    consent_status_at_input=None,
                )

    @pytest.mark.asyncio
    async def test_full_pipeline_sg_check_fails(self):
        manager = ThreeLayerDesensitizationManager()

        with patch(
            "app.services.safety_gateway_client.safety_gateway_client.check_output",
            new_callable=AsyncMock,
            return_value=SG_FAIL_RESULT,
        ):
            result = await manager.process_full_pipeline(
                patient_id="P001",
                input_data=PII_INPUT_DATA,
                consent_status_at_input="granted",
            )

        assert result.security_check_failed is True
        assert result.all_passed is False

    @pytest.mark.asyncio
    async def test_full_pipeline_sg_degraded_mode(self):
        manager = ThreeLayerDesensitizationManager()

        with patch(
            "app.services.safety_gateway_client.safety_gateway_client.check_output",
            new_callable=AsyncMock,
            return_value=SG_DEGRADED_RESULT,
        ):
            result = await manager.process_full_pipeline(
                patient_id="P001",
                input_data=PII_INPUT_DATA,
                consent_status_at_input="granted",
            )

        assert result.degraded_mode is True
        assert result.all_passed is False

    @pytest.mark.asyncio
    async def test_full_pipeline_fusion_degraded_propagates_to_sg(self):
        manager = ThreeLayerDesensitizationManager()

        with patch(
            "app.services.safety_gateway_client.safety_gateway_client.check_output",
            new_callable=AsyncMock,
            return_value=SG_PASS_RESULT,
        ) as mock_sg:
            await manager.process_full_pipeline(
                patient_id="P001",
                input_data=PII_INPUT_DATA,
                consent_status_at_input="granted",
                fusion_desensitization_degraded=True,
            )

        _, kwargs = mock_sg.call_args
        assert kwargs.get("desensitization_degraded") is True

    @pytest.mark.asyncio
    async def test_full_pipeline_empty_input_data(self):
        manager = ThreeLayerDesensitizationManager()

        with patch(
            "app.services.safety_gateway_client.safety_gateway_client.check_output",
            new_callable=AsyncMock,
            return_value=SG_PASS_RESULT,
        ):
            result = await manager.process_full_pipeline(
                patient_id="P001",
                input_data={},
                consent_status_at_input="granted",
            )

        assert result.all_passed is True
        assert result.security_check_failed is False

    @pytest.mark.asyncio
    async def test_full_pipeline_with_llm_output(self):
        manager = ThreeLayerDesensitizationManager()

        llm_output = {
            "diagnosis": "甲状腺结节，患者张三需复查",
            "phone": "13812345678",
        }

        with patch(
            "app.services.safety_gateway_client.safety_gateway_client.check_output",
            new_callable=AsyncMock,
            return_value=SG_PASS_RESULT,
        ) as mock_sg:
            result = await manager.process_full_pipeline(
                patient_id="P001",
                input_data=PII_INPUT_DATA,
                llm_output_data=llm_output,
                consent_status_at_input="granted",
            )

        sg_call_data = mock_sg.call_args[1].get("output_data") or mock_sg.call_args[0][2]
        assert "甲状腺结节" in str(sg_call_data)

    @pytest.mark.asyncio
    async def test_full_pipeline_pii_leakage_detected_property(self):
        manager = ThreeLayerDesensitizationManager()

        with patch(
            "app.services.safety_gateway_client.safety_gateway_client.check_output",
            new_callable=AsyncMock,
            return_value=SG_FAIL_RESULT,
        ):
            result = await manager.process_full_pipeline(
                patient_id="P001",
                input_data=PII_INPUT_DATA,
                consent_status_at_input="granted",
            )

        assert result.pii_leakage_detected is True
        assert result.pii_leakage_detected == result.security_check_failed
