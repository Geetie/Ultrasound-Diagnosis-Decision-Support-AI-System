#!/bin/bash
# 服务器侧差异盘点：本机 HEAD 的 git archive 已铺在 /tmp/sync_stage/
# 与 ~/uai/src 对比，排除运行时目录与行尾差异
set -u
SRC="$HOME/uai/src"
STAGE="/tmp/sync_stage"

DIFFS=$(diff -rq "$STAGE" "$SRC" \
  -x '.git' -x 'node_modules' -x '__pycache__' -x '*.pyc' \
  -x 'reports' -x 'data' -x 'logs' -x 'venv' -x 'models' -x 'kb_docs' \
  -x '.pytest_cache' -x 'htmlcov' -x '.env' 2>/dev/null | grep -v '^Only in '"$SRC"'')
echo "=== 内容不同的文件 ==="
echo "$DIFFS" | grep '^Files' | sed "s|$STAGE/||; s| and .*||; s|Files ||" || echo "(none)"
echo ""
echo "=== 仅本机 HEAD 有（服务器缺）==="
echo "$DIFFS" | grep "^Only in $STAGE" | sed "s|$STAGE/||" | head -30 || echo "(none)"
echo ""
echo "=== 逐文件差异规模（前 20）==="
echo "$DIFFS" | grep '^Files' | sed "s|$STAGE/||; s| and .*||; s|Files ||" | while read -r f; do
  n=$(diff "$STAGE/$f" "$SRC/$f" 2>/dev/null | grep -c '^[<>]')
  # 忽略纯行尾差异：把 \r 去掉后再比
  real=$(diff <(tr -d '\r' < "$STAGE/$f") <(tr -d '\r' < "$SRC/$f") 2>/dev/null | grep -c '^[<>]')
  echo "$f: raw=$n real=$real"
done | head -25
