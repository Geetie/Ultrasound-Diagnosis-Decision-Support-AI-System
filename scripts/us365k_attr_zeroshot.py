"""US-365K 9 维诊断属性 zero-shot 探针（BiomedCLIP vs ULC，全量 test 71,919）。

目的：判定"自训图融合换属性可解释输出"是否值得（先看 zero-shot 基线）。
标签源：full_data.json 的 9 个 UDAF 维度字段（与 test.jsonl 同 media_name 严格对齐）。
Prompt 模板对齐论文附录 F.2（Task1/2 与 Task3-9 两套句式）。
"""
from __future__ import annotations

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

M = Path("/mnt/ee16/home/student03/uai/models")
US = Path("/mnt/ee16/home/student03/uai/data/us365k")
OUT = Path("/mnt/ee16/home/student03/uai/reports/us365k_attr_zeroshot.json")

# (维度字段, 是否用 lesion 句式)
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
MAX_LABELS = 12  # 单维最多取 12 类（论文同量级；按频次截断）


def load_model():
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

    model, _, _ = open_clip.create_model_and_transforms(
        "hf-hub:microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224",
    )
    tok = open_clip.get_tokenizer(
        "hf-hub:microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224",
    )
    return model, tok


def main() -> int:
    device = "cuda"
    fd = json.loads((US / "full_data.json").read_text(encoding="utf-8"))
    by_name = {r["media_name"]: r for r in fd}

    rows = []
    with (US / "test.jsonl").open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            rec = by_name.get(Path(d["image"]).name)
            if rec is not None and d.get("caption"):
                rows.append((d["caption"], rec))
    print(f"pairs with labels: {len(rows)} (official test split)", flush=True)

    # 每维标签集合（按频次截断）
    label_sets: dict[str, list[str]] = {}
    gold: dict[str, list[str]] = {}
    for field, _ in TASKS:
        c = Counter()
        for _, rec in rows:
            v = rec.get(field)
            if isinstance(v, list):
                c.update(v)
            elif v:
                c[str(v)] += 1
        labs = [k for k, _ in c.most_common(MAX_LABELS)]
        label_sets[field] = labs
        gold[field] = []
    print({k: len(v) for k, v in label_sets.items()}, flush=True)

    # caption 编码（两臂共用 base 权重，ULC 臂只换 text 塔——zero-shot 只用文本侧）
    model, tok = load_model()
    model.to(device).eval()
    sd = torch.load(str(M / "ultrasound_clip" / "checkpoints.pt"),
                    map_location="cpu", weights_only=True)
    usd = sd["state_dict"] if "state_dict" in sd else sd
    T_SD = {k.replace("base_clip.text.", "", 1): v
            for k, v in usd.items() if k.startswith("base_clip.text.")}

    caps = [c for c, _ in rows]
    feats: dict[str, np.ndarray] = {}
    for tag in ("biomedclip_base", "ultrasound_clip_texttower"):
        cache = OUT.parent / f"attrfeat_{tag}.npy"
        if cache.exists():
            feats[tag] = np.load(cache)
            print(f"  [{tag}] features from cache", flush=True)
            continue
        if tag.startswith("ultrasound_clip"):
            model.text.load_state_dict(T_SD, strict=False)
            model.to(device).eval()
        t0 = time.time()
        out = np.zeros((len(caps), 512), dtype=np.float32)
        with torch.no_grad():
            for i in range(0, len(caps), 256):
                t = tok(caps[i:i + 256]).to(device)
                f = model.encode_text(t)
                out[i:i + len(t)] = F.normalize(f.float(), dim=-1).cpu().numpy()
                if (i // 256) % 100 == 0:
                    print(f"  [{tag}] txt {i}/{len(caps)} ({time.time()-t0:.0f}s)", flush=True)
        np.save(cache, out)
        feats[tag] = out

    # 逐维 zero-shot 分类（**每臂用各自 text 塔编码 prompt**，避免臂间污染）
    snap = torch.load(
        str(M / "biomedclip-base-patch16-224" / "open_clip_pytorch_model.bin"),
        map_location="cpu", weights_only=True,
    )
    BASE_TEXT_SD = {
        k[len("module.text."):]: v for k, v in snap.items() if k.startswith("module.text.")
    }
    ARM_SD = {"biomedclip_base": BASE_TEXT_SD, "ultrasound_clip_texttower": T_SD}

    results: dict[str, dict] = {}
    for field, lesion in TASKS:
        labs = label_sets[field]
        if len(labs) < 2:
            continue
        prompts = [
            (f"a {lab} lesion in an ultrasound image" if lesion
             else f"a ultrasound image of {lab}")
            for lab in labs
        ]
        gold_idx, keep = [], []
        for i, (_, rec) in enumerate(rows):
            v = rec.get(field)
            if isinstance(v, list):
                v = v[0] if v else None
            if v in labs:
                gold_idx.append(labs.index(v))
                keep.append(i)
        if not keep:
            continue
        g = np.array(gold_idx)
        for tag, cf in feats.items():
            model.text.load_state_dict(ARM_SD[tag], strict=False)
            model.to(device).eval()
            with torch.no_grad():
                tf = F.normalize(
                    model.encode_text(tok(prompts).to(device)).float(), dim=-1
                ).cpu().numpy()
            sim = cf[keep] @ tf.T
            pred = sim.argmax(axis=1)
            acc = float((pred == g).mean())
            results.setdefault(field, {})[tag] = {
                "acc": round(acc, 4),
                "n": int(len(g)),
                "n_labels": len(labs),
                "majority_baseline": round(
                    float(max(Counter(g.tolist()).values()) / len(g)), 4),
            }
        print(f"{field}: {results[field]} ({len(g)} samples)", flush=True)

    for tag in feats:
        per_task = [v[tag] for k, v in results.items() if not k.startswith("__") and tag in v]
        if not per_task:
            continue
        accs = [x["acc"] for x in per_task]
        maj = [x["majority_baseline"] for x in per_task]
        results[f"__avg__{tag}"] = {
            "macro_avg_acc": round(float(np.mean(accs)), 4),
            "avg_majority_baseline": round(float(np.mean(maj)), 4),
            "n_tasks": len(accs),
        }

    OUT.write_text(json.dumps({
        "dataset": "US-365K official case-level test split (no sampling)",
        "n_pairs": len(rows),
        "task": "9-dim UDAF attribute zero-shot classification via text tower",
        "max_labels_per_dim": MAX_LABELS,
        "script": "scripts/us365k_attr_zeroshot.py",
        "results": results,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in results.items() if k.startswith("__")},
                     ensure_ascii=False), flush=True)
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
