#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
echo trainval:
cut -d, -f2 "$UAI/data/tn3k_dataset/label4trainval.csv" | sort | uniq -c
echo test:
cut -d, -f2 "$UAI/data/tn3k_dataset/label4test.csv" | sort | uniq -c
