"""真机链路冒烟（鉴权 + 一次最小诊断），**只输出状态枚举，不产出任何数值结论**。

用法：`~/uai/venv/bin/python ~/server_smoke_probe.py`

设计要点（都是真机上踩出来的）：
* `POST /api/v1/diagnosis/request` 需要**鉴权**（401 E1003 "Not authenticated"）——
  端点签名里 `current_user` 虽标注可选，但依赖链上是强校验；
* `users` 表是 0001 迁移新建的、初始为空 → 需要**先建一个冒烟用户**；
* 口令**不进命令行、不进仓库**：运行时生成一次写入 `~/uai/.smoke_pw`（600），
  哈希与登录都在进程内完成（argv 里看不到明文）。
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import secrets
import sys
import time
import urllib.error
import urllib.request

API = "http://127.0.0.1:18000"
USERNAME = "smoke-doctor"
PW_FILE = pathlib.Path(os.path.expanduser("~/uai/.smoke_pw"))
SESSION_ID = "smoke-session-0001"

# `python /path/to/script.py` 会把**脚本所在目录**（$HOME）而非 CWD 放进 sys.path，
# 于是 `import app...` 会失败（实测 ModuleNotFoundError）。这里显式补上源码目录。
_SRC = pathlib.Path(os.path.expanduser("~/uai/src"))
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
CLINICAL_TEXT = "冒烟用自造文本：低回声结节，边界清，未见明显钙化。"  # 非真实患者数据


def _post(path: str, payload: dict, token: str | None = None, timeout: float = 30.0):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        API + path, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body[:200]}


def _get(path: str, token: str | None = None, timeout: float = 10.0):
    request = urllib.request.Request(API + path)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body[:200]}


def smoke_password() -> str:
    if PW_FILE.exists():
        return PW_FILE.read_text(encoding="utf-8").strip()
    password = secrets.token_urlsafe(24)
    PW_FILE.write_text(password, encoding="utf-8")
    PW_FILE.chmod(0o600)
    return password


async def ensure_user(password: str) -> None:
    """建/更新冒烟用户（口令哈希在进程内算好，只把哈希写库）。"""
    import asyncpg

    from app.config.dsn import to_asyncpg_dsn
    from app.config.settings import settings
    from app.security.auth import get_password_hash

    dsn = to_asyncpg_dsn(settings.database_url)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO users (username, email, full_name, hashed_password, disabled, role,
                               created_at, updated_at)
            VALUES ($1, $2, $3, $4, false, 'ULTRASOUND_DOCTOR', now(), now())
            ON CONFLICT (username) DO UPDATE
              SET hashed_password = EXCLUDED.hashed_password,
                  disabled = false,
                  updated_at = now()
            """,
            USERNAME,
            "smoke@localhost",
            "SMOKE DOCTOR",
            get_password_hash(password),
        )
        print(f"  ✓ 冒烟用户就绪: {USERNAME}")
    finally:
        await conn.close()


def main() -> int:
    print("=== A) 准备鉴权 ===")
    password = smoke_password()
    try:
        asyncio.run(ensure_user(password))
    except Exception as exc:
        print(f"  ✗ 建用户失败: {type(exc).__name__}: {exc}")
        return 2

    code, body = _post("/api/v1/auth/login", {"username": USERNAME, "password": password})
    token = body.get("access_token")
    print(f"  POST /api/v1/auth/login -> {code} token={'yes' if token else 'no'}")
    if not token:
        print(f"  响应: {json.dumps(body, ensure_ascii=False)[:300]}")
        return 3

    print()
    print("=== B) 提交最小诊断（自造文本）===")
    code, body = _post(
        "/api/v1/diagnosis/request",
        {
            "session_id": SESSION_ID,
            "image_ids": [],
            "clinical_text": CLINICAL_TEXT,
            "target_organ": "thyroid",
        },
        token=token,
    )
    print(f"  POST /api/v1/diagnosis/request -> {code}")
    request_id = body.get("request_id")
    if not request_id:
        print(f"  响应: {json.dumps(body, ensure_ascii=False)[:400]}")
        return 4
    print(f"  request_id = {request_id}  status = {body.get('status')}")

    print()
    print("=== C) 轮询终态（只看状态枚举）===")
    terminal = {"completed", "failed", "timeout", "needs_review", "cancelled"}
    last = None
    for _ in range(12):
        time.sleep(5)
        code, body = _get(f"/api/v1/diagnosis/result/{request_id}", token=token)
        status = body.get("status")
        flags = [
            f"{key}={body.get(key)}"
            for key in ("ai_diagnosis_failed", "requires_manual_review", "desensitization_status")
            if body.get(key) is not None
        ]
        review = (body.get("review_reason") or "")
        line = f"  http={code} status={status} " + " ".join(flags) + (f" review={review}" if review else "")
        if line != last:
            print(f"  [t~{_}*5s] {line}")
            last = line
        if status in terminal:
            break

    print()
    print("=== 说明：本冒烟只验证链路可达性与状态流转，不产出任何数值结论 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
