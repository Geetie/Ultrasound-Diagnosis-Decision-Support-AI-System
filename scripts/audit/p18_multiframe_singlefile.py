#!/usr/bin/env python3
"""p18 单文件多帧「择优帧」真机探针（A.51 · B 项第二条路径）。

p17 验的是**多影像**择优；本探针验**一个 DICOM 文件内含多帧**这条路径：
  CAMUS 真实序列 → `--multiframe` 写成 NumberOfFrames=7 的单文件 →
  上传（dicom_images.frame_count=7）→ 判读 → 分割旁路应：
    · 采样 mid / mid±2 共 3 帧（candidates_evaluated=3）
    · 落 frame_index + total_frames=7，择优帧 == 逐帧表 argmax
    · 报告正文出现「第 X/Y 帧（多帧候选择优）」

跑法（服务器）：
    cd ~/uai/src/scripts/audit && ~/uai/venv/bin/python p18_multiframe_singlefile.py
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _audit_common import UAI, check, http, login, pw, summary
from p11_rag_evidence import upload_dicom

FRAME_COUNT = 7


def make_multiframe_dicom(count: int = FRAME_COUNT) -> str:
    """CAMUS 真实帧 → 单文件多帧 DICOM（真实数据制度；不得用合成样本）。"""
    out_dir = "/tmp/p18_multiframe"
    os.makedirs(out_dir, exist_ok=True)
    out = f"{out_dir}/camus_multiframe.dcm"
    if os.path.exists(out):
        os.remove(out)
    subprocess.run(
        [
            os.path.expanduser("~/uai/venv/bin/python"),
            os.path.expanduser("~/uai/src/scripts/camus_frame_to_dicom.py"),
            "--out", out, "--count", str(count),
            "--patient", "P18-MULTIFRAME", "--multiframe",
        ],
        check=True, capture_output=True,
    )
    return out


def main() -> int:
    admin = login("smoke-admin", pw("smoke_admin_pw"))
    doctor = login("smoke-doctor", pw("smoke_pw"))
    dtok, atok = doctor["access_token"], admin["access_token"]

    pid = f"probe-p18-{int(time.time())}"
    http("POST", "/fhir/Patient", token=dtok,
         body={"patient_id": pid, "name": "P18 MULTIFRAME PROBE", "gender": "unknown"})
    http("POST", "/api/v1/consent", token=atok,
         body={"patient_id": pid, "action": "grant", "operator_id": "smoke-admin"})
    code, body = http("POST", "/api/v1/sessions", token=dtok,
                      body={"patient_id": pid, "device_id": "probe-p18", "target_organs": ["cardiac"]})
    sid = body.get("session_id") if isinstance(body, dict) else None
    check("建会话", code == 200 and sid, f"http={code}")

    try:
        path = make_multiframe_dicom()
        check("CAMUS → 单文件 7 帧 DICOM", os.path.exists(path), path)
    except Exception as exc:
        check("CAMUS → 单文件多帧转换", False, f"{type(exc).__name__}: {exc}")
        return summary("A.51 单文件多帧择优")

    code, body = upload_dicom(dtok, sid, path)
    iid = body.get("image_id") if code == 200 else None
    check("上传多帧 DICOM", code == 200 and iid, f"http={code} image_id={iid}")
    if not iid:
        return summary("A.51 单文件多帧择优")

    code, body = http("POST", "/api/v1/diagnosis/request", token=dtok,
                      body={"session_id": sid, "target_organ": "cardiac", "image_ids": [iid],
                            "clinical_text": "单文件多帧探针：心尖四腔，7 帧序列。"})
    rid = body.get("request_id") if isinstance(body, dict) else None
    check("发判读", code == 200 and rid, f"http={code} rid={rid}")

    result = None
    for _ in range(90):
        time.sleep(2)
        code, body = http("GET", f"/api/v1/diagnosis/result/{rid}", token=dtok)
        if code == 200 and isinstance(body, dict) and body.get("status") in (
            "completed", "failed", "needs_review"
        ):
            result = body
            break
    check("判读终态返回", result is not None, f"status={result.get('status') if result else 'timeout'}")

    seg = (result or {}).get("nodule_segmentation") or {}
    check("分割旁路可用", seg.get("available") is True,
          f"available={seg.get('available')} reason={seg.get('reason')}")
    if seg.get("available") is not True:
        return summary("A.51 单文件多帧择优")

    total = seg.get("total_frames")
    frames = seg.get("frames_evaluated") or []
    check(f"⭐ 单文件多帧被识别（total_frames={FRAME_COUNT}）", total == FRAME_COUNT,
          f"total_frames={total} frame_index={seg.get('frame_index')}")
    check("⭐ 中间帧 ±2 三帧采样", seg.get("candidates_evaluated") == 3 and len(frames) == 3,
          f"candidates={seg.get('candidates_evaluated')} frames={[f.get('frame_index') for f in frames]}")
    if frames:
        mid = FRAME_COUNT // 2
        check("采样帧号 = {mid, mid-2, mid+2}（截断在界内）",
              sorted(f.get("frame_index") for f in frames) == sorted({mid, max(0, mid - 2), mid + 2}),
              f"expect mid={mid} got={[f.get('frame_index') for f in frames]}")
        best = max(frames, key=lambda f: f.get("mean_prob") or -1)
        check("⭐ 择优帧 == 逐帧表 argmax",
              seg.get("frame_index") == best.get("frame_index"),
              f"chosen={seg.get('frame_index')} argmax={best.get('frame_index')} "
              f"probs={[round(f.get('mean_prob') or 0, 4) for f in frames]}")
    check("policy=best_mean_prob", seg.get("selection_policy") == "best_mean_prob",
          f"policy={seg.get('selection_policy')}")

    # 报告正文帧归属
    code, rep = http("POST", "/api/v1/report", token=dtok,
                     body={"request_id": rid, "operator_id": "smoke-doctor"})
    content = str(rep.get("content") or "") if code == 200 and isinstance(rep, dict) else ""
    if not content and code == 200 and isinstance(rep, dict) and rep.get("report_id"):
        code2, rep2 = http("GET", f"/api/v1/report/{rep['report_id']}", token=dtok)
        if code2 == 200 and isinstance(rep2, dict):
            content = str(rep2.get("content") or "")
    fi = seg.get("frame_index")
    check("⭐ 报告正文含「第 X/Y 帧（多帧候选择优）」",
          isinstance(fi, int) and f"第 {fi + 1}/{total} 帧" in content and "多帧候选择优" in content,
          f"期望片段=第 {fi + 1}/{total} 帧 · 报告长度={len(content)}")

    out_dir = UAI / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"multiframe_single_{time.strftime('%Y%m%d_%H%M%S')}.json").write_text(
        json.dumps({"session": sid, "request": rid, "segmentation": seg,
                    "report_excerpt": [ln for ln in content.splitlines() if "帧" in ln][:4]},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return summary("A.51 单文件多帧择优")


if __name__ == "__main__":
    sys.exit(main())
