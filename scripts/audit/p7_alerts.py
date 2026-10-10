"""P7 告警升级链探针（走查全部未覆盖，A.49）。

webhook（Alertmanager 形态；未配 token 时无鉴权，除鉴权失败恒 200）
→ contacts GET/PUT → active-alerts → CUSUM calibrate（Query 参数）→
calibration-report → sg-threshold refresh。全组管理员 Bearer。
PUT /contacts 带可达性验证，探针不推假联系人（避免写坏生产联系人表）——
改为读改写当前联系人（无变化提交，验证 PUT 链路自身可达）。
"""
from __future__ import annotations

import sys

from _audit_common import check, http, login, pw, summary

ADMIN = "smoke-admin"
ALERTMANAGER_BODY = {
    "version": "4",
    "status": "firing",
    "groupKey": "audit",
    "receiver": "audit",
    "alerts": [
        {
            "status": "firing",
            "labels": {"alertname": "AuditProbe", "severity": "info"},
            "annotations": {"description": "A.49 管线摸排探针告警（非真实事件）"},
        }
    ],
}


def main() -> int:
    admin = login(ADMIN, pw("smoke_admin_pw"))
    atok = admin["access_token"]

    # 1) webhook（无鉴权路径）
    code, body = http("POST", "/api/v1/internal/alerts/webhook", body=ALERTMANAGER_BODY)
    check("webhook(Alertmanager 形态) 200", code == 200, f"http={code} {str(body)[:80]}")

    # 2) contacts GET
    code, body = http("GET", "/api/v1/admin/alert/contacts", token=atok)
    contacts = body if isinstance(body, list) else (body or {}).get("contacts") if isinstance(body, dict) else None
    check("GET contacts 200", code == 200, f"http={code} n={len(contacts) if isinstance(contacts, list) else '?'}")

    # 3) contacts PUT（读改写原值，验证链路；可达性验证若拒即 4xx 也如实记录）
    if isinstance(contacts, list):
        code, body = http("PUT", "/api/v1/admin/alert/contacts", body=contacts, token=atok)
        check("PUT contacts(原值回写)", code == 200, f"http={code} {str(body)[:80]}")
    else:
        check("PUT contacts(原值回写)", False, "contacts 非 list，跳过")

    # 4) active-alerts
    code, body = http("GET", "/api/v1/admin/alert/active-alerts", token=atok)
    check("GET active-alerts 200", code == 200, f"http={code}")

    # 5) CUSUM 校准（Query 参数）
    code, body = http(
        "POST", "/api/v1/admin/alert/cusum/calibrate?target_rate=0.02&daily_cases=50&simulation_days=30",
        token=atok,
    )
    check("cusum/calibrate 200", code == 200, f"http={code} {str(body)[:80]}")

    # 6) 校准报告（校准后应可得）
    code, _ = http("GET", "/api/v1/admin/alert/cusum/calibration-report", token=atok)
    check("cusum/calibration-report 200（校准后）", code == 200, f"http={code}")

    # 7) SG 阈值 refresh
    code, body = http("POST", "/api/v1/admin/alert/sg-threshold", body={"action": "refresh"}, token=atok)
    check("sg-threshold refresh 200", code == 200, f"http={code} {str(body)[:80]}")

    return summary("P7 告警升级链")


if __name__ == "__main__":
    sys.exit(main())
