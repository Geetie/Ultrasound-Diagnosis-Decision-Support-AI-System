"""T2b 探针导出：用微调后 visual 在 train 集上拟合切面 LR + 质量 LinearRegression，
落盘 joblib 供 8003 /v1/analyze_view 生产端点消费（test 集不参与拟合）。

用法（真机 ~/uai/src）:
    ~/uai/venv/bin/python scripts/t2b_export_probes.py
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import joblib
import numpy as np
import open_clip
import torch
import torch.nn.functional as F
from sklearn.linear_model import LinearRegression, LogisticRegression
from torch.utils.data import DataLoader

sys.path.insert(0, str(Path(__file__).resolve().parent))
from t2b_cactus_train import STANDARD_VIEWS, CactusDataset, make_loader  # noqa: E402

DATA_DIR = Path("/mnt/ee16/home/student03/uai/data/t2b_cactus")
MODEL_DIR = Path("/mnt/ee16/home/student03/uai/models/biomedclip-base-patch16-224")
WEIGHTS = Path("/mnt/ee16/home/student03/uai/src/reports/t2b_cactus.pt")
OUT_DIR = Path("/mnt/ee16/home/student03/uai/models/t2b")


def main() -> int:
    device = "cuda"
    uri = "local-dir:" + str(MODEL_DIR)
    model, _, preprocess = open_clip.create_model_and_transforms(uri, device=device)
    sd = torch.load(WEIGHTS, map_location=device, weights_only=True)
    model.visual.load_state_dict(sd)
    model = model.to(device).eval()
    print("[t2b-export] 微调 visual 已加载", flush=True)

    rows = list(csv.DictReader(open(DATA_DIR / "train.csv", encoding="utf-8")))
    loader = make_loader(CactusDataset(rows, preprocess), 256, 8, False)

    feats: list[torch.Tensor] = []
    from io import BytesIO  # noqa: F401

    with torch.no_grad():
        for imgs, _caps in loader:
            imgs = imgs.to(device, non_blocking=True)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                f = model.encode_image(imgs)
            feats.append(F.normalize(f.float(), dim=-1).cpu())
    X = torch.cat(feats).numpy()
    print("[t2b-export] 特征:", X.shape, flush=True)

    views = [r["view"] for r in rows]
    lr = LogisticRegression(max_iter=2000)
    lr.fit(X, views)
    print("[t2b-export] view probe train acc:", round(float(lr.score(X, views)), 4), flush=True)

    idx = [i for i, r_ in enumerate(rows)
           if r_["view"] in STANDARD_VIEWS and int(r_["grade"]) >= 1]
    reg = LinearRegression().fit(X[idx], np.array([float(rows[i]["grade"]) for i in idx]))
    print("[t2b-export] quality reg R2 (train):",
          round(float(reg.score(X[idx], np.array([float(rows[i]["grade"]) for i in idx]))), 4),
          flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(lr, OUT_DIR / "view_probe.joblib")
    joblib.dump(reg, OUT_DIR / "quality_reg.joblib")
    print("[t2b-export] saved:", OUT_DIR, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
