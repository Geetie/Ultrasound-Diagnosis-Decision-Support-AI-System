"""T2a: kNN vs probe, baseline vs finetuned weights."""
import json
import random
from pathlib import Path

import numpy as np
import open_clip
import torch
import torch.nn.functional as F
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from torch.utils.data import DataLoader

import sys
sys.path.insert(0, "/mnt/ee16/home/student03/uai/src/scripts")
from t2a_finetune import CLASSES, AULDataset, stratified_split

CKPT_DIR = "/mnt/ee16/home/student03/uai/models/biomedclip-base-patch16-224"
FT_WEIGHTS = "/mnt/ee16/home/student03/uai/src/reports/t2a_finetune.pt"
DATA = "/mnt/ee16/home/student03/uai/data/aul"
device = "cuda"


def knn_auc(train_f, train_y, test_f, test_y, k=5):
    tr_mal = (train_y == 2).astype(float)
    sim = test_f @ train_f.T
    scores = [float(tr_mal[np.argsort(-row)[:k]].mean()) for row in sim]
    return roc_auc_score((test_y == 2).astype(int), scores)


def all_feats(m, sets):
    m.eval()
    out = []
    with torch.no_grad():
        for ds in sets:
            dl = DataLoader(ds, batch_size=64, num_workers=8, pin_memory=True)
            fs, ys = [], []
            for imgs, yb in dl:
                imgs = imgs.to(device, non_blocking=True)
                with torch.autocast("cuda", dtype=torch.bfloat16):
                    f = F.normalize(m.encode_image(imgs).float(), dim=-1)
                fs.append(f.cpu())
                ys.append(torch.tensor(yb))
            out.append((torch.cat(fs).numpy(), torch.cat(ys).numpy()))
    return out


random.seed(42)
uri = "local-dir:" + CKPT_DIR
model, _, preprocess = open_clip.create_model_and_transforms(uri, device=device)

samples = []
root = Path(DATA)
for ci, cls in enumerate(CLASSES):
    for img in sorted((root / cls / "image").glob("*")):
        if img.suffix.lower() in (".png", ".jpg", ".jpeg"):
            samples.append((img, ci))
train, _v, test = stratified_split(samples)
tr_ds = AULDataset(train, preprocess)
te_ds = AULDataset(test, preprocess)

out = {}
f_tr, y_tr, f_te, y_te = all_feats(model, [tr_ds, te_ds])
out["knn_baseline"] = round(knn_auc(f_tr, y_tr, f_te, y_te), 4)
clf = LogisticRegression(max_iter=2000, random_state=42)
clf.fit(f_tr, y_tr)
out["probe_baseline"] = round(roc_auc_score(
    (y_te == 2).astype(int), clf.predict_proba(f_te)[:, 2]), 4)

sd = torch.load(FT_WEIGHTS, map_location=device, weights_only=True)
model.visual.load_state_dict(sd)
f_tr, y_tr, f_te, y_te = all_feats(model, [tr_ds, te_ds])
out["knn_finetuned"] = round(knn_auc(f_tr, y_tr, f_te, y_te), 4)
clf = LogisticRegression(max_iter=2000, random_state=42)
clf.fit(f_tr, y_tr)
out["probe_finetuned"] = round(roc_auc_score(
    (y_te == 2).astype(int), clf.predict_proba(f_te)[:, 2]), 4)
out["gate"] = 0.75
Path("/mnt/ee16/home/student03/uai/src/reports/t2a_mech_compare.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False))
