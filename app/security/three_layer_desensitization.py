"""
三层脱敏集成模块
第一层：标准化层 - 数据进入系统时立即脱敏
第二层：融合层 - LLM输出后额外校验脱敏
第三层：SafetyGateway - 最终输出前脱敏兜底
"""
import threading
from enum import Enum
from typing import Any

import structlog

from app.fusion.desensitization import fusion_desensitization_engine
from app.security.desensitization import desensitization_engine
from app.services.safety_gateway_client import safety_gateway_client

logger = structlog.get_logger()


class DesensitizationLayer(str, Enum):
    STANDARDIZATION = "standardization"
    FUSION = "fusion"
    SAFETY_GATEWAY = "safety_gateway"


class ThreeLayerDesensitizationResult:
    """三层脱敏结果"""
    def __init__(self):
        self.layer_results: dict[DesensitizationLayer, dict[str, Any]] = {}
        self.final_data: dict[str, Any] | None = None
        self.all_passed: bool = True
        self.security_check_failed: bool = False
        self.degraded_mode: bool = False

    def add_layer_result(self, layer: DesensitizationLayer, result: dict[str, Any]):
        self.layer_results[layer] = result

    def set_final_data(self, data: dict[str, Any]):
        self.final_data = data

    @property
    def pii_leakage_detected(self) -> bool:
        return self.security_check_failed


class ThreeLayerDesensitizationManager:
    """三层脱敏管理器"""

    def __init__(self):
        logger.info("three_layer_desensitization_manager_initialized")

    async def _store_mappings_recursive(
        self,
        restore_service,
        session_id: str,
        original: dict[str, Any],
        desensitized: dict[str, Any],
        prefix: str = "",
    ) -> None:
        for field_key, original_value in original.items():
            desens_value = desensitized.get(field_key, original_value)
            full_key = f"{prefix}{field_key}" if not prefix else f"{prefix}.{field_key}"
            if isinstance(original_value, dict) and isinstance(desens_value, dict):
                await self._store_mappings_recursive(
                    restore_service, session_id, original_value, desens_value, full_key
                )
            elif isinstance(original_value, str) and original_value != desens_value:
                await restore_service.store_mapping(
                    session_id=session_id,
                    field_type=full_key,
                    original_value=original_value,
                    desensitized_value=desens_value,
                )

    async def process_standardization_layer(
        self,
        data: dict[str, Any],
        session_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """
        第一层：标准化层脱敏

        在数据进入系统时立即执行，防止敏感数据在内部流转
        """
        logger.debug("standardization_layer_processing")

        desensitized_data = desensitization_engine.desensitize_dict(data)

        if session_id and desensitized_data != data:
            try:
                from app.security.desensitization_restore import get_desensitization_restore_service
                restore_service = get_desensitization_restore_service()
                await self._store_mappings_recursive(
                    restore_service, session_id, data, desensitized_data
                )
            except Exception as e:
                logger.warning("failed_to_store_desensitization_mapping", error=str(e))

        result = {
            "layer": DesensitizationLayer.STANDARDIZATION,
            "desensitized": desensitized_data,
            "modified": data != desensitized_data,
            "degraded": False,
        }

        return desensitized_data, result

    async def process_fusion_layer(
        self,
        data: dict[str, Any],
        fusion_desensitization_degraded: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """
        第二层：融合层脱敏

        在LLM输出后执行，防止推理过程中意外生成的身份信息

        Args:
            data: 待脱敏数据
            fusion_desensitization_degraded: 融合层脱敏服务是否降级
                （由调用方根据融合层脱敏服务状态传入，True表示脱敏服务超时/不可用）
        """
        logger.debug("fusion_layer_processing")

        desensitized_data = fusion_desensitization_engine.desensitize_dict(data)

        result = {
            "layer": DesensitizationLayer.FUSION,
            "desensitized": desensitized_data,
            "modified": data != desensitized_data,
            "degraded": fusion_desensitization_degraded,
        }

        return desensitized_data, result

    async def process_safety_gateway_layer(
        self,
        patient_id: str,
        data: dict[str, Any],
        consent_status_at_input: str | None = None,
        desensitization_degraded: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        logger.debug("safety_gateway_layer_processing", patient_id=patient_id)

        if consent_status_at_input is None:
            logger.error("consent_status_at_input_is_none", patient_id=patient_id)
            raise ValueError("consent_status_at_input must not be None — unconfirmed consent status must be explicitly rejected")

        check_result = await safety_gateway_client.check_output(
            session_id=f"sg-layer-{patient_id}",
            patient_id=patient_id,
            output_data=data,
            consent_status_at_input=consent_status_at_input,
            desensitization_degraded=desensitization_degraded,
        )

        degraded_mode = check_result.get("degraded_mode", False)
        degraded_output = check_result.get("degraded_output")
        final_data = degraded_output if degraded_output else data

        result = {
            "layer": DesensitizationLayer.SAFETY_GATEWAY,
            "desensitized": data,
            "final": final_data,
            "check_result": check_result,
            "modified": data != final_data,
            "degraded": degraded_mode,
        }

        return final_data, result

    async def process_full_pipeline(
        self,
        patient_id: str,
        input_data: dict[str, Any],
        llm_output_data: dict[str, Any] | None = None,
        consent_status_at_input: str | None = None,
        fusion_desensitization_degraded: bool = False,
    ) -> ThreeLayerDesensitizationResult:
        """
        执行完整的三层脱敏流程

        Args:
            patient_id: 患者ID
            input_data: 原始输入数据（用于第一层）
            llm_output_data: LLM输出数据（用于第二层和第三层）
            consent_status_at_input: 入口处同意状态
            fusion_desensitization_degraded: 融合层脱敏服务是否降级
                （对齐01-系统架构设计降级策略总表"融合层脱敏不可用"场景，
                True时SG将触发DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED→E4005A1）
        """
        result = ThreeLayerDesensitizationResult()

        # 第一层：标准化层 - 处理输入数据
        if input_data:
            standardized_data, layer1_result = await self.process_standardization_layer(input_data)
            result.add_layer_result(DesensitizationLayer.STANDARDIZATION, layer1_result)
        else:
            standardized_data = {}

        # 使用LLM输出或标准化后的数据
        current_data = llm_output_data if llm_output_data is not None else (standardized_data if input_data else {})

        # 第二层：融合层 - 处理LLM输出
        layer2_degraded = False
        if current_data:
            fused_data, layer2_result = await self.process_fusion_layer(
                current_data,
                fusion_desensitization_degraded=fusion_desensitization_degraded,
            )
            result.add_layer_result(DesensitizationLayer.FUSION, layer2_result)
            layer2_degraded = layer2_result.get("degraded", False)
        else:
            fused_data = current_data

        # 第三层：SafetyGateway - 最终校验
        # desensitization_degraded语义：融合层脱敏服务是否降级（第2层），
        # 而非标准化层是否修改了数据（第1层modified≠降级）。
        # 对齐01-系统架构设计§3.4降级策略总表。
        if fused_data:
            final_data, layer3_result = await self.process_safety_gateway_layer(
                patient_id, fused_data,
                consent_status_at_input=consent_status_at_input,
                desensitization_degraded=layer2_degraded,
            )
            result.add_layer_result(DesensitizationLayer.SAFETY_GATEWAY, layer3_result)
            result.set_final_data(final_data)

        # 语义修正：区分"已执行脱敏(modified)"与"安全校验失败(allowed=False)"
        # - 第1/2层 modified=True 表示脱敏引擎正常工作（预期行为）
        # - 第3层(SafetyGateway) check_result.allowed=False 表示安全校验未通过（真正失败）
        # - any_pii_leakage: 任一层检测到残留敏感数据且未被处理
        has_security_violation = False
        has_degradation = False
        for layer_type, layer_result in result.layer_results.items():
            check_result = layer_result.get("check_result", {})
            passed = check_result.get("passed", True)
            degraded = check_result.get("degraded_mode", False)
            if not passed:
                has_security_violation = True
            if degraded:
                has_degradation = True

        result.all_passed = not has_security_violation and not has_degradation
        result.security_check_failed = has_security_violation or has_degradation
        result.degraded_mode = has_degradation

        logger.info(
            "three_layer_desensitization_completed",
            patient_id=patient_id,
            all_passed=result.all_passed,
            layers_processed=len(result.layer_results)
        )

        return result


_three_layer_manager_instance: ThreeLayerDesensitizationManager | None = None
_three_layer_manager_lock = threading.Lock()


def get_three_layer_desensitization_manager() -> ThreeLayerDesensitizationManager:
    global _three_layer_manager_instance
    if _three_layer_manager_instance is None:
        with _three_layer_manager_lock:
            if _three_layer_manager_instance is None:
                _three_layer_manager_instance = ThreeLayerDesensitizationManager()
    return _three_layer_manager_instance


three_layer_manager = get_three_layer_desensitization_manager()
