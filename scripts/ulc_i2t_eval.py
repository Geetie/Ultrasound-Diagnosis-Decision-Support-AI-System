import logging
import os
import shutil

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import json
import random
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

M = Path("/mnt/ee16/home/student03/uai/models")
CACHE = Path.home() / ".cache" / "huggingface" / "hub"
REPO_DIR = CACHE / "models--microsoft--BiomedCLIP-PubMedBERT_256-vit_base_patch16_224"
SNAP = REPO_DIR / "snapshots" / "offline"
SNAP.mkdir(parents=True, exist_ok=True)
for fn in ("open_clip_config.json", "open_clip_pytorch_model.bin", "tokenizer.json",
           "vocab.txt", "special_tokens_map.json", "tokenizer_config.json"):
    src = M / "biomedclip-base-patch16-224" / fn
    dst = SNAP / fn
    if src.exists() and not dst.exists():
        shutil.copy(src, dst)
refs = REPO_DIR / "refs"
refs.mkdir(parents=True, exist_ok=True)
(refs / "main").write_text("offline", encoding="utf-8")

import open_clip

logger = logging.getLogger(__name__)

model, _, preprocess = open_clip.create_model_and_transforms(
    "hf-hub:microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224",
)
tokenizer = open_clip.get_tokenizer(
    "hf-hub:microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224",
)
device = "cuda" if torch.cuda.is_available() else "cpu"
model.to(device).eval()
print("base BiomedCLIP loaded", flush=True)

sd = torch.load(str(M / "ultrasound_clip" / "checkpoints.pt"),
                map_location="cpu", weights_only=True)
usd = sd.get("state_dict", sd)
v_sd = {k.replace("base_clip.visual.", "", 1): v
        for k, v in usd.items() if k.startswith("base_clip.visual.")}
t_sd = {k.replace("base_clip.text.", "", 1): v
        for k, v in usd.items() if k.startswith("base_clip.text.")}
model.visual.load_state_dict(v_sd, strict=False)
missing_t = set(model.text.state_dict()) - set(t_sd)
model.text.load_state_dict(t_sd, strict=False)
print(f"ULC weights: visual={len(v_sd)} text={len(t_sd)} text_missing={len(missing_t)}",
      flush=True)

rows = []
with open("/mnt/ee16/home/student03/uai/data/us365k/test.jsonl", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            d = json.loads(line)
            p = Path(d["image"])
            if p.exists() and d.get("caption"):
                rows.append((p, d["caption"]))
random.Random(42).shuffle(rows)
rows = rows[:500]
print(f"pairs: {len(rows)}", flush=True)

results = {}
for tag in ("biomedclip_base", "ultrasound_clip"):
    if tag == "ultrasound_clip":
        model.visual.load_state_dict(v_sd, strict=False)
        model.text.load_state_dict(t_sd, strict=False)
    model.to(device).eval()
    img_feats, txt_feats = [], []
    t0 = time.time()
    with torch.no_grad():
        for i, (p, cap) in enumerate(rows):
            try:
                img = preprocess(Image.open(p).convert("RGB"))
            except Exception:
                logger.warning("<module>: 未预期异常，按原逻辑降级", exc_info=True)
                img = torch.zeros(3, 224, 224)
            toks = tokenizer([cap])[0]
            img_f = F.normalize(model.visual(img.unsqueeze(0).to(device)), dim=-1)
            txt_f = F.normalize(model.encode_text(toks.unsqueeze(0).to(device)), dim=-1)
            img_feats.append(img_f.cpu().numpy()[0])
            txt_feats.append(txt_f.cpu().numpy()[0])
            if (i + 1) % 100 == 0:
                print(f"  [{tag}] {i+1}/{len(rows)} ({time.time()-t0:.0f}s)", flush=True)
    img_f = np.asarray(img_feats)
    txt_f = np.asarray(txt_feats)
    sim = img_f @ txt_f.T
    k = 10
    topk = np.argsort(-sim, axis=1)[:, :k]
    gt = np.arange(len(rows)).reshape(-1, 1)
    i2t10 = float((topk == gt).any(axis=1).mean())
    i2t1 = float((np.argmax(sim, axis=1) == np.arange(len(rows))).mean())
    results[tag] = {"i2t_at_10": round(i2t10, 4), "i2t_at_1": round(i2t1, 4)}
    print(f"[{tag}] I2T@10={i2t10:.4f} I2T@1={i2t1:.4f}", flush=True)

out = Path("/mnt/ee16/home/student03/uai/reports/ulc_i2t_report.json")
out.write_text(json.dumps({"n_pairs": len(rows),
                           "note": "graph fusion NOT included (text tower only)",
                           "results": results,
                           "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                          ensure_ascii=False, indent=2), encoding="utf-8")
print(f"-> {out}")
