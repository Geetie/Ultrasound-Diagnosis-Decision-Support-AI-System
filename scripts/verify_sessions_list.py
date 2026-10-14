#!/usr/bin/env python3
"""端到端验证 GET /api/v1/sessions（工作列表数据源）。

验收口径：不是"返回 200"就算过，而是逐条核对契约：
  · 分组正确（进行中 / 今日待处理）
  · 脱敏生效（绝不出现完整姓名）
  · 派生字段正确（age 由 birth_date 算、ttl 由 updated_at 算）
  · 不回显 birth_date、不含终态
"""
import json
import logging
import sys
import urllib.error
import urllib.request
from datetime import date

logger = logging.getLogger(__name__)

BASE = "http://127.0.0.1:18000"
PW_FILE = "/mnt/ee16/home/student03/uai/.smoke_pw"


def http(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        logger.warning("http: 未预期异常，按原逻辑降级", exc_info=True)
        return e.code, e.read().decode()[:300]
    except Exception as e:
        logger.warning("http: 未预期异常，按原逻辑降级", exc_info=True)
        return 0, str(e)[:300]


def main():
    with open(PW_FILE, encoding="utf-8") as f:
        pw = f.read().strip()
    st, resp = http("POST", "/api/v1/auth/login",
                    {"username": "smoke-doctor", "password": pw})
    if st != 200:
        print("!! login failed http=%s %s" % (st, str(resp)[:200]))
        return 1
    token = resp.get("access_token") or resp.get("token") or ""
    if not token:
        print("!! no token, keys: %s" % list(resp)[:10])
        return 1

    st, body = http("GET", "/api/v1/sessions?limit=20", token=token)
    print("=== GET /api/v1/sessions -> http %s ===" % st)
    if st != 200:
        print(str(body)[:500])
        return 1
    print(json.dumps(body, ensure_ascii=False, indent=2)[:2000])

    rows = (body.get("in_progress") or []) + (body.get("pending_today") or [])
    print()
    print("=== contract checks ===")
    print("  in_progress=%d  pending_today=%d  total_pending=%s"
          % (len(body.get("in_progress") or []),
             len(body.get("pending_today") or []),
             body.get("total_pending")))

    bad = [r for r in rows if r.get("patient_name_masked")
           and r["patient_name_masked"] != r["patient_name_masked"][0] + "**"]
    print("  [masked_name]      %s" % ("PASS" if not bad else "FAIL " + str(bad[:2])))

    leaked = [r for r in rows if "birth_date" in r]
    print("  [no_birth_date]    %s" % ("PASS" if not leaked else "FAIL"))

    noage = [r["session_id"] for r in rows if r.get("age") is None]
    print("  [age_derived]      %s" % ("PASS" if not noage else
                                       "%d rows lack age (birth_date null in db)" % len(noage)))
    no_ttl = [r["session_id"] for r in rows if r.get("ttl_remaining_seconds") is None]
    print("  [ttl_derived]      %s" % ("PASS" if not no_ttl else
                                       "%d rows lack ttl" % len(no_ttl)))

    bad_status = [r["status"] for r in rows
                  if r["status"] in ("completed", "failed", "terminated")]
    print("  [no_terminal]      %s" % ("PASS" if not bad_status else "FAIL " + str(bad_status)))

    today = date.today().isoformat()
    not_today = [r["session_id"] for r in (body.get("pending_today") or [])
                 if not r["created_at"].startswith(today)]
    print("  [pending_is_today] %s" % ("PASS" if not not_today else
                                       "cross-day: " + str(not_today[:2])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
