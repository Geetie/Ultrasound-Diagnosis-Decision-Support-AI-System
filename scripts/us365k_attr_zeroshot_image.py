"""US-365K 图像侧 9 维属性 zero-shot（image→attribute，对齐论文 Table 1 口径）。

与 A.21 的文本侧探针（caption→标签）配对：那个数只证明报告可归类，
这个数才是"从超声图像能否推断属性"，也是论文 9 维 Acc 的同口径对照。
标签源：full_data.json 的 9 个 UDAF 维度（与 test.jsonl 同 media_name 严格对齐）。
"""
from __future__ import annotations

import logging
import os

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import json
import shutil
import time
from collections import Counter
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

logger = logging.getLogger(__name__)

M = Path("/mnt/ee16/home/student03/uai/models")
US = Path("/mnt/ee16/home/student03/uai/data/us365k")
OUT = Path("/mnt/ee16/home/student03/uai/reports/us365k_attr_zeroshot_image.json")
IMGDIR = US / "images" / "0613-1-images-new"

TASKS = [
    ("Body_system_level", False),
    ("Organ_level", False),
    ("Diagnosis", True),
    ("Shape", True),
    ("Margins", True),
    ("Echogenicity", True),
    ("InternalCharacteristics", True),
    ("PosteriorAcoustics", True),
    ("Vascularity", True),
]
MAX_LABELS = 12
BS_IMG = 64


def load_clip():
    CACHE = Path.home() / ".cache" / "huggingface" / "hub"
    REPO = CACHE / "models--microsoft--BiomedCLIP-PubMedBERT_256-vit_base_patch16_224"
    SNAP = REPO / "snapshots" / "offline"
    SNAP.mkdir(parents=True, exist_ok=True)
    for fn in ("open_clip_config.json", "open_clip_pytorch_model.bin", "tokenizer.json",
               "vocab.txt", "special_tokens_map.json", "tokenizer_config.json"):
        s, d = M / "biomedclip-base-patch16-224" / fn, SNAP / fn
        if s.exists() and not d.exists():
            shutil.copy(s, d)
    (REPO / "refs").mkdir(parents=True, exist_ok=True)
    (REPO / "refs" / "main").write_text("offline", encoding="utf-8")
    import open_clip

    model, _, preprocess = open_clip.create_model_and_transforms(
        "hf-hub:microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224",
    )
    tok = open_clip.get_tokenizer(
        "hf-hub:microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224",
    )
    return model, tok, preprocess


def main() -> int:
    device = "cuda"
    fd = json.loads((US / "full_data.json").read_text(encoding="utf-8"))
    by_name = {r["media_name"]: r for r in fd}

    paths, recs = [], []
    with (US / "test.jsonl").open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            p = Path(d["image"])
            if not p.exists():
                p = IMGDIR / p.name
            rec = by_name.get(p.name)
            if rec is not None and p.exists():
                paths.append(p)
                recs.append(rec)
    N = len(paths)
    print(f"images with labels: {N} (official US-365K case-level test split, no sampling)", flush=True)

    label_sets = {}
    for field, _ in TASKS:
        c = Counter()
        for rec in recs:
            v = rec.get(field)
            if isinstance(v, list):
                c.update(v)
            elif v:
                c[str(v)] += 1
        label_sets[field] = [k for k, _ in c.most_common(MAX_LABELS)]
    print({k: len(v) for k, v in label_sets.items()}, flush=True)

    model, tok, preprocess = load_clip()
    sd = torch.load(str(M / "ultrasound_clip" / "checkpoints.pt"),
                    map_location="cpu", weights_only=True)
    usd = sd["state_dict"] if "state_dict" in sd else sd
    V_SD = {k.replace("base_clip.visual.", "", 1): v
            for k, v in usd.items() if k.startswith("base_clip.visual.")}
    T_SD = {k.replace("base_clip.text.", "", 1): v
            for k, v in usd.items() if k.startswith("base_clip.text.")}
    snap = torch.load(str(M / "biomedclip-base-patch16-224" / "open_clip_pytorch_model.bin"),
                      map_location="cpu", weights_only=True)
    BASE_V = {k[len("module.visual."):]: v for k, v in snap.items() if k.startswith("module.visual.")}
    BASE_T = {k[len("module.text."):]: v for k, v in snap.items() if k.startswith("module.text.")}
    ARM_SD = {
        "biomedclip_base": (BASE_V, BASE_T),
        "ultrasound_clip": (V_SD, T_SD),
    }

    results: dict[str, dict] = {}
    for tag in ("biomedclip_base", "ultrasound_clip"):
        vsd, tsd = ARM_SD[tag]
        model.visual.load_state_dict(vsd, strict=False)
        model.text.load_state_dict(tsd, strict=False)
        model.to(device).eval()

        cache = OUT.parent / f"imgattr_{tag}.npy"
        if cache.exists():
            feats = np.load(cache)
            print(f"  [{tag}] image features from cache", flush=True)
        else:
            t0 = time.time()
            feats = np.zeros((N, 512), dtype=np.float32)
            with torch.no_grad():
                for i in range(0, N, BS_IMG):
                    batch = []
                    for p in paths[i:i + BS_IMG]:
                        try:
                            batch.append(preprocess(Image.open(p).convert("RGB")))
                        except Exception:
                            logger.warning("main: 未预期异常，按原逻辑降级", exc_info=True)
                            batch.append(torch.zeros(3, 224, 224))
                    x = torch.stack(batch).to(device, dtype=torch.float16)
                    with torch.autocast("cuda", dtype=torch.float16):
                        f = model.visual(x)
                    feats[i:i + len(batch)] = F.normalize(
                        f.float(), dim=-1).cpu().numpy()
                    if (i // BS_IMG) % 200 == 0:
                        done = i + len(batch)
                        rate = done / max(time.time() - t0, 1e-6)
                        print(f"  [{tag}] img {done}/{N} ({rate:.0f}/s, eta "
                              f"{(N-done)/max(rate,1e-6):.0f}s)", flush=True)
            np.save(cache, feats)

        for field, lesion in TASKS:
            labs = label_sets[field]
            if len(labs) < 2:
                continue
            prompts = [
                (f"a {lab} lesion in an ultrasound image" if lesion
                 else f"a ultrasound image of {lab}")
                for lab in labs
            ]
            with torch.no_grad():
                tf = F.normalize(
                    model.encode_text(tok(prompts).to(device)).float(), dim=-1
                ).cpu().numpy()
            gold, keep = [], []
            for i, rec in enumerate(recs):
                v = rec.get(field)
                if isinstance(v, list):
                    v = v[0] if v else None
                if v in labs:
                    gold.append(labs.index(v))
                    keep.append(i)
            if not keep:
                continue
            g = np.array(gold)
            pred = (feats[keep] @ tf.T).argmax(axis=1)
            results.setdefault(field, {})[tag] = {
                "acc": round(float((pred == g).mean()), 4),
                "n": int(len(g)),
                "n_labels": len(labs),
                "majority_baseline": round(
                    float(max(Counter(g.tolist()).values()) / len(g)), 4),
            }
            print(f"  [{tag}] {field}: {results[field][tag]}", flush=True)

    for tag in ARM_SD:
        per = [v[tag] for k, v in results.items() if tag in v]
        results[f"__avg__{tag}"] = {
            "macro_avg_acc": round(float(np.mean([x["acc"] for x in per])), 4),
            "avg_majority_baseline": round(
                float(np.mean([x["majority_baseline"] for x in per])), 4),
            "n_tasks": len(per),
        }

    OUT.write_text(json.dumps({
        "dataset": "US-365K official case-level test split (no sampling)",
        "n_images": N,
        "task": "IMAGE -> 9-dim UDAF attribute zero-shot classification (paper Table 1 protocol)",
        "max_labels_per_dim": MAX_LABELS,
        "script": "scripts/us365k_attr_zeroshot_image.py",
        "paper_reference": {
            "source": "Ultrasound-CLIP arXiv:2604.01749 Table 1 (AvgAcc)",
            "ULC_full_AvgAcc": 0.5961,
            "BiomedCLIP_AvgAcc": 0.3381,
            "PMC-CLIP_AvgAcc": 0.3474,
        },
        "results": results,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in results.items() if k.startswith("__")},
                     ensure_ascii=False), flush=True)
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
