#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
grep -E 'kbv11-liver|evidence_insufficient|pgvector_search|knowledge_cache|bm25_fallback' "$UAI/logs/api.log" | tr -d '\000' | tail -14 | cut -c1-180
