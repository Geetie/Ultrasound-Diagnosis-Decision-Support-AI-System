#!/usr/bin/env bash
set -u
TOKEN="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"
echo "=== label4trainval.csv 结构"
head -5 /tmp/tn3k_probe/label4trainval.csv 2>/dev/null
wc -l /tmp/tn3k_probe/label4trainval.csv /tmp/tn3k_probe/label4test.csv 2>/dev/null
echo "=== ThyroidDataSet 4 ZIP 服务器直下（后台）"
mkdir -p "$HOME/uai/data/us_datasets"
cd "$HOME/uai/data/us_datasets" || exit 1
for name in TN3K.zip TN5000.zip DDTI_processed.zip PKTN.zip; do
  if [ ! -f "$name" ]; then
    nohup curl -sL --max-time 900 -H "Authorization: Bearer $TOKEN" \
      -o "$name" "https://hf-mirror.com/datasets/Doul0414/ThyroidDataSet/resolve/main/data/$name" &
  fi
done
echo "downloads-started"
