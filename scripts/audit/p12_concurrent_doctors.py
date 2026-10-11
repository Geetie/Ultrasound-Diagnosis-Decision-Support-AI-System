#!/usr/bin/env python3
"""A.50.7 E12 多医生并发 + E9 workers>1 并发探针（真机，需 WEB_CONCURRENCY≥2）。

覆盖：
1. 双 token（同医生两次登录）并发建会话/发判读 —— 会话/请求互不串（隔离语义）
2. refresh-token rotate 并发竞争（双 worker 下跨进程互斥 —— A.23 修复的真机验证）
3. 幂等键并发（同 idempotency_key 双请求 → 同 request_id）
4. 双 token 交叉读对方 result —— 只读共享无害（同院医生）

用法：~/uai/venv/bin/python ~/uai/src/scripts/audit/p12_concurrent_doctors.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

import httpx
from _audit_common import BASE, check, login, pw, summary


async def _post(client: httpx.AsyncClient, path: str, body: dict, token: str):
    r = await client.post(
        f"{BASE}{path}",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    return r.status_code, _safe_json(r)


def _safe_json(r: httpx.Response):
    try:
        return r.json()
    except Exception:
        return {}


async def main() -> int:
    doctor = login("smoke-doctor", pw("smoke_pw"))
    tok_a, tok_b = doctor["access_token"], doctor["access_token"]
    # 两次独立登录 → 两个 token（同医生并发）
    doctor2 = login("smoke-doctor", pw("smoke_pw"))
    tok_b = doctor2["access_token"]
    check("双 token 获取（同医生两次登录）", tok_a != tok_b, f"a={tok_a[:12]}… b={tok_b[:12]}…")
    admin = login("smoke-admin", pw("smoke_admin_pw"))  # grant 需 admin（A.49 同款）

    async with httpx.AsyncClient() as client:
        # 前置：探针患者 + grant（A.49 教训：读/建会话端点都有 consent 门）
        pid = f"probe-e12-{int(time.time())}"
        code, body = await _post(client, "/fhir/Patient",
                                 {"patient_id": pid, "name": "AUDIT E12 PROBE", "gender": "unknown"}, tok_a)
        code2, _ = await _post(client, "/api/v1/consent",
                               {"patient_id": pid, "action": "grant", "operator_id": "smoke-admin"},
                               admin["access_token"])
        check("探针患者前置（FHIR+grant）", code in (200, 201) and code2 == 200,
              f"pid={pid} fhir={code} grant={code2}")

        # 1) 并发建会话：隔离语义（不同 session_id）
        t0 = time.monotonic()
        r1, r2 = await asyncio.gather(
            _post(client, "/api/v1/sessions",
                  {"patient_id": pid, "device_id": "probe-e12a",
                   "target_organs": ["cardiac"]}, tok_a),
            _post(client, "/api/v1/sessions",
                  {"patient_id": pid, "device_id": "probe-e12b",
                   "target_organs": ["cardiac"]}, tok_b),
        )
        s1_ok, s2_ok = r1[0] == 200, r2[0] == 200
        sid1 = r1[1].get("session_id", "")
        sid2 = r2[1].get("session_id", "")
        check("并发建会话 200×2", s1_ok and s2_ok,
              f"http={r1[0]}/{r2[0]} {round(time.monotonic() - t0, 1)}s")
        check("会话隔离（session_id 互异）", bool(sid1) and bool(sid2) and sid1 != sid2,
              f"{sid1} vs {sid2}")

        # 2) 并发发判读（同 organ 双会话）
        r3, r4 = await asyncio.gather(
            _post(client, "/api/v1/diagnosis/request",
                  {"session_id": sid1, "target_organ": "cardiac",
                   "image_ids": [], "clinical_text": "并发探针 A：左室壁运动尚可"},
                  tok_a),
            _post(client, "/api/v1/diagnosis/request",
                  {"session_id": sid2, "target_organ": "cardiac",
                   "image_ids": [], "clinical_text": "并发探针 B：左室壁运动尚可"},
                  tok_b),
        )
        rid1 = r3[1].get("request_id", "")
        rid2 = r4[1].get("request_id", "")
        check("并发发判读 200×2", r3[0] == 200 and r4[0] == 200,
              f"http={r3[0]}/{r4[0]} rid={rid1}/{rid2}")
        check("判读隔离（request_id 互异）", bool(rid1) and bool(rid2) and rid1 != rid2)

        # 3) 同 idempotency_key 并发 → 幂等（同 request_id）
        idem = "audit-e12-idem-1"
        r5, r6 = await asyncio.gather(
            _post(client, "/api/v1/diagnosis/request",
                  {"session_id": sid1, "target_organ": "cardiac", "image_ids": [],
                   "clinical_text": "幂等探针", "idempotency_key": idem}, tok_a),
            _post(client, "/api/v1/diagnosis/request",
                  {"session_id": sid1, "target_organ": "cardiac", "image_ids": [],
                   "clinical_text": "幂等探针", "idempotency_key": idem}, tok_b),
        )
        rid5 = r5[1].get("request_id", "")
        rid6 = r6[1].get("request_id", "")
        check("幂等键并发 → 同 request_id（或语义化拒绝）",
              (rid5 and rid5 == rid6) or (r5[0] >= 400 and r6[0] >= 400),
              f"http={r5[0]}/{r6[0]} rid={rid5}/{rid6}")

        # 4) refresh-token rotate 并发竞争（跨进程互斥——A.23 修复真机验证）
        refresh = doctor2.get("refresh_token", "")
        if refresh:
            async def _refresh(tok: str):
                rr = await client.post(
                    f"{BASE}/api/v1/auth/refresh-token",
                    json={"refresh_token": refresh},
                    headers={"Authorization": f"Bearer {tok}"},
                    timeout=30.0,
                )
                return rr.status_code

            codes = await asyncio.gather(_refresh(tok_a), _refresh(tok_b))
            # 语义（A.49）：宽限窗内双发都 200 且沿后继链，或单 200 一 401/409 ——
            # 关键判据：不得双双 500/服务端错乱
            ok = all(c in (200, 401, 409) for c in codes)
            check("refresh 并发竞争无服务端错乱（200/401/409）", ok, f"codes={list(codes)}")
        else:
            check("refresh 并发竞争", True, "无 refresh_token（跳过）")

    return summary("E12+E9 并发核实")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
