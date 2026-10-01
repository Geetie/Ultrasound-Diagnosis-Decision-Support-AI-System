import re
p = "scripts/t1_eval_faithfulness.py"
s = open(p, encoding="utf-8").read()

# 1) 同步 _chat 后追加异步 _achat（保留 _chat 兼容）
achat = '''

async def _achat(client, url: str, model: str, messages: list, temperature: float = 0.0) -> str:
    """T-Perf：异步 chat（替换串行 urllib 版，评测器并发化）。"""
    resp = await client.post(
        f"{url}/v1/chat/completions",
        json={"model": model, "messages": messages, "temperature": temperature},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

'''
marker = "def _extract_json(text: str)"
s = s.replace(marker, achat + "\n" + marker, 1)

# 2) imports
s = s.replace("import urllib.request", "import urllib.request\n\nimport httpx", 1)

# 3) 调用点 _chat( → await _achat(client, （仅生成/判官三处，函数内引用）
s = s.replace("answer = _chat(\n                args.generator_url,", "answer = await _achat(\n                client,\n                args.generator_url,")
s = s.replace("_chat(\n                    args.judge_url,", "await _achat(\n                    client,\n                    args.judge_url,")

# 4) 主循环改并发：for 循环体抽协程 + gather
s = s.replace("    results: list[dict] = []\n    t_start = time.perf_counter()\n    for qi, q in enumerate(queries):",
"""    results = [None] * len(queries)
    t_start = time.perf_counter()
    client = httpx.AsyncClient()
    sem = asyncio.Semaphore(16)
    t1e = sys.modules[__name__]

    async def _process_one(qi: int, q: dict) -> None:
        async with sem:
            results[qi] = await _eval_one(client, args, corpus, corpus_texts, fused, qi, q)

    await asyncio.gather(*(_process_one(i, q) for i, q in enumerate(queries)))""")

open(p, "w", encoding="utf-8", newline="\n").write(s)
print("step1 done")
