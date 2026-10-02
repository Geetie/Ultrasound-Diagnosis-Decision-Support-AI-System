"""Ultrasound-CLIP 官方权重结构鉴定（只读，不加载进显存）。

输出：顶层结构 / key 前缀分布 / 张量形状样本 / backbone 匹配建议，
供决定拼装策略（完整双塔推理 vs 仅 image encoder 探针）。
"""
from __future__ import annotations

import argparse
from collections import Counter

import torch


def describe(obj, depth: int = 0, max_depth: int = 2) -> None:
    pad = "  " * depth
    if isinstance(obj, dict):
        print(f"{pad}dict, {len(obj)} keys")
        if depth < max_depth:
            for k in list(obj)[:12]:
                print(f"{pad}  [{k!r}] -> {type(obj[k]).__name__}")
                describe(obj[k], depth + 1, max_depth)
    elif isinstance(obj, torch.Tensor):
        print(f"{pad}Tensor {tuple(obj.shape)} {obj.dtype}")
    else:
        print(f"{pad}{type(obj).__name__}: {str(obj)[:120]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("checkpoint", help="权重文件路径")
    args = ap.parse_args()

    sd = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    print("== top-level ==")
    describe(sd, max_depth=1)

    # 可能的包一层结构
    if isinstance(sd, dict) and "state_dict" in sd and isinstance(sd["state_dict"], dict):
        sd = sd["state_dict"]
        print("== unwrapped state_dict ==")
    elif isinstance(sd, dict) and "model" in sd and isinstance(sd["model"], dict):
        sd = sd["model"]
        print("== unwrapped model ==")

    if not sd or not hasattr(next(iter(sd.values()), None), "shape"):
        print("顶层不是纯 state_dict（含超参/文本），仅打印结构")
        return 0

    print(f"\n== state_dict: {len(sd)} tensors ==")
    prefixes = Counter()
    for k in sd:
        prefixes[".".join(k.split(".")[:2])] += 1
    for prefix, n in prefixes.most_common(20):
        print(f"  {prefix}: {n} tensors")

    print("\n== sample shapes ==")
    for k in list(sd)[:10]:
        t = sd[k]
        print(f"  {k}: {tuple(t.shape)} {t.dtype}")

    # backbone 粗判
    total = sum(t.numel() for t in sd.values() if hasattr(t, "numel"))
    print(f"\ntotal params: {total/1e6:.1f}M")
    kw = " ".join(sd.keys())
    hints = []
    if "visual.conv1" in kw or "visual.class_embedding" in kw:
        hints.append("open_clip/CLIP ViT visual tower (visual.*)")
    if "text.transformer" in kw or "transformer.resblocks" in kw:
        hints.append("CLIP text transformer")
    if "patch_embed" in kw:
        hints.append("Swin/UNETR 风格 patch_embed")
    if "relative_position" in kw:
        hints.append("Swin relative_position_bias")
    print("backbone hints:", hints or ["未识别——需对照参考实现"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
