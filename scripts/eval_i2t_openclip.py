"""open_clip 格式 CLIP 权重的 I2T@k 评测（US-365K resolved jsonl）。

背景：microsoft/BiomedCLIP 官方仓库是 open_clip 格式（open_clip_pytorch_model.bin），
其 HF 格式第三方转换版与 transformers 5.x 不兼容（CLIPConfig 位置参数错误），
故评测走 open_clip 库。用法：

    python scripts/eval_i2t_openclip.py \
        --model-name biomedclip_local \
        --pretrained ~/uai/models/biomedclip-base-patch16-224/open_clip_pytorch_model.bin \
        --cache-dir ~/uai/models/biomedclip-base-patch16-224 \
        --test-dataset-path ~/uai/data/us365k/test.resolved.jsonl \
        --max-samples 500 --batch-size 32 \
        --output ~/uai/reports/i2t10_biomedclip_openclip.json

指标：I2T@k —— 对每张图，其配对 caption 是否落在该图向量检索的 top-k 文本内。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="open_clip I2T@k 评测")
    parser.add_argument(
        "--model-name", default=None, help="open_clip 内置模型名（与 --pretrained 配对）"
    )
    parser.add_argument("--pretrained", default=None, help="权重文件路径")
    parser.add_argument("--cache-dir", default=None, help="open_clip 缓存/配置目录")
    parser.add_argument(
        "--model-dir",
        default=None,
        help="模型目录（含 open_clip_config.json/open_clip_pytorch_model.bin，走 local-dir: schema；给定时无需 model-name/pretrained）",
    )
    parser.add_argument("--test-dataset-path", required=True, help="resolved jsonl 路径")
    parser.add_argument("--max-samples", type=int, default=500)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--output", default="i2t_openclip_report.json")
    return parser.parse_args()


def main() -> int:
    import open_clip
    import torch
    from PIL import Image

    args = parse_args()

    print(f"[INFO] 加载模型: {args.model_name} @ {args.pretrained}")
    if args.model_dir:
        # open_clip 3.3 的 local-dir: schema：从目录读配置与权重，tokenizer 同源
        uri = f"local-dir:{Path(args.model_dir).resolve()}"
        model, _, preprocess = open_clip.create_model_and_transforms(
            uri, device=args.device
        )
        tokenizer = open_clip.get_tokenizer(uri)
    else:
        model, _, preprocess = open_clip.create_model_and_transforms(
            args.model_name,
            pretrained=args.pretrained,
            cache_dir=args.cache_dir,
            device=args.device,
        )
        tokenizer = open_clip.get_tokenizer(args.model_name)
    model.eval()

    print(f"[INFO] 加载测试集: {args.test_dataset_path}")
    samples: list[tuple[str, str]] = []
    with open(args.test_dataset_path, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            samples.append((row["image"], row["caption"]))
            if len(samples) >= args.max_samples:
                break
    print(f"[INFO] 样本数: {len(samples)}")

    # 编码图像
    t0 = time.monotonic()
    image_feats: list[torch.Tensor] = []
    for i in range(0, len(samples), args.batch_size):
        batch = samples[i : i + args.batch_size]
        imgs = torch.stack(
            [preprocess(Image.open(p).convert("RGB")) for p, _ in batch]
        ).to(args.device)
        with torch.no_grad():
            feats = model.encode_image(imgs)
            feats = feats / feats.norm(dim=-1, keepdim=True)
        image_feats.append(feats.float().cpu())
    image_feats = torch.cat(image_feats)

    # 编码文本
    text_feats: list[torch.Tensor] = []
    for i in range(0, len(samples), args.batch_size):
        batch = samples[i : i + args.batch_size]
        toks = tokenizer([t for _, t in batch]).to(args.device)
        with torch.no_grad():
            feats = model.encode_text(toks)
            feats = feats / feats.norm(dim=-1, keepdim=True)
        text_feats.append(feats.float().cpu())
    text_feats = torch.cat(text_feats)
    elapsed_ms = (time.monotonic() - t0) * 1000

    # 相似度矩阵与 I2T@k
    sim = image_feats @ text_feats.T  # [N, N]，行=图像
    k = min(args.k, len(samples))
    topk = sim.topk(k, dim=1).indices  # [N, k]
    gt = torch.arange(len(samples)).unsqueeze(1)
    hits = (topk == gt).any(dim=1)
    i2t_at_k = hits.float().mean().item()

    # 参考：I2T@1（命中率上限）
    i2t_at_1 = (sim.argmax(dim=1) == gt.squeeze(1)).float().mean().item()

    report = {
        "model": f"{args.model_name}@{args.pretrained}",
        "dataset": args.test_dataset_path,
        "total_samples": len(samples),
        "i2t_at_k": round(i2t_at_k, 4),
        "k": k,
        "i2t_at_1": round(i2t_at_1, 4),
        "random_baseline_at_k": round(min(1.0, k / len(samples)), 5),
        "encode_elapsed_ms": round(elapsed_ms, 1),
        "note": "US-365K caption 为英文报告语句；随机基线 = k/N",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[INFO] 报告已写入: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
