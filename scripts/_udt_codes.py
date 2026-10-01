import re
from pathlib import Path

# 从 consistency.py 提取全部 UDT 码（键 + 列表值 + primary_codes 元组）
src = Path("app/fusion/consistency.py").read_text(encoding="utf-8")
codes = set(re.findall(r'"(thyroid_[a-z_]+|liver_[a-z_]+|cardiac_[a-z_]+)"', src))
print("consistency.py UDT codes:", len(codes))
for c in sorted(codes):
    print(" ", c)
# 其他文件中的 disease_code 赋值
for p in Path("app").rglob("*.py"):
    if p.name == "consistency.py":
        continue
    t = p.read_text(encoding="utf-8", errors="ignore")
    for m in re.findall(r'disease_code\s*=\s*"([a-z_]+)"', t):
        if m not in codes:
            print("EXTRA:", p.name, m)
            codes.add(m)
print("total:", len(codes))
