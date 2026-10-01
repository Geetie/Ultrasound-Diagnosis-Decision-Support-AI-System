#!/usr/bin/env bash
set -u
ls -la "$HOME/uai/data/us_datasets/" | grep zip
pgrep -c curl 2>/dev/null || echo curl-done
