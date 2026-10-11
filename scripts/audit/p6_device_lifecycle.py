"""P6 设备生命周期探针（走查全部未覆盖，A.49）。

认证模型：register/deregister/recovery/remote-recovery = 管理员 Bearer；
checkin/heartbeat/health/ota = X-Device-Id + X-Device-Token（原始 api_key）。
OTA：A.50.8 E7 落地真分发（settings.ota_package_dir 配置后 200/404）。
状态机负断言：active 设备不可 recovery/remote-recovery（quarantined 前置）；
deregistered 后设备头认证必须失效。
"""
from __future__ import annotations

import sys
import time

from _audit_common import check, http, login, pw, summary

ADMIN = "smoke-admin"


def main() -> int:
    admin = login(ADMIN, pw("smoke_admin_pw"))
    atok = admin["access_token"]
    name = f"probe-audit-{int(time.time())}"

    # 1) register（api_key 只在此时明文给一次）
    code, body = http(
        "POST", "/api/v1/admin/device/register",
        body={"device_name": name, "device_type": "ultrasound", "location": "audit-room", "jetpack_version": "0"},
        token=atok,
    )
    did = body.get("device_id") if isinstance(body, dict) else None
    key = body.get("api_key") if isinstance(body, dict) else None
    check("register 200 + device_id/api_key", code == 200 and bool(did and key), f"http={code} device_id={did}")
    dev_hdr = {"X-Device-Id": did, "X-Device-Token": key}

    # 2) 重名语义（device.py 注册端点）：device_name 是设备身份，**任何同名注册
    #    都拒绝**——deregistered 违反安全策略、active 会造成同名多行（首轮探针
    #    active 同名 200 曾让 get_device_by_name 的 scalar_one_or_none 炸 500）。
    code, _ = http(
        "POST", "/api/v1/admin/device/register",
        body={"device_name": name, "device_type": "ultrasound", "location": "x", "jetpack_version": "0"},
        token=atok,
    )
    check("register active 同名 -> 400", code == 400, f"http={code}")

    # 3) checkin（设备头）
    code, body = http("POST", "/api/v1/admin/device/checkin", headers=dev_hdr)
    ok = code == 200 and isinstance(body, dict) and body.get("registered") is True
    check("checkin 200 registered=true", ok, f"http={code}")

    # 4) 设备头错 token -> 401
    code, _ = http("POST", "/api/v1/admin/device/checkin",
                   headers={"X-Device-Id": did, "X-Device-Token": "bad-key"})
    check("checkin 错 api_key -> 401", code == 401, f"http={code}")

    # 5) heartbeat
    code, _ = http("POST", "/api/v1/admin/device/heartbeat", body={"device_id": did, "state": "registered"}, headers=dev_hdr)
    check("heartbeat 200", code == 200, f"http={code}")

    # 6) health
    code, _ = http("POST", "/api/v1/admin/device/health", body={"device_id": did}, headers=dev_hdr)
    check("health 200", code == 200, f"http={code}")

    # 7) OTA：A.50.8 E7 落地后真分发（200 二进制 1MiB；不存在的版本 404；
    #    未配置分发目录的服务器仍 501 契约位）
    code, blob = http("GET", "/api/v1/admin/device/ota/edge-model/1.0.1",
                      headers=dev_hdr, raw_response=True)
    check("OTA 下载 200（真分发 1MiB）", code == 200 and len(blob) == 1048576,
          f"http={code} bytes={len(blob)}")
    code, _ = http("GET", "/api/v1/admin/device/ota/edge-model/9.9.9", headers=dev_hdr)
    check("OTA 不存在版本 -> 404", code == 404, f"http={code}")

    # 8) 状态机负断言：active 不可 recovery / remote-recovery
    code, _ = http("POST", f"/api/v1/admin/device/{did}/recovery", token=atok)
    check("recovery(active) -> 4xx", 400 <= code < 500, f"http={code}")
    code, _ = http("POST", f"/api/v1/admin/device/{did}/remote-recovery", token=atok)
    check("remote-recovery(active, 非 quarantined) -> 4xx", 400 <= code < 500, f"http={code}")

    # 9) deregister
    code, _ = http("POST", f"/api/v1/admin/device/{did}/deregister", token=atok)
    check("deregister 200", code == 200, f"http={code}")

    # 10) 注销后设备头失效 + recovery 仍被状态机拒绝（deregistered 不可恢复）
    code, _ = http("POST", "/api/v1/admin/device/checkin", headers=dev_hdr)
    check("checkin(deregistered) -> 401", code == 401, f"http={code}")
    code, _ = http("POST", f"/api/v1/admin/device/{did}/recovery", token=atok)
    check("recovery(deregistered) -> 4xx", 400 <= code < 500, f"http={code}")

    # 11) 安全策略：deregistered 的设备名禁止复用
    code, _ = http(
        "POST", "/api/v1/admin/device/register",
        body={"device_name": name, "device_type": "ultrasound", "location": "x", "jetpack_version": "0"},
        token=atok,
    )
    check("register(deregistered 同名) -> 400", code == 400, f"http={code}")

    return summary("P6 设备生命周期")


if __name__ == "__main__":
    sys.exit(main())
