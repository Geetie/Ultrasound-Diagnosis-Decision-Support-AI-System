"""T-GB-EDG-04 边缘端 GPU 温度处理策略。

规格来源：`05-可观测性与运维.md` §GPU 温度（边缘端 Jetson）四级策略：

    < 75°C           正常          导航推理 15fps
    75-80°C          主动降频      导航推理 10fps（提前降频，避免频繁触及 80°C）
    80-85°C          隔帧推理      7.5fps + Warning
    85-90°C          暂停导航推理  + Critical（诊断推理仍可继续）
    > 90°C 持续 60s  紧急降级      暂停所有 GPU 推理 + 保存检查点，提示转人工诊断

设计要点：
- 纯函数式决策（`decide` 不产生副作用），便于单测与复用；
- 「持续 60s」需要时间输入，`now` 可注入，避免测试依赖真实等待；
- 任何降级都必须是"可恢复"的：温度回落后自动逐级恢复。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

# 四级温度边界（℃）
THROTTLE_TEMP = 75.0
FRAME_SKIP_TEMP = 80.0
NAV_PAUSE_TEMP = 85.0
EMERGENCY_TEMP = 90.0

# 紧急降级所需的持续时长（秒）
DEFAULT_OVERHEAT_DURATION_SECONDS = 60.0

# 各档位目标帧率（fps）
NORMAL_FPS = 15.0
THROTTLED_FPS = 10.0
FRAME_SKIP_FPS = 7.5


class ThermalLevel(str, Enum):
    """温度档位（数值越大越严重）。"""

    NORMAL = "normal"
    THROTTLED = "throttled"
    FRAME_SKIP = "frame_skip"
    NAV_PAUSED = "nav_paused"
    EMERGENCY = "emergency"


@dataclass(frozen=True)
class ThermalDecision:
    """温度策略的决策结果。"""

    level: ThermalLevel
    target_fps: float
    nav_inference_allowed: bool
    diagnosis_inference_allowed: bool
    frame_skip: int
    require_checkpoint: bool
    reason: str

    @property
    def inference_paused(self) -> bool:
        return not self.diagnosis_inference_allowed


_NORMAL_DECISION = ThermalDecision(
    level=ThermalLevel.NORMAL,
    target_fps=NORMAL_FPS,
    nav_inference_allowed=True,
    diagnosis_inference_allowed=True,
    frame_skip=0,
    require_checkpoint=False,
    reason="gpu_temperature_normal",
)


class GPUThermalPolicy:
    """按 GPU 温度给出推理调度决策。

    有状态之处仅在于"超过 90°C 已持续多久"，其余为纯分级。
    """

    def __init__(
        self,
        overheat_duration_seconds: float = DEFAULT_OVERHEAT_DURATION_SECONDS,
        checkpoint_interval_seconds: float = 0.0,
    ) -> None:
        self._overheat_duration_seconds = overheat_duration_seconds
        # >0 时表示紧急降级阶段每该秒数重复要求一次 checkpoint（默认只要求一次）
        self._checkpoint_interval_seconds = checkpoint_interval_seconds
        self._emergency_since: Optional[float] = None
        self._checkpoint_issued = False
        self._last_checkpoint_at: Optional[float] = None
        self._current_level = ThermalLevel.NORMAL

    @property
    def current_level(self) -> ThermalLevel:
        return self._current_level

    @property
    def emergency_seconds(self) -> float:
        """已处于 >90°C 状态的时长（秒）；未处于该状态时为 0。"""
        if self._emergency_since is None:
            return 0.0
        return max(0.0, time.monotonic() - self._emergency_since)

    def reset(self) -> None:
        """清空过热计时（温度回落或设备重启时调用）。"""
        self._emergency_since = None
        self._checkpoint_issued = False
        self._last_checkpoint_at = None
        self._current_level = ThermalLevel.NORMAL

    def decide(self, celsius: float, now: Optional[float] = None) -> ThermalDecision:
        """根据温度给出决策。

        Args:
            celsius: 当前 GPU 温度。
            now: 单调时钟秒；默认取 ``time.monotonic()``（测试可注入）。
        """
        now = time.monotonic() if now is None else now

        if celsius < THROTTLE_TEMP:
            self.reset()
            return _NORMAL_DECISION

        if celsius >= EMERGENCY_TEMP:
            if self._emergency_since is None:
                self._emergency_since = now
            elapsed = now - self._emergency_since

            if elapsed >= self._overheat_duration_seconds:
                require_checkpoint = False
                if not self._checkpoint_issued:
                    require_checkpoint = True
                    self._checkpoint_issued = True
                    self._last_checkpoint_at = now
                elif self._checkpoint_interval_seconds > 0 and self._last_checkpoint_at is not None:
                    if now - self._last_checkpoint_at >= self._checkpoint_interval_seconds:
                        require_checkpoint = True
                        self._last_checkpoint_at = now

                self._current_level = ThermalLevel.EMERGENCY
                return ThermalDecision(
                    level=ThermalLevel.EMERGENCY,
                    target_fps=0.0,
                    nav_inference_allowed=False,
                    diagnosis_inference_allowed=False,
                    frame_skip=0,
                    require_checkpoint=require_checkpoint,
                    reason="gpu_overheat_emergency",
                )

            # 超过 90°C 但未达持续时长：与 85-90°C 同档处理（暂停导航）
            self._current_level = ThermalLevel.NAV_PAUSED
            return ThermalDecision(
                level=ThermalLevel.NAV_PAUSED,
                target_fps=0.0,
                nav_inference_allowed=False,
                diagnosis_inference_allowed=True,
                frame_skip=0,
                require_checkpoint=False,
                reason="gpu_temperature_above_90_not_sustained",
            )

        # 温度回落到 90°C 以下 → 清空过热计时（下一轮重新累计）
        self._emergency_since = None
        self._checkpoint_issued = False

        if celsius >= NAV_PAUSE_TEMP:
            self._current_level = ThermalLevel.NAV_PAUSED
            return ThermalDecision(
                level=ThermalLevel.NAV_PAUSED,
                target_fps=0.0,
                nav_inference_allowed=False,
                diagnosis_inference_allowed=True,
                frame_skip=0,
                require_checkpoint=False,
                reason="gpu_temperature_nav_paused",
            )

        if celsius >= FRAME_SKIP_TEMP:
            self._current_level = ThermalLevel.FRAME_SKIP
            return ThermalDecision(
                level=ThermalLevel.FRAME_SKIP,
                target_fps=FRAME_SKIP_FPS,
                nav_inference_allowed=True,
                diagnosis_inference_allowed=True,
                # 隔帧推理：每 2 帧推理 1 帧
                frame_skip=1,
                require_checkpoint=False,
                reason="gpu_temperature_frame_skip",
            )

        self._current_level = ThermalLevel.THROTTLED
        return ThermalDecision(
            level=ThermalLevel.THROTTLED,
            target_fps=THROTTLED_FPS,
            nav_inference_allowed=True,
            diagnosis_inference_allowed=True,
            frame_skip=0,
            require_checkpoint=False,
            reason="gpu_temperature_throttled",
        )


__all__ = [
    "DEFAULT_OVERHEAT_DURATION_SECONDS",
    "EMERGENCY_TEMP",
    "FRAME_SKIP_FPS",
    "FRAME_SKIP_TEMP",
    "GPUThermalPolicy",
    "NAV_PAUSE_TEMP",
    "NORMAL_FPS",
    "THROTTLE_TEMP",
    "THROTTLED_FPS",
    "ThermalDecision",
    "ThermalLevel",
]
