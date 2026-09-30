#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
cd "$UAI/models"
URL="https://github.com/ggml-org/llama.cpp/releases/download/b11272/llama-b11272-bin-ubuntu-cuda-13.4-x64.tar.gz"
echo "下载 $URL"
curl -sL --max-time 900 -o llama-server.tar.gz "$URL"
ls -la llama-server.tar.gz
tar xzf llama-server.tar.gz
ls
find . -maxdepth 2 -name 'llama-server' -o -maxdepth 2 -name 'llama-cli' | head -3
