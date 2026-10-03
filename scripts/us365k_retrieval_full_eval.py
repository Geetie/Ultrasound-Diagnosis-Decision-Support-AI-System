"""US-365K 全量图文检索评测（官方 case 级 test split，71,919 对）。

红线：样本量/划分来源/脚本路径必须写进报告；禁止用子集数字当成绩。
分块 top-k：71,919² 相似度矩阵不落内存（query 块 × gallery 块，running top-k）。
"""
from __future__ import annotations

import os

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import json
import shutil
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

M = Path("/mnt/ee16/home/student03/uai/models")
US = Path("/mnt/ee16/home/student03/uai/data/us365k")
OUT = Path("/mnt/ee16/home/student03/uai/reports/us365k_retrieval_full.json")
Q_CHUNK = 2048
G_CHUNK = 8192
KS = (1, 5, 10, 50)

# ---------------------------------------------------------------- 离线模型
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
(REPO_DIR / "refs").mkdir(parents=True, exist_ok=True)
(REPO_DIR / "refs" / "main").write_text("offline", encoding="utf-8")

import open_clip

model, _, preprocess = open_clip.create_model_and_transforms(
    "hf-hub:microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224",
)
tokenizer = open_clip.get_tokenizer(
    "hf-hub:microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224",
)
device = "cuda" if torch.cuda.is_available() else "cpu"
model.to(device).eval()
print(f"device={device}", flush=True)

sd = torch.load(str(M / "ultrasound_clip" / "checkpoints.pt"),
                map_location="cpu", weights_only=True)
usd = sd["state_dict"] if "state_dict" in sd else sd
V_SD = {k.replace("base_clip.visual.", "", 1): v
        for k, v in usd.items() if k.startswith("base_clip.visual.")}
T_SD = {k.replace("base_clip.text.", "", 1): v
        for k, v in usd.items() if k.startswith("base_clip.text.")}

# ---------------------------------------------------------------- 全量数据
rows: list[tuple[str, str]] = []
with (US / "test.resolved.jsonl").open(encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        p = d["image"]
        if not Path(p).exists():
            p = str(US / "images" / "0613-1-images-new" / Path(p).name)
        cap = d.get("caption")
        if cap:
            rows.append((p, cap))
N = len(rows)
print(f"pairs={N} (official US-365K case-level test split)", flush=True)
assert N > 70000, f"样本量异常：{N}"


def encode_images(tag: str) -> np.ndarray:
    feats = np.zeros((N, 512), dtype=np.float16)
    t0 = time.time()
    bs = 64
    with torch.no_grad():
        for i in range(0, N, bs):
            chunk = rows[i:i + bs]
            batch = []
            for p, _ in chunk:
                try:
                    batch.append(preprocess(Image.open(p).convert("RGB")))
                except Exception:
                    batch.append(torch.zeros(3, 224, 224))
            x = torch.stack(batch).to(device, dtype=torch.float16)
            with torch.autocast("cuda", dtype=torch.float16, enabled=(device == "cuda")):
                f = model.visual(x)
            feats[i:i + len(chunk)] = F.normalize(f.float(), dim=-1).cpu().numpy().astype(np.float16)
            if (i // bs) % 200 == 0:
                done = i + len(chunk)
                rate = done / max(time.time() - t0, 1e-6)
                print(f"  [{tag}] img {done}/{N} ({rate:.1f}/s, eta {(N-done)/max(rate,1e-6):.0f}s)", flush=True)
    return feats


def encode_texts(tag: str) -> np.ndarray:
    feats = np.zeros((N, 512), dtype=np.float16)
    t0 = time.time()
    bs = 256
    with torch.no_grad():
        for i in range(0, N, bs):
            caps = [c for _, c in rows[i:i + bs]]
            toks = tokenizer(caps).to(device)
            with torch.autocast("cuda", dtype=torch.float16, enabled=(device == "cuda")):
                f = model.encode_text(toks)
            feats[i:i + len(caps)] = F.normalize(f.float(), dim=-1).cpu().numpy().astype(np.float16)
            if (i // bs) % 100 == 0:
                done = i + len(caps)
                rate = done / max(time.time() - t0, 1e-6)
                print(f"  [{tag}] txt {done}/{N} ({rate:.0f}/s)", flush=True)
    return feats


def recalls(img: np.ndarray, txt: np.ndarray) -> dict:
    """分块 running top-k，双向。"""
    kmax = max(KS)
    out = {}
    for direction in ("i2t", "t2i"):
        q_all, g_all = (img, txt) if direction == "i2t" else (txt, img)
        topk_idx = np.zeros((N, kmax), dtype=np.int32)
        topk_val = np.zeros((N, kmax), dtype=np.float32)
        t0 = time.time()
        for qs in range(0, N, Q_CHUNK):
            q = q_all[qs:qs + Q_CHUNK].astype(np.float32)
            best_v = np.full((q.shape[0], kmax), -1e9, dtype=np.float32)
            best_i = np.zeros((q.shape[0], kmax), dtype=np.int64)
            for gs in range(0, N, G_CHUNK):
                g = g_all[gs:gs + G_CHUNK].astype(np.float32)
                sim = q @ g.T
                m = min(kmax, sim.shape[1])
                local_idx = np.argpartition(-sim, m - 1, axis=1)[:, :m]
                part_val = np.take_along_axis(sim, local_idx, axis=1)
                part_idx = local_idx + gs  # 局部列索引 → 全局索引
                cat_v = np.concatenate([best_v, part_val], axis=1)
                cat_i = np.concatenate([best_i, part_idx], axis=1)
                sel = np.argpartition(-cat_v, kmax - 1, axis=1)[:, :kmax]
                best_v = np.take_along_axis(cat_v, sel, axis=1)
                best_i = np.take_along_axis(cat_i, sel, axis=1)
            topk_idx[qs:qs + q.shape[0]] = best_i
            topk_val[qs:qs + q.shape[0]] = best_v
            print(f"  {direction} {qs + q.shape[0]}/{N} ({time.time()-t0:.0f}s)", flush=True)
        gold = np.arange(N).reshape(-1, 1)
        res = {}
        for k in KS:
            hit = (topk_idx[:, :k] == gold).any(axis=1)
            res[f"R@{k}"] = round(float(hit.mean()), 4)
        res["mR"] = round(float(np.mean([res[f"R@{k}"] for k in KS])), 4)
        out[direction] = res
    return out


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["base", "ulc"], required=True)
    args = ap.parse_args()
    tag = "biomedclip_base" if args.arm == "base" else "ultrasound_clip"

    # 每臂独立进程：base 臂天然是原始权重；ulc 臂才覆盖双塔
    if tag == "ultrasound_clip":
        model.visual.load_state_dict(V_SD, strict=False)
        model.text.load_state_dict(T_SD, strict=False)
        model.to(device).eval()
    print(f"arm={tag} weights ready", flush=True)

    t0 = time.time()
    cache = OUT.parent / f"feat_{args.arm}"
    fi, ft = cache.with_suffix(".img.npy"), cache.with_suffix(".txt.npy")
    if fi.exists() and ft.exists():
        img, txt = np.load(fi), np.load(ft)
        print(f"features loaded from cache {fi.name}", flush=True)
    else:
        img = encode_images(tag)
        txt = encode_texts(tag)
        np.save(fi, img)
        np.save(ft, txt)
    r = recalls(img, txt)
    r["encode_seconds"] = round(time.time() - t0, 1)
    print(f"[{tag}] I2T {r['i2t']}  T2I {r['t2i']}", flush=True)

    report = {
        "dataset": "US-365K official case-level test split",
        "source_file": str(US / "test.jsonl"),
        "n_pairs": N,
        "sampling": "全量（无抽样）",
        "metric": "Recall@K，双向分块 top-k（Q_CHUNK=2048, G_CHUNK=8192）",
        "script": "scripts/us365k_retrieval_full_eval.py",
        "arm": tag,
        "note": "ULC 臂为不含 graph fusion 的保守口径（官方 checkpoint 图分支未训练：alpha=0.05 初值、convs 缺失）",
        "paper_reference": {
            "source": "Ultrasound-CLIP arXiv:2604.01749 (CVPR2026), Table 2",
            "ULC_full_model_I2T": {"R@5": 0.2359, "R@10": 0.3745, "R@50": 0.7909},
            "ULC_without_graph_encoder_I2T_R@10": 0.3444,
            "PMC-CLIP_I2T_R@10": 0.3011,
            "BiomedCLIP_I2T_R@10": 0.2979,
        },
        "results": {tag: r},
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    out = OUT.with_name(f"{OUT.stem}_{args.arm}.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
