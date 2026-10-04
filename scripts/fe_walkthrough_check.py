"""前端调用序列真机走查（模拟医生点击，按 frontend/src 的真实路径与参数）。

为什么需要
----------
门禁与演练都是"后端视角"（我按自己理解的参数调）。前端是**另一套调用序列**：
axios 客户端、自己的入参 schema（``types/api.ts``）、自己的轮询终止条件。
HTTP 200 全通 ≠ 前端能跑——历史上就出过三处 404（前端注释里记着：
建会话/查会话/结束会话全写错）。

本脚本**严格照抄前端源码**：
  - 路径取自 ``frontend/src/services/*.ts``（apiClient 的 baseURL 是 /api/v1）
  - 入参取自 ``frontend/src/types/api.ts`` 的 interface
  - 轮询间隔与终止条件取自 ``diagnosisService.ts``（pollInterval=2000）

差异处会显式标注（例：前端 gender 是 'M'|'F'，我之前演练传 'female'）。
"""
from __future__ import annotations

import json
import mimetypes
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
import uuid

API = os.environ.get("DRILL_API", "http://127.0.0.1:18000")
BASE = f"{API}/api/v1"          # ← 前端 apiClient 的 baseURL
PW = pathlib.Path(os.path.expanduser("~/uai/.smoke_pw"))
UPLOAD_DIR = pathlib.Path(os.path.expanduser("~/uai/data/drill"))

PASS, FAIL = 0, 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  [PASS] {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {msg}")


def call(path: str, data=None, token=None, method=None, timeout=90):
    """等价前端的 apiClient.request：baseURL + path + Bearer。"""
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        BASE + path, data=body, headers={"Content-Type": "application/json"},
        method=method or ("POST" if body is not None else "GET"))
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw[:300]}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def upload_dicom(path: pathlib.Path, session_id: str, token: str):
    """等价前端上传：multipart/form-data。"""
    boundary = f"----fe{uuid.uuid4().hex[:12]}"
    body = bytearray()
    for k, v in (("session_id", session_id), ("priority", "1")):
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    ctype = mimetypes.guess_type(path.name)[0] or "application/dicom"
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
             f"filename=\"{path.name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode()
    body += path.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{API}/api/v1/imaging/dicom/upload", data=bytes(body), method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw[:300]}


def main() -> int:
    print("=== 前端调用序列真机走查（照抄 frontend/src 的路径与入参）===")

    print("--- 0) 准备演练 DICOM ---")
    dcm = UPLOAD_DIR / "drill.dcm"
    if not dcm.is_file():
        src = sorted(pathlib.Path(os.path.expanduser(
            "~/uai/data/tn3k_dataset/test-image")).glob("*"))
        if not src:
            bad("找不到演练用图，无法建 DICOM")
            return 1
        sys.path.insert(0, os.path.expanduser("~/uai/src/scripts"))
        from make_drill_dicom import build as build_dicom
        build_dicom(src[0], dcm)
    print(f"  using {dcm}")

    print("--- 1) authService.login：POST /auth/login ---")
    if not PW.exists():
        bad("缺 ~/uai/.smoke_pw")
        return 1
    code, body = call("/auth/login", {
        "username": "smoke-doctor",
        "password": PW.read_text(encoding="utf-8").strip(),
    })
    if code != 200 or not body.get("access_token"):
        bad(f"登录失败 http={code} {json.dumps(body, ensure_ascii=False)[:200]}")
        return 1
    token = body["access_token"]
    ok("登录拿到 token")

    print("--- 2) consentService.ensureConsent：同意闸门 ---")
    # 前端若未签署同意会阻断后续；演练用 phantom patient 已在准备阶段落库
    print("  （演练数据已预置同意记录，跳过 UI 勾选）")

    print("--- 3) sessionService.createSession：POST /sessions ---")
    code, sess = call("/sessions", {
        "patient_id": "DRILL-PATIENT-001",
        "device_id": "fe-walk-device-01",
        "target_organs": ["thyroid"],
    }, token)
    if code not in (200, 201) or not sess.get("session_id"):
        bad(f"建会话失败 http={code} {json.dumps(sess, ensure_ascii=False)[:200]}")
        return 1
    session_id = sess["session_id"]
    ok(f"建会话 {session_id}")

    print("--- 4) 上传 DICOM：POST /imaging/dicom/upload ---")
    code, up = upload_dicom(dcm, session_id, token)
    if code != 200 or not up.get("image_id"):
        bad(f"上传失败 http={code} {json.dumps(up, ensure_ascii=False)[:200]}")
        return 1
    image_id = up["image_id"]
    ok(f"上传成功 {image_id}")

    print("--- 5) diagnosisService.requestDiagnosis：POST /diagnosis/request ---")
    # ★ 入参严格取自 types/api.ts 的 DiagnosisRequest：
    #   gender 是 'M'|'F'（不是 'female'）——之前演练传 'female' 是后端宽容兜住了，
    #   前端不会那么传，这里按前端真实值走。
    payload = {
        "session_id": session_id,
        "image_ids": [image_id],
        "clinical_text": "自造演练数据：右叶低回声结节，边界清，形态规则。",
        "target_organ": "thyroid",
        "response_tier": "standard",
        "patient_context": {"age": 45, "gender": "F", "relevant_history": ""},
    }
    code, req_body = call("/diagnosis/request", payload, token)
    if code not in (200, 201) or not req_body.get("request_id"):
        bad(f"提交诊断失败 http={code} {json.dumps(req_body, ensure_ascii=False)[:250]}")
        return 1
    request_id = req_body["request_id"]
    ok(f"提交诊断 {request_id}（tier={req_body.get('response_tier')}, "
       f"estimated={req_body.get('estimated_time_ms')}ms）")

    print("--- 6) 轮询 /diagnosis/result/{id}（前端 pollInterval=2000）---")
    terminal = {"completed", "failed", "timeout", "partial"}
    status = None
    result = {}
    t0 = time.time()
    for i in range(90):          # 前端 maxAttempts 量级
        time.sleep(2)
        code, result = call(f"/diagnosis/result/{request_id}", token=token, timeout=30)
        if code == 200:
            status = result.get("status")
            if status in terminal:
                break
        if i % 10 == 9:
            print(f"    ... {int(time.time()-t0)}s status={status or code}")
    if status == "completed":
        ok(f"管线完成（{int(time.time()-t0)}s）")
    else:
        bad(f"管线未完成 status={status} code={code}")
        return 1

    print("--- 7) 结果字段是否够前端渲染 ---")
    # WorkspaceConclusionBody 至少要用到这些
    for field in ("diagnosis", "confidence", "requires_manual_review",
                  "evidence", "malignancy_risk", "clinical_actions"):
        if field in result:
            ok(f"结果含 {field}")
        else:
            bad(f"结果缺 {field}（前端会渲染空）")
    ev = result.get("evidence")
    if isinstance(ev, list) and ev:
        ok(f"证据条目 {len(ev)} 条")
    else:
        bad("证据为空（前端证据区会空白）")
    seg = result.get("nodule_segmentation")
    if isinstance(seg, dict) and seg.get("available"):
        ok(f"分割旁路 area_mm2={seg.get('area_mm2')}")
    else:
        print("  [INFO] 分割旁路未产出（默认关闭属预期）")

    print("--- 8) reportService.getReportByRequest：POST /report ---")
    code, rep = call("/report", {"request_id": request_id}, token, timeout=90)
    if code in (200, 201) and rep.get("report_id"):
        ok(f"报告已生成 {rep['report_id']}（status={rep.get('status')}）")
        content = rep.get("content") or {}
        if content.get("nodule_summary"):
            ok(f"报告含结节段：{str(content['nodule_summary'])[:60]}")
    else:
        bad(f"报告生成失败 http={code} {json.dumps(rep, ensure_ascii=False)[:200]}")

    print("--- 9) sessionService.endSession：PATCH /sessions/{id} ---")
    # 前端注释明确：没有 /end 端点，结束会话 = PATCH 带 { status }
    code, endbody = call(f"/sessions/{session_id}", {"status": "completed"},
                         token, method="PATCH")
    if code == 200:
        ok(f"结束会话（PATCH status=completed）→ {endbody.get('status')}")
    else:
        bad(f"结束会话失败 http={code} {json.dumps(endbody, ensure_ascii=False)[:200]}")

    print("--- 10) sessionService.listSessions：GET /sessions ---")
    code, lst = call("/sessions?limit=5", token=token)
    if code == 200:
        items = lst.get("items") or lst.get("sessions") or []
        ok(f"会话列表可读（{len(items)} 条）")
    else:
        bad(f"会话列表失败 http={code}")

    print()
    print(f"PASS={PASS} FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
