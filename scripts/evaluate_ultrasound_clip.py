"""Ultrasound-CLIP 评估 CLI 脚本。

用法示例:
    # 评估微调后模型
    python scripts/evaluate_ultrasound_clip.py \\
        --model-path ./models/ultrasound-clip-finetuned \\
        --test-dataset-path ./data/busi_test \\
        --k 10

输出:
    - I2T@k 分数
    - top-k 准确率
    - 检索延迟 P50/P99
    - 报告写入: --output 指定的 JSON 文件(默认 eval_report.json)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.embedding.busi_adapter import BUSIAdapter  # noqa: E402
from app.embedding.clip_model import EvaluationResult, UltrasoundCLIPModel  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ultrasound-CLIP I2T@k 评估 CLI")
    parser.add_argument("--model-path", required=True, help="模型路径")
    parser.add_argument("--test-dataset-path", required=True, help="测试集路径")
    parser.add_argument("--k", type=int, default=10, help="top-k 值(默认 10)")
    parser.add_argument("--max-samples", type=int, default=None, help="最大样本数")
    parser.add_argument(
        "--device", default="cuda", choices=["cuda", "cpu"],
        help="推理设备(默认 cuda)",
    )
    parser.add_argument(
        "--use-lora", action="store_true",
        help="若模型是 LoRA adapter 则启用",
    )
    parser.add_argument(
        "--use-hf", action="store_true",
        help="使用 HuggingFace datasets 后端",
    )
    parser.add_argument(
        "--output", default="eval_report.json",
        help="评估报告输出路径(默认 eval_report.json)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print(f"[INFO] 加载测试集: {args.test_dataset_path}")
    adapter = BUSIAdapter(dataset_path=args.test_dataset_path, use_hf=args.use_hf)
    test_samples = adapter.load(max_samples=args.max_samples)
    print(f"[INFO] 测试样本数: {len(test_samples)}")

    if not test_samples:
        print("[ERROR] 无可用测试样本,退出")
        return 1

    print(f"[INFO] 加载模型: {args.model_path}")
    model = UltrasoundCLIPModel(device=args.device, use_lora=args.use_lora)
    try:
        model.load(model_path=args.model_path)
    except Exception as exc:
        print(f"[ERROR] 模型加载失败: {exc}")
        return 2

    print(f"[INFO] 开始评估: k={args.k}")
    try:
        result: EvaluationResult = model.evaluate(test_samples, k=args.k)
    except Exception as exc:
        print(f"[ERROR] 评估失败: {exc}")
        return 3

    print(f"[RESULT] I2T@{args.k} = {result.i2t_at_k:.4f}")
    print(f"[RESULT] 测试样本数 = {result.total_samples}")
    print(f"[RESULT] P50 延迟 = {result.latency_p50_ms:.2f}ms")
    print(f"[RESULT] P99 延迟 = {result.latency_p99_ms:.2f}ms")

    output_path = Path(args.output)
    output_path.write_text(result.to_json(), encoding="utf-8")
    print(f"[INFO] 报告已保存: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
