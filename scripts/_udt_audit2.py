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
print("=== 按器官 ===")
for organ in ("thyroid", "liver", "cardiac"):
    try:
        terms = v.get_terms_by_organ(organ)
        n_syn = sum(len(t.get("synonyms", [])) if isinstance(t, dict) else 1 for t in terms) if isinstance(terms, list) else len(terms)
        print(f"{organ}: terms={len(terms) if hasattr(terms,'__len__') else '?'} 附加同义词≈{n_syn}")
        if isinstance(terms, list) and terms:
            print("  样例:", json.dumps(terms[0], ensure_ascii=False, default=str)[:220])
    except Exception as e:
        print(f"{organ}: ERR {e}")

print("=== 按分类 ===")
try:
    cats = v.get_terms_by_category()
    if isinstance(cats, dict):
        for k, val in list(cats.items())[:15]:
            print(f"  {k}: {len(val) if hasattr(val, '__len__') else val}")
except Exception as e:
    print("ERR", e)

print("=== 覆盖率 ===")
for probe in (["甲状腺低回声结节", "TI-RADS 4a"], ["肝右叶稍高回声团块", "LI-RADS 4"],
              ["左室壁运动减低", "EF 40%"]):
    try:
        print(probe, "->", v.coverage_rate(probe))
    except Exception as e:
        print(probe, "ERR", e)
EOF
