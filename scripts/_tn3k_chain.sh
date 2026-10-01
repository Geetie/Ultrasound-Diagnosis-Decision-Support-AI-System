#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
pkill -f "port 8006" 2>/dev/null
pkill -f "VLLM::EngineCore" 2>/dev/null
sleep 4
bash -c "export CUDA_VISIBLE_DEVICES=0; $UAI/venv/bin/python /tmp/_tn3k_gen.py" 2>&1 | grep -vE "compact|info |UserWarning|ys.append" | tail -12
nohup bash /tmp/_deploy_dual_vllm.sh > "$UAI/logs/dual_deploy.log" 2>&1 &
echo "judge-restoring"
