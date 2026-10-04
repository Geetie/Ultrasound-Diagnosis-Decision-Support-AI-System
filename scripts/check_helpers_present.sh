#!/usr/bin/env bash
# 清理前先确认门禁依赖的助手脚本已在正式目录（否则清理完门禁会坏）
set -u
for f in _tok.py _pick_report.py _tamper.py; do
  if [ -f ~/uai/src/scripts/$f ]; then
    echo "  OK   ~/uai/src/scripts/$f"
  else
    echo "  MISS ~/uai/src/scripts/$f  （从 /tmp 补）"
    [ -f /tmp/$f ] && cp /tmp/$f ~/uai/src/scripts/$f && echo "       copied"
  fi
done
