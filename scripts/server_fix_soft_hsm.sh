#!/usr/bin/env bash
# 刷新 soft HSM 启用时间戳（过期 → 签名路径抛 RuntimeError）。
# 这是**运维动作**：先备份原文件，再写当前 UTC 时间，并打印前后对比。
# 生产环境应改用真实 HSM；本机为 USE_SOFT_HSM=true 的开发/验收档。
set -u
UAI="$HOME/uai"
SRC="$UAI/src"
TS="$SRC/soft_hsm_enabled_at.txt"

echo "--- 现状 ---"
if [ -f "$TS" ]; then
  echo "file: $TS"
  cat "$TS"
  echo
  backup="${TS}.bak.$(date '+%Y%m%d_%H%M%S')"
  cp -p "$TS" "$backup"
  echo "backup: $backup"
else
  echo "no timestamp file（首次运行会自动写入当前时间）"
fi

"$UAI/venv/bin/python" - <<'PY'
import datetime
import os
import pathlib

ts = pathlib.Path(os.path.expanduser("~/uai/src/soft_hsm_enabled_at.txt"))
now = datetime.datetime.now(datetime.timezone.utc)
ts.write_text(now.isoformat(), encoding="utf-8")
print("new enabled_at =", now.isoformat())
PY

echo "--- 现状（刷新后）---"
cat "$TS"
echo
echo "=== soft hsm done ==="
