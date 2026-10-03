"""vLLM 引擎的 HTTP 包装（8002，只绑回环）。

用途：项目内部用 OpenAI 风格之外的「裸 completions」协议调 vLLM——
``scripts/server_start_vllm_engine.sh`` 启动本文件，产物是
``http://127.0.0.1:8002`` 上的 ``/health`` 与 ``/completions``。

为什么需要这个文件
------------------
vLLM 自带 ``vllm.entrypoints.openai.api_server``（那是 8006 那个实例的形态，
用 ``-m`` 启动、带完整 OpenAI 兼容层）。本项目另有一层需求只要
「prompt 进、文本出」的裸接口，且要精确控制 gpu_memory_utilization=0.3
（与 8006 的 Qwen3-8B 共卡）。两种形态各有用途，故并存：
  - 8002：本文件（Qwen2.5-1.5B-Instruct，裸 completions）
  - 8006：``-m vllm.entrypoints.openai.api_server``（Qwen3-8B，OpenAI 兼容）

历史：本文件在 4f6eb41「收敛与部署脚本入库」里被当作探针删除，但
``server_start_vllm_engine.sh`` 仍在引用它 → 悬空引用，真机表现为
``can't open file '.../scripts/vllm_server.py'``、8002 起不来。
此次按"删除探针要连带清理引用"的原则**反向修复**：引用方在，就恢复被删方。
"""
import argparse
import os
import time
import uuid

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

llm = None


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def completions(request: Request) -> JSONResponse:
    body = await request.json()
    prompt = body.get("prompt", "")
    max_tokens = body.get("max_tokens", 256)
    temperature = body.get("temperature", 0.7)

    from vllm import SamplingParams

    sampling_params = SamplingParams(
        max_tokens=max_tokens,
        temperature=temperature,
    )
    outputs = llm.generate([prompt], sampling_params)
    generated_text = outputs[0].outputs[0].text
    return JSONResponse(
        {
            "id": f"cmpl-{uuid.uuid4().hex[:12]}",
            "object": "text_completion",
            "created": int(time.time()),
            "text": generated_text,
            "model": getattr(llm, "model_config", None) and llm.model_config.model,
            "usage": {
                "prompt_tokens": len(outputs[0].prompt_token_ids or []),
                "completion_tokens": len(outputs[0].outputs[0].token_ids or []),
            },
        }
    )


async def metrics(request: Request) -> JSONResponse:
    """极简运行时指标（不引 prometheus 依赖，够健康巡检用）。"""
    return JSONResponse(
        {
            "status": "ok",
            "model_loaded": llm is not None,
            "timestamp": int(time.time()),
        }
    )


def build_app(engine) -> Starlette:
    global llm
    llm = engine
    return Starlette(
        routes=[
            Route("/health", health),
            Route("/completions", completions, methods=["POST"]),
            Route("/metrics", metrics),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="vLLM 引擎 HTTP 包装（8002）")
    parser.add_argument("--model", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8002)
    # vLLM 0.30 的 EngineArgs/LLM 已**移除** device 参数（实测 2026-10-04：
    # EngineArgs.__init__() got an unexpected keyword argument 'device'）。
    # 设备改由 CUDA_VISIBLE_DEVICES 环境变量控制（启动脚本已 export=0）。
    # 保留这个参数只为兼容旧调用方：给 cuda 时设 CUDA_VISIBLE_DEVICES=0，
    # 给 cpu 时设 CUDA_VISIBLE_DEVICES=""（让 vLLM 走 CPU 路径）。
    parser.add_argument("--device", default=None,
                        help="兼容参数：cuda(默认卡)/cpu；vLLM 0.30+ 请用 CUDA_VISIBLE_DEVICES")
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.3)
    args = parser.parse_args()

    if args.device:
        os.environ["CUDA_VISIBLE_DEVICES"] = "" if args.device == "cpu" else os.environ.get(
            "CUDA_VISIBLE_DEVICES", "0"
        )

    from vllm import LLM

    engine = LLM(
        model=args.model,
        gpu_memory_utilization=args.gpu_memory_utilization,
    )
    uvicorn.run(build_app(engine), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
