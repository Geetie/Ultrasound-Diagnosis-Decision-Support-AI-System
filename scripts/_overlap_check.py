import csv
from pathlib import Path

TV_ROOT = Path("/mnt/ee16/home/student03/uai/data/tn3k_dataset")

# TN3K 官方语义验证：label4test 的文件名在 label4trainval 中的标签对比
def load(p):
    d = {}
    with open(p, encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) == 2 and row[1] in ("0", "1"):
                d[row[0]] = int(row[1])
    return d

tv = load(TV_ROOT / "label4trainval.csv")
te = load(TV_ROOT / "label4test.csv")
overlap = set(tv) & set(te)
agree = sum(1 for n in overlap if tv[n] == te[n])
disagree = [(n, tv[n], te[n]) for n in overlap if tv[n] != te[n]][:5]
print("overlap:", len(overlap), "| label agree:", agree, "| disagree:", len(disagree))
print("disagree sample:", disagree)
