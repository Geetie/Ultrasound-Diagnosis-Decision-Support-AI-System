#!/bin/bash
# UDT Phase 1 技术审定审计：词汇量/同义词/三器官覆盖/映射测试
set -u
UAI="$HOME/uai"
SRC="$UAI/src"
cd "$SRC"
export PYTHONPATH="$SRC"
"$UAI/venv/bin/python" - <<'EOF'
import sys
sys.path.insert(0, '/mnt/ee16/home/student03/uai/src')
from app.knowledge.vocabulary import UDAFVocabulary

v = UDAFVocabulary()
terms = v.get_all_terms() if hasattr(v, 'get_all_terms') else []
try:
    syns = v.get_all_synonyms()
except Exception:
    syns = []
print(f"标准术语数: {len(terms)}")
print(f"同义词数: {len(syns)}")
print(f"总计(去重前): {len(terms) + len(syns)}")

# 结构采样
sample = terms[:8] if terms else []
print("术语采样:", sample)

# 分级/器官相关字段探测
import inspect
src = inspect.getsource(type(v)._initialize_vocabulary)
for kw in ('thyroid', 'liver', 'cardiac', 'organ'):
    print(f"initialize_vocabulary 中 '{kw}' 出现: {src.lower().count(kw)} 次")
EOF
echo "=== 映射相关单测 ==="
"$UAI/venv/bin/python" -m pytest tests/unit/rag/test_tokenizer_utils.py tests/unit/rag/test_udaf_posterior_acoustic.py -q 2>&1 | tail -3
