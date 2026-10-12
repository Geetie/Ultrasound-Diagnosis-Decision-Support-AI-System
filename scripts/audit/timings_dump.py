#!/usr/bin/env python3
"""dump 一次判读的完整 timings_ms 键名（E14 口径校准）。"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

import httpx
from _audit_common import BASE, login, pw


async def main() -> int:
    doctor = login("smoke-doctor", pw("smoke_pw"))
    tok = doctor["access_token"]
    admin = login("smoke-admin", pw("smoke_admin_pw"))
    pid = f"probe-timing-{int(time.time())}"
    async with httpx.AsyncClient() as c:
        await c.post(f"{BASE}/fhir/Patient", json={"patient_id": pid, "name": "T", "gender": "unknown"},
                     headers={"Authorization": f"Bearer {tok}"}, timeout=30.0)
        await c.post(f"{BASE}/api/v1/consent", json={"patient_id": pid, "action": "grant", "operator_id": "smoke-admin"},
                     headers={"Authorization": f"Bearer {admin['access_token']}"}, timeout=30.0)
        r = await c.post(f"{BASE}/api/v1/sessions",
                         json={"patient_id": pid, "device_id": "probe-t", "target_organs": ["cardiac"]},
                         headers={"Authorization": f"Bearer {tok}"}, timeout=30.0)
        sid = r.json()["session_id"]
        t0 = time.monotonic()
        r = await c.post(f"{BASE}/api/v1/diagnosis/request",
                         json={"session_id": sid, "target_organ": "cardiac", "image_ids": [],
                               "clinical_text": "室间隔运动尚可。"},
                         headers={"Authorization": f"Bearer {tok}"}, timeout=30.0)
        rid = r.json()["request_id"]
        while time.monotonic() - t0 < 300:
            rr = await c.get(f"{BASE}/api/v1/diagnosis/result/{rid}",
                             headers={"Authorization": f"Bearer {tok}"}, timeout=15.0)
            b = rr.json()
            if b.get("status") in ("completed", "failed", "timeout"):
                f = (b.get("diagnosis") or {}).get("findings")
                print("status:", b.get("status"))
                print("diagnosis keys:", list((b.get("diagnosis") or {}).keys()))
                print("findings type:", type(f).__name__)
                if isinstance(f, dict):
                    print("findings keys:", list(f.keys()))
                    print("timings:", json.dumps(f.get("timings_ms"), ensure_ascii=False)[:600])
                return 0
            await asyncio.sleep(1.0)
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
