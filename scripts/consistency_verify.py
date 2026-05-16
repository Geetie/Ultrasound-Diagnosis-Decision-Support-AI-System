import argparse
import asyncio
import json
import sys

from app.inference.consistency import ConsistencyVerifier
from app.inference.factory import create_inference_engine


async def main() -> None:
    parser = argparse.ArgumentParser(description="vLLM vs llama.cpp consistency verification")
    parser.add_argument("--cases", required=True, help="Path to test cases JSON file")
    parser.add_argument("--llama-cpp-host", default="localhost")
    parser.add_argument("--llama-cpp-port", type=int, default=8001)
    parser.add_argument("--vllm-host", default="localhost")
    parser.add_argument("--vllm-port", type=int, default=8002)
    parser.add_argument("--vllm-model", default="")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--output", default="consistency_report.json")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    with open(args.cases, encoding="utf-8") as f:
        cases = json.load(f)

    print(f"Loaded {len(cases)} test cases from {args.cases}")

    llama_engine = create_inference_engine(
        engine_type="llama.cpp",
        llama_cpp_host=args.llama_cpp_host,
        llama_cpp_port=args.llama_cpp_port,
        timeout_seconds=args.timeout,
    )
    vllm_engine = create_inference_engine(
        engine_type="vllm",
        vllm_host=args.vllm_host,
        vllm_port=args.vllm_port,
        vllm_default_model=args.vllm_model,
        timeout_seconds=args.timeout,
    )

    verifier = ConsistencyVerifier(
        engine_a=llama_engine,
        engine_b=vllm_engine,
        engine_a_name="llama.cpp",
        engine_b_name="vLLM",
        concurrency=args.concurrency,
        request_timeout_seconds=args.timeout,
    )

    report = await verifier.verify(cases)

    report_json = verifier.report_to_json(report)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(report_json)

    print("\nConsistency Report:")
    print(f"  Total cases: {report.total_cases}")
    print(f"  Grading consistency: {report.grading_consistency_rate:.2%} (target: ≥95%)")
    print(f"  Clinical boundary diff: {report.clinical_boundary_diff_rate:.2%} (target: ≤1%)")
    print(f"  Hallucination rate diff: {report.hallucination_rate_diff:.2%} (target: ≤0.5%)")
    print(f"  Latency diff: {report.latency_diff_pct:.1f}% (target: ≤20%)")
    print(f"  PASSED: {report.passed}")
    print(f"\nReport saved to {args.output}")

    await llama_engine.close()
    await vllm_engine.close()

    sys.exit(0 if report.passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
