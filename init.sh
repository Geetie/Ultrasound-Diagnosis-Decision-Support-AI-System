#!/usr/bin/env bash
# init.sh — harness 统一入口：环境自检 + 快速验证
#
# 用法：
#   ./init.sh            # 快检：环境 + harness/文档守卫测试 + ruff（约 1~2 分钟）
#   ./init.sh --full     # 全量：make test（单元 + INT 集成，约数分钟）
#   ./init.sh --skip-tests   # 只做环境自检（用于快速开工）
#
# 设计约束：fail-fast（set -e），任何一步失败即非零退出——不要用「跑完再说」的心态。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

MODE="quick"
for arg in "$@"; do
  case "$arg" in
    --full) MODE="full" ;;
    --skip-tests|--env-only) MODE="env" ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "未知参数：$arg"; exit 2 ;;
  esac
done

echo "=== Harness Initialization ($MODE) ==="
echo "repo: $ROOT"

# ---------------------------------------------------------------- 1. 环境自检
echo ""
echo "=== [1/4] 环境自检 ==="
command -v git >/dev/null 2>&1 || { echo "FAIL: 未找到 git"; exit 1; }
echo "HEAD: $(git rev-parse --short HEAD 2>/dev/null || echo 'n/a')  branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'n/a')"

PY=""
for candidate in "${PY:-}" python python3; do
  if [ -n "$candidate" ] && command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
[ -n "$PY" ] || { echo "FAIL: 未找到 python/python3"; exit 1; }
echo "python: $("$PY" -V 2>&1)  ($(command -v "$PY"))"

"$PY" -c "import pytest" >/dev/null 2>&1 || {
  echo "FAIL: 当前解释器缺 pytest。请先激活项目 venv 或：make install"; exit 1;
}
echo "pytest: ok"

# ------------------------------------------------------- 2. harness 状态文件自检
echo ""
echo "=== [2/4] harness 状态文件 ==="
missing=0
for f in AGENTS.md feature_list.json progress.md session-handoff.md; do
  if [ -f "$f" ]; then echo "  ok   $f"; else echo "  MISS $f"; missing=1; fi
done
[ "$missing" -eq 0 ] || { echo "FAIL: 状态文件缺失——先按 AGENTS.md 补齐"; exit 1; }
"$PY" -c "import json,sys; d=json.load(open('feature_list.json',encoding='utf-8')); assert isinstance(d.get('features'),list) and d['features']; print('  ok   feature_list.json features =', len(d['features']))"

if [ "$MODE" = "env" ]; then
  echo ""
  echo "=== 环境自检完成（--skip-tests） ==="
  echo "Next steps: 读 progress.md → 选一个 feature → 完成后跑 ./init.sh --full"
  exit 0
fi

# ------------------------------------------------------------ 3. 快速/全量验证
echo ""
if [ "$MODE" = "full" ]; then
  echo "=== [3/4] 全量测试 make test ==="
  make test
  echo ""
  echo "提示: 运行时守卫 make fe-smoke（真浏览器渲染冒烟）需本机 Chrome + 可达后端 +"
  echo "      凭据环境变量（UAI_DOCTOR_USER/UAI_DOCTOR_PASSWORD），环境具备时按需执行。"
else
  echo "=== [3/4] 快检（harness/文档质量守卫 + RAG 契约） ==="
  "$PY" -m pytest tests/unit/quality -q -p no:cacheprovider --no-header --tb=short
fi

# ------------------------------------------------------------------- 4. 静态检查
echo ""
echo "=== [4/4] 静态检查（ruff：app / safety_gateway / edge） ==="
"$PY" -m ruff check app safety_gateway edge --no-cache || {
  echo "WARN: ruff 报错。全量历史债见 make lint；本次改动文件不得新增 F/E9 级问题。"; exit 1;
}

echo ""
echo "=== Verification Complete ==="
echo ""
echo "Next steps:"
echo "1. 读 progress.md：Current State / Current Objective / Recommended Next Step"
echo "2. 读 feature_list.json：选 ONE 个 status 非 done 的 feature 开工（one feature at a time）"
echo "3. 完成后跑 ./init.sh --full，并留存 Verification Evidence（命令 + 输出）"
echo "4. 按 AGENTS.md 的 End of Session 更新 progress.md / feature_list.json / session-handoff.md（保证 clean / restartable）"
