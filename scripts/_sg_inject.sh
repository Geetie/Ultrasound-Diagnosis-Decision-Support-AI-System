#!/bin/bash
# 降级路径真机故障注入（06 号 §6.2 一期集成测试 7 场景的可注入子集）
# 探针：server_t2c_e2e_eval.py --sample 1（全链路诊断单例）
# 场景：S1 Redis 不可用→PG 降级；S3 SG 不可用→E4005；S5' Redis+SG 组合
# S2/S6（融合层故障）需代码级注入钩子——CI 侧 test_sg_degradation_e2e.py 已覆盖，真机不注
# S4（边缘端）未部署 N/A；S7（Redis 主从）单实例 N/A
set -u
UAI="$HOME/uai"
SRC="$UAI/src"
PROBE="$UAI/venv/bin/python $SRC/scripts/server_t2c_e2e_eval.py"
RPT="$SRC/reports/sg_inject_last.json"
probe() { cd "$SRC" && export PYTHONPATH="$SRC" && $PROBE --output "$RPT" --sample 1 --seed 7 2>&1 | tail -1; }

echo "=== 0. 基线 ==="
probe

echo "=== S1a. 停 uai-redis ==="
docker stop uai-redis
probe
echo "=== S1b. 恢复 redis ==="
docker start uai-redis
sleep 5
probe

echo "=== S3a. 停 SafetyGateway(9001) ==="
SGPID=$(pgrep -f 'safety_gateway.main' | head -1)
kill -9 "$SGPID" && echo "killed SG $SGPID"
sleep 2
probe
echo "=== S3b. 重启 SG ==="
cd "$SRC" && export PYTHONPATH="$SRC"
nohup "$UAI/venv/bin/python" -m uvicorn safety_gateway.main:app \
  --host 127.0.0.1 --port 9001 --log-level info \
  > "$UAI/logs/safety_gateway.log" 2>&1 &
for i in $(seq 1 20); do
  curl -s --max-time 3 http://127.0.0.1:9001/health | grep -q status && break
  sleep 3
done
curl -s --max-time 3 http://127.0.0.1:9001/health || echo "SG NOT READY"
probe

echo "=== S5a. 组合：redis + SG 同时停 ==="
docker stop uai-redis
SGPID=$(pgrep -f 'safety_gateway.main' | head -1)
kill -9 "$SGPID" && echo "killed SG $SGPID"
sleep 2
probe
echo "=== S5b. 全部恢复 ==="
docker start uai-redis
nohup "$UAI/venv/bin/python" -m uvicorn safety_gateway.main:app \
  --host 127.0.0.1 --port 9001 --log-level info \
  > "$UAI/logs/safety_gateway.log" 2>&1 &
sleep 8
probe

echo "=== 终态 ==="
curl -s -o /dev/null -w '18000=%{http_code} ' --max-time 3 http://127.0.0.1:18000/api/v1/health
curl -s -o /dev/null -w '9001=%{http_code}\n' --max-time 3 http://127.0.0.1:9001/health
docker ps --format '{{.Names}} {{.Status}}' | grep uai
echo "=== DONE ==="
