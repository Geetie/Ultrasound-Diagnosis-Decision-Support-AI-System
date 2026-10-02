#!/usr/bin/env bash
# 探测 llama-cpp-python 的可用安装源（PyPI 官方 wheel 是 CPU-only；
# CUDA 版本只在 abetlen 的预编译 wheel 索引里）。只打印可达性与状态码。
set -u
UAI="$HOME/uai"

echo "=== 是否已安装 ==="
"$UAI/venv/bin/pip" show llama-cpp-python 2>/dev/null | head -3 || echo "not installed"

echo
echo "=== 各源可达性（HTTP 状态码；000=不可达）==="
for url in \
  "https://abetlen.github.io/llama-cpp-python/whl/cu124/" \
  "https://abetlen.github.io/llama-cpp-python/whl/cu122/" \
  "https://abetlen.github.io/llama-cpp-python/whl/cpu/" \
  "https://github.com/abetlen/llama-cpp-python" \
  "https://pypi.tuna.tsinghua.edu.cn/simple/llama-cpp-python/" \
  "https://pypi.org/simple/llama-cpp-python/"
do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 12 "$url" 2>/dev/null || true)
  echo "  ${code:-000}  $url"
done

echo
echo "=== abetlen 索引里是否有 cp312 wheel（只看有没有，不下载）==="
curl -s --max-time 20 "https://abetlen.github.io/llama-cpp-python/whl/cu124/llama-cpp-python/" 2>/dev/null \
  | grep -o 'llama_cpp_python-[^"<]*cp312[^"<]*' | head -5 || echo "  (无响应/无 cp312)"
echo "=== done ==="
