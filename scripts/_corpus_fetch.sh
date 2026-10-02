#!/bin/bash
# 中文指南 PDF 抓取（faithfulness 语料路线 #5）
set -u
RAW="/mnt/ee16/home/student03/uai/data/corpus/raw"
mkdir -p "$RAW"
cd "$RAW"

fetch() {
  local name="$1"; shift
  local url="$1"; shift
  if [ -s "$name" ]; then echo "SKIP $name (exists)"; return; fi
  # TLS1.2 + 跳过校验（hbusqcc 类站点证书问题，既有姿势）
  code=$(curl -sL --tlsv1.2 -k --connect-timeout 15 --max-time 120 \
    -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" \
    -o "$name" -w '%{http_code}' "$url")
  size=$(stat -c%s "$name" 2>/dev/null || echo 0)
  head3=$(head -c 5 "$name" 2>/dev/null)
  echo "$name -> code=$code size=$size head=$head3"
  # 非 PDF 开头（%PDF）且失败则删
  if [ "$head3" != "%PDF-" ]; then rm -f "$name"; echo "  ^ not a PDF, removed"; fi
}

fetch thyroid_cancer_guideline_2022.pdf \
  "https://www.nhc.gov.cn/yzygj/c100068/202204/0c1f7d3aca0545abbeb02030ce255930/files/1732863055520_88237.pdf"

fetch ctirads_2021.pdf \
  "https://www.hbusqcc.com/attachment/2023112/1700536695184_09%20%E7%94%B2%E7%8A%B6%E8%85%BA%E7%BB%93%E8%8A%82%E8%B6%85%E5%A3%B0%E6%81%B6%E6%80%A7%E5%8D%B1%E9%99%A9%E5%88%86%E5%B1%82%E4%B8%AD%E5%9B%BD%E6%8C%87%E5%8D%97%EF%BC%9AC-TIRADS%EF%BC%882021%E7%89%88%EF%BC%89.pdf"

fetch liver_us_diagnosis_guideline_2021.pdf \
  "https://www.jhxrmyy.com/upfile/202110/2021101249899305.pdf"

echo "=== 当前 raw 目录 ==="
ls -la "$RAW"
