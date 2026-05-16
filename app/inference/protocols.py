"""LLM推理引擎协议定义模块。

本模块定义了推理引擎的核心接口契约和数据模型，是整个推理子系统的基础抽象层。

核心组件：
- InferenceEngine: 推理引擎抽象基类(ABC)，定义统一的推理接口
- InferenceRequest/InferenceResponse: 请求和响应的数据模型
- InferenceError/InferenceErrorCode: 统一异常处理体系
- ModelInfo/TokenUsage: 模型信息和Token使用统计

设计原则：
- 使用ABC模式实现多后端适配，支持llama.cpp、vLLM等多种推理后端
- 统一的错误码和异常处理机制，便于问题定位和重试策略
- 类型安全的请求/响应数据结构，使用Pydantic进行严格校验
- 向后兼容的错误码映射，支持已废弃的错误码自动映射到活跃码
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class InferenceErrorCode(str, Enum):
    """推理引擎错误码枚举。
    
    定义推理过程中可能出现的错误类型，用于统一异常处理和日志记录。
    
    活跃错误码：
    - TIMEOUT: 请求超时
    - CONNECTION_FAILED: 连接失败
    - GENERATION_FAILED: 生成失败（包含模型加载、无效请求等子类型）
    
    废弃错误码通过_missing_方法自动映射到活跃码，保证向后兼容。
    """
    TIMEOUT = "E2002_TIMEOUT"
    CONNECTION_FAILED = "E2002_CONNECTION_FAILED"
    GENERATION_FAILED = "E2002_GENERATION_FAILED"

    @classmethod
    def _missing_(cls, value: object) -> "InferenceErrorCode | None":
        """将废弃的错误码映射到活跃码。
        
        当传入已废弃的错误码时，自动映射到对应的活跃错误码，
        保证系统升级后的向后兼容性。
        
        Args:
            value: 错误码字符串
            
        Returns:
            对应的活跃错误码，如果无法映射返回None
        """
        _DEPRECATED_MAP: dict[str, InferenceErrorCode] = {
            "E2002_MODEL_LOAD_FAILED": cls.GENERATION_FAILED,
            "E2002_INVALID_REQUEST": cls.GENERATION_FAILED,
            "E2002_HEALTH_CHECK_FAILED": cls.GENERATION_FAILED,
        }
        return _DEPRECATED_MAP.get(str(value))


class DeprecatedErrorCode:
    """已废弃的错误码定义，仅用于向后兼容。
    
    这些错误码已不再直接使用，通过InferenceErrorCode._missing_方法
    自动映射到活跃错误码。保留此定义是为了兼容旧代码的引用。
    """
    MODEL_LOAD_FAILED = "E2002_MODEL_LOAD_FAILED"
    INVALID_REQUEST = "E2002_INVALID_REQUEST"
    HEALTH_CHECK_FAILED = "E2002_HEALTH_CHECK_FAILED"


class InferenceError(Exception):
    """推理引擎统一异常类。
    
    封装推理过程中的所有错误，包含错误码、错误信息、详细描述和重试策略。
    
    Args:
        error_code: 错误码枚举值
        message: 错误简要描述
        detail: 错误详细信息（可选）
        retryable: 是否可重试
        retry_strategy: 重试策略标识
        
    Attributes:
        error_code: 错误码
        message: 错误消息
        detail: 详细信息
        retryable: 是否可重试
        retry_strategy: 重试策略
    """
    def __init__(
        self,
        error_code: InferenceErrorCode,
        message: str,
        detail: str | None = None,
        retryable: bool = False,
        retry_strategy: str = "no_retry",
    ):
        self.error_code = error_code
        self.message = message
        self.detail = detail
        self.retryable = retryable
        self.retry_strategy = retry_strategy
        super().__init__(f"[{error_code.value}] {message}")


class TokenUsage(BaseModel):
    """Token使用统计模型。
    
    记录推理请求的Token消耗情况，用于计费和性能监控。
    
    Attributes:
        prompt_tokens: 输入Prompt的Token数量
        completion_tokens: 生成结果的Token数量
        total_tokens: 总Token数量（prompt_tokens + completion_tokens）
    """
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ModelInfo(BaseModel):
    """模型信息模型。
    
    描述当前推理引擎加载的模型信息，用于监控和调试。
    
    Attributes:
        model_name: 模型名称
        model_version: 模型版本
        quantization: 量化类型（如Q4_K_M、FP16等）
        context_length: 上下文窗口长度
        vocab_size: 词表大小
    """
    model_name: str = ""
    model_version: str = ""
    quantization: str = ""
    context_length: int = 0
    vocab_size: int = 0


class InferenceRequest(BaseModel):
    """推理请求模型。
    
    封装LLM推理请求的所有参数，包含严格的字段校验。
    
    Attributes:
        prompt: 输入提示文本（必填，非空）
        max_tokens: 最大生成Token数（可选，正整数）
        temperature: 采样温度（可选，0.0-2.0）
        top_p: Top-p采样参数（可选，0.0-1.0）
        stop: 停止词列表（可选，非空列表）
        request_id: 请求唯一标识（可选）
        session_id: 会话标识（可选）
        extra: 额外参数字典（可选）
    """
    prompt: str
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: list[str] | None = None
    request_id: str | None = None
    session_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("prompt")
    @classmethod
    def prompt_must_not_be_empty(cls, v: str) -> str:
        """校验prompt不能为空或纯空白字符。"""
        if not v.strip():
            raise ValueError("prompt must not be empty or whitespace-only")
        return v

    @field_validator("max_tokens")
    @classmethod
    def max_tokens_must_be_positive(cls, v: int | None) -> int | None:
        """校验max_tokens必须为正整数或None。"""
        if v is not None and v <= 0:
            raise ValueError("max_tokens must be a positive integer")
        return v

    @field_validator("temperature")
    @classmethod
    def temperature_must_be_valid(cls, v: float | None) -> float | None:
        """校验temperature必须在0.0到2.0之间或None。"""
        if v is not None and (v < 0.0 or v > 2.0):
            raise ValueError("temperature must be between 0.0 and 2.0")
        return v

    @field_validator("top_p")
    @classmethod
    def top_p_must_be_valid(cls, v: float | None) -> float | None:
        """校验top_p必须在0.0到1.0之间或None。"""
        if v is not None and (v < 0.0 or v > 1.0):
            raise ValueError("top_p must be between 0.0 and 1.0")
        return v

    @field_validator("stop")
    @classmethod
    def stop_must_not_be_empty_list(cls, v: list[str] | None) -> list[str] | None:
        """校验stop不能是空列表。"""
        if v is not None and len(v) == 0:
            raise ValueError("stop must be None or a non-empty list")
        return v


class InferenceResponse(BaseModel):
    """推理响应模型。
    
    封装LLM推理的响应结果。
    
    Attributes:
        text: 生成的文本结果
        model: 模型名称
        usage: Token使用统计
        finish_reason: 结束原因（如stop、length等）
        request_id: 请求标识（与请求中的request_id对应）
        latency_ms: 推理耗时（毫秒）
    """
    text: str
    model: str = ""
    usage: TokenUsage = TokenUsage()
    finish_reason: str = ""
    request_id: str | None = None
    latency_ms: float = 0.0
    retry_count: int = 0


class InferenceEngine(ABC):
    """推理引擎抽象基类。
    
    定义所有推理引擎必须实现的核心接口，实现多后端的统一抽象。
    
    子类需要实现的抽象方法：
    - generate: 执行推理生成
    - health_check: 健康检查
    - get_model_info: 获取模型信息
    - close: 关闭引擎资源
    """

    @abstractmethod
    async def generate(self, request: InferenceRequest) -> InferenceResponse:
        """执行LLM推理生成。
        
        Args:
            request: 推理请求对象
            
        Returns:
            InferenceResponse: 推理响应对象
            
        Raises:
            InferenceError: 推理过程中发生错误
        """
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        """检查推理引擎健康状态。
        
        Returns:
            bool: True表示健康，False表示不健康
        """
        raise NotImplementedError

    @abstractmethod
    async def get_model_info(self) -> ModelInfo:
        """获取当前加载的模型信息。
        
        Returns:
            ModelInfo: 模型信息对象
        """
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """关闭推理引擎，释放资源。
        
        子类应在此方法中关闭HTTP客户端、释放连接池等资源。
        """
        raise NotImplementedError
