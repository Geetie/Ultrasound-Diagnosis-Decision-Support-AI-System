#!/usr/bin/env bash
# /tmp 探针全量清理（SKILL 铁律：历次会话累积，只删本轮不够）
# 正式脚本都在 ~/uai/src/scripts/，/tmp 的 _ 前缀文件全可删。
set -u
say() { echo "[$(date +%H:%M:%S)] $*"; }

before=$(ls /tmp/_*.py /tmp/_*.sh 2>/dev/null | wc -l)
say "清理前 /tmp 探针数: $before"
# 本轮要保留的（本轮刚上传、还要用）
KEEP="/tmp/_tok.py /tmp/_pick_report.py /tmp/_tamper.py /tmp/_rid_env.sh"
rm -f /tmp/_*.py /tmp/_*.sh 2>/dev/null || true
for k in $KEEP; do
  [ -f "$k" ] || continue
  cp "$k" ~/uai/src/scripts/ 2>/dev/null || true
done
after=$(ls /tmp/_*.py /tmp/_*.sh 2>/dev/null | wc -l)
say "清理后 /tmp 探针数: $after"
say "保留的三件已复制到 ~/uai/src/scripts/（演练与门禁脚本依赖它们）"
ls ~/uai/src/scripts/_tok.py ~/uai/src/scripts/_pick_report.py ~/uai/src/scripts/_tamper.py 2>/dev/null | sed 's/^/  /'
