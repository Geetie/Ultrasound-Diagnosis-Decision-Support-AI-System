#!/bin/bash
set -u
echo "=== 完整元数据（许可/标签）==="
curl -s --max-time 15 "https://hf-mirror.com/api/datasets/Doul0414/ThyroidDataSet" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for k in ('id', 'gated', 'disabled', 'lastModified', 'tags', 'description'):
    v = d.get(k)
    if k == 'description' and v:
        v = v.strip()[:600]
    print(f'{k}: {v}')"
echo ""
echo "=== 文件清单（tree）==="
curl -s --max-time 15 "https://hf-mirror.com/api/datasets/Doul0414/ThyroidDataSet/tree/main/data" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    for f in d:
        print(f\"{f.get('size',0)/1e6:.1f}MB  {f.get('path','')}\")
except Exception as e:
    print('parse fail:', e)"
echo ""
echo "=== 服务器已有甲状腺数据盘点 ==="
echo "tn3k:"; find /mnt/ee16/home/student03/uai/data/tn3k_dataset -name '*.png' -o -name '*.jpg' 2>/dev/null | wc -l
echo "aul:"; for d in Normal Benign Malignant; do echo -n "  $d: "; find "/mnt/ee16/home/student03/uai/data/aul/$d" -name '*.png' -o -name '*.jpg' 2>/dev/null | wc -l; done
echo "us365k images:"; find /mnt/ee16/home/student03/uai/data/us365k/images -type f 2>/dev/null | wc -l
