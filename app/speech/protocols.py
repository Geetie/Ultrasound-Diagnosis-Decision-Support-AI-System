"""语音识别引擎协议定义模块。

本模块定义了语音识别引擎的核心接口契约和数据模型，支持医疗领域的语音转写需求。

核心组件：
- SpeechEngine: 语音引擎抽象基类(ABC)，定义统一的语音识别接口
- SpeechRequest/SpeechResponse: 请求和响应的数据模型
- SpeechError/SpeechErrorCode: 统一异常处理体系

设计原则：
- 使用ABC模式实现多后端适配
- 支持FunASR等语音识别后端
- 统一的错误码和异常处理机制
- 内置医疗术语识别能力
"""

from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class SpeechErrorCode(str, Enum):
    """语音识别错误码枚举。
    
    定义语音识别过程中可能出现的错误类型，用于统一异常处理和日志记录。
    
    活跃错误码：
    - E3001_TIMEOUT: 请求超时
    - E3001_CONNECTION_FAILED: 连接失败
    - E3001_RECOGNITION_FAILED: 识别失败
    - E3001_MODEL_LOAD_FAILED: 模型加载失败

    成员名与取值保持一致（带 E3001_ 前缀）：app/speech/funasr.py 的
    错误处理路径按带前缀形式引用，此前成员名漏了前缀，导致模型加载/识别
    失败时抛 AttributeError 而不是 SpeechError（错误路径整体失效）。
    """
    E3001_TIMEOUT = "E3001_TIMEOUT"
    E3001_CONNECTION_FAILED = "E3001_CONNECTION_FAILED"
    E3001_RECOGNITION_FAILED = "E3001_RECOGNITION_FAILED"
    E3001_MODEL_LOAD_FAILED = "E3001_MODEL_LOAD_FAILED"


class SpeechError(Exception):
    """语音引擎统一异常类。
    
    封装语音识别过程中的所有错误，包含错误码、错误信息、详细描述和重试策略。
    
    Args:
        error_code: 错误码枚举值
        message: 错误简要描述
        detail: 错误详细信息（可选）
        retryable: 是否可重试
        
    Attributes:
        error_code: 错误码
        message: 错误消息
        detail: 详细信息
        retryable: 是否可重试
    """
    def __init__(
        self,
        error_code: SpeechErrorCode,
        message: str,
        detail: str | None = None,
        retryable: bool = False,
    ):
        self.error_code = error_code
        self.message = message
        self.detail = detail
        self.retryable = retryable
        super().__init__(f"[{error_code.value}] {message}")


class SpeechRequest(BaseModel):
    """语音识别请求模型。
    
    封装语音识别请求的所有参数，包含严格的字段校验。
    
    Attributes:
        audio_data: 音频数据（字节数组，必填）
        sample_rate: 采样率（默认16000Hz）
        language: 语言（默认中文，支持zh/en）
        request_id: 请求唯一标识（可选）
        session_id: 会话标识（可选）
        extra: 额外参数字典（可选）
    """
    audio_data: bytes
    sample_rate: int = 16000
    language: str = "zh"
    request_id: str | None = None
    session_id: str | None = None
    extra: dict[str, object] = Field(default_factory=dict)

    @field_validator("sample_rate")
    @classmethod
    def sample_rate_must_be_positive(cls, v: int) -> int:
        """校验采样率必须为正整数。"""
        if v <= 0:
            raise ValueError("sample_rate must be positive")
        return v

    @field_validator("language")
    @classmethod
    def language_must_be_valid(cls, v: str) -> str:
        """校验语言必须为zh或en。"""
        allowed = {"zh", "en"}
        if v not in allowed:
            raise ValueError(f"language must be one of {allowed}, got '{v}'")
        return v


class SpeechResponse(BaseModel):
    """语音识别响应模型。
    
    封装语音识别的响应结果，包含识别文本和术语匹配信息。
    
    Attributes:
        text: 识别出的文本结果
        language: 语言标识
        confidence: 识别置信度（0.0-1.0）
        latency_ms: 识别耗时（毫秒）
        request_id: 请求标识（与请求中的request_id对应）
        segments: 分段识别结果（可选）
        terminology_matches: 识别到的医疗术语列表（可选）
    """
    text: str
    language: str = "zh"
    confidence: float = 0.0
    latency_ms: float = 0.0
    request_id: str | None = None
    segments: list[dict[str, object]] = Field(default_factory=list)
    terminology_matches: list[dict[str, object]] = Field(default_factory=list)


class SpeechEngineInfo(BaseModel):
    """语音引擎信息模型。
    
    描述当前语音引擎加载的模型信息，用于监控和调试。
    
    Attributes:
        engine_name: 引擎名称
        model_name: 模型名称
        model_version: 模型版本
        sample_rate: 采样率
        language: 支持的语言
    """
    engine_name: str = ""
    model_name: str = ""
    model_version: str = ""
    sample_rate: int = 16000
    language: str = "zh"


class SpeechEngine(ABC):
    """语音引擎抽象基类。
    
    定义所有语音引擎必须实现的核心接口，实现多后端的统一抽象。
    
    子类需要实现的抽象方法：
    - recognize: 执行语音识别
    - health_check: 健康检查
    - get_engine_info: 获取引擎信息
    - close: 关闭引擎资源
    """

    @abstractmethod
    async def recognize(self, request: SpeechRequest) -> SpeechResponse:
        """执行语音识别。
        
        Args:
            request: 语音识别请求对象
            
        Returns:
            SpeechResponse: 语音识别响应对象
            
        Raises:
            SpeechError: 识别过程中发生错误
        """
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        """检查语音引擎健康状态。
        
        Returns:
            bool: True表示健康，False表示不健康
        """
        raise NotImplementedError

    @abstractmethod
    async def get_engine_info(self) -> SpeechEngineInfo:
        """获取当前加载的引擎信息。
        
        Returns:
            SpeechEngineInfo: 引擎信息对象
        """
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """关闭语音引擎，释放资源。
        
        子类应在此方法中关闭模型、释放内存等资源。
        """
        raise NotImplementedError
