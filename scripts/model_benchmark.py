import argparse
import asyncio
import json
import sys

from app.inference.benchmark import ModelBenchmark, ModelCandidate
from app.inference.factory import create_inference_engine


async def main() -> None:
    parser = argparse.ArgumentParser(description="LLM base model selection benchmark")
    parser.add_argument("--cases", required=True, help="Path to test cases JSON file")
    parser.add_argument("--output", default="benchmark_report.json")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--candidates",
        nargs="+",
        default=["qwen2.5-7b", "chatglm4-9b", "internlm2.5-7b"],
        help="Model candidates to benchmark",
    )
    args = parser.parse_args()

    with open(args.cases, encoding="utf-8") as f:
        cases = json.load(f)

    print(f"Loaded {len(cases)} test cases from {args.cases}")
    print(f"Candidates: {args.candidates}")

    candidate_configs = {
        "qwen2.5-7b": ModelCandidate(
            name="Qwen2.5-7B",
            engine_type="llama.cpp",
            model_path_or_id="Qwen2.5-7B-Instruct-GGUF",
            quantization="Q4_K_M",
            parameter_count="7B",
            context_length=32768,
        ),
        "chatglm4-9b": ModelCandidate(
            name="ChatGLM4-9B",
            engine_type="llama.cpp",
            model_path_or_id="chatglm4-9b-GGUF",
            quantization="Q4_K_M",
            parameter_count="9B",
            context_length=8192,
        ),
        "internlm2.5-7b": ModelCandidate(
            name="InternLM2.5-7B",
            engine_type="llama.cpp",
            model_path_or_id="InternLM2.5-7B-Chat-GGUF",
            quantization="Q4_K_M",
            parameter_count="7B",
            context_length=32768,
        ),
    }

    benchmark = ModelBenchmark(
        concurrency=args.concurrency,
        request_timeout_seconds=args.timeout,
    )

    results = []
    for candidate_key in args.candidates:
        candidate = candidate_configs.get(candidate_key)
        if not candidate:
            print(f"Unknown candidate: {candidate_key}, skipping")
            continue

        print(f"\nBenchmarking {candidate.name}...")
        engine = create_inference_engine(
            engine_type=candidate.engine_type,
            timeout_seconds=args.timeout,
        )

        try:
            result = await benchmark.benchmark_candidate(
                engine=engine,
                candidate=candidate,
                test_cases=cases,
            )
            results.append(result)
            print(f"  Accuracy: {result.accuracy:.2%}")
            print(f"  P99 Latency: {result.p99_latency_ms:.0f}ms")
            print(f"  Hallucination Rate: {result.hallucination_rate:.2%}")
        except Exception as exc:
            print(f"  Benchmark failed: {exc}")
        finally:
            await engine.close()

    if not results:
        print("No benchmark results, exiting")
        sys.exit(1)

    report = benchmark.generate_report(
        results=results,
        test_set_name="ultrasound_medical_qa",
        test_set_size=len(cases),
    )

    report_dict = report.to_dict()
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report_dict, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print("Model Selection Report")
    print(f"{'='*60}")
    for r in results:
        c = r.candidate
        acc_pass = "✓" if r.accuracy >= 0.85 else "✗"
        lat_pass = "✓" if r.p99_latency_ms <= 3000 else "✗"
        hal_pass = "✓" if r.hallucination_rate <= 0.005 else "✗"
        print(f"\n{c.name} ({c.quantization}):")
        print(f"  Accuracy: {r.accuracy:.2%} {acc_pass} (≥85%)")
        print(f"  P99 Latency: {r.p99_latency_ms:.0f}ms {lat_pass} (≤3000ms)")
        print(f"  Hallucination: {r.hallucination_rate:.2%} {hal_pass} (≤0.5%)")

    print(f"\nWinner: {report.winner}")
    print(f"Report saved to {args.output}")

    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
