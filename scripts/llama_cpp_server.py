"""llama.cpp 本地推理服务（OpenAI 风格的 `/v1/completions` 与 `/v1/chat/completions`）。

## 2026-09-28 真机修复：支持本地 GGUF 路径

原实现把模型**硬编码**为 `Llama.from_pretrained(repo_id="Qwen/Qwen2.5-1.5B-Instruct-GGUF")`
—— 那需要运行期能访问 huggingface.co。校内 GPU 机上 huggingface.co **不可达**
（只有 hf-mirror.com 可达，而 `from_pretrained` 不走我们的镜像配置），
于是该脚本在真机上**根本起不来**。现支持 `--model`：

    # 本地 GGUF（权重已下好，推荐）
    python scripts/llama_cpp_server.py --model ~/uai/models/.../xxx-q4_k_m.gguf \
        --host 127.0.0.1 --port 8001 --n_ctx 4096 --n_gpu_layers -1
    # 不传 --model 时保持原行为（走 HF repo_id，需能访问 HF）

另加 `--n_gpu_layers`（-1 = 全部层上 GPU）并打印 GPU offload 能力，便于确认没退化成 CPU。
注意 `--host` 默认仍是 `0.0.0.0`（容器内映射需要）；共享机部署请显式传 `127.0.0.1`。
"""
import argparse
import os

import uvicorn
from llama_cpp import Llama
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

llm: Llama | None = None


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


_grammar_cache: dict[str, object] = {}


def _grammar(body: dict):
    """请求体 `grammar`（GBNF 字符串）→ LlamaGrammar（带缓存）。

    P0-2 约束解码：llama.cpp 侧按 GBNF 在采样层强制输出语法。
    """
    gbnf = body.get("grammar")
    if not gbnf:
        return None
    if gbnf not in _grammar_cache:
        from llama_cpp import LlamaGrammar

        _grammar_cache[gbnf] = LlamaGrammar.from_string(gbnf, verbose=False)
    return _grammar_cache[gbnf]


async def completions(request: Request) -> JSONResponse:
    body = await request.json()
    prompt = body.get("prompt", "")
    max_tokens = body.get("max_tokens", 256)
    temperature = body.get("temperature", 0.7)
    top_p = body.get("top_p", 0.9)
    stop = body.get("stop")

    response = llm.create_completion(
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        stop=stop,
        grammar=_grammar(body),
    )
    return JSONResponse(response)


async def chat_completions(request: Request) -> JSONResponse:
    body = await request.json()
    messages = body.get("messages", [])
    max_tokens = body.get("max_tokens", 256)
    temperature = body.get("temperature", 0.7)

    response = llm.create_chat_completion(
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        grammar=_grammar(body),
    )
    return JSONResponse(response)


app = Starlette(routes=[
    Route("/health", health),
    Route("/v1/completions", completions, methods=["POST"]),
    Route("/v1/chat/completions", chat_completions, methods=["POST"]),
])

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0", help="共享机部署请传 127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--n_ctx", type=int, default=512)
    parser.add_argument(
        "--model",
        default="",
        help="本地 GGUF 文件路径；留空则回退到 HF repo_id（需能访问 huggingface.co）",
    )
    parser.add_argument(
        "--n_gpu_layers",
        type=int,
        default=-1,
        help="-1 = 全部层交给 GPU（需 CUDA 版 llama-cpp-python）",
    )
    args = parser.parse_args()

    import llama_cpp

    print(f"GPU offload supported: {llama_cpp.llama_supports_gpu_offload()}")
    print("Loading model...")
    if args.model and os.path.isfile(args.model):
        llm = Llama(
            model_path=args.model,
            n_ctx=args.n_ctx,
            n_gpu_layers=args.n_gpu_layers,
            verbose=False,
        )
    else:
        if args.model:
            print(f"WARN: {args.model} 不是本地文件，按 HF repo_id 处理")
        llm = Llama.from_pretrained(
            repo_id=args.model or "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
            filename="qwen2.5-1.5b-instruct-q4_k_m.gguf",
            n_ctx=args.n_ctx,
            n_gpu_layers=args.n_gpu_layers,
            verbose=False,
        )
    print(f"Model loaded. Starting server on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)
