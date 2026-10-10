"""P4 认证生命周期探针（走查从未覆盖，A.49）。

序列：JSON 登录 → 错误口令 401 → OAuth2 form /token → verify-token →
refresh rotate（旧 token 复用必须 401）→ revoke-refresh-token（撤销后 refresh 401）→
nav-token → token-stats 医生 403/管理员 200 → revoke-all-tokens。
"""
from __future__ import annotations

import sys

from _audit_common import check, http, login, pw, summary

DOCTOR = "smoke-doctor"


def main() -> int:
    doc_pw = pw("smoke_pw")

    # 1) JSON 登录
    tok = login(DOCTOR, doc_pw)
    access = tok["access_token"]
    refresh = tok["refresh_token"]
    check("login(JSON) 200 + 双 token", bool(access and refresh), f"expires_in={tok.get('expires_in')}")

    # 2) 错误口令
    code, _ = http("POST", "/api/v1/auth/login", body={"username": DOCTOR, "password": "wrong-pass"})
    check("login 错误口令 -> 401", code == 401, f"http={code}")

    # 3) OAuth2 form /token
    code, body = http("POST", "/api/v1/auth/token", form={"username": DOCTOR, "password": doc_pw})
    check("login(form /token) 200", code == 200 and bool(body.get("access_token")) if isinstance(body, dict) else False, f"http={code}")

    # 4) verify-token
    code, body = http("GET", "/api/v1/auth/verify-token", token=access)
    ok = code == 200 and isinstance(body, dict) and body.get("valid") is True
    check("verify-token valid=true", ok, f"http={code} role={body.get('role') if isinstance(body, dict) else body}")

    # 5) refresh rotate：新 refresh 生效。旧 refresh 在**宽限窗内**（默认 30s，
    #    settings.refresh_token_grace_period_s）复用属**设计语义**=合法并发刷新
    #    （多标签页/双击，refresh_token.py _grace_successor_redis）：返回后继
    #    而不是 401；窗口外复用才落回 reuse 撤族原语义。
    code, body = http("POST", "/api/v1/auth/refresh-token", body={"refresh_token": refresh})
    rotated = code == 200 and isinstance(body, dict) and body.get("refresh_token")
    check("refresh-token rotate 200 + 新 refresh", rotated, f"http={code}")
    code, body2 = http("POST", "/api/v1/auth/refresh-token", body={"refresh_token": refresh})
    ok = (
        code == 200
        and isinstance(body2, dict)
        and body2.get("refresh_token") == (body.get("refresh_token") if rotated else None)
    )
    check("宽限窗内旧 refresh 复用 → 200 且返回同一后继（设计语义）", ok,
          f"http={code} same_successor={isinstance(body2, dict) and body2.get('refresh_token') == body.get('refresh_token')}")
    refresh2 = body.get("refresh_token") if rotated else None

    # 6) revoke-refresh-token：撤销后再 refresh 401
    if refresh2:
        code, _ = http("POST", "/api/v1/auth/revoke-refresh-token", body={"refresh_token": refresh2}, token=access)
        check("revoke-refresh-token 200", code == 200, f"http={code}")
        code, _ = http("POST", "/api/v1/auth/refresh-token", body={"refresh_token": refresh2})
        check("已撤销 refresh 再用 -> 401", code == 401, f"http={code}")
    else:
        check("revoke-refresh-token 200", False, "无 rotate 后 refresh，跳级失败")

    # 7) nav-token
    tok = login(DOCTOR, doc_pw)
    access = tok["access_token"]
    code, body = http("POST", "/api/v1/auth/nav-token", body={"session_id": "audit-nav"}, token=access)
    ok = code == 200 and isinstance(body, dict) and bool(body.get("nav_token"))
    check("nav-token 200 + nav_token", ok, f"http={code} token_type={body.get('token_type') if isinstance(body, dict) else body}")

    # 8) token-stats：医生 403，管理员 200
    code, _ = http("GET", "/api/v1/auth/token-stats", token=access)
    check("token-stats 医生 -> 403", code == 403, f"http={code}")
    admin = login("smoke-admin", pw("smoke_admin_pw"))
    code, body = http("GET", "/api/v1/auth/token-stats", token=admin["access_token"])
    check("token-stats 管理员 200", code == 200, f"http={code}")

    # 9) revoke-all-tokens
    code, _ = http("POST", "/api/v1/auth/revoke-all-tokens", token=access)
    check("revoke-all-tokens 200", code == 200, f"http={code}")

    return summary("P4 认证生命周期")


if __name__ == "__main__":
    sys.exit(main())
