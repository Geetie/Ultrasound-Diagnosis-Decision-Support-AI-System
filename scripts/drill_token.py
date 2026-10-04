"""取登录 token（供交付门禁/演练脚本调用）。

只打印 token 到 stdout，调用方自己重定向到文件——**不要**把 token 写进
仓库或日志（skill 铁律：密码/令牌不进命令行、不进仓库）。
口令来源固定为 ``~/uai/.smoke_pw``（600 权限，由 server_smoke_probe.py 生成）。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.request

API = os.environ.get("DRILL_API", "http://127.0.0.1:18000")
PW = pathlib.Path(os.path.expanduser("~/uai/.smoke_pw"))


def main() -> int:
    if not PW.exists():
        print("MISS_PW: 先跑 server_smoke_probe.py（会写 ~/uai/.smoke_pw）", file=sys.stderr)
        return 2
    pw = PW.read_text(encoding="utf-8").strip()
    req = urllib.request.Request(
        f"{API}/api/v1/auth/login",
        data=json.dumps({"username": "smoke-doctor", "password": pw}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        token = json.loads(resp.read().decode()).get("access_token", "")
    if not token:
        print("NO_TOKEN", file=sys.stderr)
        return 3
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
