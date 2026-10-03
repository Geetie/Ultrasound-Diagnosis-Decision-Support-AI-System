"""边缘端主流程编排：帧 → 导航推理 → 本地安全校验 → 上行推送。

把 EDG 各模块串成一个可用系统，并落实两条门控规则：

1. **温度门控**（T-GB-EDG-04）：由 :class:`NavigationInferenceService` 依据
   GPU 温度档位决定是否推理/隔帧；紧急降级时连本地推理也停。
2. **隔离门控**（T-GB-EDG-02）：设备被隔离时**停止上行**，但**继续本地推理**
   ——医生仍能看到叠加结果，只是不进入后端/其他终端。这是"隔离"与"停机"的
   区别，也是安全侧更保守的选择（宁可不上行，也不让脏数据进入后端）。

上行失败不计为业务失败：结果已经本地产出，队列由 WS 客户端负责补发。
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog

from edge.device_manager import EdgeDeviceManager
from edge.gpu_thermal import ThermalDecision
from edge.navigation import NavigationInferenceService
from edge.protocols import Frame, NavResult

logger = structlog.get_logger(__name__)


class EdgePipeline:
    """边缘端帧处理管线（同步推理 + 可选异步上行）。"""

    def __init__(
        self,
        nav_service: NavigationInferenceService,
        *,
        uplink: Any = None,
        device_manager: EdgeDeviceManager | None = None,
    ) -> None:
        self._nav = nav_service
        self._uplink = uplink
        self._device = device_manager
        self._frames_seen = 0
        self._results_produced = 0
        self._results_pushed = 0
        self._push_suppressed = 0

    @property
    def should_uplink(self) -> bool:
        if self._device is None:
            return True
        return self._device.should_uplink

    async def on_frame(
        self, frame: Frame, now: float | None = None
    ) -> NavResult | None:
        """处理一帧。

        Args:
            now: 单调时钟秒；默认由推理服务取当前时间。显式传入可用于
                确定性重放/离线评估（也避免 Windows 上 time.monotonic()
                约 15.6ms 的粒度让相邻两帧被判为"同时"而被节流跳过）。

        Returns:
            本地推理结果（None 表示本帧被温度策略跳过）。
        """
        self._frames_seen += 1

        # 并发审查 P0-5：process_frame 内含模型推理 + 切面评估 + 图像质量评估
        # （edge/navigation.py:169/207/208），是数十~数百毫秒级的同步 CPU/GPU
        # 工作。超声帧率下**每帧**都阻塞事件循环，WS 上行与心跳被整段卡住。
        # offload 到线程池。
        result = await asyncio.to_thread(self._nav.process_frame, frame, now=now)
        if result is None:
            return None

        self._results_produced += 1

        if self._uplink is None:
            return result

        if not self.should_uplink:
            self._push_suppressed += 1
            logger.info(
                "edge_uplink_suppressed_device_isolated",
                frame_id=frame.frame_id,
                state=self._device.state.value if self._device else None,
            )
            return result

        try:
            pushed = await self._uplink.push_nav_result(result)
        except Exception as e:
            # 上行是旁路：不能让推送失败影响本地诊断能力
            logger.warning("edge_uplink_push_error", frame_id=frame.frame_id, error=str(e))
            return result

        if pushed:
            self._results_pushed += 1
        return result

    def on_temperature(self, celsius: float) -> ThermalDecision:
        """上报温度并刷新推理门控。"""
        return self._nav.record_temperature(celsius)

    def status(self) -> dict[str, Any]:
        health = self._device.health.as_dict() if self._device else None
        return {
            "frames_seen": self._frames_seen,
            "results_produced": self._results_produced,
            "results_pushed": self._results_pushed,
            "push_suppressed": self._push_suppressed,
            "should_uplink": self.should_uplink,
            "navigation": self._nav.stats(),
            "device": health,
        }


__all__ = ["EdgePipeline"]
