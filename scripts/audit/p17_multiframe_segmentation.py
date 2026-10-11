#!/usr/bin/env python3
"""p17 多帧分割帧级归属真机探针（A.51 · B 项）。

缺口：分割旁路此前固定 `rows[0]` + 中间帧，**无择优语义**，报告/前端也没有帧归属。
核实：
  1) 三张 CAMUS 真实帧上传 → 判读后 `nodule_segmentation` 必须带
     `selection_policy/candidates_evaluated/frames_evaluated`，且择优帧
     == 逐帧表里 mean_prob 最大那条（帧号与 image_id 都要对上）；
  2) 评估帧数 ≤3（控推理成本）；
  3) 报告正文出现「第 X/Y 帧（多帧候选择优）」。

跑法（服务器）：
    cd ~/uai/src/scripts/audit && ~/uai/venv/bin/python p17_multiframe_segmentation.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _audit_common import UAI, check, http, login, pw, save_ctx, summary
from p11_rag_evidence import make_camus_dicoms, upload_dicom


def main() -> int:
    admin = login("smoke-admin", pw("smoke_admin_pw"))
    doctor = login("smoke-doctor", pw("smoke_pw"))
    dtok, atok = doctor["access_token"], admin["access_token"]

    pid = f"probe-p17-{int(time.time())}"
    code, _ = http("POST", "/fhir/Patient", token=dtok,
                   body={"patient_id": pid, "name": "P17 MULTIFRAME PROBE", "gender": "unknown"})
    check("创建探针患者", code in (200, 201), f"http={code}")
    code, _ = http("POST", "/api/v1/consent", token=atok,
                   body={"patient_id": pid, "action": "grant", "operator_id": "smoke-admin"})
    check("grant 同意", code == 200, f"http={code}")
    code, body = http("POST", "/api/v1/sessions", token=dtok,
                      body={"patient_id": pid, "device_id": "probe-p17", "target_organs": ["cardiac"]})
    sid = body.get("session_id") if isinstance(body, dict) else None
    check("建会话", code == 200 and sid, f"http={code} sid={sid}")

    img_ids: list[str] = []
    try:
        for p in make_camus_dicoms(3):  # 真实 CAMUS 帧（用户制度：不得用合成样本）
            code, body = upload_dicom(dtok, sid, p)
            iid = body.get("image_id") if code == 200 else None
            if iid:
                img_ids.append(iid)
            check(f"上传 {os.path.basename(p)}", bool(iid), f"http={code}")
    except Exception as exc:
        check("CAMUS 帧转换/上传", False, f"{type(exc).__name__}: {exc}")

    if len(img_ids) < 2:
        return summary("A.51 多帧帧级归属")

    code, body = http("POST", "/api/v1/diagnosis/request", token=dtok,
                      body={"session_id": sid, "target_organ": "cardiac", "image_ids": img_ids,
                            "clinical_text": "多帧帧级归属探针：心尖四腔，室间隔运动减弱。"})
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
    check("分割旁路可用（模型+影像就绪）", seg.get("available") is True,
          f"available={seg.get('available')} reason={seg.get('reason')}")
    if seg.get("available") is not True:
        return summary("A.51 多帧帧级归属")

    frames = seg.get("frames_evaluated") or []
    check("⭐ 择优语义：policy=best_mean_prob", seg.get("selection_policy") == "best_mean_prob",
          f"policy={seg.get('selection_policy')}")
    check("评估帧数 ≤3 且与逐帧表一致",
          isinstance(seg.get("candidates_evaluated"), int)
          and seg["candidates_evaluated"] == len(frames) and len(frames) <= 3,
          f"candidates={seg.get('candidates_evaluated')} len(frames)={len(frames)}")

    probs = [f.get("mean_prob") for f in frames if isinstance(f.get("mean_prob"), int | float)]
    if probs:
        best = max(frames, key=lambda f: f.get("mean_prob") or -1)
        check("⭐ 择优帧 == 逐帧表最大值（帧号一致）",
              seg.get("frame_index") == best.get("frame_index"),
              f"chosen={seg.get('frame_index')} argmax={best.get('frame_index')} "
              f"probs={[round(p, 4) for p in probs]}")
        check("择优帧的 image_id 与逐帧表一致",
              seg.get("image_id") == best.get("image_id"),
              f"chosen={seg.get('image_id')} argmax={best.get('image_id')}")
    check("帧归属字段齐全（frame_index/total_frames）",
          isinstance(seg.get("frame_index"), int) and isinstance(seg.get("total_frames"), int),
          f"frame={seg.get('frame_index')}/{seg.get('total_frames')}")

    # 报告正文帧号呈现
    code, rep = http("POST", "/api/v1/report", token=dtok,
                     body={"request_id": rid, "operator_id": "smoke-doctor"})
    content = ""
    if code == 200 and isinstance(rep, dict):
        content = str(rep.get("content") or "")
        rid_rep = rep.get("report_id")
        if not content and rid_rep:
            code2, rep2 = http("GET", f"/api/v1/report/{rid_rep}", token=dtok)
            if code2 == 200 and isinstance(rep2, dict):
                content = str(rep2.get("content") or "")
    total_frames = seg.get("total_frames") or 1
    if total_frames > 1:
        check("⭐ 报告正文含「第 X/Y 帧」帧归属",
              f"第 {seg.get('frame_index', 0) + 1}/{total_frames} 帧" in content,
              f"报告长度={len(content)}")
    else:
        check("报告：单帧影像不写帧号（避免噪声）", "帧（" not in content, f"报告长度={len(content)}")

    out_dir = UAI / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"multiframe_seg_{time.strftime('%Y%m%d_%H%M%S')}.json").write_text(
        json.dumps({"session": sid, "request": rid, "segmentation": seg}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    save_ctx(p17_session=sid or "", p17_request=rid or "")
    return summary("A.51 多帧帧级归属")


if __name__ == "__main__":
    sys.exit(main())
