"""分段拉取远端文件（本机到 10.20.28.21 单连接常被 reset / 建连超时）。

用法：python3 .tmp_work/pull.py <远端路径> <本地路径> [每块字节数]
"""
from __future__ import annotations

import base64
import subprocess
import sys
import time
from pathlib import Path

HOST = "student03@10.20.28.21"


def sh(cmd: str, *, timeout: int = 90) -> tuple[int, bytes, bytes]:
    p = subprocess.run(["bash", "-c", cmd], capture_output=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def ssh_cmd(inner: str, *, timeout: int = 90) -> tuple[int, bytes, bytes]:
    return sh(
        f"ssh -o ConnectTimeout=15 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 "
        f"{HOST} {inner!r}",
        timeout=timeout,
    )


def main() -> int:
    remote, local = sys.argv[1], sys.argv[2]
    chunk = int(sys.argv[3]) if len(sys.argv) > 3 else 32768

    total = 0
    for _ in range(10):
        rc, out, err = ssh_cmd(f"stat -c %s {remote}")
        if rc == 0 and out.strip().isdigit():
            total = int(out.strip())
            break
        print(f"stat retry: {err.decode('utf-8', 'replace').strip()[:120]}", file=sys.stderr)
        time.sleep(3)
    if not total:
        print("cannot stat remote file", file=sys.stderr)
        return 1

    out_path = Path(local)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        off = 0
        while off < total:
            data = b""
            for attempt in range(12):
                rc, so, se = ssh_cmd(
                    f"tail -c +{off + 1} {remote} | head -c {chunk} | base64 -w0"
                )
                if rc == 0 and so.strip():
                    try:
                        data = base64.b64decode(so.strip())
                        if data:
                            break
                    except Exception:  # noqa: BLE001 — 截断的 base64 视为失败重试
                        data = b""
                time.sleep(2 + attempt)
            else:
                print(f"\nFAILED at {off}/{total}", file=sys.stderr)
                return 1
            f.write(data)
            off += len(data)
            print(f"\r{off}/{total}", end="", flush=True)
    print()
    print(f"done {out_path} ({out_path.stat().st_size} B)")
    return 0


raise SystemExit(main())
