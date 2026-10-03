#!/usr/bin/env bash
# 修 8002 的 OpenAI 兼容端点后：重启 → 验端点 → 复测完整诊断链路
set -u
cd ~/uai/src
set -a; source ~/uai/.env; set +a
export PYTHONPATH=~/uai/src
say() { echo "[$(date +%H:%M:%S)] $*"; }

say "=== 1) 重启 8002（新代码）==="
bash ~/uai/src/scripts/server_start_vllm_engine.sh 2>&1 | tail -2
for i in $(seq 1 60); do
  sleep 3
  c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:8002/health 2>/dev/null)
  [ "$c" = "200" ] && { say "  8002 OK ($((i*3))s)"; break; }
done

say "=== 2) 验 /v1/chat/completions（主服务调的就是这个）==="
curl -s --max-time 60 -X POST http://127.0.0.1:8002/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen2.5-1.5B-Instruct","messages":[{"role":"user","content":"用一句话说明甲状腺低回声结节的临床意义"}],"max_tokens":60}' \
  | head -c 400; echo ""

say "=== 3) 验裸 /completions 仍可用（向后兼容）==="
curl -s --max-time 60 -X POST http://127.0.0.1:8002/completions \
  -H "Content-Type: application/json" \
  -d '{"prompt":"say OK","max_tokens":8}' | head -c 160; echo ""

say "=== 4) 重启主服务 ==="
pkill -9 -f "uvicorn app.main:app --host 0.0.0.0 --port 18000" 2>/dev/null || true
sleep 3
export CUDA_VISIBLE_DEVICES=0
setsid nohup ~/uai/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 18000 \
  --log-level warning > /tmp/uvicorn18000.log 2>&1 < /dev/null &
disown
for i in $(seq 1 40); do
  sleep 3
  c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:18000/api/v1/health 2>/dev/null)
  [ "$c" = "200" ] && { say "  18000 OK ($((i*3))s)"; break; }
done

say "=== 5) 复测完整链路（诊断意见是否真有 LLM 内容）==="
export NODULE_CONTOUR_IN_PIPELINE_ENABLED=1
IMG=$(ls ~/uai/data/tn3k_dataset/test-image/* 2>/dev/null | head -1)
IMG_PATH="$IMG" ~/uai/venv/bin/python ~/uai/src/scripts/deploy_drill_e2e.py 2>&1 | tail -8

say "=== 6) LLM 失败日志（应无 generation_failed）==="
n=$(grep -c "generation_failed" /tmp/uvicorn18000.log 2>/dev/null || echo 0)
say "  generation_failed count = $n"
grep -iE "generation_failed|llm_unavailable|specialist_llm_failed" /tmp/uvicorn18000.log 2>/dev/null \
  | tail -4 | cut -c1-170

say "DONE"
