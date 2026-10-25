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

    def record_safety_gateway_existing_check(
        self,
        data: dict[str, Any],
        check_result: dict[str, Any],
    ) -> dict[str, Any]:
        """把**既有**的 SG ``check_output`` 结果登记为第三层（不重复调用 SG）。

        背景（2026-09-28 接线审计）：诊断管线已自行调用
        ``safety_gateway_client.check_output``（输入侧同意直查 + 输出侧校验）。
        若第三层改走 :meth:`process_safety_gateway_layer`，会对**同一个请求第二次**
        调用 SG —— 重复审计事件 + 额外延迟 + 双份 E4005 语义。

        因此第三层改为"登记既有结果"：语义（``degraded_mode`` / ``degraded_output``
        / ``final``）与 :meth:`process_safety_gateway_layer` 保持一致，
        但不产生新的 SG 调用。
        """
        degraded_mode = bool(check_result.get("degraded_mode", False))
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
        logger.info(
            "safety_gateway_layer_registered_existing_check",
            passed=check_result.get("passed", check_result.get("allowed")),
            degraded=degraded_mode,
        )
        return result

    async def process_full_pipeline(
        self,
        patient_id: str,
        input_data: dict[str, Any],
        llm_output_data: dict[str, Any] | None = None,
        consent_status_at_input: str | None = None,
        fusion_desensitization_degraded: bool = False,
        safety_gateway_check: dict[str, Any] | None = None,
        recorded_stages: dict[DesensitizationLayer, dict[str, Any]] | None = None,
    ) -> ThreeLayerDesensitizationResult:
        """
        执行完整的三层脱敏流程（**本次接线后成为生产单一入口**）。

        Args:
            patient_id: 患者ID
            input_data: 原始输入数据（用于第一层）
            llm_output_data: LLM输出数据（用于第二层和第三层）
            consent_status_at_input: 入口处同意状态
            fusion_desensitization_degraded: 融合层脱敏服务是否降级
                （对齐01-系统架构设计降级策略总表"融合层脱敏不可用"场景，
                True时SG将触发DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED→E4005A1）
            safety_gateway_check: **既有的** SG ``check_output`` 结果。给了就走
                :meth:`record_safety_gateway_existing_check`（登记，不再调一次 SG）。
                生产管线自己已经调过 SG；若本方法再调一次 → 重复审计事件 +
                额外延迟 + 双份 E4005 语义。
            recorded_stages: 已经在外部分阶段执行过的层结果（``{层: layer_result}``）。
                生产管线的三层**必须**发生在三个不同时刻（LLM 之前 / 落库之前 /
                SG 之后），无法在同一个时间点串行执行 —— 这正是它此前"只用测试
                调用"的原因。给了 ``recorded_stages`` 时，对应层**不再重跑**
                （重跑会二次脱敏、语义被破坏），只登记并参与汇总。

        ## A.88 T5-4：为什么要有 `recorded_stages`

        《架构真相》§3.3 记录：本方法**仅测试调用**，生产是三次分散调用。
        但"三次分散"不是遗漏 —— 是三层在时间上本就分离。于是收口的正确做法
        不是"把三次调用硬塞进一个时刻"，而是：

        * **生产**：三层仍在各自时刻就地执行（语义不变），把各自的
          ``layer_result`` 登记进本次运行台账；运行结束时调**本方法一次**
          （``recorded_stages`` 模式），由它产出**唯一的汇总判定**。
        * **测试/批处理**：三段都给它，它真的串行执行（既有行为不变）。

        这样收口换来的是一个此前生产上**根本不存在**的东西：三层合并后的
        ``all_passed`` / ``security_check_failed`` / ``degraded_mode``
        （此前只有本方法内部计算，而它没被生产调用 → 该判定在生产上恒缺）。
        """
        result = ThreeLayerDesensitizationResult()
        recorded = dict(recorded_stages or {})

        # 第一层：标准化层 - 处理输入数据
        if DesensitizationLayer.STANDARDIZATION in recorded:
            standardized_data = {}
            result.add_layer_result(
                DesensitizationLayer.STANDARDIZATION,
                recorded[DesensitizationLayer.STANDARDIZATION],
            )
        elif input_data:
            standardized_data, layer1_result = await self.process_standardization_layer(input_data)
            result.add_layer_result(DesensitizationLayer.STANDARDIZATION, layer1_result)
        else:
            standardized_data = {}

        # 使用LLM输出或标准化后的数据
        current_data = llm_output_data if llm_output_data is not None else (standardized_data if input_data else {})

        # 第二层：融合层 - 处理LLM输出
        layer2_degraded = False
        if DesensitizationLayer.FUSION in recorded:
            fused_data = current_data
            layer2_result = recorded[DesensitizationLayer.FUSION]
            result.add_layer_result(DesensitizationLayer.FUSION, layer2_result)
            layer2_degraded = layer2_result.get("degraded", False)
        elif current_data:
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
        if DesensitizationLayer.SAFETY_GATEWAY in recorded:
            layer3_result = recorded[DesensitizationLayer.SAFETY_GATEWAY]
            result.add_layer_result(DesensitizationLayer.SAFETY_GATEWAY, layer3_result)
            if "final" in layer3_result:
                result.set_final_data(layer3_result["final"])
        elif fused_data:
            if safety_gateway_check is not None:
                # 生产路径：复用管线**已经**调过的 SG 结果（不重复调用）
                layer3_result = self.record_safety_gateway_existing_check(
                    fused_data, safety_gateway_check
                )
                result.add_layer_result(
                    DesensitizationLayer.SAFETY_GATEWAY, layer3_result
                )
                result.set_final_data(layer3_result.get("final", fused_data))
            else:
                final_data, layer3_result = await self.process_safety_gateway_layer(
                    patient_id, fused_data,
                    consent_status_at_input=consent_status_at_input,
                    desensitization_degraded=layer2_degraded,
                )
                result.add_layer_result(DesensitizationLayer.SAFETY_GATEWAY, layer3_result)
                result.set_final_data(final_data)

        self._apply_aggregate_verdict(result)

        logger.info(
            "three_layer_desensitization_completed",
            patient_id=patient_id,
            all_passed=result.all_passed,
            layers_processed=len(result.layer_results),
            replayed_layers=[layer.value for layer in recorded],
        )

        return result

    @staticmethod
    def _apply_aggregate_verdict(result: ThreeLayerDesensitizationResult) -> None:
        """把三层结果汇总成**唯一**的合规判定（单一事实源）。

        语义修正：区分"已执行脱敏(modified)"与"安全校验失败(allowed=False)"
        - 第1/2层 modified=True 表示脱敏引擎正常工作（预期行为）
        - 第3层(SafetyGateway) check_result.allowed=False 表示安全校验未通过（真正失败）
        - any_pii_leakage: 任一层检测到残留敏感数据且未被处理

        **A.88 T5-4**：抽成独立函数 —— 生产（``recorded_stages`` 模式）与
        测试/批处理（全量执行模式）必须用**同一套**判定，否则两条路径会各自
        漂移出不同的"合规结论"。
        """
        has_security_violation = False
        has_degradation = False
        for _layer_type, layer_result in result.layer_results.items():
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
