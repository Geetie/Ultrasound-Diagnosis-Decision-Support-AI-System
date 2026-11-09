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

#: 关闭思考的 assistant 预填串（与官方 Qwen3 chat template 在
#: ``enable_thinking=false`` 时渲染出的内容**逐字相同**）。
#:
#: ``vllm.py`` 与测试共用这一份事实源；改它等于改"关思考"的语义。
NO_THINK_PREFILL = "<think>\n\n</think>\n\n"


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

    def _should_escalate_max_tokens(self, result: InferenceResponse) -> bool:
        """``finish_reason == "length_empty"`` → 加倍预算重试一次。

        为什么只对这种失败翻倍（2026-11-09 真机依据）：
        ``length_empty`` 的含义是"token 全被思考吃掉、剥完没正文" —— 是**预算不够**，
        不是模型没能力。P.208 实测（8 条真实输入，旧配置）：预算 ×2 救回 **3/8**，
        ×4 救回 **5/8**（而且 ×4 的延迟到 3~10 分钟，不可接受）。

        所以取 **×2 一次**：救回一部分，且最坏延迟有界（判读档 180s 内）。
        翻倍后仍为空 → 交给上层守卫按 ``length_empty`` 降级转人工，不无限加预算。

        注意：**预填生效后这类失败应趋近于 0**（P.205 实测 0/8）。本兜底是给
        "模型偶发无视预填"和"其它思考型变体"留的安全网，不是主要手段。
        """
        return result.finish_reason == "length_empty"

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

        # ------------------------------------------------------------------
        # 思考模式（2026-11-09 根治，替换 2026-11-08 的 `chat_template_kwargs` 方案）
        #
        # ## 旧方案为什么无效（不是"没试对参数"，是**模型模板里没有这个变量**）
        #
        # `chat_template_kwargs={"enable_thinking": False}` 要生效，前提是模型自带的
        # chat template 里有 ``enable_thinking`` 分支。官方 Qwen3-8B 有：
        #
        #     {%- if add_generation_prompt %}
        #         {{- '<|im_start|>assistant\n' }}
        #         {%- if enable_thinking is defined and enable_thinking is false %}
        #             {{- '<think>\n\n</think>\n\n' }}
        #         {%- endif %}
        #     {%- endif %}
        #
        # —— 它把**空的思考块预填**进 assistant 轮，模型从预填之后续写，
        # 第一个 token 必然是正文。
        #
        # 而 II-Medical-8B（Qwen3 架构，`config.json: Qwen3ForCausalLM`）的
        # `tokenizer_config.json` 里装的是一份 **295 字节的 Qwen2 风格模板**，
        # 全文没有任何 ``enable_thinking`` 分支（P.201 离线渲染实证：传 True/False/
        # 不传，渲染结果**逐字节相同**）。所以那个 kwarg 传进去没有任何变量读它，
        # vLLM 也不会报错 —— 静默无效。这就是"绕了几次没解决"的真正原因。
        #
        # ## 新方案：客户端预填 assistant 轮（``continue_final_message``）
        #
        # 不依赖模型模板、不改服务端启动参数，效果与官方模板的 ``enable_thinking=false``
        # 等价 —— 把 ``<think>\n\n</think>\n\n`` 作为最后一条 assistant 消息的**已有内容**
        # 下发，vLLM 以 ``continue_final_message=True`` 让它从这段之后继续生成。
        # P.205 真机 8 条输入实测：``<think>`` 出现 0/8、正文为空 0/8、
        # finish_reason 全部 ``stop``（旧方案：``<think>`` 8/8、空正文 5/8、length 8/8）。
        #
        # ## 兼容性
        #
        # 调用方明确要思考（``extra["enable_thinking"]=True``）时仍然只传模板 kwarg
        #（对官方模板有效）；字段 ``extra["thinking_prefill"]=False`` 可关闭预填
        #（给"模板本来就支持 enable_thinking"的部署用，避免重复预填）。
        # llama.cpp 引擎不读这些键，行为不变。
        enable_thinking = bool(request.extra.get("enable_thinking", False))
        if "enable_thinking" not in request.extra:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        elif enable_thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": True}

        prefill_enabled = request.extra.get("thinking_prefill", True)
        last_is_assistant = bool(messages) and messages[-1].get("role") == "assistant"
        if not enable_thinking and prefill_enabled and not last_is_assistant:
            # 模板生效时（官方 Qwen3）这里会与模板的预填重复一次空思考块 —— 无害
            #（模型看到的仍是"已结束思考"），但更干净的做法是调用方按模型置 False。
            #
            # 调用方已经以 assistant 消息结尾（多轮/自行预填）时**不再追加**：
            # 那会改写它本来的续写语义（`continue_final_message` 只续最后一条）。
            payload["messages"] = [*messages, {"role": "assistant", "content": NO_THINK_PREFILL}]
            payload["continue_final_message"] = True
            # vLLM 明确拒绝 continue_final_message 与 add_generation_prompt 同时为真
            payload["add_generation_prompt"] = False

        # A.155：仍保留引擎侧剥除（模型偶发无视预填、自己再开一次 <think> 时兜底；
        # 预填本身不进模型输入，所以剥除不会误删正文）。
        payload["_strip_think"] = True

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

        # A.155：思考模型兜底——剥除 <think>…</think>（含未闭合的 <think> 到末尾）。
        # 部分微调变体的 chat template 不读 enable_thinking（真机实测 II-Medical-8B），
        # 引擎侧统一剥除，保证上层看到的永远是"干净结论"。
        import re as _re
        text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL)
        # 未闭合的 <think>（finish_reason=length 常见）：从 <think> 起全部剥掉
        if "<think>" in text:
            text = text.split("<think>")[0]
        text = text.lstrip()

        # ------------------------------------------------------------------
        # 2026-11-08：**剥完变空**要显式暴露，不能当成功返回。
        #
        # 现象（p107b 实测）：II-Medical-8B 的 chat template 不读 enable_thinking，
        # token 全花在 <think> 上 → finish_reason=length → 引擎剥除后 text == ""。
        # 而 32.50% 的生产输入产生了"只有分级码、无正文"的退化结论。
        #
        # 原实现把这个情况当正常响应返回（空串 + finish_reason=length），
        # 上层拿不到"这次生成其实没产出正文"的信号，于是落库成退化结论。
        #
        # 现在：明确打日志 + 把 finish_reason 标成 "length_empty"，
        # 让上层 output_guard 能据此拒收（而不是静默落库）。
        if not text and finish_reason == "length":
            self._log.warning(
                "inference_empty_after_think_strip",
                request_id=request.request_id,
                completion_tokens=usage_data.get("completion_tokens", 0)
                if (usage_data := data.get("usage", {}))
                else 0,
                hint=(
                    "token 全消耗在 <think> 内，剥除后无正文。"
                    "该模型 chat template 不读 enable_thinking；"
                    "处理方式：提高 max_tokens 或换非思考模型变体。"
                ),
            )
            finish_reason = "length_empty"

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
