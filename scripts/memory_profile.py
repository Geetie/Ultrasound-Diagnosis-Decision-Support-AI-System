import argparse
import asyncio
import json
import sys

from app.inference.gpu_monitor import SystemMemoryProfiler


async def main() -> None:
    parser = argparse.ArgumentParser(description="Compact mode memory profiling (64GB config)")
    parser.add_argument("--budget-mb", type=float, default=22048.0, help="Memory budget limit in MB (default: 22GB)")
    parser.add_argument("--output", default="memory_profile_report.json")
    args = parser.parse_args()

    profiler = SystemMemoryProfiler(budget_limit_mb=args.budget_mb)

    print("Profiling system memory usage...")
    print(f"  Budget limit: {args.budget_mb:.0f}MB ({args.budget_mb/1024:.1f}GB)")

    report = await profiler.profile_all_services()

    report_dict = {
        "deployment_profile": report.deployment_profile,
        "total_system_memory_mb": report.total_system_memory_mb,
        "measured_total_used_mb": f"{report.measured_total_used_mb:.0f}",
        "measured_available_mb": f"{report.measured_available_mb:.0f}",
        "budget_limit_mb": f"{report.budget_limit_mb:.0f}",
        "within_budget": report.within_budget,
        "component_breakdown": {
            k: f"{v:.0f}" for k, v in report.component_breakdown.items()
        },
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report_dict, f, indent=2, ensure_ascii=False)

    print("\nMemory Profile Report:")
    print(f"  Total system memory: {report.total_system_memory_mb:.0f}MB ({report.total_system_memory_mb/1024:.1f}GB)")
    print(f"  Measured used: {report.measured_total_used_mb:.0f}MB ({report.measured_total_used_mb/1024:.1f}GB)")
    print(f"  Available: {report.measured_available_mb:.0f}MB ({report.measured_available_mb/1024:.1f}GB)")
    print(f"  Budget limit: {report.budget_limit_mb:.0f}MB ({report.budget_limit_mb/1024:.1f}GB)")
    print(f"  WITHIN BUDGET: {report.within_budget}")
    print("\n  Component Breakdown:")
    for name, mem_mb in sorted(report.component_breakdown.items(), key=lambda x: -x[1]):
        print(f"    {name}: {mem_mb:.0f}MB ({mem_mb/1024:.2f}GB)")
    print(f"\nReport saved to {args.output}")

    sys.exit(0 if report.within_budget else 1)


if __name__ == "__main__":
    asyncio.run(main())
