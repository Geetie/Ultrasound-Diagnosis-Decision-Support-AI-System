#!/usr/bin/env bash
set -u
if [ -f /tmp/uai_final.tar ]; then
  tar -xf /tmp/uai_final.tar -C ~/uai/src && echo "EXTRACT_OK $(date +%H:%M:%S)"
  rm -f /tmp/uai_final.tar
else
  echo "TAR_MISSING"
fi
# 最终验证：门禁仍全绿
bash ~/uai/src/scripts/deploy_acceptance_gate.sh 2>&1 | tail -4
