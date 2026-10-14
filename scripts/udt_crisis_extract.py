"""Task 2 backup: cardiac crisis-term extraction via 8002 (Qwen2.5-7B).

Targets kb_src_v13/cardiac/pdf_cardiac_*.json guideline docs with a
crisis-focused prompt. Merges result into existing crisis_terms_cardiac.json
(kept terms from keyword filter, e.g. 主动脉夹层).

Output: data/udt/crisis_terms_cardiac.json {term: count}
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

SRC = Path("/mnt/ee16/home/student03/uai/src")
KB = SRC / "data" / "kb_src_v13" / "cardiac"
OUT = SRC / "data" / "udt" / "crisis_terms_cardiac.json"
CONC = 8
MODEL = "Qwen2.5-7B-Instruct"

PROMPT = """从下列心脏超声文本中抽取危象/危急病症相关术语（提示需紧急临床处置的严重异常）。
候选方向：心包填塞、主动脉夹层、室壁穿孔、腱索断裂、瓣膜赘生物、心脏破裂、大量心包积液、室间隔穿孔、血栓形成、重度瓣膜反流，以及同类危急病变。
要求：
- 只输出文本中实际出现的术语，不要编造
- 每个术语 2~12 个汉字
- 输出 JSON 对象，terms 数组最多 15 个

文本：
{text}"""

JSON_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "crisis_terms",
        "schema": {
            "type": "object",
            "properties": {
                "terms": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["terms"],
        },
    },
}

CJK_RE = re.compile(r"^[\u4e00-\u9fff]{2,12}$")


async def one(client: AsyncOpenAI, sem: asyncio.Semaphore, text: str,
              counter: Counter, lock: asyncio.Lock) -> None:
    async with sem:
        text = text[:1200]
        try:
            resp = await asyncio.wait_for(client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": PROMPT.format(text=text)}],
                max_tokens=256, temperature=0.0,
                response_format=JSON_SCHEMA,
            ), timeout=45)
            data = json.loads(resp.choices[0].message.content or "{}")
            terms = data.get("terms", [])
            async with lock:
                for t in terms:
                    if isinstance(t, str) and CJK_RE.match(t):
                        counter[t] += 1
        except Exception:
            logger.warning("one: 未预期异常，按原逻辑降级", exc_info=True)
            return


async def main() -> int:
    client = AsyncOpenAI(base_url="http://127.0.0.1:8002/v1",
                         api_key="EMPTY", timeout=45)
    sem = asyncio.Semaphore(CONC)
    lock = asyncio.Lock()
    texts = []
    for p in sorted(KB.glob("pdf_cardiac_*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("main: 未预期异常，按原逻辑降级", exc_info=True)
            continue
        t = d.get("content", "") or d.get("text", "")
        if len(t) > 100:
            texts.append(t)
    print(f"[crisis-extract] docs={len(texts)}", flush=True)
    counter: Counter = Counter()
    await asyncio.gather(*(one(client, sem, t, counter, lock) for t in texts))
    # seed: keyword-filtered survivors from previous step
    merged: dict[str, int] = {}
    if OUT.exists():
        merged = json.loads(OUT.read_text(encoding="utf-8"))
    for t, c in counter.items():
        merged[t] = merged.get(t, 0) + c
    merged = dict(sorted(merged.items(), key=lambda kv: -kv[1]))
    OUT.write_text(json.dumps(merged, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"[crisis-extract] llm raw={len(counter)} merged={len(merged)} -> {OUT}",
          flush=True)
    for t, c in merged.items():
        print(f"  {t}: {c}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
