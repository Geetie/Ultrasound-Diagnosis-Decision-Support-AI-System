"""打印两侧健康接口的紧凑摘要（避免 shell 引号地狱，单独放一个文件）。"""

import json
import urllib.request


def fetch(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def walk(prefix: str, node: dict) -> None:
    if not isinstance(node, dict):
        print(f"{prefix}: {node}")
        return
    status = node.get("status")
    if status is not None:
        extras = {
            k: v
            for k, v in node.items()
            if k
            in (
                "status_code",
                "loaded",
                "threshold_count",
                "error",
                "total_breakers",
                "open_breakers",
                "p99_status",
            )
        }
        print(f"{prefix}: {status} {extras if extras else ''}")
        return
    for key, value in node.items():
        walk(f"{prefix}.{key}" if prefix else key, value)


print("=== SG /health ===")
try:
    sg = fetch("http://127.0.0.1:9001/health")
    print("overall:", sg.get("status"))
    for key, value in sg.items():
        if key in ("latency_stats", "e2e_latency_stats", "timestamp", "uptime_human", "version"):
            continue
        walk(key, value)
except Exception as exc:
    print("SG fetch failed:", exc)

print()
print("=== 主服务 /api/v1/health ===")
try:
    api = fetch("http://127.0.0.1:18000/api/v1/health")
    print("overall:", api.get("status"))
    walk("", api.get("services") or {})
except Exception as exc:
    print("api fetch failed:", exc)
