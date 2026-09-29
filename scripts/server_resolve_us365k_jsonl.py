"""把 US-365K jsonl 的裸文件名解析成绝对路径，输出 <name>.resolved.jsonl。

用法：python server_resolve_us365k_jsonl.py <jsonl> <images_dir>
仅保留能实际找到的条目（找不到的行跳过并在 stderr 计数）。
"""
import json
import sys
from pathlib import Path

jsonl, images_dir = sys.argv[1], Path(sys.argv[2])
out_path = Path(jsonl).with_suffix(".resolved.jsonl")

index: dict[str, Path] = {}
for p in images_dir.rglob("*.jpg"):
    index[p.name] = p
for p in images_dir.rglob("*.png"):
    index[p.name] = p

kept = missing = 0
with open(jsonl, encoding="utf-8") as fin, open(
    out_path, "w", encoding="utf-8"
) as fout:
    for line in fin:
        row = json.loads(line)
        resolved = index.get(row["image"])
        if resolved is None:
            missing += 1
            continue
        row["image"] = str(resolved)
        fout.write(json.dumps(row, ensure_ascii=False) + "\n")
        kept += 1

print(f"kept={kept} missing={missing} -> {out_path}")
