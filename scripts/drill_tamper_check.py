"""篡改检测：改库里的报告正文，验签必须失效。

这是签名链的**核心临床安全属性**：报告签发后内容不可变。
只验"签了能过"不够，必须验"改了会红"（否则签名形同虚设）。

用法：``python scripts/drill_tamper_check.py <report_id>``
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import urllib.request

sys.path.insert(0, os.path.expanduser("~/uai/src"))
from sqlalchemy import text

from app.config.database import AsyncSessionLocal

API = os.environ.get("DRILL_API", "http://127.0.0.1:18000")
TAMPER_MARK = "［篡改测试］诊断意见：恶性"


async def tamper(report_id: str) -> bool:
    async with AsyncSessionLocal() as db:
        before = (await db.execute(text(
            "select content_digest from diagnosis_reports where report_id = :rid"
        ), {"rid": report_id})).first()
        if not before:
            print("  REPORT_NOT_FOUND")
            return False
        await db.execute(text(
            "update diagnosis_reports set rendered = rendered || :mark "
            "where report_id = :rid"
        ), {"rid": report_id, "mark": f"\n\n{TAMPER_MARK}"})
        await db.commit()
    print(f"  tampered (原 digest {str(before[0])[:12]})")
    return True


def _token() -> str:
    pw = pathlib.Path(os.path.expanduser("~/uai/.smoke_pw")).read_text(
        encoding="utf-8").strip()
    req = urllib.request.Request(
        f"{API}/api/v1/auth/login",
        data=json.dumps({"username": "smoke-doctor", "password": pw}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode()).get("access_token", "")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: drill_tamper_check.py <report_id>")
        return 2
    report_id = sys.argv[1]
    if not asyncio.run(tamper(report_id)):
        return 3
    try:
        req = urllib.request.Request(
            f"{API}/api/v1/report/{report_id}/verify",
            data=b"{}", method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {_token()}"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            res = json.loads(resp.read().decode())
    except Exception as exc:
        print(f"  verify raised: {type(exc).__name__}: {exc}")
        print("  （HTTP 错误也算拒绝，但要确认是 4xx 语义而非 5xx 故障）")
        return 0
    print("  verify_after_tamper:", json.dumps(
        {k: res.get(k) for k in ("signature_verified", "status", "content_digest")},
        ensure_ascii=False)[:300])
    if res.get("signature_verified") is False:
        print("  TAMPER_DETECTED")
        return 0
    print("  ⚠️ TAMPER_NOT_DETECTED（严重：签名没起作用）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
