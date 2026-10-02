"""UDT Layer3：LLM 术语抽取（vLLM 8002, Qwen2.5-7B）。
对每器官采样 N 篇文档，抽取超声术语，输出频次表。
产出: data/udt/llm_terms.json {organ: {term: count}}
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

from openai import AsyncOpenAI

SRC = Path("/mnt/ee16/home/student03/uai/src")
KB = SRC / "data" / "kb_src_v13"
OUT = SRC / "data" / "udt" / "llm_terms.json"
SAMPLE_PER_ORGAN = 120
CONC = 8

PROMPT = """从下列超声科文本中抽取医学专业术语（超声所见、病灶描述、解剖结构、测量指标、诊断名词）。
要求：
- 只输出文本中出现的术语，不要编造
- 每个术语 2~10 个汉字
- 输出 JSON 数组，最多 15 个

文本：
{text}

输出格式：["术语1", "术语2", ...]"""

JSON_RE = re.compile(r"\[.*\]", re.DOTALL)


async def one(client: AsyncOpenAI, model: str, sem: asyncio.Semaphore,
              text: str, counter: Counter, lock: asyncio.Lock) -> None:
    async with sem:
        text = text[:1200]
        try:
            resp = await asyncio.wait_for(client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": PROMPT.format(text=text)}],
                max_tokens=256, temperature=0.0,
            ), timeout=45)
            m = JSON_RE.search(resp.choices[0].message.content or "")
            if not m:
                return
            terms = json.loads(m.group(0))
            async with lock:
                for t in terms:
                    if isinstance(t, str) and 2 <= len(t) <= 12:
                        counter[t] += 1
        except Exception:
            return


async def main() -> int:
    rng = random.Random(42)
    client = AsyncOpenAI(base_url="http://127.0.0.1:8002/v1",
                         api_key="EMPTY", timeout=45)
    sem = asyncio.Semaphore(CONC)
    lock = asyncio.Lock()
    out: dict[str, dict[str, int]] = {}
    for organ in ("thyroid", "liver", "cardiac"):
        docs = sorted((KB / organ).glob("*.json"))
        rng.shuffle(docs)
        texts = []
        for p in docs[:SAMPLE_PER_ORGAN]:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            t = d.get("content", "") or d.get("text", "")
            if len(t) > 100:
                texts.append(t)
        counter: Counter[str] = Counter()
        await asyncio.gather(*(one(client, "Qwen2.5-7B-Instruct", sem, t,
                                   counter, lock) for t in texts))
        keep = {t: c for t, c in counter.most_common() if c >= 2}
        out[organ] = keep
        print(f"[llm-terms] {organ}: {len(keep)} terms (count>=2, docs={len(texts)})",
              flush=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[llm-terms] ->", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
