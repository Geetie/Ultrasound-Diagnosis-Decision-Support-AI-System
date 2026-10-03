#!/usr/bin/env bash
# 服务器侧全量规范化 .sh 行尾（幂等）。仓库 blob 本身是 LF（core.autocrlf=true
# 只影响 Windows 工作区检出），服务器上的 CRLF 是历史 scp 直推留下的旧账。
set -u
SRC=~/uai/src
n=0
for f in $(find "$SRC" -name "*.sh" -type f); do
  if grep -qU $'\r' "$f" 2>/dev/null; then
    sed -i 's/\r$//' "$f"
    n=$((n + 1))
  fi
done
echo "normalized=$n"
bad=0
for f in $(find "$SRC" -maxdepth 2 -name "*.sh" -type f | head -25); do
  bash -n "$f" 2>/dev/null || { echo "SYNTAX_FAIL: $f"; bad=$((bad+1)); }
done
echo "syntax_checked=25 failed=$bad"
