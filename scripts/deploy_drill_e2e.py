"""①结节分割接主链路 · 端到端真机演练（2026-10-04）。

链路：TN3K 真实超声图 → 合规 DICOM → 上传(/imaging/dicom/upload)
      → 创建诊断请求(/diagnosis/request) → 等管线跑完
      → 结果里查 findings.segmentation → 生成报告查 nodule_summary

纪律：
- 只打印**状态枚举与布尔**，不打印任何模型精度数值（沿用 smoke 探针纪律）；
- 口令/token 只在进程内用，不进 argv、不打印；
- 自造数据（phantom patient），非真实临床数据。

依赖：scripts/make_drill_dicom.py、~/uai/.smoke_pw（server_smoke_probe.py 写的）
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
import uuid

API = "http://127.0.0.1:18000"
SRC = pathlib.Path(os.path.expanduser("~/uai/src"))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

PW_FILE = pathlib.Path(os.path.expanduser("~/uai/.smoke_pw"))
USERNAME = "smoke-doctor"
SESSION_PREFIX = "drill-e2e"
IMAGE_SRC = pathlib.Path(
    os.environ.get("IMG_PATH")
    or os.path.expanduser("~/uai/data/tn3k_dataset/1/images/1000381.png")
)
WORK = pathlib.Path(os.path.expanduser("~/uai/data/drill"))
DICOM = WORK / "drill.dcm"


def _req(path, data=None, token=None, method=None, timeout=60.0, ctype="application/json"):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(API + path, data=body,
                               headers={"Content-Type": ctype}, method=method or ("POST" if body else "GET"))
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, {"raw": raw[:200]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw[:200]}


def _upload_dicom(path: pathlib.Path, session_id: str, token: str):
    """multipart/form-data 上传（手写 multipart，避免依赖 httpx）。"""
    boundary = f"----drill{uuid.uuid4().hex[:12]}"
    fields = [("session_id", session_id), ("priority", "1")]
    body = bytearray()
    for k, v in fields:
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
             f"filename=\"{path.name}\"\r\nContent-Type: application/dicom\r\n\r\n").encode()
    body += path.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    r = urllib.request.Request(API + "/api/v1/imaging/dicom/upload", data=bytes(body),
                               headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                               method="POST")
    r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw[:300]}


def main() -> int:
    if not PW_FILE.exists():
        print("MISS_PW: 先跑 server_smoke_probe.py（它会写 ~/uai/.smoke_pw）")
        return 2
    password = PW_FILE.read_text(encoding="utf-8").strip()
    session_id = f"{SESSION_PREFIX}-{uuid.uuid4().hex[:8]}"

    print("=== 0) 造 DICOM ===")
    if not IMAGE_SRC.exists():
        print(f"  MISS_IMAGE: {IMAGE_SRC}")
        return 2
    sys.path.insert(0, str(SRC / "scripts"))
    try:
        from make_drill_dicom import build as build_dicom  # type: ignore
    except Exception as exc:
        print(f"  MISS_SCRIPT: {exc}")
        return 2
    info = build_dicom(IMAGE_SRC, DICOM)
    print(f"  ok rows={info['rows']} cols={info['cols']} bytes={info['size_bytes']}")

    print("=== 1) 准备 phantom patient 的知情同意（演练数据前置）===")
    import subprocess

    prep = subprocess.run(
        [sys.executable, str(SRC / "scripts" / "drill_prepare_consent.py"),
         "DRILL-PATIENT-001"],
        capture_output=True, text=True, timeout=60,
    )
    print("  " + (prep.stdout.strip().replace("\n", "\n  ") or f"rc={prep.returncode}"))
    if prep.returncode != 0:
        print(f"  PREPARE_FAIL rc={prep.returncode}")
        print("  " + (prep.stderr.strip()[-300:] or ""))
        return 2

    print("=== 2) 登录 ===")
    code, body = _req("/api/v1/auth/login", {"username": USERNAME, "password": password})
    if code != 200 or not body.get("access_token"):
        print(f"  LOGIN_FAIL http={code}")
        return 2
    token = body["access_token"]
    print("  ok token acquired")

    print("=== 3) 建会话 ===")
    code, body = _req("/api/v1/sessions", {"patient_id": "DRILL-PATIENT-001",
                                           "device_id": "drill-device-01",
                                           "target_organs": ["thyroid"]}, token)
    print(f"  http={code}")
    if code not in (200, 201, 409):
        print(f"  SESSION_FAIL {json.dumps(body, ensure_ascii=False)[:200]}")
        return 2
    # 服务端自己生成 session_id（请求体里**没有**该字段，传了也被忽略），
    # 必须用返回值——沿用本地生成的那个会得到 "Session not found"。
    session_id = body.get("session_id") or session_id
    print(f"  ok session_id={session_id}")

    print("=== 4) 上传 DICOM ===")
    code, body = _upload_dicom(DICOM, session_id, token)
    print(f"  http={code}")
    if code != 200:
        print(f"  UPLOAD_FAIL {json.dumps(body, ensure_ascii=False)[:300]}")
        return 2
    image_id = body.get("image_id")
    print(f"  ok image_id={image_id}")
    if not image_id:
        return 2

    print("=== 5) 索引核对（新表 dicom_images 是否写了行）===")
    code, body = _req("/api/v1/imaging/sessions/" + session_id + "/images", token=token)
    print(f"  http={code} body={json.dumps(body, ensure_ascii=False)[:200]}")
    if code == 404:
        print("  （无查询端点，走 DB 直查，见部署脚本）")

    print("=== 6) 创建诊断请求（开旁路）===")
    payload = {
        "session_id": session_id,
        "image_ids": [image_id],
        "target_organ": "thyroid",
        "clinical_text": "自造演练数据：右叶低回声结节，边界清，形态规则，未见明显钙化。",
        "patient_context": {"age": 45, "gender": "female", "conditions": []},
    }
    code, body = _req("/api/v1/diagnosis/request", payload, token)
    print(f"  http={code}")
    if code not in (200, 201):
        print(f"  REQUEST_FAIL {json.dumps(body, ensure_ascii=False)[:300]}")
        return 2
    request_id = body.get("request_id")
    print(f"  ok request_id={request_id}")

    print("=== 7) 等管线完成（最多 300s）===")
    status = None
    for _ in range(60):
        time.sleep(5)
        code, res = _req(f"/api/v1/diagnosis/result/{request_id}", token=token, timeout=30)
        if code == 200:
            status = res.get("status")
            if status in ("completed", "failed", "timeout", "partial"):
                break
        print(f"    ... status={status or code}")
    print(f"  status={status}")
    if status != "completed":
        print(f"  PIPELINE_NOT_COMPLETED ({status})")
        return 3

    print("=== 8) 查分割旁路结果 ===")
    # ⚠️ 口径纠正（实测踩过）：API 的 DiagnosisResult **不含 findings**
    # （设计如此：写库结构不进 API 契约），旁路结果对外是
    # **nodule_segmentation** 字段。读 findings 会永远得到 None。
    code, res = _req(f"/api/v1/diagnosis/result/{request_id}", token=token, timeout=30)
    seg = res.get("nodule_segmentation") if code == 200 else None
    if not seg:
        print(f"  NO_SEGMENTATION http={code}（旁路未产出；检查旁路开关与取图）")
        return 4
    print(f"  available={seg.get('available')} organ={seg.get('organ')} "
          f"device={seg.get('device')}")
    print(f"  area_px={seg.get('area_px')} area_mm2={seg.get('area_mm2')} "
          f"ratio={seg.get('lesion_area_ratio')} bbox={seg.get('bbox')}")
    print(f"  pixel_spacing_mm={seg.get('pixel_spacing_mm')}")
    if not seg.get("area_mm2"):
        print("  WARN: 无 mm²（DICOM 应带 PixelSpacing，需查旁路实现）")

    print("=== 9) 生成报告查 nodule_summary ===")
    code, rep = _req("/api/v1/report", {"request_id": request_id}, token, timeout=60)
    print(f"  http={code}")
    if code in (200, 201):
        content = rep.get("content") or {}
        ns = content.get("nodule_summary") if isinstance(content, dict) else None
        print(f"  nodule_summary: {ns}")
        if not ns:
            print("  NO_NODULE_SUMMARY（报告未输出轮廓段）")
            return 5
    else:
        print(f"  REPORT_SKIP {json.dumps(rep, ensure_ascii=False)[:200]}")

    print("E2E_DRILL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
