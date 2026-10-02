#!/bin/bash
# 对 47 个真实差异文件判定方向：服务器 mtime > 本机最后提交时间 → 服务器侧改过（需审查）
STAGE="/tmp/sync_stage"
SRC="$HOME/uai/src"
while IFS= read -r line; do
  case "$line" in
    Files*)
      f=$(echo "$line" | sed "s|^Files $STAGE/||; s| and .*||")
      if ! diff -q <(tr -d '\r' < "$STAGE/$f") <(tr -d '\r' < "$SRC/$f") >/dev/null 2>&1; then
        smt=$(stat -c %Y "$SRC/$f" 2>/dev/null || echo 0)
        sdate=$(date -d @$smt '+%m-%d %H:%M' 2>/dev/null)
        ssize=$(stat -c %s "$SRC/$f" 2>/dev/null)
        hsize=$(stat -c %s "$STAGE/$f" 2>/dev/null)
        echo "$f | srv_mtime=$sdate srv_size=$ssize head_size=$hsize"
      fi
      ;;
  esac
done < <(LC_ALL=C diff -rq "$STAGE" "$SRC" \
  -x '.git' -x 'node_modules' -x '__pycache__' -x '*.pyc' \
  -x 'reports' -x 'data' -x 'logs' -x 'models' -x 'kb_docs' \
  -x '.pytest_cache' -x 'htmlcov' -x '.venv' -x 'dist' 2>/dev/null)
