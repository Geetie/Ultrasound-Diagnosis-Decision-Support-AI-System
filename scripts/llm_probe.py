"""LLM 在主服务视角的真实可用性（演练里的 llm_unavailable 根因）。

直接问主服务用的那个 LLM 客户端配置（不自己猜端口），跑一次真实生成。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.expanduser("~/uai/src"))


async def main():
    from app.config.settings import settings

    for attr in sorted(dir(settings)):
        if any(k in attr.lower() for k in ("llm", "model", "openai", "vllm", "llama")):
            v = getattr(settings, attr)
            if isinstance(v, str) and len(v) < 200:
                print(f"  {attr} = {v}")

    print("--- 真实调用 ---")
    try:
        from app.llm import get_llm_client  # type: ignore

        client = get_llm_client()
        print("  client:", type(client).__name__)
    except Exception as exc:
        print(f"  get_llm_client failed: {type(exc).__name__}: {exc}")

    # 试 dispatcher 走的那条路
    for mod, fn in (
        ("app.llm.client", "LLMClient"),
        ("app.llm", "generate"),
        ("app.agents.dispatcher", "dispatch"),
    ):
        try:
            __import__(mod)
            print(f"  import ok: {mod}.{fn}")
        except Exception as exc:
            print(f"  import fail: {mod} ({type(exc).__name__}: {exc})")


asyncio.run(main())
