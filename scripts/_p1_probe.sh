#!/usr/bin/env bash
set -u
curl -s --connect-timeout 15 -L "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=3" -o /tmp/rels.json
"$HOME/uai/venv/bin/python" -c "
import json
for rel in json.load(open('/tmp/rels.json')):
    assets = rel.get('assets', [])
    if not assets:
        continue
    print('tag:', rel['tag_name'], '| assets:', len(assets))
    for a in assets:
        n = a['name']
        if 'linux' in n.lower() or 'ubuntu' in n.lower():
            print('  ', n, f\"{a['size']/1e6:.0f}MB\")
    break
"
echo "=== nvcc ==="
which nvcc && nvcc --version | tail -1 || echo no-nvcc
which cmake || echo no-cmake
