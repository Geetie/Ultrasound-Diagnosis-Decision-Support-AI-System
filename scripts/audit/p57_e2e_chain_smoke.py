"""A.76：**端到端链路验证**（真机、真实影像、走公开接口）。

**为什么要发真请求**：`p55` 的逐环审计只说明"历史账死在哪一环"；A.75 又证明四类失败
**都在 10-12 之前**（结构化重试 10-13 上线后零发生）。但"8 天没复现" ≠ "已修好" ——
**没有新流量，任何结论都不可证**。本脚本用小批量真实请求把链路跑一遍。

**做法（不复用别人的业务数据，只用现成影像 + 标准问句）**
1. 从 `dicom_images` 取**真实 US 影像**（带 `session_id`），按器官各挑 N 条；
2. 以 `smoke-doctor` 登录（密码只从 `~/uai/.smoke_pw` 读，不落日志）；
3. `POST /api/v1/diagnosis/request`（`image_ids` 用真影像、`patient_context.conditions` 显式给，
   否则安全网关走保守分支）；
4. 轮询 `GET /api/v1/diagnosis/result/{id}`，**逐环打点**：
   提交 → 检索块数 → 生成/解析（结论可读、是否带分级/EF）→ 落库状态 → 是否有正文；
5. 输出一张逐条表 + 各环通过率。

**安全**：只提交、只读结果；不删改数据；限流 10/min → 默认每器官 2 条（共 6 条）。

用法（服务器）：
    ~/uai/venv/bin/python scripts/audit/run_with_env.py scripts/audit/p57_e2e_chain_smoke.py --per-organ 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys
import time

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_SCRIPTS), str(_SCRIPTS / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import httpx  # noqa: E402

BASE = "http://127.0.0.1:18000"
PW_FILE = pathlib.Path.home() / "uai" / ".smoke_pw"
USERNAME = "smoke-doctor"

#: 每个器官一句标准问句（与语料同域，避免"输入太短"干扰链路判定）
QUERIES = {
    "thyroid": "甲状腺右叶低回声结节，边界清，形态规则，无钙化，需要穿刺吗？",
    "cardiac": "老年患者活动后气短，超声示左室射血分数 45%，二尖瓣中度反流，如何评估？",
    "liver": "肝右叶见 3cm 低回声灶，边界欠清，需要进一步做什么检查？",
}

_TR = re.compile(r"TR\s*[1-5]", re.I)
_LVEF = re.compile(r"(LVEF|射血分数)[^\d]{0,12}\d{1,3}\s*%", re.I)
_DEGRADED = ("llm_unavailable", "manual review required", "unparseable", "evidence_insufficient")
_ECHO = ("chunk_a1b2", "PATIENT_001")


def chain_of(result: dict) -> dict:
    """从结果响应里判定各环状态（纯函数，守卫钉住）。

    响应形态可能是 `{findings: str, status, confidence, ...}` 或带 `fusion_result`；
    这里对两种都认，且**不抛异常**（缺字段按"未产出"处理）。
    """
    findings = result.get("findings")
    if isinstance(findings, dict):
        fr = findings.get("fusion_result") or {}
        text = (fr.get("content") or "") if isinstance(fr, dict) else ""
        degraded_flag = bool(isinstance(fr, dict) and fr.get("degraded"))
    else:
        text = str(findings or "")
        degraded_flag = False

    low = text.lower()
    has_body = bool(text.strip())
    is_degraded = degraded_flag or any(m in low for m in _DEGRADED)
    is_echo = any(m in text for m in _ECHO)

    organ = str(result.get("organ") or result.get("target_organ") or "")
    if organ == "thyroid":
        graded = bool(_TR.search(text))
    elif organ == "cardiac":
        graded = bool(_LVEF.search(text))
    else:
        graded = has_body  # 肝脏无强制分级码

    return {
        "status": result.get("status"),
        "confidence": result.get("confidence"),
        "body_len": len(text),
        "has_body": has_body,
        "degraded": is_degraded,
        "prompt_echo": is_echo,
        "graded": graded,
        "link_ok": bool(has_body and not is_degraded and not is_echo and graded),
        "head": text[:120].replace("\n", " "),
    }


async def _login(client: httpx.AsyncClient) -> str:
    pw = PW_FILE.read_text(encoding="utf-8").strip()
    r = await client.post(
        f"{BASE}/api/v1/auth/login", json={"username": USERNAME, "password": pw}
    )
    r.raise_for_status()
    return r.json()["access_token"]


async def _pick_images(per_organ: int) -> list[dict]:
    """取真实 US 影像（带 session_id），按器官配标准问句。"""
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import DicomImage

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    DicomImage.image_id,
                    DicomImage.session_id,
                    DicomImage.modality,
                ).order_by(DicomImage.created_at)
            )
        ).all()

    us = [r for r in rows if str(r[2] or "").upper() == "US"]
    if not us:
        return []
    out: list[dict] = []
    for i, organ in enumerate(("thyroid", "cardiac", "liver")):
        for j in range(per_organ):
            row = us[(i * per_organ + j) % len(us)]
            out.append(
                {
                    "organ": organ,
                    "image_id": str(row[0]),
                    "session_id": str(row[1]),
                    "clinical_text": QUERIES[organ],
                }
            )
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--per-organ", type=int, default=2)
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("--report", default=str(pathlib.Path.home() / "uai/reports/e2e_chain.json"))
    args = ap.parse_args()

    try:
        items = await _pick_images(args.per_organ)
    except Exception as exc:
        print(f"[FATAL] 取影像失败：{type(exc).__name__}: {exc}")
        return 2
    if not items:
        print("[FATAL] 库里没有 US 影像")
        return 2

    rows: list[dict] = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            token = await _login(client)
        except Exception as exc:
            print(f"[FATAL] 登录失败：{type(exc).__name__}: {exc}")
            return 2
        headers = {"Authorization": f"Bearer {token}"}

        for it in items:
            payload = {
                "session_id": it["session_id"],
                "image_ids": [it["image_id"]],
                "clinical_text": it["clinical_text"],
                "target_organ": it["organ"],
                "patient_context": {"age": 50, "gender": "female", "conditions": []},
            }
            sub = await client.post(f"{BASE}/api/v1/diagnosis/request", json=payload, headers=headers)
            row: dict = {
                "organ": it["organ"],
                "image_id": it["image_id"],
                "session_id": it["session_id"],
                "submit_status": sub.status_code,
            }
            if sub.status_code not in (200, 202):
                row["error"] = sub.text[:180]
                rows.append(row)
                print(f"[{it['organ']}] 提交失败 {sub.status_code}: {row['error'][:120]}")
                continue
            request_id = sub.json().get("request_id")
            row["request_id"] = request_id

            deadline = time.monotonic() + args.timeout
            got: dict = {}
            while time.monotonic() < deadline:
                await asyncio.sleep(4)
                r = await client.get(f"{BASE}/api/v1/diagnosis/result/{request_id}", headers=headers)
                if r.status_code != 200:
                    continue
                body = r.json()
                if str(body.get("status") or "").lower() in ("completed", "failed", "cancelled"):
                    got = body
                    break
            row["chain"] = chain_of(got) if got else {"status": "timeout", "link_ok": False}
            rows.append(row)
            c = row["chain"]
            print(
                f"[{it['organ']}] {request_id} status={c.get('status')} "
                f"body={c.get('body_len')} graded={c.get('graded')} "
                f"degraded={c.get('degraded')} echo={c.get('prompt_echo')} → "
                f"{'OK' if c.get('link_ok') else 'FAIL'}"
            )
            if c.get("head"):
                print(f"     {c['head'][:110]}")

    ok = sum(1 for r in rows if r.get("chain", {}).get("link_ok"))
    summary = {
        "n": len(rows),
        "ok": ok,
        "ok_rate": round(ok / len(rows), 4) if rows else None,
        "submitted": sum(1 for r in rows if r["submit_status"] in (200, 202)),
        "timeout": sum(1 for r in rows if r.get("chain", {}).get("status") == "timeout"),
        "degraded": sum(1 for r in rows if r.get("chain", {}).get("degraded")),
        "prompt_echo": sum(1 for r in rows if r.get("chain", {}).get("prompt_echo")),
        "no_grade": sum(
            1 for r in rows if r.get("chain", {}).get("has_body") and not r["chain"].get("graded")
        ),
    }
    print(f"\n== 汇总 == {summary}")

    report = {"probe": "p57_e2e_chain_smoke", "base": BASE, "summary": summary, "rows": rows}
    path = pathlib.Path(args.report).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
