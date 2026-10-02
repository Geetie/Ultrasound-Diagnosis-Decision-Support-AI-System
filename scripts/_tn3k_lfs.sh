#!/usr/bin/env bash
set -u
cd /tmp/tn3k_probe || exit 1
ls trainval-image/*.jpg 2>/dev/null | wc -l
ls trainval-image/*.jpg | xargs -P 8 -I{} sh -c "
  f=\"{}\"
  name=\$(basename \"\$f\")
  sz=\$(stat -c%s \"\$f\")
  if [ \"\$sz\" -lt 200 ]; then
    curl -sL --max-time 120 -o \"\$f\" \"https://hf-mirror.com/datasets/haifan-gong/TN3K/resolve/main/trainval-image/\$name\" || echo \"FAIL \$name\"
  fi
"
small=$(find trainval-image -name "*.jpg" -size -200c | wc -l)
total=$(ls trainval-image/*.jpg | wc -l)
echo "total=$total small=$small"
du -sh trainval-image
