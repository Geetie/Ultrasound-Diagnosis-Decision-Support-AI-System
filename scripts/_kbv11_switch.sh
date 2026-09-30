#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
# KB 版本切 v1.1.0 + 重启
python3 - <<'EOF'
from pathlib import Path
p = Path.home() / "uai/.env"
lines = p.read_text().splitlines()
key, val = "RAG_KB_VERSION", "kb_v1.1.0"
seen = False
out = []
for l in lines:
    if l.split("=", 1)[0].strip() == key:
        out.append(f"{key}={val}")
        seen = True
    else:
        out.append(l)
if not seen:
    out.append(f"{key}={val}")
p.write_text("\n".join(out) + "\n")
print("env ok")
EOF
grep RAG_KB_VERSION "$UAI/.env"
bash /tmp/_tperf_restart_main.sh 2>&1 | tail -1
