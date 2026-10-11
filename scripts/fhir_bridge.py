#!/usr/bin/env python3
"""FHIR Bridge —— 报告推送对端（A.50.9 E13/E11 真实对端层）。

HttpPacsAdapter（text/plain + Idempotency-Key）POST 到这里，
本服务把报告正文封装成 **标准 FHIR R4 DiagnosticReport** 资源
（LOINC 34117-2 Ultrasound study），提交到 HAPI FHIR server（JPA）。
HAPI 对资源做**行业标准合规校验**——不合法直接 4xx 拒收。

对端真实性语义：报告以标准 R4 资源形态进入行业参考实现，可被任意
FHIR 客户端检索——不是 LocalFake 的内存记账。

运行（真机）：
    PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/fhir_bridge.py
    # 监听 127.0.0.1:8091；HAPI 地址用 FHIR_BRIDGE_HAPI_URL 覆盖
"""
from __future__ import annotations

import base64
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


HAPI = os.environ.get("FHIR_BRIDGE_HAPI_URL", "http://127.0.0.1:8080/fhir")

app = FastAPI(title="UAI FHIR Bridge", docs_url=None, redoc_url=None)


def _build_diagnostic_report(payload: bytes, report_id: str,
                             patient_id: str | None, organ: str | None) -> dict:
    """报告正文 → 标准 FHIR R4 DiagnosticReport（LOINC 34117-2）。"""
    return {
        "resourceType": "DiagnosticReport",
        "identifier": [{"system": "urn:uai:report", "value": report_id}],
        "status": "final",
        "category": [{"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/v2-0074",
            "code": "US", "display": "Ultrasound",
        }]}],
        "code": {"coding": [{
            "system": "http://loinc.org", "code": "34117-2",
            "display": "Ultrasound study",
        }]},
        "subject": {"display": patient_id or "unknown"},
        "effectiveDateTime": datetime.now(timezone.utc).isoformat(),
        "conclusionCode": [{"text": organ or "unspecified"}],
        "presentedForm": [{
            "contentType": "text/plain; charset=utf-8",
            "data": base64.b64encode(payload).decode(),
        }],
    }


@app.get("/health")
async def health() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{HAPI}/metadata")
        return {"bridge": "ok", "hapi": r.status_code == 200}
    except Exception as exc:
        return {"bridge": "ok", "hapi": False, "error": str(exc)[:120]}


@app.post("/reports")
async def receive_report(request: Request) -> dict:
    payload = await request.body()
    report_id = request.headers.get("X-Report-Id", "")
    idem = request.headers.get("Idempotency-Key", "")
    patient_id = request.query_params.get("patient_id")
    organ = request.query_params.get("organ")
    if not payload or not report_id:
        raise HTTPException(status_code=400, detail="payload/X-Report-Id required")

    resource = _build_diagnostic_report(payload, report_id, patient_id, organ)

    # 幂等：同 report_id 已在 HAPI 则直接返回（不再二次创建）
    async with httpx.AsyncClient(timeout=30.0) as c:
        dup = await c.get(
            f"{HAPI}/DiagnosticReport",
            params={"identifier": f"urn:uai:report|{report_id}"},
        )
        entries = dup.json().get("entry", [])
        if entries:
            return {
                "received": True, "idempotent_replay": True,
                "hapi_resource_id": entries[0]["resource"]["id"],
                "hapi_version_id": entries[0]["resource"]["meta"]["versionId"],
            }

        r = await c.post(f"{HAPI}/DiagnosticReport", json=resource)
    if r.status_code not in (200, 201):
        raise HTTPException(status_code=502, detail=f"HAPI rejected: {r.status_code} {r.text[:300]}")
    created = r.json()
    return {
        "received": True,
        "idempotent_replay": False,
        "hapi_resource_id": created.get("id", ""),
        "hapi_version_id": created.get("meta", {}).get("versionId", ""),
    }


@app.get("/reports/{report_id}")
async def read_back(report_id: str) -> dict:
    """对端读回验证：按 identifier 从 HAPI 检索报告。"""
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(
            f"{HAPI}/DiagnosticReport",
            params={"identifier": f"urn:uai:report|{report_id}"},
        )
    bundle = r.json()
    entries = bundle.get("entry", [])
    if not entries:
        raise HTTPException(status_code=404, detail=f"report {report_id} not in HAPI")
    res = entries[0]["resource"]
    form = (res.get("presentedForm") or [{}])[0].get("data", "")
    return {
        "found_in_hapi": True,
        "hapi_resource_id": res["id"],
        "status": res.get("status"),
        "loinc": (res.get("code", {}).get("coding") or [{}])[0].get("code"),
        "payload_bytes": len(base64.b64decode(form)),
    }


if __name__ == "__main__":
    port = int(os.environ.get("FHIR_BRIDGE_PORT", "8091"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
