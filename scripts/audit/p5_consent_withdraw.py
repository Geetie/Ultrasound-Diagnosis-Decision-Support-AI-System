"""P5 同意与撤回消费探针（走查只测正向态，A.49）。

撤回消费链（app/security/consent_withdrawal_consumer.py:130-140 →
diagnosis_pipeline.py L438-466）：POST consent withdraw → Redis Streams →
consumer 取消在途 → 管线 asyncio.shield 写 status="cancelled" 终态。

序列：FHIR 建探针患者 → admin GET consent(无记录 denied) → grant → 医生建会话 →
发判读 → withdraw → 轮询 result 到 cancelled 终态 → GET consent=withdrawn。
探针患者不复原（withdrawn 保持，属探针实体）。
"""
from __future__ import annotations

import sys
import time

from _audit_common import check, http, login, pw, save_ctx, summary

DOCTOR = "smoke-doctor"


def main() -> int:
    admin = login("smoke-admin", pw("smoke_admin_pw"))
    doctor = login(DOCTOR, pw("smoke_pw"))
    atok, dtok = admin["access_token"], doctor["access_token"]

    pid = f"probe-cw-{int(time.time())}"
    # FHIR 建患者（医生可建：仅 Bearer）
    code, _ = http("POST", "/fhir/Patient", body={"patient_id": pid, "name": "AUDIT CONSENT PROBE"}, token=dtok)
    check("FHIR 建探针患者", code in (200, 201), f"http={code} pid={pid}")

    # 无记录 → denied
    code, body = http("GET", f"/api/v1/consent/{pid}", token=atok)
    ok = code == 200 and isinstance(body, dict) and body.get("consent_status") == "denied"
    check("GET consent 无记录 -> denied", ok, f"http={code} status={body.get('consent_status') if isinstance(body, dict) else body}")

    # grant
    code, body = http(
        "POST", "/api/v1/consent",
        body={"patient_id": pid, "action": "grant", "operator_id": "smoke-admin"},
        token=atok,
    )
    ok = code == 200 and isinstance(body, dict) and body.get("consent_status") == "granted"
    check("POST consent grant -> granted", ok, f"http={code}")

    # 医生建会话（需 granted 前置）
    code, body = http(
        "POST", "/api/v1/sessions",
        body={"patient_id": pid, "device_id": "audit-probe", "target_organs": ["thyroid"]},
        token=dtok,
    )
    sid = body.get("session_id") if isinstance(body, dict) else None
    check("建会话（granted 前置）", code == 200 and bool(sid), f"http={code} session_id={sid}")

    # 发判读（自造文本，非真实患者数据；SG 条件显式给空列表）
    code, body = http(
        "POST", "/api/v1/diagnosis/request",
        body={
            "session_id": sid,
            "image_ids": [],
            "clinical_text": "审计探针自造文本：低回声结节，边界清。",
            "target_organ": "thyroid",
            "patient_context": {"age": 45, "gender": "female", "conditions": []},
        },
        token=dtok,
    )
    rid = body.get("request_id") if isinstance(body, dict) else None
    check("判读受理 processing", code == 200 and bool(rid), f"http={code} request_id={rid}")

    # 撤回 → 在途取消
    code, body = http(
        "POST", "/api/v1/consent",
        body={"patient_id": pid, "action": "withdraw", "operator_id": "smoke-admin"},
        token=atok,
    )
    check("POST consent withdraw", code == 200 and isinstance(body, dict) and body.get("consent_status") == "withdrawn",
          f"http={code}")

    # 轮询 cancelled 终态（异步消费，窗口 90s）
    got = None
    t0 = time.time()
    while time.time() - t0 < 90:
        time.sleep(4)
        code, body = http("GET", f"/api/v1/diagnosis/result/{rid}", token=dtok)
        got = body.get("status") if isinstance(body, dict) else None
        if got in ("cancelled", "completed", "failed"):
            break
    check("撤回消费 -> status=cancelled 终态", got == "cancelled", f"observed={got} ({time.time()-t0:.0f}s)")

    # 终态核对：GET consent withdrawn
    code, body = http("GET", f"/api/v1/consent/{pid}", token=atok)
    check("GET consent withdrawn", code == 200 and isinstance(body, dict) and body.get("consent_status") == "withdrawn",
          f"http={code}")

    save_ctx(consent_probe_patient=pid, consent_probe_session=sid or "", consent_probe_request=rid or "")
    return summary("P5 同意与撤回消费")


if __name__ == "__main__":
    sys.exit(main())
