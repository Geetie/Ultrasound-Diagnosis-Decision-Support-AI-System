#!/usr/bin/env python3
"""按轨道精准运行测试套件

用法:
    python scripts/run_track_tests.py sec              # 运行SEC轨道测试
    python scripts/run_track_tests.py sec --deep       # 运行SEC主+次轨道测试
    python scripts/run_track_tests.py llm inf          # 运行LLM和INF轨道测试
    python scripts/run_track_tests.py --list           # 列出所有轨道及测试文件数
    python scripts/run_track_tests.py sec --failfast   # 首个失败即停止
    python scripts/run_track_tests.py sec -v           # 详细输出
    python scripts/run_track_tests.py sec --benchmark  # 包含性能基准测试
    python scripts/run_track_tests.py sec --file test_auth.py          # 仅运行指定文件
    python scripts/run_track_tests.py sec --class TestAuth             # 仅运行指定测试类
    python scripts/run_track_tests.py sec --test test_login            # 仅运行指定测试方法
    python scripts/run_track_tests.py sec --phase auth                 # 仅运行匹配阶段关键字的测试
    python scripts/run_track_tests.py sec --skip-slow                  # 跳过慢速测试
    python scripts/run_track_tests.py sec --report                     # 显示耗时汇总报告
    python scripts/run_track_tests.py sec --compare-baseline           # 与基线对比
    python scripts/run_track_tests.py sec --changed-only               # 仅运行变更文件的测试
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASELINE_DIR = PROJECT_ROOT / "tests" / ".benchmark_baselines"

TRACKS = {
    "sec":  {"name": "SEC 安全合规", "dirs": ["tests/unit/sec", "tests/benchmark/sec"]},
    "sg":   {"name": "SG 安全网关",  "dirs": ["tests/unit/sg", "tests/benchmark/sg"]},
    "llm":  {"name": "LLM 推理",    "dirs": ["tests/unit/llm", "tests/integration/llm"]},
    "api":  {"name": "API 接口",    "dirs": ["tests/unit/api"]},
    "inf":  {"name": "INF 基础设施", "dirs": ["tests/unit/inf"]},
    "obs":  {"name": "OBS 可观测性", "dirs": ["tests/unit/obs", "tests/benchmark/obs"]},
    "data": {"name": "DATA 数据",   "dirs": ["tests/unit/data"]},
    "emb":  {"name": "EMB 嵌入",    "dirs": ["tests/unit/emb"]},
    "rag":  {"name": "RAG 检索",    "dirs": ["tests/unit/rag"]},
    "edg":  {"name": "EDG 边缘端",  "dirs": ["tests/unit/edg"]},
}


def count_test_files(dirs):
    count = 0
    for d in dirs:
        p = PROJECT_ROOT / d
        if p.exists():
            count += len(list(p.glob("test_*.py")))
    return count


def list_tracks():
    print(f"{'轨道':<8} {'名称':<16} {'目录':<40} {'文件数'}")
    print("-" * 80)
    for code, info in TRACKS.items():
        file_count = count_test_files(info["dirs"])
        dirs_str = ", ".join(info["dirs"])
        print(f"{code:<8} {info['name']:<16} {dirs_str:<40} {file_count}")


def parse_durations(output_text):
    file_times = {}
    for match in re.finditer(r'^([\d.]+)s\s+(.+)$', output_text, re.MULTILINE):
        duration = float(match.group(1))
        path = match.group(2).strip()
        file_path = path.split("::")[0]
        file_times[file_path] = file_times.get(file_path, 0.0) + duration
    return file_times


def save_baseline(track_code, file_times):
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    baseline_file = BASELINE_DIR / f"{track_code}_baseline.json"
    data = {"timestamp": time.time(), "file_times": file_times}
    baseline_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def load_baseline(track_code):
    baseline_file = BASELINE_DIR / f"{track_code}_baseline.json"
    if not baseline_file.exists():
        return None
    return json.loads(baseline_file.read_text())


def show_report(file_times):
    print("\n" + "=" * 60)
    print("测试报告 - 按文件耗时汇总")
    print("=" * 60)
    total = 0.0
    for file_path, duration in sorted(file_times.items(), key=lambda x: -x[1]):
        print(f"  {file_path:<50} {duration:>6.2f}s")
        total += duration
    print("-" * 60)
    print(f"  {'总计':<50} {total:>6.2f}s")
    print("=" * 60)


def compare_baseline(track_code, current_times):
    baseline = load_baseline(track_code)
    if baseline is None:
        print(f"\n未找到轨道 {track_code} 的基线数据，已保存当前结果作为新基线。")
        save_baseline(track_code, current_times)
        return
    baseline_times = baseline.get("file_times", {})
    print("\n" + "=" * 70)
    print(f"基线对比 - 轨道 {track_code}")
    print("=" * 70)
    print(f"  {'文件':<45} {'当前':>8} {'基线':>8} {'差异':>8}")
    print("-" * 70)
    all_files = sorted(set(list(current_times.keys()) + list(baseline_times.keys())))
    for f in all_files:
        cur = current_times.get(f, 0.0)
        base = baseline_times.get(f, 0.0)
        diff = cur - base
        sign = "+" if diff >= 0 else ""
        cur_str = f"{cur:.2f}s" if f in current_times else "  ---  "
        base_str = f"{base:.2f}s" if f in baseline_times else "  ---  "
        diff_str = f"{sign}{diff:.2f}s"
        print(f"  {f:<45} {cur_str:>8} {base_str:>8} {diff_str:>8}")
    print("=" * 70)
    save_baseline(track_code, current_times)


def get_changed_files():
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=str(PROJECT_ROOT),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []
    return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]


def resolve_test_file(file_arg, track_codes):
    if os.path.isabs(file_arg):
        return file_arg
    for code in track_codes:
        if code not in TRACKS:
            continue
        for d in TRACKS[code]["dirs"]:
            candidate = PROJECT_ROOT / d / file_arg
            if candidate.exists():
                return str(candidate)
    direct = PROJECT_ROOT / file_arg
    if direct.exists():
        return str(direct)
    return file_arg


def run_tracks(track_codes, deep=False, failfast=False, verbose=False,
               benchmark=False, file=None, klass=None, test=None,
               phase=None, skip_slow=False, report=False,
               compare_baseline_flag=False, changed_only=False):
    pytest_args = [sys.executable, "-m", "pytest"]
    env = None
    changed_files = []

    if changed_only:
        changed_files = get_changed_files()
        if changed_files:
            env = os.environ.copy()
            env["CHANGED_FILES"] = os.pathsep.join(changed_files)
        else:
            print("未检测到变更文件，将运行全部测试。")

    if deep:
        marker_expr = " or ".join(f"track_{c}" for c in track_codes)
        if skip_slow:
            marker_expr = f"({marker_expr}) and not slow"
        pytest_args.extend(["-m", marker_expr])
        if file:
            pytest_args.append(resolve_test_file(file, track_codes))
        else:
            pytest_args.append("tests/")
    else:
        target_dirs = []
        for code in track_codes:
            if code not in TRACKS:
                print(f"未知轨道: {code}")
                print(f"可用轨道: {', '.join(TRACKS.keys())}")
                sys.exit(1)
            for d in TRACKS[code]["dirs"]:
                full_path = PROJECT_ROOT / d
                if not full_path.exists():
                    continue
                if changed_only and changed_files:
                    src_prefix = d.replace("tests/unit/", "src/").replace(
                        "tests/integration/", "src/").replace("tests/benchmark/", "src/")
                    if not any(cf.startswith(src_prefix) for cf in changed_files):
                        continue
                target_dirs.append(str(full_path))

        if benchmark and not changed_only:
            for code in track_codes:
                bench_dir = PROJECT_ROOT / "tests" / "benchmark" / code
                if bench_dir.exists():
                    target_dirs.append(str(bench_dir))

        if file:
            pytest_args.append(resolve_test_file(file, track_codes))
        else:
            pytest_args.extend(target_dirs)

    if not deep and skip_slow:
        pytest_args.extend(["-m", "not slow"])

    if phase:
        pytest_args.extend(["-k", phase])

    node_target = None
    for i, a in enumerate(pytest_args):
        if not a.startswith("-") and ("/" in a or a.endswith(".py") or a.startswith("tests")):
            node_target = i

    if klass and node_target is not None:
        pytest_args[node_target] = f"{pytest_args[node_target]}::{klass}"
    elif klass:
        pytest_args.append(f"::{klass}")

    if test and node_target is not None:
        pytest_args[node_target] = f"{pytest_args[node_target]}::{test}"
    elif test:
        pytest_args.append(f"::{test}")

    pytest_args.extend(["--tb=short", "-q"])

    if failfast:
        pytest_args.append("-x")
    if verbose:
        pytest_args.append("-v")

    if report or compare_baseline_flag:
        pytest_args.append("--durations=0")

    print(f"运行命令: {' '.join(pytest_args)}")
    print()

    if report or compare_baseline_flag:
        result = subprocess.run(pytest_args, cwd=str(PROJECT_ROOT),
                                capture_output=True, text=True, env=env)
        print(result.stdout)
        if result.stderr:
            print(result.stderr)
        file_times = parse_durations(result.stdout)
        if report:
            show_report(file_times)
        if compare_baseline_flag:
            track_key = "_".join(track_codes)
            compare_baseline(track_key, file_times)
        sys.exit(result.returncode)
    else:
        result = subprocess.run(pytest_args, cwd=str(PROJECT_ROOT), env=env)
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description="按轨道精准运行测试套件")
    parser.add_argument("tracks", nargs="*", help="轨道代码 (sec/sg/llm/api/inf/obs/data/emb/rag/edg)")
    parser.add_argument("--list", action="store_true", help="列出所有轨道及测试文件数")
    parser.add_argument("--deep", action="store_true", help="包含次轨道标记的测试（跨目录）")
    parser.add_argument("--failfast", "-x", action="store_true", help="首个失败即停止")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument("--benchmark", action="store_true", help="包含性能基准测试")
    parser.add_argument("--file", metavar="FILE", help="仅运行轨道内的指定测试文件")
    parser.add_argument("--class", metavar="CLASS", dest="klass", help="仅运行指定测试类")
    parser.add_argument("--test", metavar="TEST", help="仅运行指定测试方法")
    parser.add_argument("--phase", metavar="PHASE", help="仅运行匹配阶段关键字的测试")
    parser.add_argument("--skip-slow", action="store_true", help="跳过标记为慢速的测试")
    parser.add_argument("--report", action="store_true", help="运行后显示按文件耗时的汇总报告")
    parser.add_argument("--compare-baseline", action="store_true", help="与已保存的基线数据对比")
    parser.add_argument("--changed-only", action="store_true", help="仅运行变更源文件对应的测试")

    args = parser.parse_args()

    if args.list:
        list_tracks()
        return

    if not args.tracks:
        parser.print_help()
        sys.exit(1)

    run_tracks(args.tracks, deep=args.deep, failfast=args.failfast,
               verbose=args.verbose, benchmark=args.benchmark,
               file=args.file, klass=args.klass, test=args.test,
               phase=args.phase, skip_slow=args.skip_slow,
               report=args.report, compare_baseline_flag=args.compare_baseline,
               changed_only=args.changed_only)


if __name__ == "__main__":
    main()
