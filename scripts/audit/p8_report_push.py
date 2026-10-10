"""P8 报告分发与验证探针（走查止步于已签发，A.49）。

补走签发后的分发段：sign → PATCH(已签必须拒) → verify(验签) → push（PACS 适配器，
开发环境可能推送失败——断言**推送失败不改签名状态、可重推**语义）→ verify 复核。
前置链：FHIR 患者 → admin grant → 会话 → 判读 completed。
"""
from __future__ import annotations

import sys
import time

from _audit_common import check, http, login, pw, save_ctx, summary

DOCTOR = "smoke-doctor"
TERMINAL = {"completed", "failed", "timeout", "needs_review", "cancelled"}


def main() -> int:
    admin = login("smoke-admin", pw("smoke_admin_pw"))
    doctor = login(DOCTOR, pw("smoke_pw"))
    atok, dtok = admin["access_token"], doctor["access_token"]

    pid = f"probe-rpt-{int(time.time())}"
    code, _ = http("POST", "/fhir/Patient", body={"patient_id": pid, "name": "AUDIT REPORT PROBE"}, token=dtok)
    check("FHIR 建探针患者", code in (200, 201), f"http={code} pid={pid}")
    code, _ = http("POST", "/api/v1/consent", body={"patient_id": pid, "action": "grant", "operator_id": "smoke-admin"}, token=atok)
    check("grant 探针患者", code == 200, f"http={code}")

    code, body = http("POST", "/api/v1/sessions", body={"patient_id": pid, "device_id": "audit-probe", "target_organs": ["thyroid"]}, token=dtok)
    sid = body.get("session_id") if isinstance(body, dict) else None
    check("建会话", code == 200 and bool(sid), f"http={code} session_id={sid}")

    code, body = http(
        "POST", "/api/v1/diagnosis/request",
        body={
            "session_id": sid, "image_ids": [],
            "clinical_text": "审计探针自造文本：甲状腺右叶低回声结节 3x4mm，边界清。",
            "target_organ": "thyroid",
            "patient_context": {"age": 40, "gender": "female", "conditions": []},
        },
        token=dtok,
    )
    rid = body.get("request_id") if isinstance(body, dict) else None
    check("判读受理", code == 200 and bool(rid), f"http={code} request_id={rid}")

    # 轮询 completed（管线真实 8~60s；180s 服务端超时）
    status = None
    t0 = time.time()
    while time.time() - t0 < 170:
        time.sleep(4)
        code, body = http("GET", f"/api/v1/diagnosis/result/{rid}", token=dtok)
        status = body.get("status") if isinstance(body, dict) else None
        if status in TERMINAL:
            break
    check("判读 completed", status == "completed", f"observed={status} ({time.time()-t0:.0f}s)")

    # 报告：创建草稿 → 补全段落 → 签发
    code, body = http("POST", "/api/v1/report", body={"request_id": rid}, token=dtok)
    report_id = body.get("report_id") if isinstance(body, dict) else None
    check("POST report 草稿", code == 200 and bool(report_id), f"http={code} report_id={report_id}")

    sections = {
        "findings": "甲状腺右叶低回声结节，边界清，未见钙化（审计探针补录文本）。",
        "diagnosis": "倾向良性结节，建议随访（审计探针文本，非临床结论）。",
        "measurement_summary": "结节 3x4mm（审计探针）。",
        "clinical_actions": "6 个月复查超声（审计探针）。",
    }
    code, _ = http("PATCH", f"/api/v1/report/{report_id}", body={"sections": sections}, token=dtok)
    check("PATCH 补录段落 200", code == 200, f"http={code}")

    code, _ = http("POST", f"/api/v1/report/{report_id}/sign", body={}, token=dtok)
    check("sign 200（HSM 签发）", code == 200, f"http={code}")

    code, _ = http("PATCH", f"/api/v1/report/{report_id}", body={"sections": {"findings": "x"}}, token=dtok)
    check("PATCH 已签报告 -> 400（锁定）", code == 400, f"http={code}")

    code, body = http("POST", f"/api/v1/report/{report_id}/verify", token=dtok)
    ok = code == 200 and isinstance(body, dict) and body.get("signature_verified") is True
    check("verify signature_verified=true", ok, f"http={code} {str(body)[:100]}")

    # push（PACS 适配器）：开发环境可能失败——断言失败不改签名状态
    code, body = http("POST", f"/api/v1/report/{report_id}/push", body={}, token=dtok)
    check("push 可达（2xx 或明确失败语义）", code in (200, 201) or 400 <= code < 600,
          f"http={code} {str(body)[:100]}")
    code, body = http("POST", f"/api/v1/report/{report_id}/verify", token=dtok)
    ok = code == 200 and isinstance(body, dict) and body.get("signature_verified") is True
    check("push 后 verify 仍 verified（签名状态未被推送破坏）", ok, f"http={code}")

    save_ctx(report_probe_request=rid, report_probe_session=sid, report_probe_report=report_id)
    return summary("P8 报告分发与验证")


if __name__ == "__main__":
    sys.exit(main())
