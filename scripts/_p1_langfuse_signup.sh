#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
echo "=== Langfuse signup（创建 org/project/keys）==="
BODY='{"name":"uai-admin","email":"admin@uai.local","password":"UaiLangfuse2026!"}'
SIGNUP=$(curl -s --max-time 20 -X POST http://127.0.0.1:30100/api/public/auth/signup -H 'Content-Type: application/json' -d "$BODY")
echo "$SIGNUP" | "$UAI/venv/bin/python" -c "
import json, sys
d = json.load(sys.stdin)
keys = d.get('publicKey') and {'public': d['publicKey'], 'secret': d['secretKey']}
if keys:
    open('/mnt/ee16/home/student03/uai/langfuse/keys.json', 'w').write(json.dumps(keys))
    print('keys saved')
else:
    print('resp:', json.dumps(d, ensure_ascii=False)[:300])"
