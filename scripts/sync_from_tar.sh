#!/usr/bin/env bash
set -u
if [ -f /tmp/uai_h2.tar ]; then
  tar -xf /tmp/uai_h2.tar -C ~/uai/src && echo "EXTRACT_OK"
  rm -f /tmp/uai_h2.tar
else
  echo "TAR_MISSING"
fi
ls -la ~/uai/src/scripts/drill_token.py ~/uai/src/scripts/drill_pick_report.py ~/uai/src/scripts/drill_tamper_check.py 2>/dev/null | awk '{print "  "$NF, $5"B"}'
