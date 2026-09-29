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
    parser.add_argument(
        "--structured-output",
        action="store_true",
        help="给两引擎追加完全相同的输出格式指令段（末行 `分级: X`），消除自由文本噪音",
    )
    parser.add_argument(
        "--use-chat",
        action="store_true",
        help="llama.cpp 也走 /v1/chat/completions（与 vLLM 同形态，消除模板差异）",
    )
    parser.add_argument(
        "--guided-json",
        action="store_true",
        help="P0-2 约束解码：采样层 schema 强制（vLLM guided_json / llama.cpp GBNF），"
        "分级输出 {\"grading\": \"<枚举>\"}；启用时不追加 prompt 指令段",
    )
    args = parser.parse_args()

    with open(args.cases, encoding="utf-8") as f:
        loaded = json.load(f)
    # 兼容两种格式：裸列表，或 generate_test_cases/server_build_eval_cases 的
    # {"dataset_version":..., "total_cases":..., "cases": [...]} 包裹结构。
    cases = loaded["cases"] if isinstance(loaded, dict) else loaded

    print(f"Loaded {len(cases)} test cases from {args.cases}")

    llama_engine = create_inference_engine(
        engine_type="llama.cpp",
        llama_cpp_host=args.llama_cpp_host,
        llama_cpp_port=args.llama_cpp_port,
        timeout_seconds=args.timeout,
        llama_cpp_use_chat=args.use_chat,
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
        structured_output=args.structured_output,
        guided_json=args.guided_json,
    )

    report = await verifier.verify(cases)

    report_json = verifier.report_to_json(report)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(report_json)

    # P0-2 分层口径（16 号 A.2.2 管理决策项）：常规+高风险 与 边界模糊用例
    # 分开统计——边界用例为刻意 ±1.5 抖动构造，两引擎分歧是设计内行为。
    rows = report.case_rows or []

    def _tier(cats: list[str | None]) -> dict:
        sub = [r for r in rows if (r.get("category") or "") in cats or
               (cats == ["boundary"] and (r.get("category") or "").startswith("boundary_"))]
        if not sub:
            return {}
        consistent = sum(1 for r in sub if r.get("consistent"))
        return {
            "total": len(sub),
            "grading_consistency_rate": round(consistent / len(sub), 4),
        }

    categories = {r.get("category") for r in rows}
    non_boundary = sorted(c for c in categories if c and not c.startswith("boundary"))
    report_dict = json.loads(report_json)
    report_dict["stratified_criteria"] = {
        "regular_high_risk": _tier(non_boundary),
        "boundary_fuzzy": _tier(["boundary"]),
        "note": "分层口径依据 10 号 v1.3 修订：边界模糊用例（刻意阈值抖动构造）"
        "两引擎分歧属设计内行为，≥95% 门控适用于常规+高风险层",
    }
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(json.dumps(report_dict, ensure_ascii=False, indent=2))

    strat = report_dict["stratified_criteria"]
    print("\nConsistency Report:")
    print(f"  Total cases: {report.total_cases}")
    print(f"  Grading consistency: {report.grading_consistency_rate:.2%} (target: ≥95%)")
    print(f"  Clinical boundary diff: {report.clinical_boundary_diff_rate:.2%} (target: ≤1%)")
    print(f"  Hallucination rate diff: {report.hallucination_rate_diff:.2%} (target: ≤0.5%)")
    print(f"  Latency diff: {report.latency_diff_pct:.1f}% (target: ≤20%)")
    print(f"  PASSED: {report.passed}")
    if strat:
        print(
            f"  分层口径: 常规+高风险={strat['regular_high_risk'].get('grading_consistency_rate')} "
            f"(n={strat['regular_high_risk'].get('total')}) | "
            f"边界模糊={strat['boundary_fuzzy'].get('grading_consistency_rate')} "
            f"(n={strat['boundary_fuzzy'].get('total')})"
        )
    print(f"\nReport saved to {args.output}")

    await llama_engine.close()
    await vllm_engine.close()

    sys.exit(0 if report.passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
