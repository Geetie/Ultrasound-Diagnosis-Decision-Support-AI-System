#!/bin/bash
set -u
UAI="$HOME/uai"
SRC="$UAI/src"
cd "$SRC"
export PYTHONPATH="$SRC"
"$UAI/venv/bin/python" - <<'EOF'
import json
import sys
sys.path.insert(0, '/mnt/ee16/home/student03/uai/src')
from app.knowledge.vocabulary import UDAFVocabulary

v = UDAFVocabulary()
syns = sorted(v.get_all_synonyms())
print("同义词采样:", syns[:40])
print("方法:", [m for m in dir(v) if not m.startswith('_') and callable(getattr(v, m))])
for attr in ('_vocabulary', '_synonyms', '_terms', 'vocabulary'):
    if hasattr(v, attr):
        d = getattr(v, attr)
        print(f"attr {attr}: type={type(d).__name__} len={len(d) if hasattr(d,'__len__') else '?'}")
        if isinstance(d, dict) and d:
            keys = list(d.keys())[:6]
            print("  keys 采样:", keys)
            first = d[keys[0]]
            print("  value 样例:", json.dumps(first, ensure_ascii=False, default=str)[:300])
EOF
