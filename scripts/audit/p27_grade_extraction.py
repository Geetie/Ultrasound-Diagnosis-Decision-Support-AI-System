#!/usr/bin/env python3
"""p27 —— 分级提取**降级率**门禁（真机端到端，退出码可进 CI）。

问的是什么
----------
`grade_unparseable` / `lvef_unparseable` 这类降级**在语义上允许存在**（模型确实可能
给不出可判定分级），但必须满足三条（2026-10-13 分级提取根治）：

1. **可读**：医生可见结论正文是中文说明，**不含**英文技术占位串；
2. **有标记**：结果带 ``needs_review``（转人工判读），不是静默照出；
3. **比例可控**：降级率低于阈值（本探针统计并给退出码）。

为什么单独做门禁
----------------
修复前"降级"是**静默**的：结论区直接显示
``[thyroid] grade_unparseable: LLM output did not contain a valid TI-RADS grade …``，
医生读到的是技术日志。修掉文案只是第一层 —— 第二层是**让比例可观测、可回归**，
否则下次模型/prompt 一漂移又会大面积降级而无人察觉。

用法（服务器，主服务需在跑）：

    ~/uai/venv/bin/python scripts/audit/p27_grade_extraction.py --rounds 8

退出码：0 = 全部判据通过；1 = 有判据不过（降级超阈值 / 出现技术串 / 判读失败）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from _audit_common import BASE, UAI, check, http, login, pw, summary  # noqa: E402
from p11_rag_evidence import make_camus_dicoms, upload_dicom  # noqa: E402

# 医生可见正文里**禁止**出现的英文技术串（零静默纪律）
#
# A.59 扩围（真机 p28 实测到的形态）：除分级降级串外，**器官代码方括号**、心脏的
# **英文键值对**与三处**降级串**同样不许进正文 —— 实测正文曾是
# `[cardiac] LVEF=unknown, severity=normal — …`。
_TECHNICAL_MARKERS = (
    "unparseable",
    "LLM output did not",
    "manual review required",
    "[thyroid]",
    "[liver]",
    "[cardiac]",
    "[pipeline]",
    "LVEF=",
    "severity=",
    "evidence_insufficient",
    "llm_unavailable",
    "parse_failed",
    "specialist_unavailable",
)

#: 器官代码 → 中文名（结论正文应出现中文部位名；与 `app.models.schemas.ORGAN_CN_LABELS` 同源）
_ORGAN_CN = {"thyroid": "甲状腺", "liver": "肝脏", "cardiac": "心脏"}

#: 各器官的临床信息（喂给探针的 `clinical_text`；心脏腿不能喂甲状腺描述）
_CLINICAL_TEXT = {
    "thyroid": "p27 分级提取降级率：甲状腺结节，实性低回声，边界尚清。",
    "liver": "p27 分级提取降级率：肝内低回声灶，边界清晰。",
    "cardiac": "p27 分级提取降级率：心慌气短，超声评估左室收缩功能。",
}
# 判定"本轮发生了分级降级"的正文标记（可读中文降级文案，见三专科 _parse_response）
_DEGRADED_MARKERS = ("未能解析出",)


_SKIP_KEYS = (
    "trace",
    "evidence",
    "reasoning",
    "chunk",
    "preview",
    "timing",
    "url",
    "hash",
    "id",
    "version",
    "status",
    "organ",
    "created_at",
    "updated_at",
)


def _has_cjk(text: str) -> bool:
    """结论文本（含降级说明）必含中文 —— 用它把 `req_…`/`sess_…` 这类标识符挡掉。"""
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _pick_conclusion(result: dict) -> str:
    """取医生可见的结论正文。

    结果结构随版本演进（`diagnosis.conclusion` / 顶层 `conclusion` / 融合后的
    逐器官结论都可能），因此**按内容递归找**：跳过 ``reasoning_trace`` /
    ``evidence`` 这类技术字段（那里本来就带 ``grading_unparseable`` 标记，误取
    会让"降级判定"永远命中），取第一个像结论的长文本。
    """
    found: list[str] = []

    def walk(node: object, key: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k))
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str):
            if any(s in key.lower() for s in _SKIP_KEYS):
                return
            text = node.strip()
            if len(text) >= 20 and _has_cjk(text):
                found.append(text)

    walk(result)
    return found[0] if found else ""


async def _one_round(idx: int, dtok: str, organ: str, patient: str) -> dict:
    """一轮真实判读：建会话 → 上传真实 CAMUS 帧 → 判读 → 轮询终态。

    患者必须是**已签署知情同意**的（后端 E1008/403 会挡未同意的患者 —— 这是
    会诊护栏，不是缺陷；探针不该绕过它，只该挑一个已同意的患者）。
    """
    import httpx

    out: dict = {"round": idx, "ok": False, "degraded": False, "conclusion": "", "wall_s": None}
    pid = patient
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.post(
            f"{BASE}/api/v1/sessions",
            json={"patient_id": pid, "device_id": "probe-p27", "target_organs": [organ]},
            headers={"Authorization": f"Bearer {dtok}"},
        )
        sid = r.json().get("session_id", "") if r.status_code == 200 else ""
    if not sid:
        out["error"] = f"建会话失败 http={r.status_code}"
        return out

    img_ids: list[str] = []
    try:
        for p in make_camus_dicoms(3):  # 真实 CAMUS 帧（禁合成样本）
            code, body = upload_dicom(dtok, sid, p)
            iid = body.get("image_id") if code == 200 else None
            if iid:
                img_ids.append(iid)
    except Exception as exc:
        out["error"] = f"上传异常 {type(exc).__name__}: {exc}"
        return out
    if len(img_ids) < 2:
        out["error"] = f"上传不足 n={len(img_ids)}"
        return out

    t0 = time.monotonic()
    code, body = http(
        "POST",
        "/api/v1/diagnosis/request",
        token=dtok,
        body={
            "session_id": sid,
            "target_organ": organ,
            "image_ids": img_ids,
            "clinical_text": _CLINICAL_TEXT.get(organ, _CLINICAL_TEXT["thyroid"]),
        },
        timeout=60.0,
    )
    rid = body.get("request_id") if isinstance(body, dict) else None
    if code != 200 or not rid:
        out["error"] = f"发起判读失败 http={code}"
        return out

    result: dict | None = None
    while time.monotonic() - t0 < 300:
        await asyncio.sleep(2.0)
        rc, rb = http("GET", f"/api/v1/diagnosis/result/{rid}", token=dtok, timeout=30.0)
        if rc == 200 and isinstance(rb, dict) and rb.get("status") in (
            "completed",
            "failed",
            "needs_review",
            "cancelled",
        ):
            result = rb
            break
    out["wall_s"] = round(time.monotonic() - t0, 1)
    if result is None:
        out["error"] = "判读未到达终态（>300s）"
        return out

    out["status"] = result.get("status")
    # 结构键留档：结果模型随版本演进，探针报错时要能一眼看出该去哪取结论
    out["result_keys"] = sorted(result.keys())[:24]
    diag = result.get("diagnosis")
    if isinstance(diag, dict):
        out["diagnosis_keys"] = sorted(diag.keys())[:24]
    text = _pick_conclusion(result)
    out["conclusion"] = text[:1500]
    out["needs_review"] = bool(result.get("needs_review"))
    out["ok"] = result.get("status") in ("completed", "needs_review") and bool(text)
    out["degraded"] = any(m in text for m in _DEGRADED_MARKERS)
    out["technical_leak"] = [m for m in _TECHNICAL_MARKERS if m in text]
    # A.59：结论正文应给**中文部位名**（器官代码不得进正文）
    out["organ_label_ok"] = _ORGAN_CN.get(organ, organ) in text
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--organ", default="thyroid", choices=["thyroid", "liver", "cardiac"])
    ap.add_argument("--max-degrade", type=int, default=1, help="允许的最大降级轮数")
    ap.add_argument(
        "--patient",
        default="DRILL-PATIENT-001",
        help="必须已签署知情同意的患者（未同意会被 E1008/403 挡，属会诊护栏）",
    )
    args = ap.parse_args()

    print("== p27 分级提取降级率门禁（真机端到端）==", flush=True)
    dtok = login("smoke-doctor", pw("smoke_pw"))["access_token"]

    rounds: list[dict] = []
    for i in range(1, args.rounds + 1):
        r = await _one_round(i, dtok, args.organ, args.patient)
        rounds.append(r)
        flag = "DEGRADED" if r.get("degraded") else ("OK" if r.get("ok") else "ERR")
        print(
            f"  [{i}/{args.rounds}] {flag} wall={r.get('wall_s')}s "
            f"status={r.get('status')} err={r.get('error', '')} "
            f"conclusion={r.get('conclusion', '')[:60]!r}",
            flush=True,
        )

    ok_rounds = [r for r in rounds if r.get("ok")]
    degraded = [r for r in rounds if r.get("degraded")]
    leaked = [r for r in rounds if r.get("technical_leak")]

    check("P27-1 每轮判读均到达终态并有结论文本",
          len(ok_rounds) == args.rounds,
          f"ok={len(ok_rounds)}/{args.rounds}")
    check("P27-2 结论正文不含英文技术占位串（零静默/可读性）",
          not leaked,
          f"leaked={[r['round'] for r in leaked]}")
    check("P27-5 结论正文含中文部位名（器官代码不进正文；A.59）",
          all(r.get("organ_label_ok") for r in ok_rounds),
          f"organ={args.organ} label={_ORGAN_CN.get(args.organ, args.organ)} "
          f"miss={[r['round'] for r in ok_rounds if not r.get('organ_label_ok')]}")
    check(f"P27-3 降级轮数 ≤ {args.max_degrade}（分级提取稳定度）",
          len(degraded) <= args.max_degrade,
          f"degraded={len(degraded)}/{args.rounds} rounds={[r['round'] for r in degraded]}")
    if degraded:
        check("P27-4 降级轮均带 needs_review（转人工，非静默）",
              all(r.get("needs_review") for r in degraded),
              f"flags={[r.get('needs_review') for r in degraded]}")

    report = {
        "organ": args.organ,
        "rounds": args.rounds,
        "degraded_rounds": len(degraded),
        "degrade_rate": round(len(degraded) / args.rounds, 4) if args.rounds else 0.0,
        "wall_s_median": sorted(r["wall_s"] for r in rounds if r.get("wall_s"))[len(rounds) // 2]
        if any(r.get("wall_s") for r in rounds)
        else None,
        "detail": rounds,
    }
    out_path = UAI / "reports" / "p27_grade_extraction.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n   报告：{out_path}")

    return summary("p27 分级提取降级率门禁")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
