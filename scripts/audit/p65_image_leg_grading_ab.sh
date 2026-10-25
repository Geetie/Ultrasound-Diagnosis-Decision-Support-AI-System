#!/usr/bin/env bash
# p65 影像腿判读质量 A/B —— 真机驱动（**在远端 bash 下跑，勿在 fish 下跑**）
#
# 三臂：off → on → off2，每臂之间硬性做四件事（顺序不可换）：
#   ① 改 ~/uai/.env 的 RAG_USE_MULTIMODAL
#   ② **重启 18000**（pkill 与重启必须分两条命令 —— AGENTS.md 硬规则 2）
#   ③ 读 /api/v1/health 的 services.image_leg.enabled 确认**生效值**
#   ④ 清 Redis 的 kb:cache:* / rag:cache:*（否则 off 臂写进 L1 的纯文本块
#      会让 on 臂直接命中缓存 → 检索根本不跑 → 假阴性）
#
# 收尾**无条件**恢复 RAG_USE_MULTIMODAL=False 并重启（trap EXIT 保证异常也恢复）。
set -u

UAI="$HOME/uai"
SRC="$UAI/src"
ENV_FILE="$UAI/.env"
PY="$UAI/venv/bin/python"
SCRIPT="$SRC/scripts/audit/p65_image_leg_grading_ab.py"
LOG="$UAI/logs/p65_ab_driver.log"
LIMIT="${LIMIT:-12}"

mkdir -p "$UAI/reports" "$UAI/logs"
: > "$LOG"

# .env 备份：收尾按**原样**恢复（不是简单写回 False——原文件里本来就没有这个键）
ENV_BACKUP="$UAI/.env.p65_backup"
cp -p "$ENV_FILE" "$ENV_BACKUP"

say() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

# ---------- 开关写入（幂等；无该键则追加）----------
set_env_kv() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  fi
}

set_switch() {
  local want="$1"          # true | false
  local val="False"
  [ "$want" = "true" ] && val="True"
  set_env_kv RAG_USE_MULTIMODAL "$val"
  say "  .env → RAG_USE_MULTIMODAL=${val}"
}

# 证据链日志必须开在**服务进程**里（`_log_evidence_chain` 读的是服务端 os.environ），
# 只给客户端脚本 export 是没用的。收尾靠整份 .env 还原清掉。
enable_evidence_chain_log() {
  set_env_kv UAI_EVIDENCE_CHAIN_DEBUG 1
  say "  .env → UAI_EVIDENCE_CHAIN_DEBUG=1（拿真实 prompt 块清单）"
}

# ---------- 重启 18000（**单条命令内只做重启，不做 pkill 模式匹配自身**）----------
restart_main() {
  local f="$SRC/scripts/_restart_main_std.sh"
  if [ ! -f "$f" ]; then
    say "  [FATAL] 缺重启脚本 $f"
    return 1
  fi
  bash "$f" >/dev/null 2>&1
  # 等健康端点（最多 90s）
  local i
  for i in $(seq 1 45); do
    if curl -s -m 3 http://127.0.0.1:18000/api/v1/health >/dev/null 2>&1; then
      say "  18000 健康（等待 ${i}×2s）"
      return 0
    fi
    sleep 2
  done
  say "  [FATAL] 18000 90s 内未起来"
  return 1
}

# ---------- 核验**进程内**生效值（不信 .env 文本）----------
verify_switch() {
  local want="$1"          # true | false（小写）
  local got
  got=$(curl -s -m 10 http://127.0.0.1:18000/api/v1/health \
        | "$PY" -c 'import json,sys;d=json.load(sys.stdin);print(d.get("services",{}).get("image_leg",{}).get("enabled"))' \
        2>/dev/null)
  say "  health.image_leg.enabled = ${got}（期望 ${want}）"
  # Python 的 bool 打印是 True/False，与脚本里的小写 want 归一后比较
  local got_lc
  got_lc=$(printf '%s' "$got" | tr '[:upper:]' '[:lower:]')
  [ "$got_lc" = "$want" ] || return 1
  return 0
}

# ---------- 清 Redis 检索缓存 ----------
clear_caches() {
  local conn
  conn=$("$PY" - <<'PY'
from app.config.settings import settings
print(settings.redis_url)
PY
)
  [ -z "$conn" ] && { say "  [WARN] 取不到 redis_url，跳过缓存清理"; return 0; }
  local n=0
  for pat in 'kb:cache:*' 'rag:cache:*'; do
    local c
    c=$("$PY" - "$conn" "$pat" <<'PY'
import sys, redis
url, pat = sys.argv[1], sys.argv[2]
r = redis.Redis.from_url(url)
keys = list(r.scan_iter(match=pat, count=1000))
if keys:
    r.delete(*keys)
print(len(keys))
PY
)
    say "  清 ${pat} → ${c:-0} 个"
    n=$((n + ${c:-0}))
  done
  say "  缓存共清 ${n} 个键"
}

# ---------- 开证据链日志（拿到"prompt 里真的喂了哪几块"）----------
run_arm() {
  local arm="$1"
  say "=== 臂 ${arm} 开始 ==="
  # 必须在子 shell 里**重新 source .env**：`settings` 走的是 `env_file='.env'`（相对 CWD），
  # 而生产 .env 在 `$UAI/.env`、`$SRC/.env` 不存在 → 不 source 就会读到代码默认值
  # （rag_use_multimodal=False），on 臂会立刻因"开关未按预期生效"而失败。
  # 注意：这里 source 的是**服务进程同一份** .env，所以臂报告里的
  # `switch_effective_in_process` 与 health 报的 enabled 是同一个值。
  ( cd "$SRC" && set -a && . "$ENV_FILE" && set +a && \
    "$PY" "$SCRIPT" --mode arm --arm "$arm" --limit "$LIMIT" ) \
    2>&1 | tee -a "$LOG" | grep -E "^\s+\[[0-9]+\]|^= |^臂 |health\.image_leg|FATAL|未按预期"
  return "${PIPESTATUS[0]}"
}

finalize_switch() {
  say "=== 收尾：恢复 RAG_USE_MULTIMODAL=False（默认值）==="
  # 优先**逐字节还原**备份：原 .env 本来没有这个键，还原后 settings 走默认 False，
  # 既满足"恢复默认"，也不给 .env 留下一个本轮实验引入的键。
  if [ -f "$ENV_BACKUP" ]; then
    cp -p "$ENV_BACKUP" "$ENV_FILE"
    say "  .env 已按备份还原（b64sha=$(sha256sum "$ENV_FILE" | cut -c1-16)）"
  else
    set_switch false
  fi
  restart_main || say "  [ERROR] 恢复后重启失败，需人工介入！"
  verify_switch false || say "  [ERROR] 恢复后 health 仍报 true，需人工介入！"
  say "  .env: $(grep -n '^RAG_USE_MULTIMODAL=' "$ENV_FILE" || echo '(无该键 → 走 settings 默认 False)')"
  say "=== 收尾完成（原 .env 备份留在 $ENV_BACKUP）==="
}

trap finalize_switch EXIT

# ==================== 主流程 ====================
say "p65 A/B 开始（limit=$LIMIT）"
cd "$SRC" || exit 1
set -a; . "$ENV_FILE"; set +a
unset LD_LIBRARY_PATH
enable_evidence_chain_log

# 0) 钉样本（三臂读同一份，这是"同批"的机械保证）
say "--- 0) 钉样本 ---"
"$PY" "$SCRIPT" --mode pin --limit "$LIMIT" 2>&1 | tee -a "$LOG" || {
  say "[FATAL] 钉样本失败"; exit 2; }

# 0b) 检索层证据（不需要切开关；用于 P1b 与解释"块在哪一步消失"）
say "--- 0b) 检索层证据 probe ---"
"$PY" "$SCRIPT" --mode probe --limit "$LIMIT" 2>&1 | tee -a "$LOG" || \
  say "[WARN] probe 失败（不影响三臂，但 P1b 会缺证据）"

# ---- 臂 1：off（生产默认）----
set_switch false || exit 2
restart_main || exit 2
verify_switch false || { say "[FATAL] 开关未生效"; exit 2; }
clear_caches
run_arm off || say "[WARN] off 臂有失败例（见臂报告）"

# ---- 臂 2：on ----
set_switch true || exit 2
restart_main || exit 2
verify_switch true || { say "[FATAL] 开关未生效"; exit 2; }
clear_caches
run_arm on || say "[WARN] on 臂有失败例（见臂报告）"

# ---- 臂 3：off2（控制）----
set_switch false || exit 2
restart_main || exit 2
verify_switch false || { say "[FATAL] 开关未生效"; exit 2; }
clear_caches
run_arm off2 || say "[WARN] off2 臂有失败例（见臂报告）"

# ---- 聚合裁决（trap 随后会恢复开关）----
say "--- 聚合裁决 ---"
"$PY" "$SCRIPT" --mode aggregate 2>&1 | tee -a "$LOG"

say "p65 A/B 结束"
