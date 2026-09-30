#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
F="$UAI/src/scripts/server_consistency_vllm_single.py"
sed -i '40i\    use_guided = not getattr(args, "no_guided", False)' "$F"
sed -i 's/if schema else {}/if (schema and use_guided) else {}/' "$F"
sed -i '/--output", required=True/a\    parser.add_argument("--no-guided", action="store_true")' "$F"
"$UAI/venv/bin/python" -m py_compile "$F" && echo compile-ok
cd "$UAI/src"
nohup env PYTHONPATH=/mnt/ee16/home/student03/uai/src "$UAI/venv/bin/python" scripts/server_consistency_vllm_single.py --cases app/inference/data/consistency_test_cases.json --no-guided --output reports/p02_780_vllm_nonguided.json > ~/uai/logs/p02_780.log 2>&1 &
echo started
