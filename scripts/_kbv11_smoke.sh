#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
cd "$UAI/src"
PYTHONPATH="$UAI/src" "$UAI/venv/bin/python" - <<'EOF'
import asyncio
import json
import time
import urllib.request

import asyncpg
from app.config.dsn import to_asyncpg_dsn
from app.config.settings import settings

CASES = [
    ("liver", "kbv11-liver", "肝右叶可见低回声占位，边界欠清，增强扫描动脉期明显强化。"),
    ("cardiac", "kbv11-cardiac", "二尖瓣轻度反流，左室射血分数正常，室间隔增厚。"),
    ("thyroid", "kbv11-thyroid", "甲状腺右叶低回声结节伴微钙化，纵横比大于1。"),
]


async def ensure(organ: str, sid: str) -> None:
    conn = await asyncpg.connect(to_asyncpg_dsn(settings.database_url))
    await conn.execute(
        "INSERT INTO sessions (session_id, patient_id, device_id, status, "
        "target_organs, organ_statuses, knowledge_base_version, created_at, "
        "updated_at, consent_status_at_creation) VALUES "
        f"('{sid}','t2c-patient','t2c-device','active','[\"{organ}\"]'::jsonb,"
        "'{}'::jsonb,'kb_v1.1.0',now(),now(),'granted') "
        "ON CONFLICT (session_id) DO NOTHING"
    )
    await conn.close()


async def main() -> None:
    for organ, sid, text in CASES:
        await ensure(organ, sid)


asyncio.run(main())

pw = open("/mnt/ee16/home/student03/uai/.smoke_pw").read().strip()


def post(url: str, body: dict, tok: str | None = None) -> dict:
    h = {"Content-Type": "application/json"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get(url: str, tok: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


tok = post("http://127.0.0.1:18000/api/v1/auth/login",
           {"username": "smoke-doctor", "password": pw})["access_token"]
for organ, sid, text in CASES:
    sub = post("http://127.0.0.1:18000/api/v1/diagnosis/request", {
        "session_id": sid, "image_ids": [], "clinical_text": text,
        "target_organ": organ, "response_tier": "full",
        "patient_context": {"age": 50, "gender": "female", "conditions": []}},
        tok)
    rid = sub["request_id"]
    t0 = time.time()
    for _ in range(40):
        time.sleep(3)
        d = get(f"http://127.0.0.1:18000/api/v1/diagnosis/result/{rid}", tok)
        st = d.get("status")
        if st in ("completed", "failed", "timeout", "needs_review"):
            reco = (d.get("recommendations") or "")[:90].replace("\n", " ")
            print(f"{organ:8} {time.time()-t0:5.1f}s {st:12} | {reco}", flush=True)
            break
EOF
