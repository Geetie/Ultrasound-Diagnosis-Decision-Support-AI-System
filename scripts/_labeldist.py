import csv
from pathlib import Path

root = Path("/mnt/ee16/home/student03/uai/data/tn3k_dataset")
for name in ("label4trainval.csv", "label4test.csv"):
    from collections import Counter
    c = Counter()
    n = 0
    with open(root / name, encoding="utf-8") as f:
        for row in csv.reader(f):
            n += 1
            if len(row) >= 2:
                c[row[1].strip()] += 1
    print(name, "rows:", n, "| dist:", dict(c))
