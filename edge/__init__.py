"""边缘端（EDG）轨道。

部署形态：边缘端运行在超声设备旁的 Jetson 工控机上，职责是
「采集 → 本地推理 → 本地安全校验 → 上行推送」：

    T-GA-EDG-01  DICOM SCP 服务（pynetdicom + python-gdcm）
    T-GA-EDG-02  TensorRT 导航推理（模型加载 + 15fps）
    T-GA-EDG-03  WebSocket 服务 + 设备认证（API Key + HKDF）
    T-GB-EDG-01  OTA 推送（Token 认证 + 断点续传 + 限速）
    T-GB-EDG-02  设备管理（注册心跳 + 健康探测 + 隔离恢复）
    T-GB-EDG-03  本地安全检查（标签白名单 + 坐标范围校验）
    T-GB-EDG-04  GPU 温度处理策略（主动降频 + 隔帧推理 + 过热保护）

依赖说明：`pynetdicom` / `python-gdcm` / `tensorrt` 只在边缘设备上安装
（见 `requirements-edge.txt`），本包对其一律懒加载，未安装时给出明确
报错而不是在导入期崩溃；核心策略逻辑（温度分级、本地安全校验、认证、
推理调度）不依赖上述三方库，可在开发机直接单测。
"""

from edge.device_auth import EdgeDeviceAuth
from edge.gpu_thermal import GPUThermalPolicy, ThermalDecision, ThermalLevel
from edge.protocols import Frame, NavInferenceBackend, NavResult, NavLabel
from edge.safety import LocalSafetyChecker, LocalSafetyResult

__all__ = [
    "EdgeDeviceAuth",
    "Frame",
    "GPUThermalPolicy",
    "LocalSafetyChecker",
    "LocalSafetyResult",
    "NavInferenceBackend",
    "NavLabel",
    "NavResult",
    "ThermalDecision",
    "ThermalLevel",
]
