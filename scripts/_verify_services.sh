#!/bin/bash
# Task 6: verify all services after restart
echo "== 18000 主服务 =="
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18000/api/v1/health
echo "== 8002 vLLM 主生成 =="
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8002/health
echo "== 8003 嵌入 =="
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8003/health
echo "== 8006 判官 =="
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8006/health
echo "== 8004 bge-m3 =="
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8004/health
echo "== 8005 reranker =="
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8005/health
echo "== 9001 SafetyGateway =="
curl -s http://127.0.0.1:9001/health | head -c 200
echo
echo "== vocab extended load in api.log =="
tr -d '\000' < ~/uai/logs/api.log | grep -o 'extended_vocab[a-z_]*[^,}]*' | head -5
tr -d '\000' < ~/uai/logs/api.log | grep -c 'hallucination' || true
