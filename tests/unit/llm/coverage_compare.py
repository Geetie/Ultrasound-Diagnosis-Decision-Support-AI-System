"""LLM测试套件覆盖率对比与性能分析工具

用法：
    python tests/unit/llm/coverage_compare.py --baseline   # 运行并保存基线
    python tests/unit/llm/coverage_compare.py --compare    # 运行并与基线对比
    python tests/unit/llm/coverage_compare.py --report     # 生成完整报告
"""
import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
LLM_TEST_DIR = PROJECT_ROOT / "tests" / "unit" / "llm"
BASELINE_FILE = LLM_TEST_DIR / ".benchmark_baseline.json"
REPORT_FILE = LLM_TEST_DIR / ".optimization_report.json"

COV_SOURCES = ["app.inference"]


def run_tests(extra_args=None):
    cmd = [
        sys.executable, "-m", "pytest",
        str(LLM_TEST_DIR),
        "--tb=no", "-q",
        f"--cov={','.join(COV_SOURCES)}",
        "--cov-report=term-missing",
        "--cov-report=json",
        "--durations=0",
    ]
    if extra_args:
        cmd.extend(extra_args)

    start = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    elapsed = time.time() - start

    return {
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "elapsed": elapsed,
        "timestamp": datetime.now().isoformat(),
    }


def parse_coverage_output(stdout):
    coverage_data = {}
    current_module = None
    for line in stdout.split("\n"):
        line = line.strip()
        if line.startswith("app/"):
            parts = line.split()
            if len(parts) >= 4:
                current_module = parts[0]
                try:
                    coverage_data[current_module] = {
                        "stmts": int(parts[1]),
                        "miss": int(parts[2]),
                        "cover_pct": parts[3].replace("%", ""),
                        "missing_lines": parts[4] if len(parts) > 4 else "",
                    }
                except (ValueError, IndexError):
                    logger.warning("parse_coverage_output: 未预期异常，按原逻辑降级", exc_info=True)
                    pass
        elif "TOTAL" in line:
            parts = line.split()
            if len(parts) >= 4:
                try:
                    coverage_data["TOTAL"] = {
                        "stmts": int(parts[1]),
                        "miss": int(parts[2]),
                        "cover_pct": parts[3].replace("%", ""),
                    }
                except (ValueError, IndexError):
                    logger.warning("parse_coverage_output: 未预期异常，按原逻辑降级", exc_info=True)
                    pass
    return coverage_data


def parse_durations(stdout):
    durations = []
    in_durations = False
    for line in stdout.split("\n"):
        if "slowest durations" in line.lower() or (line.startswith("=") and "durations" in line):
            in_durations = True
            continue
        if in_durations and line.strip():
            parts = line.strip().split()
            if len(parts) >= 3:
                try:
                    dur = float(parts[0].replace("s", ""))
                    durations.append({"duration_s": dur, "test": " ".join(parts[2:])})
                except ValueError:
                    logger.warning("parse_durations: 未预期异常，按原逻辑降级", exc_info=True)
                    pass
    return durations


def save_baseline(data):
    BASELINE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"基线已保存到 {BASELINE_FILE}")


def load_baseline():
    if BASELINE_FILE.exists():
        return json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    return None


def compare_with_baseline(current):
    baseline = load_baseline()
    if not baseline:
        print("未找到基线数据，请先运行 --baseline")
        return None

    report = {
        "baseline_timestamp": baseline["timestamp"],
        "current_timestamp": current["timestamp"],
        "performance": {
            "baseline_elapsed_s": baseline["elapsed"],
            "current_elapsed_s": current["elapsed"],
            "improvement_pct": round(
                (1 - current["elapsed"] / baseline["elapsed"]) * 100, 1
            ) if baseline["elapsed"] > 0 else 0,
        },
        "coverage": {},
    }

    base_cov = parse_coverage_output(baseline["stdout"])
    curr_cov = parse_coverage_output(current["stdout"])

    for module in set(list(base_cov.keys()) + list(curr_cov.keys())):
        b = base_cov.get(module, {})
        c = curr_cov.get(module, {})
        report["coverage"][module] = {
            "baseline_pct": b.get("cover_pct", "N/A"),
            "current_pct": c.get("cover_pct", "N/A"),
            "change": (
                f"{float(c.get('cover_pct', 0)) - float(b.get('cover_pct', 0)):+.1f}%"
                if b.get("cover_pct") and c.get("cover_pct")
                else "N/A"
            ),
        }

    REPORT_FILE.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def print_report(report):
    print("\n" + "=" * 70)
    print("LLM测试套件优化报告")
    print("=" * 70)

    perf = report["performance"]
    print("\n📊 性能对比:")
    print(f"  基线时间: {perf['baseline_elapsed_s']:.1f}s")
    print(f"  当前时间: {perf['current_elapsed_s']:.1f}s")
    print(f"  改善幅度: {perf['improvement_pct']}%")

    print("\n📈 覆盖率对比:")
    for module, data in report["coverage"].items():
        print(f"  {module}: {data['baseline_pct']} → {data['current_pct']} ({data['change']})")

    print("\n" + "=" * 70)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    mode = sys.argv[1]

    if mode == "--baseline":
        print("运行基线测试...")
        data = run_tests()
        save_baseline(data)
        print(f"基线运行时间: {data['elapsed']:.1f}s")

    elif mode == "--compare":
        print("运行当前测试...")
        data = run_tests()
        report = compare_with_baseline(data)
        if report:
            print_report(report)

    elif mode == "--report":
        if REPORT_FILE.exists():
            report = json.loads(REPORT_FILE.read_text(encoding="utf-8"))
            print_report(report)
        else:
            print("报告未生成，请先运行 --compare")


if __name__ == "__main__":
    main()
