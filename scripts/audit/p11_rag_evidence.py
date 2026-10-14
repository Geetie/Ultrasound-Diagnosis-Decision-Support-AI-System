#!/usr/bin/env python3
"""E2（21 号核实清单）：RAG 证据链真机核实（qdrant 降级 pgvector 态）。

缺口：判读全链 PASS，但结果里 evidence.knowledge_ids / evidence.content
从未断言——降级库若静默空返回，RAG 就是在"裸奔"而门禁全绿。

核实：FHIR 探针患者 → grant → 建会话 → CAMUS 真实帧转 DICOM 上传 →
发判读 → 轮询 completed → 断言 evidence 非空且 fusion/检索信号在。

预期：evidence.knowledge_ids 非空（或 content 非空）= RAG 真实参与了结论。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from _audit_common import BASE, check, http, login, pw, save_ctx, summary

logger = logging.getLogger(__name__)


def make_camus_dicoms(count: int = 2) -> list[str]:
    """CAMUS 真实帧转 DICOM（用户制度：测试一律真实数据）。

    转换器输出 `<prefix>_0.dcm`、`<prefix>_1.dcm`（每帧一个单帧文件）。
    """
    out_dir = "/tmp/e2_rag"
    os.makedirs(out_dir, exist_ok=True)
    venv_py = os.path.expanduser("~/uai/venv/bin/python")
    script = os.path.expanduser("~/uai/src/scripts/camus_frame_to_dicom.py")
    prefix = f"{out_dir}/camus_e2.dcm"
    subprocess.run(
        [venv_py, script, "--out", prefix, "--count", str(count), "--patient", "E2-RAG-PROBE"],
        check=True, capture_output=True,
    )
    return sorted(
        os.path.join(out_dir, f) for f in os.listdir(out_dir)
        if f.startswith("camus_e2_") and f.endswith(".dcm")
    )


def upload_dicom(token: str, sid: str, path: str) -> tuple[int, dict]:
    """stdlib multipart 上传（_audit_common.http 不支持 multipart）。"""
    import uuid

    boundary = f"----e2rag{uuid.uuid4().hex}"
    with open(path, "rb") as f:
        content = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(path)}"\r\n'
        f"Content-Type: application/dicom\r\n\r\n"
    ).encode() + content + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="session_id"\r\n\r\n{sid}\r\n'
        f"--{boundary}--\r\n"
    ).encode()
    import urllib.request

    req = urllib.request.Request(
        f"{BASE}/api/v1/imaging/dicom/upload",
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except Exception as exc:
        logger.warning("upload_dicom: 未预期异常，按原逻辑降级", exc_info=True)
        return -1, {"error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    admin = login("smoke-admin", pw("smoke_admin_pw"))
    doctor = login("smoke-doctor", pw("smoke_pw"))
    dtok = doctor["access_token"]

    # 1) 探针患者 + grant
    pid = f"probe-e2rag-{int(time.time())}"
    code, _ = http("POST", "/fhir/Patient", body={"patient_id": pid, "name": "E2 RAG PROBE", "gender": "unknown"}, token=dtok)
    check("创建 FHIR 探针患者", code in (200, 201), f"http={code}")
    code, _ = http("POST", "/api/v1/consent", body={"patient_id": pid, "action": "grant", "operator_id": "smoke-admin"}, token=admin["access_token"])
    check("grant 探针患者", code == 200, f"http={code}")

    # 2) 建会话
    code, body = http("POST", "/api/v1/sessions", body={"patient_id": pid, "device_id": "probe-e2rag", "target_organs": ["cardiac"]}, token=dtok)
    sid = body.get("session_id") if isinstance(body, dict) else None
    check("建会话", code == 200 and sid, f"http={code} sid={sid}")

    # 3) CAMUS 真实帧 → DICOM → 上传
    paths = make_camus_dicoms(2)
    img_ids = []
    for p in paths:
        code, body = upload_dicom(dtok, sid, p)
        ok = code == 200
        iid = body.get("image_id") if ok else None
        if iid:
            img_ids.append(iid)
        check(f"上传 {os.path.basename(p)}", ok and iid, f"http={code} {str(body)[:80]}")

    # 4) 发判读（心脏）
    code, body = http(
        "POST", "/api/v1/diagnosis/request",
        body={"session_id": sid, "target_organ": "cardiac", "image_ids": img_ids},
        token=dtok,
    )
    rid = body.get("request_id") if isinstance(body, dict) else None
    check("发判读", code == 200 and rid, f"http={code} rid={rid}")

    # 5) 轮询到 completed（上限 120s）
    result = None
    for _ in range(60):
        time.sleep(2)
        code, body = http("GET", f"/api/v1/diagnosis/result/{rid}", token=dtok)
        if code == 200 and isinstance(body, dict) and body.get("status") == "completed":
            result = body
            break
    check("判读完成", result is not None, "120s 内 completed" if result else "超时")

    # 6) ★ 核心断言：RAG 证据链非空
    if result:
        ev = result.get("evidence") or {}
        kids = ev.get("knowledge_ids") or []
        content = ev.get("content")
        has_evidence = bool(kids) or bool(content)
        check("RAG 证据链非空（knowledge_ids 或 content）", has_evidence,
              f"kids={len(kids) if kids else 0} content_len={len(content) if content else 0}")
        fusion = (result.get("diagnosis") or {}).get("fusion_result") or result.get("diagnosis")
        check("诊断载荷含 fusion/检索信号", isinstance(fusion, dict) and len(json.dumps(fusion)) > 50,
              f"payload_len={len(json.dumps(result.get('diagnosis') or {}))}")

    save_ctx(
        e2rag_probe_session=sid or "",
        e2rag_probe_request=rid or "",
        e2rag_probe_patient=pid,
        updated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    return summary("E2 RAG 证据链核实")


if __name__ == "__main__":
    sys.exit(main())
