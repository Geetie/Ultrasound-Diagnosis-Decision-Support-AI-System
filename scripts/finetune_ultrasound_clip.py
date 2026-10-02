"""Ultrasound-CLIP 微调 CLI 脚本。

用法示例:
    # 完整微调(LoRA,GPU)
    python scripts/finetune_ultrasound_clip.py \\
        --dataset-path ./data/busi \\
        --output-dir ./models/ultrasound-clip-finetuned \\
        --epochs 3 --batch-size 16 --learning-rate 2e-5

    # 冒烟测试(无 GPU,小样本)
    python scripts/finetune_ultrasound_clip.py \\
        --dataset-path ./tests/fixtures/busi_mock \\
        --output-dir ./tmp/finetune_smoke \\
        --max-samples 2 --epochs 1 --device cpu

输出:
    - 微调后模型权重: <output_dir>/adapter_model.safetensors
    - 训练日志: <output_dir>/training.log (JSON Lines)
    - 评估报告: <output_dir>/finetune_result.json (I2T@10, loss 曲线)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 将项目根目录加入 sys.path,确保 `from app.xxx import ...` 可用
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.embedding.busi_adapter import BUSIAdapter  # noqa: E402
from app.embedding.clip_model import UltrasoundCLIPModel  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ultrasound-CLIP 微调 CLI(支持 LoRA 与全量微调)"
    )
    parser.add_argument(
        "--dataset-path", required=True,
        help="数据集目录或 JSONL/parquet 文件路径",
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="模型输出目录",
    )
    parser.add_argument("--epochs", type=int, default=3, help="训练轮数(默认 3)")
    parser.add_argument("--batch-size", type=int, default=16, help="批大小(默认 16)")
    parser.add_argument("--learning-rate", type=float, default=2e-5, help="学习率(默认 2e-5)")
    parser.add_argument("--lora-r", type=int, default=16, help="LoRA rank(默认 16)")
    parser.add_argument("--lora-alpha", type=int, default=32, help="LoRA alpha(默认 32)")
    parser.add_argument(
        "--full-finetune", action="store_true",
        help="切换到全量微调(默认 LoRA)",
    )
    parser.add_argument(
        "--max-samples", type=int, default=None,
        help="限制训练样本数(冒烟测试用)",
    )
    parser.add_argument(
        "--device", default="cuda", choices=["cuda", "cpu"],
        help="推理设备(默认 cuda,无 GPU 时自动降级到 cpu)",
    )
    parser.add_argument(
        "--model-name", default="Ultrasound-CLIP",
        help="模型名称或本地路径(默认 Ultrasound-CLIP)",
    )
    parser.add_argument(
        "--eval-dataset-path", default=None,
        help="评估集路径(可选,若提供则计算 I2T@10)",
    )
    parser.add_argument(
        "--use-hf", action="store_true",
        help="使用 HuggingFace datasets 后端加载 parquet",
    )
    parser.add_argument(
        "--allow-synthetic", action="store_true",
        help=(
            "冒烟专用：数据集路径不存在时允许用合成占位样本（默认关闭）。"
            "合成样本的 image_path 指向不存在的文件，训练结果无意义。"
        ),
    )
    return parser.parse_args()


def write_log(output_dir: Path, log_entry: dict) -> None:
    log_path = output_dir / "training.log"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] 加载数据集: {args.dataset_path}")
    adapter = BUSIAdapter(
        dataset_path=args.dataset_path,
        use_hf=args.use_hf,
        allow_synthetic=args.allow_synthetic,
    )
    try:
        train_samples = adapter.load(max_samples=args.max_samples)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}")
        return 4
    print(f"[INFO] 训练样本数: {len(train_samples)}")

    if not train_samples:
        print("[ERROR] 无可用训练样本,退出")
        return 1

    eval_samples = None
    if args.eval_dataset_path:
        eval_adapter = BUSIAdapter(
            dataset_path=args.eval_dataset_path,
            use_hf=args.use_hf,
            allow_synthetic=args.allow_synthetic,
        )
        eval_samples = eval_adapter.load(max_samples=min(args.max_samples or 50, 50))
        print(f"[INFO] 评估样本数: {len(eval_samples)}")

    print(
        f"[INFO] 初始化模型: model={args.model_name}, device={args.device}, "
        f"use_lora={not args.full_finetune}"
    )
    model = UltrasoundCLIPModel(
        model_name=args.model_name,
        device=args.device,
        use_lora=not args.full_finetune,
    )

    try:
        model.load()
    except Exception as exc:
        print(f"[ERROR] 模型加载失败: {exc}")
        return 2

    print(f"[INFO] 开始微调: epochs={args.epochs}, batch_size={args.batch_size}")
    try:
        result = model.finetune(
            train_samples=train_samples,
            output_dir=str(output_dir),
            epochs=args.epochs,
            batch_size=args.batch_size,
            learning_rate=args.learning_rate,
            lora_r=args.lora_r,
            lora_alpha=args.lora_alpha,
            eval_samples=eval_samples,
        )
    except Exception as exc:
        print(f"[ERROR] 微调失败: {exc}")
        return 3

    log_entry = {
        "total_steps": result.total_steps,
        "final_loss": result.final_loss,
        "i2t_at_10_pre": result.i2t_at_10_pre,
        "i2t_at_10_post": result.i2t_at_10_post,
        "elapsed_seconds": result.elapsed_seconds,
        "used_lora": result.used_lora,
    }
    write_log(output_dir, log_entry)
    print(f"[INFO] 微调完成: I2T@10 {result.i2t_at_10_pre:.4f} → {result.i2t_at_10_post:.4f}")
    print(f"[INFO] 结果已保存: {output_dir / 'finetune_result.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
