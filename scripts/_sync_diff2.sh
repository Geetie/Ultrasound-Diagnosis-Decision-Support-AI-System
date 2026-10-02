#!/bin/bash
set -u
STAGE="/tmp/sync_stage"
SRC="$HOME/uai/src"

REAL=()
CRLF_ONLY=0
ONLY_SERVER=()
ONLY_STAGE=()

while IFS= read -r line; do
  case "$line" in
    Files*)
      f=$(echo "$line" | sed "s|^Files $STAGE/||; s| and .*||")
      if diff -q <(tr -d '\r' < "$STAGE/$f") <(tr -d '\r' < "$SRC/$f") >/dev/null 2>&1; then
        CRLF_ONLY=$((CRLF_ONLY+1))
      else
        REAL+=("$f")
      fi
      ;;
    "Only in $STAGE"*)
      f=$(echo "$line" | sed "s|^Only in $STAGE: ||; s|^Only in $STAGE/||")
      ONLY_STAGE+=("$f")
      ;;
    "Only in $SRC"*)
      f=$(echo "$line" | sed "s|^Only in $SRC: ||; s|^Only in $SRC/||")
      ONLY_SERVER+=("$f")
      ;;
  esac
done < <(diff -rq "$STAGE" "$SRC" \
  -x '.git' -x 'node_modules' -x '__pycache__' -x '*.pyc' \
  -x 'reports' -x 'data' -x 'logs' -x 'models' -x 'kb_docs' \
  -x '.pytest_cache' -x 'htmlcov' -x '.venv' -x 'dist' 2>/dev/null)

echo "真实差异(非行尾): ${#REAL[@]}  仅行尾差异: $CRLF_ONLY  仅服务器有: ${#ONLY_SERVER[@]}  仅HEAD有: ${#ONLY_STAGE[@]}"
echo ""
echo "=== 真实差异文件清单 ==="
for f in "${REAL[@]}"; do echo "$f"; done
echo ""
echo "=== 仅服务器有（前 20）==="
for f in "${ONLY_SERVER[@]:0:20}"; do echo "$f"; done
echo ""
echo "=== 仅 HEAD 有（前 20）==="
for f in "${ONLY_STAGE[@]:0:20}"; do echo "$f"; done
