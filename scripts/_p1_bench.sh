#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
export PYTHONPATH="$UAI/src"
echo "=== 并发吞吐验证（4 并发 × 8 例 prompt 级生成）==="
"$UAI/venv/bin/python" - <<'EOF'
import concurrent.futures
import json
import time
import urllib.request

def gen(i: int) -> float:
    body = json.dumps({
        "messages": [{"role": "user", "content": f"甲状腺结节 case {i}，低回声边界清晰，请给出TI-RADS分级。"}],
        "max_tokens": 128,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8001/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as resp:
        json.load(resp)
    return time.perf_counter() - t0

t0 = time.perf_counter()
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    lat = list(pool.map(gen, range(8)))
wall = time.perf_counter() - t0
print(f"8 例 wall={wall:.1f}s | 平均单例={wall/8:.1f}s（并发4摊薄）")
print(f"单例延迟: min={min(lat):.1f}s max={max(lat):.1f}s")
EOF
