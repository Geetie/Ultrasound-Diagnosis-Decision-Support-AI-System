"""P10 FHIR 互操作探针（走查全部未覆盖，A.49）。

Patient CRUD + ImagingStudy（由 session 派生）+ DiagnosticReport/Observation
（由诊断结果派生，只读）。注意 FHIR 挂 /fhir 不带 /api/v1（A.33 坑），
Content-Type 为普通 application/json。
依赖 P8 的 audit_ctx（report_probe_session / report_probe_request）。
"""
from __future__ import annotations

import sys
import time

from _audit_common import check, http, load_ctx, login, pw, summary

DOCTOR = "smoke-doctor"


def main() -> int:
    admin = login("smoke-admin", pw("smoke_admin_pw"))
    doctor = login(DOCTOR, pw("smoke_pw"))
    dtok = doctor["access_token"]
    atok = admin["access_token"]
    ctx = load_ctx()
    sid = ctx.get("report_probe_session")
    rid = ctx.get("report_probe_request")
    if not sid or not rid:
        check("audit_ctx 前置", False, "缺 report_probe_session/request（先跑 p8）")
        return summary("P10 FHIR 互操作")

    pid = f"probe-fhir-{int(time.time())}"
    code, body = http("POST", "/fhir/Patient", body={"patient_id": pid, "name": "AUDIT FHIR PROBE", "gender": "unknown"}, token=dtok)
    check("POST /fhir/Patient", code in (200, 201), f"http={code} pid={pid}")

    code, _ = http("POST", "/fhir/Patient", body={"patient_id": pid}, token=dtok)
    check("POST /fhir/Patient 重复 -> 400", code == 400, f"http={code}")

    # 读端点带同意校验（fhir.py:100-115，非 granted 一律拒）——首轮探针 403
    # 是探针缺前置（探针患者从未 grant），非缺陷。先 grant 再读。
    code, _ = http("POST", "/api/v1/consent", body={"patient_id": pid, "action": "grant", "operator_id": "smoke-admin"}, token=atok)
    check("grant 探针患者（读端点前置）", code == 200, f"http={code}")

    code, body = http("GET", f"/fhir/Patient/{pid}", token=dtok)
    ok = code == 200 and isinstance(body, dict) and body.get("resourceType") == "Patient"
    check("GET /fhir/Patient/{id}", ok, f"http={code}")

    code, _ = http("PUT", f"/fhir/Patient/{pid}", body={"name": "AUDIT FHIR PROBE V2", "gender": "female"}, token=dtok)
    check("PUT /fhir/Patient/{id}", code == 200, f"http={code}")

    code, body = http("POST", "/fhir/ImagingStudy", body={"session_id": sid}, token=dtok)
    ok = code == 200 and isinstance(body, dict) and body.get("resourceType") == "ImagingStudy"
    check("POST /fhir/ImagingStudy（session 派生）", ok, f"http={code} id={body.get('id') if isinstance(body, dict) else body}")

    code, body = http("GET", f"/fhir/DiagnosticReport/{rid}", token=dtok)
    ok = code == 200 and isinstance(body, dict) and body.get("resourceType") == "DiagnosticReport"
    check("GET /fhir/DiagnosticReport/{request_id}", ok, f"http={code}")

    code, body = http("GET", f"/fhir/Observation/obs-{rid}", token=dtok)
    ok = code == 200 and isinstance(body, dict) and body.get("resourceType") == "Observation"
    check("GET /fhir/Observation/obs-{request_id}", ok, f"http={code}")

    return summary("P10 FHIR 互操作")


if __name__ == "__main__":
    sys.exit(main())
