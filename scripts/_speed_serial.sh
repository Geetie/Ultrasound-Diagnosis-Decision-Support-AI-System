#!/usr/bin/env bash
set -u
for svc in 8002:Qwen2.5-7B-Instruct 8006:Qwen3-8B; do
  port=${svc%%:*}; model=${svc##*:}
  body=$(printf '{"model":"%s","messages":[{"role":"user","content":"根据参考知识用两句话回答：甲状腺癌手术方式有哪些？参考知识：甲状腺癌手术包括腺叶切除与全切，中央区淋巴结清扫适用于怀疑转移者。"}],"max_tokens":200,"temperature":0.0,"chat_template_kwargs":{"enable_thinking":false}}' "$model")
  out=""
  for i in 1 2 3 4 5; do
    t=$(curl -s -o /dev/null -w '%{time_total}' --max-time 30 \
      "http://127.0.0.1:$port/v1/chat/completions" \
      -H 'Content-Type: application/json' -d "$body")
    out="$out $t"
  done
  echo "$model:$out"
done
