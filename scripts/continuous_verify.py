"""
持续验证流水线脚本

执行一致性验证和漂移检测，生成综合报告。
支持CI/CD集成，通过退出码表示验证结果。

退出码：
  0 - 全部通过
  1 - 警告（边界差异率偏高但未超阈值）
  2 - 失败（一致性/幻觉率/边界差异率未达标）
  3 - 运行时错误
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.inference.consistency import ConsistencyVerifier
from app.inference.drift_detector import DriftDetector, DriftSeverity, MetricType
from app.inference.factory import create_inference_engine
from app.inference.verification_config import get_verification_config


def load_test_cases(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("cases", [])


async def run_verification(
    test_cases_path: str,
    report_output_path: str,
    drift_baseline_dir: str,
    model_name: str,
    model_version: str,
    llama_cpp_host: str = "localhost",
    llama_cpp_port: int = 8001,
    vllm_host: str = "localhost",
    vllm_port: int = 8002,
) -> dict[str, Any]:
    test_cases = load_test_cases(test_cases_path)
    if not test_cases:
        print("ERROR: No test cases found")
        return {"status": "error", "message": "no test cases"}

    config = get_verification_config()

    try:
        engine_a = create_inference_engine("llama.cpp", llama_cpp_host=llama_cpp_host, llama_cpp_port=llama_cpp_port)
        engine_b = create_inference_engine("vllm", vllm_host=vllm_host, vllm_port=vllm_port, vllm_default_model=model_name)
    except Exception as e:
        print(f"ERROR: Could not create real engines: {e}")
        print("ERROR: Real inference engines are required for verification. Aborting.")
        return {"status": "error", "message": f"engine creation failed: {e}"}

    verifier = ConsistencyVerifier(
        engine_a=engine_a,
        engine_b=engine_b,
        engine_a_name=f"{model_name}-llama_cpp",
        engine_b_name=f"{model_name}-vllm",
        config=config,
    )

    print(f"Running consistency verification on {len(test_cases)} cases...")
    report = await verifier.verify(test_cases)

    drift_detector = DriftDetector(
        model_name=model_name,
        model_version=model_version,
        baseline_dir=drift_baseline_dir,
    )

    drift_report = drift_detector.run_detection(
        current_metrics={
            MetricType.ACCURACY: report.grading_consistency_rate,
            MetricType.HALLUCINATION_RATE: report.hallucination_rate_diff,
        },
        test_data_version=config.test_data_version,
    )

    combined = {
        "timestamp": datetime.now().isoformat(),
        "model_name": model_name,
        "model_version": model_version,
        "test_data_version": config.test_data_version,
        "consistency": verifier.report_to_dict(report),
        "drift": drift_detector.report_to_dict(drift_report),
        "verdict": "pass" if report.passed and drift_report.overall_status == DriftSeverity.NONE else "fail",
    }

    if report.passed and drift_report.overall_status != DriftSeverity.NONE:
        combined["verdict"] = "warning"

    report_path = Path(report_output_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)

    print("\n=== Verification Report ===")
    print(f"Verdict: {combined['verdict'].upper()}")
    print(f"Total cases: {report.total_cases}")
    print(f"Grading consistency: {report.grading_consistency_rate:.2%}")
    print(f"Boundary diff rate: {report.clinical_boundary_diff_rate:.2%}")
    print(f"Hallucination rate diff: {report.hallucination_rate_diff:.2%}")
    print(f"Drift status: {drift_report.overall_status.value}")
    print(f"Report saved to: {report_path}")

    await engine_a.close()
    await engine_b.close()

    return combined


def main():
    parser = argparse.ArgumentParser(description="Continuous Verification Pipeline")
    parser.add_argument("--test-cases", default="app/inference/data/consistency_test_cases.json", help="Path to test cases JSON")
    parser.add_argument("--report-output", default="reports/verification_report.json", help="Path to save report")
    parser.add_argument("--drift-baseline-dir", default="data/drift_baselines", help="Drift baseline directory")
    parser.add_argument("--model-name", default="qwen2.5-7b", help="Model name")
    parser.add_argument("--model-version", default="1.0", help="Model version")
    parser.add_argument("--llama-cpp-host", default=None, help="llama.cpp host (default: from settings)")
    parser.add_argument("--llama-cpp-port", type=int, default=None, help="llama.cpp port (default: from settings)")
    parser.add_argument("--vllm-host", default=None, help="vLLM host (default: from settings)")
    parser.add_argument("--vllm-port", type=int, default=None, help="vLLM port (default: from settings)")
    args = parser.parse_args()

    try:
        from app.config.settings import get_settings
        settings = get_settings()
        llama_cpp_host = args.llama_cpp_host or settings.llama_cpp_host
        llama_cpp_port = args.llama_cpp_port or settings.llama_cpp_port
        vllm_host = args.vllm_host or settings.vllm_host
        vllm_port = args.vllm_port or settings.vllm_port
    except Exception:
        llama_cpp_host = args.llama_cpp_host or "localhost"
        llama_cpp_port = args.llama_cpp_port or 8001
        vllm_host = args.vllm_host or "localhost"
        vllm_port = args.vllm_port or 8002

    combined = asyncio.run(run_verification(
        test_cases_path=args.test_cases,
        report_output_path=args.report_output,
        drift_baseline_dir=args.drift_baseline_dir,
        model_name=args.model_name,
        model_version=args.model_version,
        llama_cpp_host=llama_cpp_host,
        llama_cpp_port=llama_cpp_port,
        vllm_host=vllm_host,
        vllm_port=vllm_port,
    ))

    if combined.get("status") == "error":
        sys.exit(3)

    verdict = combined.get("verdict", "fail")
    if verdict == "pass":
        sys.exit(0)
    elif verdict == "warning":
        sys.exit(1)
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()
