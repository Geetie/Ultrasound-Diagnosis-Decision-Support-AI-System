"""vLLM推理引擎适配器模块。

本模块实现了针对vLLM server的HTTP客户端适配器，
支持通过OpenAI兼容的/chat/completions API进行推理。

主要特点：
- 使用/v1/chat/completions端点（OpenAI Chat API兼容）
- 支持多轮对话格式（通过request.extra["messages"]传入）
- 必须指定模型名称（可通过default_model或request.extra["model"]）
- 默认max_tokens=2048（适合full模式）
- 默认temperature=0.05（更确定性的医疗文本生成）

适用场景：
- full部署模式（高性能GPU环境）
- 需要高吞吐推理
- 需要多轮对话支持
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


class VLLMEngine(BaseHttpInferenceEngine):
    """vLLM推理引擎适配器。

    实现了针对vLLM server的HTTP客户端，使用OpenAI兼容的chat/completions API。

    Args:
        base_url: vLLM服务地址（默认http://localhost:8002）
        timeout_seconds: 请求超时时间（默认30秒）
        connect_timeout_seconds: 连接超时时间（默认10秒）
        health_check_timeout_seconds: 健康检查超时时间（默认5秒）
        default_max_tokens: 默认最大生成Token数（默认2048，适合full模式）
        default_temperature: 默认采样温度（默认0.05，医疗场景更需要确定性）
        default_top_p: 默认Top-p采样参数（默认0.95）
        default_model: 默认模型名称（可选，vLLM必须指定模型）
        require_model: 是否强制要求模型名称（默认True）

    Note:
        vLLM server需要预先启动，推荐配置：
        - 模型格式：HuggingFace格式（支持AutoGPTQ、AWQ等量化）
        - 服务器端口：8002
        - API端点：/v1/chat/completions
        - 启动示例：python -m vllm.entrypoints.api_server --model <model_name> --port 8002
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8002",
        timeout_seconds: float = 30.0,
        connect_timeout_seconds: float = 10.0,
        health_check_timeout_seconds: float = 5.0,
        default_max_tokens: int = 2048,
        default_temperature: float = 0.05,
        default_top_p: float = 0.95,
        default_model: str = "",
        require_model: bool = True,
        max_retries: int = 3,
        retry_delay_base: float = 1.0,
        retry_delay_max: float = 10.0,
        circuit_breaker_timeout: float = 30.0,
        circuit_breaker_failure_threshold: int = 5,
        circuit_breaker_success_threshold: int = 3,
        pool_max_connections: int = 10,
        pool_keepalive_expiry: float = 300.0,
        gpu_memory_utilization: float = 0.90,
    ):
        super().__init__(
            base_url=base_url,
            logger_name="app.inference.vllm",
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
        self._default_model = default_model
        self._require_model = require_model
        self._gpu_memory_utilization = gpu_memory_utilization

    @property
    def _generate_endpoint(self) -> str:
        """获取生成端点路径。

        Returns:
            str: /v1/chat/completions（OpenAI Chat API兼容端点）
        """
        return "/v1/chat/completions"

    def _build_messages(self, request: InferenceRequest) -> list[dict[str, str]]:
        """构建消息列表。

        支持两种输入方式：
        1. 通过request.extra["messages"]传入完整的多轮对话
        2. 通过request.prompt传入单轮用户输入（自动包装为user角色）

        Args:
            request: 推理请求对象

        Returns:
            list: 消息列表，每个消息包含role和content字段
        """
        messages = request.extra.get("messages")
        if messages:
            if request.prompt.strip():
                self._log.warning(
                    "prompt_ignored_when_messages_provided",
                    engine=self._logger_name,
                    request_id=request.request_id,
                )
            return messages
        return [{"role": "user", "content": request.prompt}]

    def _build_payload(self, request: InferenceRequest) -> dict[str, Any]:
        """构建请求体。

        将InferenceRequest转换为vLLM期望的chat/completions格式。

        Args:
            request: 推理请求对象

        Returns:
            dict: 包含messages、max_tokens、temperature等参数的请求体

        Raises:
            InferenceError: 如果require_model=True但未提供模型名称
        """
        messages = self._build_messages(request)
        payload: dict[str, Any] = {
            "messages": messages,
            "max_tokens": request.max_tokens if request.max_tokens is not None else self._default_max_tokens,
            "temperature": request.temperature if request.temperature is not None else self._default_temperature,
            "top_p": request.top_p if request.top_p is not None else self._default_top_p,
        }
        if request.stop:
            payload["stop"] = request.stop

        model_name = request.extra.get("model") or self._default_model
        if model_name:
            payload["model"] = model_name
        elif self._require_model:
            raise InferenceError(
                error_code=InferenceErrorCode.GENERATION_FAILED,
                message="vLLM requires a model name but none was provided",
                detail="Set default_model or pass model in request.extra",
                retryable=False,
            )
        else:
            self._log.warning(
                "model_field_missing",
                engine=self._logger_name,
                request_id=request.request_id,
            )
        # 结构化输出（2026-10-13 分级提取根治）：**硬规则** —— 新版 vLLM 对
        # `guided_json` 是「静默忽略」（既无约束也不报错，见 AGENTS.md 硬规则 #5），
        # 因此结构化输出**唯一**路径 = OpenAI 兼容的 `response_format`。
        # 01-4 删净（2026-10-31）：原"guided_json 兼容旧服务端回落"分支已删 ——
        # 它是静默失效陷阱（下发即以为有约束，实际没有），生产 0 调用。
        response_format = request.extra.get("response_format")
        if response_format:
            payload["response_format"] = response_format

        return payload

    def _parse_response(self, data: dict[str, Any], request: InferenceRequest) -> InferenceResponse:
        """解析vLLM响应。

        将vLLM返回的chat/completions格式响应转换为统一的InferenceResponse格式。

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
                message="vLLM returned empty choices",
                retryable=True,
                retry_strategy="retry_after_5s",
            )

        choice = choices[0]
        message = choice.get("message", {})
        text = message.get("content", "")
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
