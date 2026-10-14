"""TN3K benchmark v2 - probe arms (official split, per-model native preprocessing).

Arms:
  A1 BiomedCLIP probe  (8003 HTTP, native open_clip transform) x3 seeds
  A4 ULC visual probe  (offline HF cache, BiomedCLIP-identical transform) x3 seeds
  A3s EchoCare features (GPU/CPU, TN3K-official /255 norm, 256px) cached for finetune arm

Statistics: AUC primary; Acc via Youden threshold from TRAIN ROC; 3 seeds mean+-std.
v1 protocol (ImageNet norm, single run, fixed 0.5) is DEPRECATED.
"""
from __future__ import annotations

import csv
import json
import logging
import time
import urllib.request
import uuid
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

logger = logging.getLogger(__name__)

DATA = Path("/mnt/ee16/home/student03/uai/data/tn3k_dataset")
FEAT_DIR = Path("/mnt/ee16/home/student03/uai/reports/features_v2")
OUT = Path("/mnt/ee16/home/student03/uai/reports/tn3k_benchmark_v2.json")
ECHOCARE_PT = Path("/mnt/ee16/home/student03/uai/models/echocare/model.pt")
CLIP_URL = "http://127.0.0.1:8003/v1/embeddings/image"
SEEDS = [42, 43, 44]

FEAT_DIR.mkdir(parents=True, exist_ok=True)


def load_labels(p):
    with open(p, encoding="utf-8") as f:
        return [(n, int(ln)) for n, ln in csv.reader(f)]


def load_rows(sub, csv_name):
    rows = [(n, ln) for n, ln in load_labels(DATA / csv_name)
            if (DATA / sub / n).exists()]
    return rows


def load_image_255(path, size=256):
    """TN3K official norm: scale to [0,1] only (TRFE-Net mean=0,std=1)."""
    img = Image.open(path).convert("RGB").resize((size, size))
    arr = torch.from_numpy(np.asarray(img).copy()).permute(2, 0, 1).float() / 255.0
    return arr


def clip_feature(path):
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{path.name}"\r\n'
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        CLIP_URL, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    emb = data.get("embedding") or data.get("embeddings") or data.get("data")
    if isinstance(emb, dict):
        emb = emb.get("embedding")
    return np.asarray(emb, dtype=np.float32).reshape(-1)


def cached(name, fn):
    out = FEAT_DIR / f"{name}.npz"
    if out.exists():
        d = np.load(out)
        return d["x"], d["y"]
    x, y = fn()
    np.savez_compressed(out, x=x, y=y)
    return x, y

def extract_echocare(rows, subdir, tag):
    from monai.networks.nets.swin_unetr import SwinTransformer

    state = torch.load(ECHOCARE_PT, map_location="cpu", weights_only=True)
    if hasattr(state, "state_dict"):
        state = state.state_dict()
    state.pop("mask_token", None)
    enc = SwinTransformer(
        in_chans=3, embed_dim=128, window_size=[8, 8], patch_size=[2, 2],
        depths=[2, 2, 18, 2], num_heads=[4, 8, 16, 32], mlp_ratio=4.0,
        qkv_bias=True, use_checkpoint=False, spatial_dims=2, use_v2=True,
    )
    enc.load_state_dict(state, strict=True)
    enc.eval()
    try:
        device = "cuda"
        enc.to(device)
        torch.cuda.empty_cache()
    except Exception as exc:
        print(f"  [echocare] GPU fail ({str(exc)[:50]}), fallback CPU", flush=True)
        device = "cpu"
    print(f"  [echocare] device={device}", flush=True)
    xs, ys = [], []
    t0 = time.time()
    with torch.no_grad():
        for i, (name, label) in enumerate(rows):
            try:
                x = load_image_255(DATA / subdir / name).unsqueeze(0)
            except Exception:
                logger.warning("extract_echocare: 未预期异常，按原逻辑降级", exc_info=True)
                continue
            f = enc(x.to(device))
            xs.append(F.adaptive_avg_pool2d(f[-1], 1).flatten(1).cpu().numpy()[0])
            ys.append(label)
            if (i + 1) % 500 == 0:
                print(f"  [{tag}] {i+1}/{len(rows)} ({time.time()-t0:.0f}s)", flush=True)
    enc.to("cpu")
    if device == "cuda":
        torch.cuda.empty_cache()
    return np.asarray(xs, dtype=np.float32), np.asarray(ys)


def extract_clip(rows, subdir, tag):
    xs, ys = [], []
    t0 = time.time()
    for i, (name, label) in enumerate(rows):
        try:
            xs.append(clip_feature(DATA / subdir / name))
            ys.append(label)
        except Exception:
            logger.warning("extract_clip: 未预期异常，按原逻辑降级", exc_info=True)
            continue
        if (i + 1) % 500 == 0:
            print(f"  [{tag}] {i+1}/{len(rows)} ({time.time()-t0:.0f}s)", flush=True)
    print(f"  [{tag}] done ({time.time()-t0:.0f}s)", flush=True)
    return np.asarray(xs, dtype=np.float32), np.asarray(ys)


def extract_ulc(rows, subdir, tag):
    import open_clip

    model, _, preprocess = open_clip.create_model_and_transforms(
        "hf-hub:microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224",
    )
    sd = torch.load("/mnt/ee16/home/student03/uai/models/ultrasound_clip/checkpoints.pt",
                    map_location="cpu", weights_only=True)
    usd = sd.get("state_dict", sd)
    vsd = {k.replace("base_clip.visual.", "", 1): v
           for k, v in usd.items() if k.startswith("base_clip.visual.")}
    model.visual.load_state_dict(vsd, strict=False)
    model.visual.eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.visual.to(device)
    print(f"  [ulc] device={device}", flush=True)
    xs, ys = [], []
    t0 = time.time()
    with torch.no_grad():
        for i, (name, label) in enumerate(rows):
            try:
                img = Image.open(DATA / subdir / name).convert("RGB")
                x = preprocess(img)
            except Exception:
                logger.warning("extract_ulc: 未预期异常，按原逻辑降级", exc_info=True)
                continue
            f = model.visual(x.unsqueeze(0).to(device))
            if isinstance(f, tuple):
                f = f[0]
            xs.append(F.normalize(f, dim=-1).cpu().numpy()[0])
            ys.append(label)
            if (i + 1) % 500 == 0:
                print(f"  [{tag}] {i+1}/{len(rows)} ({time.time()-t0:.0f}s)", flush=True)
    model.visual.to("cpu")
    if device == "cuda":
        torch.cuda.empty_cache()
    return np.asarray(xs, dtype=np.float32), np.asarray(ys)


def eval_seeds(train_x, train_y, test_x, test_y, seeds):
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score, roc_auc_score, roc_curve

    runs = []
    for seed in seeds:
        clf = LogisticRegression(max_iter=3000, class_weight="balanced", random_state=seed)
        clf.fit(train_x, train_y)
        proba = clf.predict_proba(test_x)[:, 1]
        # Youden threshold from TRAIN ROC
        tr_proba = clf.predict_proba(train_x)[:, 1]
        fpr, tpr, thr = roc_curve(train_y, tr_proba)
        youden = thr[int(np.argmax(tpr - fpr))]
        pred = (proba >= youden).astype(int)
        runs.append({
            "seed": seed,
            "auc": round(float(roc_auc_score(test_y, proba)), 4),
            "acc_youden": round(float(accuracy_score(test_y, pred)), 4),
            "balanced_acc": round(float(balanced_accuracy_score(test_y, pred)), 4),
            "f1": round(float(f1_score(test_y, pred)), 4),
            "youden_thr": round(float(youden), 4),
        })
    agg = {k: (round(float(np.mean([r[k] for r in runs])), 4),
               round(float(np.std([r[k] for r in runs])), 4))
           for k in ("auc", "acc_youden", "balanced_acc", "f1")}
    return {"runs": runs, "mean_std": agg}

def main():
    train_rows = load_rows("trainval-image", "label4trainval.csv")
    test_rows = load_rows("test-image", "label4test.csv")
    print(f"trainval={len(train_rows)} test={len(test_rows)}", flush=True)

    report = {"protocol": "v2 (native per-model preprocessing, 3 seeds, Youden thr)",
              "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    print("== A1 BiomedCLIP probe (8003 native) ==", flush=True)
    tr_x, tr_y = cached("biomedclip_train", lambda: extract_clip(train_rows, "trainval-image", "biomedclip-tr"))
    te_x, te_y = cached("biomedclip_test", lambda: extract_clip(test_rows, "test-image", "biomedclip-te"))
    report["biomedclip_probe"] = eval_seeds(tr_x, tr_y, te_x, te_y, SEEDS)
    print(json.dumps(report["biomedclip_probe"]["mean_std"]), flush=True)

    print("== A4 ULC visual probe (BiomedCLIP-identical transform) ==", flush=True)
    tr_x, tr_y = cached("ulc_train", lambda: extract_ulc(train_rows, "trainval-image", "ulc-tr"))
    te_x, te_y = cached("ulc_test", lambda: extract_ulc(test_rows, "test-image", "ulc-te"))
    report["ulc_probe"] = eval_seeds(tr_x, tr_y, te_x, te_y, SEEDS)
    print(json.dumps(report["ulc_probe"]["mean_std"]), flush=True)

    print("== A3 EchoCare features (256px, /255, GPU-first) ==", flush=True)
    tr_x, tr_y = cached("echocare_train", lambda: extract_echocare(train_rows, "trainval-image", "echocare-tr"))
    te_x, te_y = cached("echocare_test", lambda: extract_echocare(test_rows, "test-image", "echocare-te"))
    report["echocare_probe_native_norm"] = eval_seeds(tr_x, tr_y, te_x, te_y, SEEDS)
    print(json.dumps(report["echocare_probe_native_norm"]["mean_std"]), flush=True)

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
