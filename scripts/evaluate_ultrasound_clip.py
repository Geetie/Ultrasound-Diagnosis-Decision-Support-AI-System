"""Ultrasound-CLIP 评估 CLI 脚本。

用法示例:
    # 评估微调后模型
    python scripts/evaluate_ultrasound_clip.py \\
        --model-path ./models/ultrasound-clip-finetuned \\
        --test-dataset-path ./data/busi_test \\
        --k 10

    # 直接用真实公开数据集（US-365K 的 jsonl，字段为 image/caption，无 label）
    python scripts/evaluate_ultrasound_clip.py \\
        --model-path <本地权重> \\
        --test-dataset-path data/external/us365k/test.jsonl \\
        --max-samples 8 --device cpu

    # 注意：数据集路径不存在时**默认报错退出**（不会再用合成样本糊过去）；
    # 仅冒烟测试可显式加 --allow-synthetic。

输出:
    - I2T@k 分数
    - top-k 准确率
    - 检索延迟 P50/P99
    - 报告写入: --output 指定的 JSON 文件(默认 eval_report.json)
"""

from __future__ import annotations

import argparse
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
        "--allow-synthetic", action="store_true",
        help=(
            "冒烟专用：数据集路径不存在时允许用合成占位样本（默认关闭）。"
            "开启后产出的指标**没有意义**，禁止用于验收/汇报。"
        ),
    )
    parser.add_argument(
        "--output", default="eval_report.json",
        help="评估报告输出路径(默认 eval_report.json)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print(f"[INFO] 加载测试集: {args.test_dataset_path}")
    adapter = BUSIAdapter(
        dataset_path=args.test_dataset_path,
        use_hf=args.use_hf,
        allow_synthetic=args.allow_synthetic,
    )
    try:
        test_samples = adapter.load(max_samples=args.max_samples)
    except FileNotFoundError as exc:
        # 退出码 4：**数据集侧**失败（区别于 2=模型加载失败 / 3=评估失败）。
        # 明确失败而不是用合成样本产出一份"看起来正常"的报告。
        print(f"[ERROR] {exc}")
        return 4
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
