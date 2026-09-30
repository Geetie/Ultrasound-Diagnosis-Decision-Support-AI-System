#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
WEB=$(docker ps --format '{{.Names}}' | grep langfuse-web | head -1)
SK=$(docker exec "$WEB" printenv LANGFUSE_INIT_PROJECT_SECRET_KEY 2>/dev/null)
AUTH=$(printf 'pk-lf-uai-prod:%s' "$SK" | base64 -w0)
BODY='{"resourceSpans":[{"resource":{"attributes":[]},"scopeSpans":[{"scope":{"name":"probe"},"spans":[{"traceId":"0102030405060708090a0b0c0d0e0f10","spanId":"0102030405060708","name":"probe-span","kind":1,"startTimeUnixNano":"1700000000000000000","endTimeUnixNano":"1700000001000000000","attributes":[]}]}]}]}'
echo "=== HTTP OTLP JSON 探针 ==="
curl -s -o /tmp/otlp_resp.txt -w "http_code=%{http_code}\n" --max-time 8 \
  -X POST "http://127.0.0.1:30100/api/public/otel/v1/traces" \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic $AUTH" \
  --data-binary "$BODY"
head -c 200 /tmp/otlp_resp.txt
echo
