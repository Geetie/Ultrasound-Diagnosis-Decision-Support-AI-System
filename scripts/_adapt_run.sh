#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
bash -c "export CUDA_VISIBLE_DEVICES=0; cd $UAI/src && PYTHONPATH=$UAI/src $UAI/venv/bin/python scripts/t2a_domain_adapt.py 2>&1" | tr -d "\000" > "$UAI/logs/t2a_adapt.log"
tail -8 "$UAI/logs/t2a_adapt.log"
