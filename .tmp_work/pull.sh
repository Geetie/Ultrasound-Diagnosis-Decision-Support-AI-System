#!/bin/bash
# 分段拉取远端文件（单次连接只能稳定传 ~96KB）
set -u
REMOTE="$1"; LOCAL="$2"; SIZE="${3:-48k}"
: > "$LOCAL"
off=0
total=$(ssh student03@10.20.28.21 "stat -c %s $REMOTE")
echo "total=$total"
while [ "$off" -lt "$total" ]; do
  chunk=$(ssh student03@10.20.28.21 "tail -c +$((off+1)) $REMOTE | head -c $SIZE" | base64 -w0 2>/dev/null || true)
  n=$(printf '%s' "$chunk" | base64 -d 2>/dev/null | wc -c)
  if [ "$n" -eq 0 ]; then echo "fail at $off"; exit 1; fi
  printf '%s' "$chunk" | base64 -d >> "$LOCAL"
  off=$((off+n))
  echo -n "."
done
echo
echo "got $(stat -c %s "$LOCAL" 2>/dev/null || wc -c < "$LOCAL")"
