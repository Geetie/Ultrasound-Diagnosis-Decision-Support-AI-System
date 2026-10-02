#!/usr/bin/env bash
# 结构校验：两个薄服务的响应形状 + 主服务的 RAG 健康布尔
# **只输出形状/计数/状态码，不输出任何向量值、分数值或时延**
set -u
UAI="$HOME/uai"
PY="$UAI/venv/bin/python"

echo "=== 1) 服务健康 ==="
echo "bge-m3   : $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8004/health)"
echo "reranker : $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8005/health)"

echo
echo "=== 2) 嵌入形状（维度/条数，不打印数值）==="
curl -s --max-time 60 -X POST http://127.0.0.1:8004/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"texts":["低回声结节，边界清晰","TI-RADS 4A"],"return_dense":true,"return_sparse":false}' \
  | "$PY" -c 'import json,sys
d=json.load(sys.stdin)
v=d.get("dense_embeddings") or d.get("embeddings") or []
print("条目数:", len(v), "| 维度:", (len(v[0]) if v else 0), "| 模型:", d.get("model"), "| token_count 键存在:", "token_count" in d)'

echo
echo "=== 3) 重排形状（只打印 index 顺序，不打印分数）==="
curl -s --max-time 30 -X POST http://127.0.0.1:8005/v1/rerank \
  -H 'Content-Type: application/json' \
  -d '{"query":"甲状腺低回声结节","documents":["无关文本A","低回声结节边界清晰","无关文本B"],"top_k":3,"model":"bge-reranker-base"}' \
  | "$PY" -c 'import json,sys
d=json.load(sys.stdin)
r=d.get("results") or []
print("结果条数:", len(r), "| index 顺序:", [i.get("index") for i in r], "| 键集合:", sorted(r[0].keys()) if r else [])'

echo
echo "=== 4) 模型信息 ==="
curl -s --max-time 10 http://127.0.0.1:8004/v1/model/info
echo
curl -s --max-time 10 http://127.0.0.1:8005/v1/model/info
echo

echo
echo "=== 5) 主服务 RAG 健康布尔（重启后看）==="
tr -d '\000' < "$UAI/logs/api.log" > "$UAI/logs/api.clean.log"
start=$(grep -an '===== RESTART' "$UAI/logs/api.clean.log" | tail -1 | cut -d: -f1)
sed -n "${start:-1},\$p" "$UAI/logs/api.clean.log" | grep -aE 'rag_health|rag_service_health|agents_health_check|embedding|rerank' | tail -6
echo "=== check done ==="
