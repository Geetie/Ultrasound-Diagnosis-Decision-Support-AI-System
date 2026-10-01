#!/usr/bin/env bash
set -u
TOKEN="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"
echo "=== ThyroidDataSet gate test"
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 30 -H "Authorization: Bearer $TOKEN" "https://hf-mirror.com/api/datasets/Doul0414/ThyroidDataSet/tree/main/data")
echo "http=$code"
echo "=== TN3K tree"
curl -s --max-time 30 "https://hf-mirror.com/api/datasets/haifan-gong/TN3K/tree/main/trainval-image" | head -c 200; echo
echo "=== TN3K git clone test"
cd /tmp || exit 1
rm -rf tn3k_probe
GIT_LFS_SKIP_SMUDGE=1 timeout 120 git clone --depth 1 "https://hf-mirror.com/datasets/haifan-gong/TN3K" tn3k_probe 2>&1 | tail -2
ls /tmp/tn3k_probe 2>/dev/null
