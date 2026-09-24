import argparse

import uvicorn
from llama_cpp import Llama
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

llm: Llama | None = None


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


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
    )
    return JSONResponse(response)


app = Starlette(routes=[
    Route("/health", health),
    Route("/v1/completions", completions, methods=["POST"]),
    Route("/v1/chat/completions", chat_completions, methods=["POST"]),
])

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--n_ctx", type=int, default=512)
    args = parser.parse_args()

    print("Loading model...")
    llm = Llama.from_pretrained(
        repo_id="Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        filename="qwen2.5-1.5b-instruct-q4_k_m.gguf",
        n_ctx=args.n_ctx,
        verbose=False,
    )
    print(f"Model loaded. Starting server on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)
