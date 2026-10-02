#!/usr/bin/env bash
set -u
grep -n 't~\|seq\|for ' ~/server_link_smoke.sh | head -6
