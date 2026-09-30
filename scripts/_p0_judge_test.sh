#!/usr/bin/env bash
set -u
"$HOME/uai/venv/bin/python" - <<'EOF'
import json
import urllib.request

body = json.dumps({
    "messages": [
        {
            "role": "user",
            "content": (
                "Given the ultrasound finding report (context) and an AI diagnosis "
                "conclusion (statement), judge ONLY whether the statement is entailed "
                'by the context. Reply with a single JSON: {"verdict": 1 or 0, '
                '"reason": "<=40 words"} /no_think\n\n'
                "Context: 甲状腺右叶低回声结节，边界清晰，形态规则，考虑良性倾向。\n\n"
                "Statement: 甲状腺右叶可见低回声结节，边界清，倾向良性。"
            ),
        }
    ],
    "max_tokens": 256,
    "temperature": 0,
}).encode()
req = urllib.request.Request(
    "http://127.0.0.1:8006/v1/chat/completions",
    data=body,
    headers={"Content-Type": "application/json"},
)
resp = json.load(urllib.request.urlopen(req, timeout=120))
text = resp["choices"][0]["message"]["content"]
print("raw:", repr(text[:250]))
EOF
