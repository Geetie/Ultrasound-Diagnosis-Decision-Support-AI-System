#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
WEB=$(docker ps --format '{{.Names}}' | grep langfuse-web | head -1)
SK=$(docker exec "$WEB" printenv LANGFUSE_INIT_PROJECT_SECRET_KEY 2>/dev/null)
AUTH=$(printf 'pk-lf-uai-prod:%s' "$SK" | base64 -w0)
echo "=== Langfuse traces API ==="
curl -s --max-time 8 "http://127.0.0.1:30100/api/public/traces?limit=5" \
  -H "Authorization: Basic $AUTH" > /tmp/lf_traces.json
"$UAI/venv/bin/python" -c "
import json
d = json.load(open('/tmp/lf_traces.json'))
items = d.get('data', d if isinstance(d, list) else [])
print('traces:', len(items))
for t in items[:5]:
    print(' -', t.get('id'), t.get('name'), t.get('timestamp'))
"
