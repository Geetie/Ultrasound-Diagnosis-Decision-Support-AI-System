"""A.85：**医生视角全流程走查**（公开接口驱动，真实影像）。

目的不是"证明系统能跑"，而是回答：**医生从进站到签发，每一步看到什么、能不能走通、哪里会卡**。

走的路径（超声判读的典型一次）：
```
登录 → 工作列表 → 新建检查会话 → 上传本次影像 → 提交判读 → 等结果
     → 看结论（分级/证据/是否需要复核/相似病例）→ 生成报告草稿 → 签发 → 提交反馈
```

分支（每条都要有明确结果，不能"大概没问题"）：
* **B1 跨会话影像**：把别的会话的影像塞进本次请求 → 应被 400 拒绝（A.78 加的闸）；
* **B2 只有文字没有影像**：医生只写描述 → 观察是否受理、结论是否可用。

用法（服务器）：
    ~/uai/venv/bin/python scripts/audit/run_with_env.py scripts/audit/p60_doctor_journey.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

BASE = "http://127.0.0.1:18000"
PW_FILE = pathlib.Path.home() / "uai" / ".smoke_pw"
USERNAME = "smoke-doctor"


def _call(method: str, path: str, token: str | None = None, payload: dict | None = None,
          timeout: float = 60.0) -> tuple[int, dict | str]:
    """最小 HTTP 辅助（不引额外依赖）。返回 (status, json 或 文本)。"""
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body


def _upload(path: str, session_id: str, content: bytes, token: str) -> tuple[int, dict | str]:
    """multipart 上传（`/imaging/dicom/upload` 要 file + session_id）。"""
    boundary = "----journey"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"session_id\"\r\n\r\n{session_id}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"priority\"\r\n\r\n1\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"study.dcm\"\r\n"
        f"Content-Type: application/dicom\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")[:300]


def _doctor_view(result: dict) -> dict:
    """把结果接口的响应翻译成"医生在屏幕上看到什么"。纯函数（守卫钉住）。"""
    diagnosis = result.get("diagnosis") or {}
    fr = diagnosis.get("fusion_result") or {}
    text = str(fr.get("content") or "")
    first_line = text.split("\n")[0][:80] if text else ""
    seg = result.get("nodule_segmentation") or {}
    sim = result.get("similar_cases") or {}
    evidence = result.get("evidence") or {}
    return {
        "状态": result.get("status"),
        "一句话结论": first_line or "（空）",
        "置信度": (result.get("quality_control") or {}).get("confidence_score"),
        "需人工复核": result.get("requires_manual_review"),
        "复核原因": result.get("review_reason"),
        "证据条数": len(evidence.get("knowledge_ids") or []),
        "分割": ("可用" if seg.get("available") else f"未做（{seg.get('reason')}）"),
        "相似病例": f"{sim.get('count')} 条" if sim.get("status") == "ok" else f"无（{sim.get('status')}）",
    }


def _as_scanner_file(path: pathlib.Path, patient_id: str) -> bytes:
    """把库里"已去标识化"的影像还原成**扫描件形态**再上传。

    为什么需要（A.85 真机）：直接重传库里的文件会 400——
    `E1012 缺少 DICOM 必填字段：PatientName`。因为上传时**PHI 已被剥离**（这是设计），
    存下来的文件本来就不完整。医生实际面对的是扫描仪原始文件（带 PatientName），
    所以走查要补回这些必填字段，否则测到的不是"上传链路"，而是"脱敏后的副本不能再上传"。
    """
    import io

    import pydicom

    ds = pydicom.dcmread(io.BytesIO(path.read_bytes()))
    ds.PatientName = "JOURNEY^TEST"
    ds.PatientID = patient_id[:64]
    buf = io.BytesIO()
    ds.save_as(buf, write_like_original=False)
    return buf.getvalue()


async def _pick_source() -> dict:
    """选素材：**必须有 granted 同意**且名下有影像。

    真机踩坑（A.85）：首版直接取"最新的一张影像"，结果新建会话返回 **403** ——
    因为该病人同意状态不是 granted（或已撤回）。医生视角同样如此：
    **没有有效同意的病人，系统根本不允许开检查会话**，这是对的，但走查脚本必须挑对素材。
    """
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import ConsentRecord, DicomImage

    async with AsyncSessionLocal() as s:
        granted = (
            await s.execute(
                select(ConsentRecord.patient_id).where(ConsentRecord.status == "granted")
            )
        ).all()
        patients = {str(p) for (p,) in granted}
        rows = (
            await s.execute(
                select(DicomImage.patient_id, DicomImage.session_id, DicomImage.storage_path)
                .order_by(DicomImage.created_at.desc())
            )
        ).all()

    for pid, sid, path in rows:
        if str(pid) in patients:
            return {"patient_id": str(pid), "session_id": str(sid), "path": str(path)}
    raise RuntimeError("没有同时满足「已同意 + 有影像」的病人，无法走查")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("--report", default=str(pathlib.Path.home() / "uai/reports/doctor_journey.json"))
    args = ap.parse_args()

    steps: list[dict] = []
    src = asyncio.run(_pick_source())

    def step(name: str, ok: bool, detail: dict) -> None:
        steps.append({"step": name, "ok": ok, **detail})
        print(f"{'✓' if ok else '✗'} {name}：{json.dumps(detail, ensure_ascii=False)[:180]}")

    # S1 登录
    pw = PW_FILE.read_text(encoding="utf-8").strip()
    code, body = _call("POST", "/api/v1/auth/login", payload={"username": USERNAME, "password": pw})
    token = body.get("access_token") if isinstance(body, dict) else None
    step("S1 登录", code == 200 and bool(token), {"http": code})
    if not token:
        print("[FATAL] 登录失败，无法继续")
        return 2

    # S2 工作列表
    code, body = _call("GET", "/api/v1/sessions?limit=10", token)
    pending = body.get("total_pending") if isinstance(body, dict) else None
    step("S2 工作列表", code == 200, {"http": code, "待处理": pending})

    # S3 新建检查会话（医生开单）
    code, body = _call(
        "POST",
        "/api/v1/sessions",
        token,
        {"patient_id": src["patient_id"], "device_id": "US-JOURNEY-01", "target_organs": ["thyroid"]},
    )
    session_id = body.get("session_id") if isinstance(body, dict) else None
    step("S3 新建会话", code in (200, 201) and bool(session_id), {"http": code, "session": session_id})

    content = b""
    try:
        content = _as_scanner_file(pathlib.Path(src["path"]), src["patient_id"])
    except Exception as exc:  # 走查脚本自身的问题不应伪装成系统问题
        print(f"[WARN] 准备上传素材失败：{type(exc).__name__}: {exc}")

    image_id = None
    if session_id and content:
        # S4 上传本次影像
        code, body = _upload("/api/v1/imaging/dicom/upload", session_id, content, token)
        image_id = body.get("image_id") if isinstance(body, dict) else None
        # 失败时把后端提示带出来（否则 400 只剩一个数字，等于没说）
        detail: dict = {"http": code, "image": image_id}
        if code not in (200, 201):
            detail["提示"] = (
                (body.get("error_message") if isinstance(body, dict) else str(body)) or ""
            )[:160]
        step("S4 上传影像", code in (200, 201) and bool(image_id), detail)

    request_id = None
    if session_id and image_id:
        # S5 提交判读
        code, body = _call(
            "POST",
            "/api/v1/diagnosis/request",
            token,
            {
                "session_id": session_id,
                "image_ids": [image_id],
                "clinical_text": "甲状腺右叶低回声结节，边界清，形态规则，无钙化，需要穿刺吗？",
                "target_organ": "thyroid",
                "patient_context": {"age": 50, "gender": "female", "conditions": []},
            },
        )
        request_id = body.get("request_id") if isinstance(body, dict) else None
        step("S5 提交判读", code in (200, 202) and bool(request_id), {"http": code, "request": request_id})

    result: dict = {}
    if request_id:
        # S6 等结果 + 医生视角
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            time.sleep(5)
            code, body = _call("GET", f"/api/v1/diagnosis/result/{request_id}", token)
            if code == 200 and str(body.get("status")) in (
                "completed", "failed", "cancelled", "needs_review"
            ):
                result = body
                break
        view = _doctor_view(result) if result else {"状态": "timeout"}
        ok = bool(result) and view.get("状态") != "timeout"
        step("S6 看结论", ok, view)

    if request_id and result:
        # S7 出报告（草稿）→ 签发
        # 注意：报告路由前缀是**单数** `/api/v1/report`（首版写成 reports → 404）
        code, body = _call("POST", "/api/v1/report", token, {"request_id": request_id})
        report_id = body.get("report_id") if isinstance(body, dict) else None
        step("S7a 生成报告草稿", code in (200, 201) and bool(report_id), {"http": code, "report": report_id})
        if report_id:
            code2, body2 = _call("POST", f"/api/v1/report/{report_id}/sign", token, {})
            step("S7b 签发报告", code2 == 200, {"http": code2,
                                                "签名校验": (body2 or {}).get("signature_verified")
                                                if isinstance(body2, dict) else None})
            # S8 反馈（医生修正）
            code3, _body3 = _call(
                "POST",
                "/api/v1/feedback",
                token,
                {
                    # 反馈的必填结构（首版按 {"comment": ...} 提交 → 422）：
                    # session_id + type + level + content(field/original/corrected/reason)
                    "request_id": request_id,
                    "session_id": session_id,
                    "feedback_type": "correction",
                    "feedback_level": "L3",
                    "content": {
                        "field": "conclusion",
                        "original_value": "系统原结论",
                        "corrected_value": "医生确认无需修改",
                        "reason": "journey walkthrough：结论与影像所见一致",
                    },
                },
            )
            step("S8 提交反馈", code3 in (200, 201), {"http": code3})

    # B1 跨会话影像（应被拒）
    if session_id:
        code, body = _call(
            "POST",
            "/api/v1/diagnosis/request",
            token,
            {
                "session_id": session_id,
                "image_ids": ["img_20261003_72bd647e"],  # 属于别的会话
                "clinical_text": "跨会话影像测试",
                "target_organ": "thyroid",
                "patient_context": {"age": 50, "gender": "female", "conditions": []},
            },
        )
        step("B1 跨会话影像被拒", code == 400, {"http": code,
                                                "提示": (body or {}).get("error_message")
                                                if isinstance(body, dict) else str(body)[:80]})

    # B2 只有文字（无影像）
    if session_id:
        code, body = _call(
            "POST",
            "/api/v1/diagnosis/request",
            token,
            {
                "session_id": session_id,
                "image_ids": [],
                "clinical_text": "甲状腺结节复查，无明显不适。",
                "target_organ": "thyroid",
                "patient_context": {"age": 50, "gender": "female", "conditions": []},
            },
        )
        step("B2 无影像仅文字", code in (200, 202), {"http": code,
                                                     "request": (body or {}).get("request_id")
                                                     if isinstance(body, dict) else None})

    ok_n = sum(1 for s in steps if s["ok"])
    print(f"\n== 走查结果：{ok_n}/{len(steps)} 步通过 ==")
    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"steps": steps, "ok": ok_n, "total": len(steps)},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
