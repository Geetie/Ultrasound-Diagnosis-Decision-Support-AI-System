"""A.49 端到端管线摸排 — 共享 helper（服务器上以 ~/uai/venv/bin/python 运行，stdlib only）。

约定：
* BASE=127.0.0.1:18000（服务器 loopback，硬规则 #3）；
* 口令只从 ~/uai/.smoke_pw（医生）/ ~/uai/.smoke_admin_pw（SYSTEM_ADMIN，p0 引导生成）读，不进 argv；
* 断言输出统一 `PASS|FAIL 名称 — 明细`，summary() 汇总并在有 FAIL 时 exit 1；
* 跨脚本上下文（request_id/session_id 等）落 ~/uai/reports/audit_ctx.json，不走 /tmp（硬规则 #11）。
"""
from __future__ import annotations

import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:18000"
UAI = pathlib.Path.home() / "uai"
CTX_FILE = UAI / "reports" / "audit_ctx.json"

_results: list[tuple[str, bool, str]] = []


def pw(name: str) -> str:
    """name: 'smoke_pw' | 'smoke_admin_pw' → 实际文件 ~/uai/.<name>（点前缀）。"""
    return (UAI / f".{name}").read_text(encoding="utf-8").strip()


def http(
    method: str,
    path: str,
    token: str | None = None,
    body: dict | list | None = None,
    form: dict | None = None,
    headers: dict | None = None,
    timeout: float = 30.0,
    raw_response: bool = False,
):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
            if raw_response:
                return r.status, payload  # 二进制（OTA 等非 JSON 端点）
            raw = payload.decode("utf-8", "replace")
            if raw.strip().startswith(("{", "[")):
                return r.status, json.loads(raw)
            return r.status, {"raw": raw[:200]}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw[:200]}
    except Exception as exc:  # 连接失败等
        return -1, {"error": f"{type(exc).__name__}: {exc}"}


def login(username: str, password: str, retries: int = 5) -> dict:
    """POST /api/v1/auth/login（JSON）。返回整个响应体（含 access_token/refresh_token）。

    登录限流按用户名维度（auth.py:130，E1007 429）——探针一天打几十次登录
    必然撞窗，429 时退避重试（A.49 P9/P10 假 FAIL 教训）。
    """
    delay = 15.0
    for attempt in range(retries):
        code, body = http("POST", "/api/v1/auth/login", body={"username": username, "password": password})
        if code == 429:
            print(f"  login 429（限流窗），{delay:.0f}s 后重试 {attempt + 1}/{retries}", flush=True)
            time.sleep(delay)
            delay = min(delay * 1.5, 60.0)
            continue
        if code != 200 or not isinstance(body, dict) or not body.get("access_token"):
            raise RuntimeError(f"login failed for {username}: http={code} {json.dumps(body, ensure_ascii=False)[:200]}")
        return body
    raise RuntimeError(f"login rate-limited after {retries} retries: {username}")


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def summary(title: str) -> int:
    fails = [r for r in _results if not r[1]]
    print(f"\n== {title}: PASS={len(_results) - len(fails)} FAIL={len(fails)}", flush=True)
    return 1 if fails else 0


def load_ctx() -> dict:
    if CTX_FILE.exists():
        return json.loads(CTX_FILE.read_text(encoding="utf-8"))
    return {}


def save_ctx(**kv: str) -> None:
    ctx = load_ctx()
    ctx.update(kv)
    ctx["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    CTX_FILE.parent.mkdir(parents=True, exist_ok=True)
    CTX_FILE.write_text(json.dumps(ctx, ensure_ascii=False, indent=1), encoding="utf-8")
