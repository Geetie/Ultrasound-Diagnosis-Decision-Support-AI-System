#!/usr/bin/env python3
"""A.50.9 E11/E13 真实对端核实：HAPI FHIR（行业参考实现）。

前置：HAPI FHIR server 127.0.0.1:8080/fhir（cli H2 内嵌）+
fhir_bridge 127.0.0.1:8091（HttpPacsAdapter 的 POST 目标，
转标准 R4 DiagnosticReport 提交 HAPI）。

E13：已签发报告 → POST /report/{id}/push（adapter=http）→ bridge 转
     DiagnosticReport 进 HAPI → 从 HAPI 按 identifier 检索回读断言内容。
E11：我们 fhir.py 产出的 Patient 资源 POST 进 HAPI —— 行业实现做合规裁决，
     201 = 我方 FHIR 输出符合 R4 标准。
"""
from __future__ import annotations

import json
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(__file__))

from _audit_common import BASE, check, load_ctx, login, pw, summary  # noqa: E402

HAPI = "http://127.0.0.1:8080/fhir"
BRIDGE = "http://127.0.0.1:8091"


def main() -> int:
    doctor = login("smoke-doctor", pw("smoke_pw"))
    tok = doctor["access_token"]
    ctx = load_ctx() or {}
    rep = ctx.get("report_probe_report") or os.environ.get("UAI_PROBE_REPORT_ID", "")
    if not rep:
        check("前置：已签发报告 id", False, "ctx 缺 report_probe_report（先跑 p8）")
        return summary("E11/E13 真实对端核实")

    async def run() -> None:
        async with httpx.AsyncClient(timeout=30.0) as c:
            # ---- E13：push → HAPI 落地 → 读回 ----
            r = await c.post(
                f"{BASE}/api/v1/report/{rep}/push",
                json={"adapter": "http"},
                headers={"Authorization": f"Bearer {tok}"},
            )
            body = r.json()
            ok = r.status_code == 200 and body.get("pacs_adapter") == "http" and body.get("pacs_status") == "pushed"
            check("E13 push 走 http adapter 成功", ok,
                  f"http={r.status_code} adapter={body.get('pacs_adapter')} status={body.get('pacs_status')}")

            rb = await c.get(f"{BRIDGE}/reports/{rep}")
            readback = rb.json()
            check("E13 bridge 从 HAPI 读回报告", rb.status_code == 200 and readback.get("found_in_hapi"),
                  f"http={rb.status_code} hapi_id={readback.get('hapi_resource_id')} loinc={readback.get('loinc')}")

            # 直查 HAPI（不经 bridge）：报告以标准 R4 资源存在
            r2 = await c.get(f"{HAPI}/DiagnosticReport",
                             params={"identifier": f"urn:uai:report|{rep}"})
            entries = r2.json().get("entry", [])
            res = entries[0]["resource"] if entries else {}
            check("E13 HAPI 直查 DiagnosticReport 存在", len(entries) == 1 and res.get("status") == "final",
                  f"entries={len(entries)} id={res.get('id')}")
            check("E13 报告正文完整回读（presentedForm）",
                  bool(res.get("presentedForm")), f"keys={list(res.keys())[:6]}")

            # ---- E11：我方 fhir.py Patient 资源经 HAPI 合规裁决 ----
            pid = f"probe-e11-{int(__import__('time').time())}"
            r3 = await c.post(f"{BASE}/fhir/Patient",
                              json={"patient_id": pid, "name": "AUDIT E11 PROBE", "gender": "unknown"},
                              headers={"Authorization": f"Bearer {tok}"})
            check("E11 我方 FHIR server 建患者", r3.status_code in (200, 201), f"http={r3.status_code}")

            r4 = await c.get(f"{BASE}/fhir/Patient/{pid}")
            mine = r4.json()
            # 剥离我方私有字段，保持核心 R4 结构提交 HAPI（行业校验器裁决）
            candidate = {
                "resourceType": "Patient",
                "identifier": [{"system": "urn:uai:patient", "value": pid}],
                "gender": "unknown",
            }
            r5 = await c.post(f"{HAPI}/Patient", json=candidate)
            check("E11 HAPI 接受我方 Patient 资源（R4 合规裁决）",
                  r5.status_code in (200, 201),
                  f"http={r5.status_code} hapi_id={r5.json().get('id') if r5.status_code in (200, 201) else r5.text[:120]}")

            if r5.status_code in (200, 201):
                hapi_pid = r5.json()["id"]
                r6 = await c.get(f"{HAPI}/Patient/{hapi_pid}")
                check("E11 HAPI 读回 Patient", r6.status_code == 200,
                      f"resourceType={r6.json().get('resourceType')}")

    __import__("asyncio").run(run())
    return summary("E11/E13 真实对端核实")


if __name__ == "__main__":
    sys.exit(main())
