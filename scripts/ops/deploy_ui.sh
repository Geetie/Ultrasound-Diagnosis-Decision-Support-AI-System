#!/usr/bin/env bash
# A.83 前端发布：把构建产物放进 `app/static/ui/`（FastAPI 在 /ui/ 托管）。
#
# 为什么是"复制目录"而不是重启服务：
#   `app/main.py` 用 StaticFiles(directory=app/static/ui) 挂 `assets`，`/ui/{path}`
#   由回落路由返回 index.html —— 两者都**按请求读磁盘**，所以换文件即可生效，
#   不需要重启（重启反而会中断正在跑的判读）。
#
# 用法（服务器上）：
#   bash scripts/ops/deploy_ui.sh /tmp/ui_dist.tar.gz
#
# 安全约束：
#   1. **先备份**现有 ui 目录（打 tar 到 ~/uai/backups/），备份失败即中止；
#   2. 只删 `app/static/ui` 内的内容，不碰其它目录；
#   3. 复制后校验：index.html 存在、且引用了 `/ui/assets/`（基路径错了就说明构建方式不对）。

set -euo pipefail

SRC="${1:-}"
UI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/app/static/ui"
BACKUP_DIR="$HOME/uai/backups"
TS="$(date +%Y%m%d_%H%M%S)"

if [[ -z "$SRC" || ! -f "$SRC" ]]; then
  echo "[FATAL] 用法：bash scripts/ops/deploy_ui.sh <dist.tar.gz>" >&2
  exit 2
fi

mkdir -p "$BACKUP_DIR"
if [[ -d "$UI_DIR" ]]; then
  BK="$BACKUP_DIR/ui_$TS.tar.gz"
  tar -czf "$BK" -C "$(dirname "$UI_DIR")" "$(basename "$UI_DIR")"
  if [[ ! -s "$BK" ]]; then
    echo "[FATAL] 备份为空，拒绝继续" >&2
    exit 3
  fi
  echo "[OK] 已备份现有前端：$BK"
fi

mkdir -p "$UI_DIR"
find "$UI_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
tar -xzf "$SRC" -C "$UI_DIR"

if [[ ! -f "$UI_DIR/index.html" ]]; then
  echo "[FATAL] 解包后没有 index.html" >&2
  exit 4
fi
if ! grep -q '/ui/assets/' "$UI_DIR/index.html"; then
  echo "[WARN] index.html 里没有 /ui/assets/ —— 构建时可能漏了 VITE_BASE=/ui/，页面会 404" >&2
fi

echo "[OK] 文件数：$(find "$UI_DIR" -type f | wc -l)，大小：$(du -sh "$UI_DIR" | cut -f1)"
echo "[OK] 校验："
curl -s -o /dev/null -w "  /ui/        -> %{http_code}\n" http://127.0.0.1:18000/ui/ || true
ASSET="$(grep -o '/ui/assets/[^"]*\.js' "$UI_DIR/index.html" | head -1 || true)"
if [[ -n "$ASSET" ]]; then
  curl -s -o /dev/null -w "  $ASSET -> %{http_code}\n" "http://127.0.0.1:18000$ASSET" || true
fi
echo "[OK] 完成（无需重启：静态文件按请求读取）"
