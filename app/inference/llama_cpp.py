"""llama.cpp推理引擎适配器模块。

本模块实现了针对llama.cpp server的HTTP客户端适配器，
支持通过OpenAI兼容的/completions API进行推理。

主要特点：
- 使用/v1/completions端点（OpenAI兼容）
- 支持标准推理参数（prompt、max_tokens、temperature、top_p、stop）
- 响应解析从choices[0].text提取生成文本
- 默认max_tokens=1024（适合compact模式）

适用场景：
- compact部署模式（资源受限环境）
- 需要CPU推理支持
- 低延迟推理需求
"""

from typing import Any

from app.inference.base import BaseHttpInferenceEngine
from app.inference.protocols import (
    InferenceError,
    InferenceErrorCode,
    InferenceRequest,
    InferenceResponse,
    TokenUsage,
)


class LlamaCppEngine(BaseHttpInferenceEngine):
    """llama.cpp推理引擎适配器。

    实现了针对llama.cpp server的HTTP客户端，使用OpenAI兼容的/completions API。

    Args:
        base_url: llama.cpp服务地址（默认http://localhost:8001）
        timeout_seconds: 请求超时时间（默认30秒）
        connect_timeout_seconds: 连接超时时间（默认10秒）
        health_check_timeout_seconds: 健康检查超时时间（默认5秒）
        default_max_tokens: 默认最大生成Token数（默认1024，适合compact模式）
        default_temperature: 默认采样温度（默认0.1，低温度更确定性输出）
        default_top_p: 默认Top-p采样参数（默认0.95）

    Note:
        llama.cpp server需要预先启动，推荐配置：
        - 模型量化格式：GGUF（Q4_K_M或Q5_K_M）
        - 服务器端口：8001
        - API端点：/v1/completions
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8001",
        timeout_seconds: float = 30.0,
        connect_timeout_seconds: float = 10.0,
        health_check_timeout_seconds: float = 5.0,
        default_max_tokens: int = 1024,
        default_temperature: float = 0.1,
        default_top_p: float = 0.95,
        max_retries: int = 3,
        retry_delay_base: float = 1.0,
        retry_delay_max: float = 10.0,
        circuit_breaker_timeout: float = 30.0,
        circuit_breaker_failure_threshold: int = 5,
        circuit_breaker_success_threshold: int = 3,
        pool_max_connections: int = 10,
        pool_keepalive_expiry: float = 300.0,
        use_chat: bool = False,
    ):
        super().__init__(
            base_url=base_url,
            logger_name="app.inference.llama_cpp",
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            health_check_timeout_seconds=health_check_timeout_seconds,
            default_max_tokens=default_max_tokens,
            default_temperature=default_temperature,
            default_top_p=default_top_p,
            max_retries=max_retries,
            retry_delay_base=retry_delay_base,
            retry_delay_max=retry_delay_max,
            circuit_breaker_timeout=circuit_breaker_timeout,
            circuit_breaker_failure_threshold=circuit_breaker_failure_threshold,
            circuit_breaker_success_threshold=circuit_breaker_success_threshold,
            pool_max_connections=pool_max_connections,
            pool_keepalive_expiry=pool_keepalive_expiry,
        )
        # 双引擎一致性评测（2026-09-29）：vLLM 走 /v1/chat/completions（chat 模板），
        # llama.cpp 默认走 /v1/completions（裸 prompt）——同一 prompt 在两栈上的
        # 形态不同是模板级差异源。use_chat=True 时 llama.cpp 也走 chat 端点
        # （llama.cpp server 原生支持），使两引擎收到完全相同的消息形态。
        self._use_chat = use_chat

    @property
    def _generate_endpoint(self) -> str:
        """获取生成端点路径。

        Returns:
            str: /v1/completions（默认，裸 prompt）或 /v1/chat/completions（use_chat）
        """
        if self._use_chat:
            return "/v1/chat/completions"
        return "/v1/completions"

    def _build_payload(self, request: InferenceRequest) -> dict[str, Any]:
        """构建请求体。

        将InferenceRequest转换为llama.cpp server期望的格式。

        Args:
            request: 推理请求对象

        Returns:
            dict: 包含prompt（或 messages）、max_tokens、temperature、top_p等参数的请求体
        """
        params: dict[str, Any] = {
            "max_tokens": request.max_tokens if request.max_tokens is not None else self._default_max_tokens,
            "temperature": request.temperature if request.temperature is not None else self._default_temperature,
            "top_p": request.top_p if request.top_p is not None else self._default_top_p,
        }
        if request.stop:
            params["stop"] = request.stop
        if self._use_chat:
            return {
                "messages": [{"role": "user", "content": request.prompt}],
                **params,
            }
        return {"prompt": request.prompt, **params}

    def _parse_response(self, data: dict[str, Any], request: InferenceRequest) -> InferenceResponse:
        """解析llama.cpp响应。

        将llama.cpp server返回的JSON响应转换为统一的InferenceResponse格式。

        Args:
            data: 后端返回的JSON数据
            request: 原始请求对象

        Returns:
            InferenceResponse: 统一的响应对象

        Raises:
            InferenceError: 如果响应中没有choices或解析失败
        """
        choices = data.get("choices", [])
        if not choices:
            raise InferenceError(
                error_code=InferenceErrorCode.GENERATION_FAILED,
                message="llama.cpp returned empty choices",
                retryable=True,
                retry_strategy="retry_after_5s",
            )

        choice = choices[0]
        # chat 端点返回 choices[0].message.content；completions 返回 choices[0].text。
        # 两种形态都兼容解析（解析与端点解耦，避免 mock/服务差异导致的静默空文本）。
        message = choice.get("message")
        text = message.get("content", "") if isinstance(message, dict) else choice.get("text", "")
        finish_reason = choice.get("finish_reason", "stop")

        usage_data = data.get("usage", {})
        usage = TokenUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )

        return InferenceResponse(
            text=text,
            model=data.get("model", ""),
            usage=usage,
            finish_reason=finish_reason,
            request_id=request.request_id,
        )
