import csv
from collections import Counter
from pathlib import Path

TV_ROOT = Path("/mnt/ee16/home/student03/uai/data/tn3k_dataset")
tv_labels = {}
with open(TV_ROOT / "label4trainval.csv", encoding="utf-8") as f:
    for row in csv.reader(f):
        if len(row) == 2 and row[1] in ("0", "1"):
            tv_labels[row[0]] = int(row[1])
test_names = set()
with open(TV_ROOT / "label4test.csv", encoding="utf-8") as f:
    for row in csv.reader(f):
        if len(row) == 2:
            test_names.add(row[0])
train_rows = []
for name, label in tv_labels.items():
    if name in test_names:
        continue
    p = TV_ROOT / "trainval-image" / name
    if p.exists() and p.stat().st_size > 200:
        train_rows.append({"label": label})
print("rows:", len(train_rows), "dist:", dict(Counter(r["label"] for r in train_rows)))
