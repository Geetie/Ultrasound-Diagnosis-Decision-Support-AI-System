import json
import random
from pathlib import Path
import pandas as pd

SRC = Path("/mnt/ee16/home/student03/uai/data/us365k/train.jsonl")
IMG_ROOT = Path("/mnt/ee16/home/student03/uai/data/us365k/images")
idx = {}
for sub in IMG_ROOT.iterdir():
    if sub.is_dir():
        for p in sub.glob("*.jpg"):
            idx[p.name] = p
rows = []
with open(SRC, encoding="utf-8") as f:
    for line in f:
        r = json.loads(line)
        p = idx.get(r["image"])
        if p and len(r.get("caption", "")) > 20:
            rows.append((str(p), r["caption"].replace("\n", " ")))
        if len(rows) >= 32000:
            break
random.seed(42)
random.shuffle(rows)
train, valid = rows[:30000], rows[30000:32000]
pd.DataFrame(train, columns=["filepath", "caption"]).to_csv(
    "/mnt/ee16/home/student03/uai/data/us365k/openclip_train_30k.csv", index=False)
pd.DataFrame(valid, columns=["filepath", "caption"]).to_csv(
    "/mnt/ee16/home/student03/uai/data/us365k/openclip_valid_2k.csv", index=False)
d = pd.read_csv("/mnt/ee16/home/student03/uai/data/us365k/openclip_train_30k.csv")
print("cols:", list(d.columns), "| rows:", len(d))
