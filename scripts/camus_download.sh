#!/usr/bin/env bash
# 下载 CAMUS-Lite（hf-mirror）并解压
set -u
D=~/uai/data/camus
mkdir -p "$D"
cd "$D"
BASE="https://hf-mirror.com/datasets/YongchengYAO/CAMUS-Lite/resolve/main"

for f in Images.zip Masks.zip; do
  if [ -f "$f" ]; then echo "have $f"; continue; fi
  echo "downloading $f ..."
  curl -sL -m 3600 -o "$f" "${BASE}/${f}" && echo "got $f ($(du -h "$f" | cut -f1))" || echo "FAIL $f"
done

for f in Images Masks; do
  if [ -d "$f" ]; then echo "extracted $f"; continue; fi
  [ -f "${f}.zip" ] && unzip -qo "${f}.zip" && echo "unzipped $f"
done

echo "=== 结果 ==="
for d in Images Masks; do
  [ -d "$d" ] && echo "$d: $(find "$d" -type f | wc -l) files, e.g. $(find "$d" -type f | head -2 | xargs -n1 basename | tr '\n' ' ')"
done
