import argparse
import time
import uuid
from typing import Optional

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

    return JSONResponse({
        "id": f"cmpl-{uuid.uuid4().hex[:8]}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": body.get("model", "default"),
        "choices": [{
            "index": 0,
            "text": generated_text,
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": len(outputs[0].prompt_token_ids),
            "completion_tokens": len(outputs[0].outputs[0].token_ids),
            "total_tokens": len(outputs[0].prompt_token_ids) + len(outputs[0].outputs[0].token_ids),
        },
    })


async def chat_completions(request: Request) -> JSONResponse:
    body = await request.json()
    messages = body.get("messages", [])
    max_tokens = body.get("max_tokens", 256)
    temperature = body.get("temperature", 0.7)

    prompt_parts = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            prompt_parts.append(f"<|im_start|>system\n{content}<|im_end|>")
        elif role == "user":
            prompt_parts.append(f"<|im_start|>user\n{content}<|im_end|>")
        elif role == "assistant":
            prompt_parts.append(f"<|im_start|>assistant\n{content}<|im_end|>")
    prompt_parts.append("<|im_start|>assistant\n")
    prompt = "\n".join(prompt_parts)

    from vllm import SamplingParams
    sampling_params = SamplingParams(
        max_tokens=max_tokens,
        temperature=temperature,
    )
    outputs = llm.generate([prompt], sampling_params)
    generated_text = outputs[0].outputs[0].text

    return JSONResponse({
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "default"),
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": generated_text},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": len(outputs[0].prompt_token_ids),
            "completion_tokens": len(outputs[0].outputs[0].token_ids),
            "total_tokens": len(outputs[0].prompt_token_ids) + len(outputs[0].outputs[0].token_ids),
        },
    })


app = Starlette(routes=[
    Route("/health", health),
    Route("/v1/completions", completions, methods=["POST"]),
    Route("/v1/chat/completions", chat_completions, methods=["POST"]),
])

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--device", default="cuda", choices=["cuda", "cpu"],
                        help="Device for inference (cuda or cpu). vLLM>=0.5 supports CPU mode.")
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.5,
                        help="GPU memory utilization ratio (ignored when --device cpu)")
    parser.add_argument("--cpu-kv-cache-capacity", type=int, default=None,
                        help="CPU KV cache capacity in number of blocks (vLLM CPU mode only)")
    args = parser.parse_args()

    from vllm import LLM
    print(f"Loading model from {args.model} (device={args.device})...")

    llm_kwargs = {
        "model": args.model,
        "enforce_eager": True,
    }
    if args.device == "cpu":
        llm_kwargs["device"] = "cpu"
        if args.cpu_kv_cache_capacity is not None:
            llm_kwargs["cpu_kv_cache_capacity"] = args.cpu_kv_cache_capacity
    else:
        llm_kwargs["gpu_memory_utilization"] = args.gpu_memory_utilization

    llm = LLM(**llm_kwargs)
    print(f"Model loaded. Starting server on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)
