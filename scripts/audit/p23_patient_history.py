#!/usr/bin/env python3
"""p23 — 患者历史检查端点真机验证（第二梯队③ 历史对比阅片，A.54）。

判据（全部走生产 HTTP，不用单元测试替身）：
  H1  端点可用且**返回结构完整**（字段集与 schema 一致）
  H2  patient_exists 语义：真实患者 True；不存在的 patient_id → False 且 total=0
  H3  items 按 created_at **严格倒序**
  H4  条目必填字段非空；结论/分割/影像计数按实际数据出现（不虚构）
  H5  organ 过滤自洽：organ=X → 所有 item.organ==X；给一个历史里没有的器官 → total=0
  H6  comparable_organs ⊆ organs，且每个都真有 ≥2 条（与 items 现算对照）
  H7  limit：limit=1 → total==1；limit=0 / limit=101 → 422
  H8  无 token → 401
  H9  **患者隔离**：另一位患者的 session 绝不出现在本患者结果里
  H10 **报告关联真实**：条目里的 report_id 能用报告端点取回，且 session/organ 一致

用法（服务器）：~/uai/venv/bin/python p23_patient_history.py [--dicom <真实 DICOM>]
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
import time

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _audit_common import BASE, check, login, pw, summary

HISTORY_FIELDS = {
    "patient_id",
    "patient_exists",
    "total",
    "organs",
    "comparable_organs",
    "items",
}
ITEM_FIELDS = {
    "session_id",
    "created_at",
    "status",
    "target_organs",
    "organ",
    "knowledge_base_version",
    "request_id",
    "confidence",
    "conclusion",
    "report_id",
    "report_status",
    "report_signed",
    "signed_at",
    "image_count",
    "first_image_id",
    "segmentation",
}


async def _poll(c: httpx.AsyncClient, tok: dict, rid: str, deadline_s: float = 120.0) -> str:
    t0 = time.monotonic()
    last = "unknown"
    while time.monotonic() - t0 < deadline_s:
        r = await c.get(f"{BASE}/api/v1/diagnosis/result/{rid}", headers=tok, timeout=30.0)
        if r.status_code == 200:
            last = str(r.json().get("status"))
            if last in ("completed", "failed", "timeout", "needs_review", "cancelled"):
                return last
        await asyncio.sleep(2.0)
    return f"poll_timeout({last})"


async def _make_patient_with_two_exams(c, tok, atok, dicom: pathlib.Path, organ: str = "thyroid"):
    """造一位患者 + 2 次同器官检查（各上传 1 张真实影像并发起判读）。"""
    pid = f"probe-p23-{int(time.time())}"
    await c.post(
        f"{BASE}/fhir/Patient",
        json={"patient_id": pid, "name": "P23 历史对比", "gender": "female"},
        headers=tok,
        timeout=30.0,
    )
    await c.post(
        f"{BASE}/api/v1/consent",
        json={"patient_id": pid, "action": "grant", "operator_id": "smoke-admin"},
        headers=atok,
        timeout=30.0,
    )
    raw = dicom.read_bytes()
    finals = []
    for i in range(2):
        r = await c.post(
            f"{BASE}/api/v1/sessions",
            json={"patient_id": pid, "device_id": f"probe-p23-{i}", "target_organs": [organ]},
            headers=tok,
            timeout=30.0,
        )
        sid = r.json()["session_id"]
        up = await c.post(
            f"{BASE}/api/v1/imaging/dicom/upload",
            files={"file": (dicom.name, raw, "application/dicom")},
            data={"session_id": sid, "priority": "1"},
            headers=tok,
            timeout=120.0,
        )
        image_id = up.json().get("image_id") if up.status_code == 200 else None
        d = await c.post(
            f"{BASE}/api/v1/diagnosis/request",
            json={
                "session_id": sid,
                "target_organ": organ,
                "image_ids": [image_id] if image_id else [],
                "clinical_text": f"第 {i + 1} 次复查：甲状腺结节随访。",
                "patient_context": {"age": 45, "gender": "female", "conditions": []},
            },
            headers=tok,
            timeout=60.0,
        )
        rid = d.json().get("request_id") if d.status_code in (200, 202) else None
        finals.append(await _poll(c, tok, rid) if rid else f"submit_http_{d.status_code}")
    return pid, finals


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dicom", default="~/uai/data/probe/a51_ui_mf7b.dcm")
    args = ap.parse_args()
    dicom = pathlib.Path(args.dicom).expanduser()

    doctor = login("smoke-doctor", pw("smoke_pw"))
    admin = login("smoke-admin", pw("smoke_admin_pw"))
    tok = {"Authorization": f"Bearer {doctor['access_token']}"}
    atok = {"Authorization": f"Bearer {admin['access_token']}"}

    async with httpx.AsyncClient(timeout=120.0) as c:
        # ---- 找一位「已有 ≥2 次同器官检查」的患者；没有就现造 ----
        pid, comparable, finals = None, [], []
        r = await c.get(f"{BASE}/api/v1/patients?q=probe-&limit=50", headers=tok, timeout=30.0)
        for item in (r.json().get("items") or []) if r.status_code == 200 else []:
            cand = item["patient_id"]
            h = await c.get(f"{BASE}/api/v1/patients/{cand}/sessions?limit=20", headers=tok, timeout=30.0)
            if h.status_code == 200 and h.json().get("comparable_organs"):
                pid = cand
                comparable = h.json()["comparable_organs"]
                break

        if pid is None:
            if not dicom.exists():
                check("P0 需要真实 DICOM 样本以造两次检查", False, f"缺 {dicom}")
                return summary("p23 患者历史检查")
            pid, finals = await _make_patient_with_two_exams(c, tok, atok, dicom)
            check("P0 现造患者 + 两次同器官检查（各发起真实判读）", True, f"{pid} 终态={finals}")
        else:
            check("P0 复用已有「≥2 次同器官检查」的患者", True, f"{pid} comparable={comparable}")

        # ---- H1 结构完整 ----
        r = await c.get(f"{BASE}/api/v1/patients/{pid}/sessions?limit=20", headers=tok, timeout=30.0)
        check("H1 端点可用", r.status_code == 200, f"http={r.status_code}")
        if r.status_code != 200:
            return summary("p23 患者历史检查")
        data = r.json()
        check("H1b 顶层字段集与 schema 一致", set(data) == HISTORY_FIELDS, f"keys={sorted(data)}")
        bad_item = [it for it in data["items"] if set(it) != ITEM_FIELDS]
        check("H1c 每个条目字段集一致", not bad_item, f"异常条目={len(bad_item)}")

        # ---- H2 patient_exists 语义 ----
        ghost = await c.get(f"{BASE}/api/v1/patients/no-such-p23-ghost/sessions", headers=tok, timeout=30.0)
        g = ghost.json() if ghost.status_code == 200 else {}
        check(
            "H2 patient_exists 语义（真实 True / 不存在 False 且 total=0）",
            data.get("patient_exists") is True
            and g.get("patient_exists") is False
            and g.get("total") == 0,
            f"real={data.get('patient_exists')} ghost={g.get('patient_exists')}/{g.get('total')}",
        )

        # ---- H3 倒序 ----
        times = [it["created_at"] for it in data["items"]]
        check("H3 items 按 created_at 严格倒序", times == sorted(times, reverse=True), f"n={len(times)}")

        # ---- H4 必填字段 ----
        missing = [
            it["session_id"]
            for it in data["items"]
            if not it.get("session_id") or not it.get("created_at") or not it.get("status")
        ]
        with_result = [it for it in data["items"] if it.get("request_id")]
        check(
            "H4 必填字段非空；有判读的条目带结论/器官",
            not missing and all(it.get("organ") for it in with_result),
            f"缺字段={missing} 有判读={len(with_result)}/{len(data['items'])}",
        )
        segs = [it for it in data["items"] if it.get("segmentation")]
        check(
            "H4b 分割摘要按实际数据出现（不虚构：无分割则为 null）",
            all(isinstance(it["segmentation"], dict) for it in segs),
            f"带分割={len(segs)}/{len(data['items'])}",
        )

        # ---- H5 organ 过滤自洽 ----
        organs_now = data.get("organs") or []
        if organs_now:
            target = organs_now[0]
            f = await c.get(
                f"{BASE}/api/v1/patients/{pid}/sessions?organ={target}", headers=tok, timeout=30.0
            )
            ok = f.status_code == 200 and all(it["organ"] == target for it in f.json()["items"])
            check("H5 organ 过滤只保留该器官条目", ok, f"organ={target} n={len(f.json().get('items', [])) if f.status_code == 200 else '-'}")
        absent = await c.get(
            f"{BASE}/api/v1/patients/{pid}/sessions?organ=no_such_organ_xyz", headers=tok, timeout=30.0
        )
        check(
            "H5b 历史里没有的器官 → total=0（不返回别器官）",
            absent.status_code == 200 and absent.json()["total"] == 0,
            f"http={absent.status_code}",
        )

        # ---- H6 comparable_organs 自洽 ----
        counts: dict[str, int] = {}
        for it in data["items"]:
            for o in ([it["organ"]] if it.get("organ") else []) + list(it.get("target_organs") or []):
                counts[o] = counts.get(o, 0) + 1
        expect_comparable = sorted([o for o, n in counts.items() if n >= 2])
        check(
            "H6 comparable_organs = 出现 ≥2 次的器官（与 items 现算一致）",
            sorted(data.get("comparable_organs") or []) == expect_comparable
            and set(data.get("comparable_organs") or []) <= set(organs_now),
            f"接口={sorted(data.get('comparable_organs') or [])} 现算={expect_comparable}",
        )

        # ---- H7 limit ----
        one = await c.get(f"{BASE}/api/v1/patients/{pid}/sessions?limit=1", headers=tok, timeout=30.0)
        bad0 = await c.get(f"{BASE}/api/v1/patients/{pid}/sessions?limit=0", headers=tok, timeout=30.0)
        bad101 = await c.get(f"{BASE}/api/v1/patients/{pid}/sessions?limit=101", headers=tok, timeout=30.0)
        check(
            "H7 limit 生效且有上下界校验",
            one.status_code == 200
            and one.json()["total"] == 1
            and bad0.status_code == 422
            and bad101.status_code == 422,
            f"limit1={one.json().get('total')} limit0={bad0.status_code} limit101={bad101.status_code}",
        )

        # ---- H8 未授权 ----
        noauth = await c.get(f"{BASE}/api/v1/patients/{pid}/sessions", timeout=30.0)
        check("H8 无 token → 401", noauth.status_code == 401, f"http={noauth.status_code}")

        # ---- H9 患者隔离 ----
        other = None
        all_patients = (await c.get(f"{BASE}/api/v1/patients?limit=50", headers=tok)).json().get("items") or []
        for item in all_patients:
            if item["patient_id"] != pid:
                other = item["patient_id"]
                break
        if other:
            oh = await c.get(f"{BASE}/api/v1/patients/{other}/sessions?limit=20", headers=tok, timeout=30.0)
            other_ids = {it["session_id"] for it in (oh.json().get("items") or [])}
            mine = {it["session_id"] for it in data["items"]}
            check(
                "H9 患者隔离：另一位患者的检查不出现在本患者结果里",
                not (other_ids & mine),
                f"other={other} 交集={sorted(other_ids & mine)}",
            )
        else:
            check("H9 患者隔离（需第二位患者）", False, "库里只有一位患者，无法验证隔离")

        # ---- H10 报告关联真实（含「结论优先取报告」）----
        # 强检查：给一次已完成判读**造报告 + 补录唯一标记结论**，历史条目必须采用它。
        # 这样才能证明关联的是"这份报告"、且优先于管线原始结论（而非恰好文本相同）。
        with_result_items = [it for it in data["items"] if it.get("request_id")]
        if not with_result_items:
            check("H10 报告关联真检查（本患者无已完成判读，无法造报告）", False, "no completed exam")
        else:
            exam = [it for it in with_result_items if not it.get("report_id")]
            exam = exam[0] if exam else with_result_items[0]
            rep_id = exam.get("report_id")
            if not rep_id:
                dr = await c.post(
                    f"{BASE}/api/v1/report",
                    json={"request_id": exam["request_id"]},
                    headers=tok,
                    timeout=60.0,
                )
                rep_id = dr.json().get("report_id") if dr.status_code in (200, 201) else None
                check("H10a 造报告草稿", bool(rep_id), f"http={dr.status_code} id={rep_id}")
            marker = f"P23 复核结论标记 {int(time.time())}"
            if rep_id:
                upd = await c.patch(
                    f"{BASE}/api/v1/report/{rep_id}",
                    json={"sections": {"diagnosis": marker}},
                    headers=tok,
                    timeout=30.0,
                )
                check("H10b 补录可识别结论（仅草稿可改）", upd.status_code == 200, f"http={upd.status_code}")

                again = await c.get(
                    f"{BASE}/api/v1/patients/{pid}/sessions?limit=20", headers=tok, timeout=30.0
                )
                items2 = again.json()["items"]
                it2 = next((i for i in items2 if i["session_id"] == exam["session_id"]), None)
                adopted = bool(it2) and it2.get("conclusion") == marker
                check(
                    "H10c ⭐ 历史条目的结论**采用报告诊断意见**（报告优先于管线原始结论）",
                    adopted,
                    f"report_status={it2.get('report_status') if it2 else '-'} conclusion={(it2 or {}).get('conclusion', '')[:40]}",
                )
                rr = await c.get(f"{BASE}/api/v1/report/{rep_id}", headers=tok, timeout=30.0)
                ok = False
                if rr.status_code == 200:
                    rep = rr.json()
                    ok = rep.get("session_id") == exam["session_id"] and (
                        rep.get("organ") in (None, exam.get("organ"))
                    )
                check(
                    "H10d report_id 可回取且 session/organ 与条目一致",
                    ok,
                    f"report={rep_id} http={rr.status_code}",
                )

    return summary("p23 患者历史检查")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
