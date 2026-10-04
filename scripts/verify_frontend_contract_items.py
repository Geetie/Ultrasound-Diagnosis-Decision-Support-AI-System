"""核实契约对账报出的 8 处"断链"到底是真断还是假报。

对账脚本把前端模板串归一成了 ``${}``、后端是 ``{id}``，第一版没处理这层
（另一 Agent 已修模板串那半）。这里做**实证核实**：拿真实路由逐个比对，
输出每个前端调用点对应后端路由是否存在、HTTP 方法是否匹配。

用法（在服务器上）：``python scripts/verify_frontend_contract_items.py``
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:18000"
FRONTEND_SRC = Path("/mnt/ee16/home/student03/uai/src/frontend/src")

# 前端调用 → 后端应有的路由（人工从对账报告整理，逐个核实）
CHECKS = [
    ("consent", "/api/v1/consent/{patientId}"),
    ("diagnosis result", "/api/v1/diagnosis/result/{requestId}"),
    ("report by id", "/api/v1/report/{reportId}"),
    ("report sign", "/api/v1/report/{reportId}/sign"),
    ("report verify", "/api/v1/report/{reportId}/verify"),
    ("report push", "/api/v1/report/{reportId}/push"),
    ("report by-request", "/api/v1/report/by-request/{requestId}"),
    ("session by id", "/api/v1/sessions/{sessionId}"),
]


def main() -> int:
    with urllib.request.urlopen(f"{API}/openapi.json", timeout=60) as resp:
        paths = json.loads(resp.read().decode()).get("paths", {})

    print(f"后端路由数: {len(paths)}")
    print("")
    missing = 0
    for label, route in CHECKS:
        norm = re.sub(r"\{[^}]+\}", "{}", route)
        hit = [p for p in paths if re.sub(r"\{[^}]+\}", "{}", p) == norm]
        if hit:
            methods = sorted(m.upper() for m in paths[hit[0]])
            print(f"  [OK]   {label:20} {route}  →  {', '.join(methods)}")
        else:
            missing += 1
            print(f"  [MISS] {label:20} {route}  →  后端无此路径")
    print("")
    print(f"真实断链: {missing} / {len(CHECKS)}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
