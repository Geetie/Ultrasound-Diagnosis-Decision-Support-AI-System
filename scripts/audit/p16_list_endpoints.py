#!/usr/bin/env python3
"""p16 列表端点真机探针（A.51 · C 项）。

背景：走查实证「设备/患者列表端点不存在」——设备管理面看不到台账、建会话时
患者只能手工录入 ID 再走 FHIR 校验。本探针核验两个新端点：
  · GET /api/v1/admin/device   （管理面设备台账：分页/过滤/**零 api_key 泄漏**）
  · GET /api/v1/patients       （患者检索：按 patient_id/姓名模糊、分页、未授权 401）

跑法（服务器）：
    cd ~/uai/src/scripts/audit && ~/uai/venv/bin/python p16_list_endpoints.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _audit_common import UAI, check, http, login, pw, summary

DEVICE_PATH = "/api/v1/admin/device"
PATIENTS_PATH = "/api/v1/patients"


def _has_key(node, key: str) -> bool:
    """递归找键（防漏检嵌套结构里的 api_key）。"""
    if isinstance(node, dict):
        return key in node or any(_has_key(v, key) for v in node.values())
    if isinstance(node, list):
        return any(_has_key(v, key) for v in node)
    return False


def main() -> int:
    admin = login("smoke-admin", pw("smoke_admin_pw"))
    doctor = login("smoke-doctor", pw("smoke_pw"))
    atok = admin["access_token"]
    dtok = doctor["access_token"]

    # ---------- 设备台账 ----------
    code, body = http("GET", DEVICE_PATH)
    check("设备列表：无 token → 401", code == 401, f"http={code}")

    code, body = http("GET", DEVICE_PATH, token=dtok)
    check("设备列表：非 admin → 403", code == 403, f"http={code}")

    code, body = http("GET", f"{DEVICE_PATH}?limit=5", token=atok)
    ok_struct = code == 200 and isinstance(body, dict) and {
        "items", "total", "offset", "limit"
    } <= set(body)
    check("设备列表：admin 200 且结构完整", ok_struct, f"http={code} keys={list(body)[:6] if isinstance(body, dict) else body}")
    if not ok_struct:
        return summary("A.51 列表端点")

    check(
        "设备列表：**响应不含 api_key**（脱敏）",
        not _has_key(body, "api_key"),
        "items 字段=" + ",".join(sorted(body["items"][0])[:12]) if body.get("items") else "（空台账）",
    )
    check("设备列表：total ≥ 已注册数（smoke 设备在内）", int(body["total"]) >= 1, f"total={body['total']}")
    check("设备列表：limit 生效", len(body["items"]) <= 5, f"items={len(body['items'])}")

    code, page1 = http("GET", f"{DEVICE_PATH}?limit=1&offset=0", token=atok)
    code2, page2 = http("GET", f"{DEVICE_PATH}?limit=1&offset=1", token=atok)
    first = page1["items"][0]["device_id"] if page1.get("items") else None
    second = page2["items"][0]["device_id"] if page2.get("items") else None
    check(
        "设备列表：offset 翻页换条目（且不重复）",
        code == 200 and code2 == 200 and (second is None or first != second),
        f"p1={first} p2={second}",
    )

    code, act = http("GET", f"{DEVICE_PATH}?status=active&limit=50", token=atok)
    all_active = code == 200 and all(i["status"] == "active" for i in act.get("items", []))
    check("设备列表：status 过滤生效", all_active, f"n={len(act.get('items', []))}")

    # ---------- 患者检索 ----------
    code, body = http("GET", PATIENTS_PATH)
    check("患者列表：无 token → 401", code == 401, f"http={code}")

    marker = f"P16-{int(time.time())}"
    code, _created = http(
        "POST",
        "/fhir/Patient",
        token=dtok,
        body={"patient_id": f"probe-{marker}", "name": f"探针 {marker}", "gender": "unknown"},
    )
    check("患者列表：预置患者成功（FHIR 写入）", code in (200, 201), f"http={code}")

    code, body = http("GET", f"{PATIENTS_PATH}?q={marker}&limit=10", token=dtok)
    hit = body.get("items", []) if isinstance(body, dict) else []
    check(
        "患者列表：按姓名/ID 模糊搜索命中刚建的患者",
        code == 200 and any(i["patient_id"] == f"probe-{marker}" for i in hit),
        f"http={code} hits={[i['patient_id'] for i in hit][:3]}",
    )
    if hit:
        keys = set(hit[0])
        check(
            "患者列表：字段契约（patient_id/name/gender/consent_status/last_session_at）",
            {"patient_id", "name", "gender", "consent_status", "last_session_at"} <= keys,
            f"keys={sorted(keys)}",
        )

    code, body = http("GET", f"{PATIENTS_PATH}?q={marker}&limit=10&offset=0", token=dtok)
    check("患者列表：limit/offset 回显", code == 200 and body.get("offset") == 0 and body.get("limit") == 10, f"http={code}")

    code, body = http("GET", f"{PATIENTS_PATH}?q=ZZZ-NO-SUCH-{marker}", token=dtok)
    check("患者列表：无命中时 total=0（不虚构）", code == 200 and body.get("total") == 0, f"total={body.get('total') if isinstance(body, dict) else body}")

    (UAI / "reports").mkdir(parents=True, exist_ok=True)
    out = UAI / "reports" / f"list_endpoints_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(
        json.dumps({"device_total": body.get("total") if isinstance(body, dict) else None}, ensure_ascii=False),
        encoding="utf-8",
    )
    return summary("A.51 列表端点")


if __name__ == "__main__":
    sys.exit(main())
