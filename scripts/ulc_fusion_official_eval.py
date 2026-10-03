"""ULC graph-fusion 拼装对照实验（官方权重忠实复现）。

结论预置：官方 checkpoint 的 graph 分支未训练（alpha=0.05 初始值、
convs 权重缺失）——本实验实证"拼装后 I2T vs 纯文本塔"差异。
"""
import os

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import json
import random
import re
import shutil
import sys
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

sys.path.insert(0, "/tmp")
from ulc_ref.enhanced_clip_model import EnhancedCLIP
from ulc_ref.graph_builder import build_single_sample_graph
from ulc_ref.graph_encoder import GraphEncoder

model, _, preprocess = open_clip.create_model_and_transforms(
    "hf-hub:microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224",
)
tokenizer = open_clip.get_tokenizer(
    "hf-hub:microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224",
)
device = "cpu"
model.to(device).eval()
print("base loaded", flush=True)

sd = torch.load(str(M / "ultrasound_clip" / "checkpoints.pt"),
                map_location="cpu", weights_only=True)
usd = sd["state_dict"] if "state_dict" in sd else sd
v_sd = {k.replace("base_clip.visual.", "", 1): v
        for k, v in usd.items() if k.startswith("base_clip.visual.")}
t_sd = {k.replace("base_clip.text.", "", 1): v
        for k, v in usd.items() if k.startswith("base_clip.text.")}
model.visual.load_state_dict(v_sd, strict=False)
model.text.load_state_dict(t_sd, strict=False)

# 拼装图融合（官方权重忠实复现：convs 缺失→保持初始随机）
ge = GraphEncoder(out_dim=128, hidden=256, n_layers=2)
enhanced = EnhancedCLIP(model, ge, freeze_graph_encoder=True)
fused_sd = {k.replace("graph_encoder.", "graph_encoder.", 1): v
            for k, v in usd.items() if k.startswith("graph_encoder.")}
missing, unexpected = enhanced.graph_encoder.load_state_dict(fused_sd, strict=False)
print("graph_encoder missing:", len(missing), "unexpected:", len(unexpected), flush=True)
fusion_sd = {k.replace("text_graph_fusion.", "", 1): v
             for k, v in usd.items() if k.startswith("text_graph_fusion.")}
enhanced.text_graph_fusion.load_state_dict(fusion_sd, strict=True)
enhanced.to(device).eval()
print("enhanced assembled, alpha =", enhanced.text_graph_fusion.alpha.item(), flush=True)

US = Path("/mnt/ee16/home/student03/uai/data/us365k")
TEST = US / "test.resolved.jsonl"
IMGDIR = US / "images" / "0613-1-images-new"
full_data = json.loads((US / "full_data.json").read_text(encoding="utf-8"))
fd_by_name = {rec["media_name"]: rec for rec in full_data}
print("full_data indexed:", len(fd_by_name), flush=True)

rows = []
with TEST.open(encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            d = json.loads(line)
            p = Path(d["image"])
            if not p.exists():
                p = IMGDIR / Path(d["image"]).name
            if p.exists() and d.get("caption"):
                rows.append((p, d["caption"]))
random.Random(42).shuffle(rows)
rows = rows[:500]
print(f"pairs: {len(rows)}", flush=True)

graphs = []
graph_miss = 0
for p, _ in rows:
    rec = fd_by_name.get(p.name)
    if rec is None:
        graph_miss += 1
        g = build_single_sample_graph({}, "_")
    else:
        g = build_single_sample_graph(fd_by_name, p.name)
    graphs.append(g)
print(f"graphs built, missing annotations: {graph_miss}", flush=True)

import dgl

t0 = time.time()
with torch.no_grad():
    txt_plain, txt_fused = [], []
    B = 16
    for i in range(0, len(rows), B):
        batch = rows[i:i + B]
        toks = tokenizer([c for _, c in batch])
        tf = F.normalize(model.encode_text(toks), dim=-1)
        txt_plain.append(tf.cpu().numpy())
        gb = dgl.batch(graphs[i:i + B])
        fused = enhanced.encode_text_with_graph(toks, gb, normalize=True)
        txt_fused.append(F.normalize(fused, dim=-1).cpu().numpy())
        if (i // B) % 5 == 0:
            print(f"  text {i + len(batch)}/{len(rows)} ({time.time()-t0:.0f}s)", flush=True)
    img_feats = []
    for i, (p, _) in enumerate(rows):
        try:
            img = preprocess(Image.open(p).convert("RGB"))
        except Exception:
            img = torch.zeros(3, 224, 224)
        img_feats.append(
            F.normalize(model.visual(img.unsqueeze(0)), dim=-1).cpu().numpy()[0]
        )
        if (i + 1) % 100 == 0:
            print(f"  image {i + 1}/{len(rows)}", flush=True)

img_m = np.asarray(img_feats)
gt = np.arange(len(rows)).reshape(-1, 1)


def i2t(txt_m, k=10):
    sim = img_m @ txt_m.T
    topk = np.argsort(-sim, axis=1)[:, :k]
    return float((topk == gt).any(axis=1).mean()), float(
        (np.argmax(sim, axis=1) == np.arange(len(rows))).mean()
    )


r10_p, r1_p = i2t(np.vstack(txt_plain))
r10_f, r1_f = i2t(np.vstack(txt_fused))
print(f"[plain_text_tower]  I2T@10={r10_p:.4f} I2T@1={r1_p:.4f}", flush=True)
print(f"[official_fusion]   I2T@10={r10_f:.4f} I2T@1={r1_f:.4f}", flush=True)

out = Path("/mnt/ee16/home/student03/uai/reports/ulc_fusion_official_report.json")
out.write_text(json.dumps({
    "n_pairs": len(rows),
    "verdict": "official checkpoint graph branch UNTRAINED",
    "evidence": {
        "alpha": 0.05000000074505806,
        "alpha_init": 0.05,
        "convs_weights_in_checkpoint": False,
    },
    "results": {
        "plain_text_tower": {"i2t_at_10": round(r10_p, 4), "i2t_at_1": round(r1_p, 4)},
        "official_fusion_assembled": {"i2t_at_10": round(r10_f, 4), "i2t_at_1": round(r1_f, 4)},
    },
    "note": "fusion assembled with released weights; convs remain random init (missing in checkpoint)",
    "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"-> {out}")
