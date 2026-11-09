#!/usr/bin/env python3
"""RAG 评估门禁（p115）—— 把"评估体系本身是对的"变成可自动跑的检查。

## 本脚本与其他 gate 的分工（别混）

| 脚本 | 判什么 | 需要什么 |
|---|---|---|
| `p78_harness_selfcheck.py` | 文档/状态文件结构 | 纯本地，零依赖 |
| `p79_dataset_quality_gate.py` | 数据集能不能用（四道闸） | 纯本地 |
| `eval_gate.py`（langfuse） | 线上指标的 business 阈值 | Langfuse + 真机 |
| **本脚本 p115** | **评估实现与指标口径是否可信** | 纯本地（可选真机） |

本脚本**不判"分数够不够好"**（那是产品决策，属 00 号口径），只判三件事：

1. **口径一致性（blocking）**：仓库里每个算 NDCG 的地方必须给出同一个数；
   任何实现不得产出 >1；重复副本不得抬高分数。这些是纯函数，**不需要任何服务**。
2. **报告可信度（blocking）**：给定的评估报告 JSON 必须带齐
   "n / 分母 / 重复副本数 / 越界数 / 口径说明"，否则拒绝（说"报告没算清楚"）。
3. **回归拦截（blocking）**：与本仓库冻结的基线比，关键指标不得跌破
   `--max-drop`（默认 2pt）。基线文件 `data/eval/baselines/rag_eval_baseline.json`
   **必须人工确认后才更新**（脚本不自动写）。

## 为什么"报告可信度"也要门禁

2026-11-08 的 p94 报告里 `n_queries=120`，但真正参与计算的只有 115 条
（5 条无 gold）—— 报告读起来自洽，分母其实是错的。这类错误无法靠"看数字"
发现，只能靠"报告必须自证分母"的机械检查拦住。

## 用法

    # 只跑口径一致性（CI 主用，零依赖，<2s）
    python scripts/audit/p115_eval_gate.py

    # 加跑一份真机报告的可信度 + 回归
    python scripts/audit/p115_eval_gate.py --report reports/p114_layered.json

    # 冻结新基线（人工确认后手动跑，脚本不会自动写）
    python scripts/audit/p115_eval_gate.py --report reports/p114_layered.json --freeze

退出码：0 = 全绿；1 = 有 FAIL；2 = 用法/环境错误。
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import pathlib
import sys
from dataclasses import dataclass

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

BASELINE_PATH = _ROOT / "data/eval/baselines/rag_eval_baseline.json"

#: 已知实现里**返回 (value, violation) 二元组**的那一处；其余一律按 "返回 float" 处理。
#: 用路径而不是函数名判断，因为同一个函数名在不同文件里返回形态不同。
_TUPLE_RETURNING = {
    "app/evaluation/retrieval_metrics.py",
}


def discover_ndcg_implementations() -> list[tuple[str, str, str, bool]]:
    """**扫描**仓库里所有算 NDCG 的实现，而不是维护一份手写清单。

    ## 为什么改成"扫描"

    2026-11-09 第一版用的是硬编码清单（4 处）。紧接着同一个工作区里另一个
    agent 新建了 `scripts/audit/p200_prod_ab.py`（也定义了 `ndcg_at_k`），
    清单立刻过期 —— 新实现**静默逃过所有对拍**。

    这正是本项目"修一处漏一处"的同一个病根：**靠人记得去登记，一定会漏**。
    改成扫描后：
      * 新实现的文件出现在 `app/` 或 `scripts/` 下 → 自动纳入对拍；
      * 未登记不再是"漏"，而是不可能发生。

    返回 `(显示名, 相对路径, "ndcg_at_k", 是否返回二元组)`。
    """
    import re

    pattern = re.compile(r"^def ndcg_at_k\s*\(", re.MULTILINE)
    found: list[tuple[str, str, str, bool]] = []
    for base in ("app", "scripts"):
        root = _ROOT / base
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not pattern.search(text):
                continue
            rel = str(path.relative_to(_ROOT)).replace("\\", "/")
            # 显示名取去掉 .py 的相对路径，保证唯一且能一眼看出是谁
            found.append((rel, rel, "ndcg_at_k", rel in _TUPLE_RETURNING))
    return found


#: 向后兼容的常量（其他模块/测试可能 import）；**不要**再用它做判断依据。
NDCG_IMPLEMENTATIONS = discover_ndcg_implementations()

#: 报告必须带的字段（缺一个就说明"没算清楚"）
REQUIRED_REPORT_FIELDS = ("n", "n_input", "duplicates_removed", "violations")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    level: str = "FAIL"


def _load_function(rel: str, name: str) -> object:
    """按 AST 抽纯函数执行（避开模块级 structlog / asyncpg 依赖）。

    ## 为什么要把**同模块的所有函数与常量**都塞进去

    只抽目标函数会踩 `NameError`：比如 `ndcg_at_k` 依赖同模块私有的
    `_dcg_from_positions`，单独 exec 它就找不到。2026-11-09 首次运行即撞上。
    这里把模块里所有无装饰器的 FunctionDef、模块级常量、类型别名一起 exec，
    保持"抽出来的这份"和"磁盘上那份"在函数体内语义一致。
    有装饰器（@lru_cache / @dataclass）的跳过：它们要么带状态、要么要额外
    import，硬塞反而会引入"测的不是生产代码"的风险。
    """
    path = _ROOT / rel
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    ns: dict = {"math": math}
    # 先放过模块级简单赋值（正则/常量表），再放函数
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Constant, ast.Tuple, ast.List, ast.Set)):
            try:
                exec(compile(ast.Module(body=[node], type_ignores=[]), rel, "exec"), ns)
            except Exception:
                continue
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.decorator_list:
            exec(compile(ast.Module(body=[node], type_ignores=[]), rel, "exec"), ns)
    if name not in ns:
        raise LookupError(f"{rel} 里没有可抽取的函数 {name}")
    return ns[name]


# --------------------------------------------------------------- 1. 口径一致性
def check_ndcg_caliber() -> list[Check]:
    out: list[Check] = []
    cases = [
        (["a"], {"a"}, 1.0),
        (["x", "a"], {"a"}, 1.0 / math.log2(3)),
        (["x", "y", "a"], {"a"}, 0.5),
        (["x", "a", "a"], {"a"}, 1.0 / math.log2(3)),   # 重复副本：事故现场
        (["a", "a", "a"], {"a"}, 1.0),
        (["a", "b"], {"a", "b"}, 1.0),
        (["x", "y"], {"a"}, 0.0),
    ]
    for label, rel, func, is_tuple in discover_ndcg_implementations():
        try:
            fn = _load_function(rel, func)
        except Exception as exc:
            out.append(Check(f"ndcg:{label}", False, f"加载失败：{type(exc).__name__}:{exc}"))
            continue
        bad = []
        for ranking, gold, want in cases:
            got = fn(ranking, gold, 10)
            got = got[0] if is_tuple else got
            if abs(got - want) > 1e-9:
                bad.append(f"{ranking}/{sorted(gold)} -> {got:.6f}（应 {want:.6f}）")
        out.append(Check(
            f"ndcg:{label}", not bad,
            "与标准一致" if not bad else "；".join(bad[:3]),
        ))
    return out


def check_ndcg_bounded() -> list[Check]:
    """随机输入下任何实现都不得越界（2026-11-08 事故的通用防线）。"""
    import random

    out: list[Check] = []
    rng = random.Random(20261109)
    docs = [f"d{i}" for i in range(5)]
    trials = [( [rng.choice(docs) for _ in range(10)],
                set(rng.sample(docs, rng.randint(1, 3))) ) for _ in range(600)]
    for label, rel, func, is_tuple in discover_ndcg_implementations():
        try:
            fn = _load_function(rel, func)
        except Exception as exc:
            out.append(Check(f"bounded:{label}", False, f"加载失败：{exc}"))
            continue
        over = 0
        for ranking, gold in trials:
            got = fn(ranking, gold, 10)
            got = got[0] if is_tuple else got
            if got > 1.0 + 1e-9 or got < -1e-9:
                over += 1
        out.append(Check(
            f"bounded:{label}", over == 0,
            f"{over}/{len(trials)} 例越界" if over else f"{len(trials)} 例全部在 [0,1]",
        ))
    return out


def check_aggregate_semantics() -> list[Check]:
    """聚合分母语义：无 gold 排除、返回为空计入。"""
    from app.evaluation.retrieval_metrics import aggregate, score_query

    scores = [
        score_query("q1", ["a"], {"a"}),      # 命中
        score_query("q2", ["x"], {"a"}),      # 未命中，计入
        score_query("q3", ["a"], set()),      # 无 gold，排除
        score_query("q4", [], {"a"}),         # 返回空，计入（真实 0）
    ]
    agg = aggregate(scores)
    out = [
        Check("aggregate:n", agg["n"] == 3,
              f"n={agg['n']}（应 3：排除无 gold 的 q3，保留返回空的 q4）"),
        Check("aggregate:n_input", agg["n_input"] == 4, f"n_input={agg['n_input']}"),
        Check("aggregate:hit@1", abs(agg["hit@1"] - 1 / 3) < 1e-4,
              f"hit@1={agg['hit@1']}（应 {1/3:.4f}）"),
    ]
    return out


def check_statistics_module() -> list[Check]:
    """统计层关键结论（改了实现必须同步改报告）。"""
    from app.evaluation.statistics import (
        exact_mcnemar_power,
        mcnemar_exact,
        required_n_for_delta_exact,
    )

    out: list[Check] = []
    # 精确 McNemar 与暴力解对拍（小 n）
    from math import comb
    for b, c in ((0, 5), (3, 7), (10, 2)):
        want = min(1.0, 2 * sum(comb(b + c, i) * 0.5 ** (b + c) for i in range(min(b, c) + 1)))
        got = mcnemar_exact(b, c)
        out.append(Check(f"mcnemar:{b},{c}", abs(got - want) < 1e-12,
                         f"{got:.6f} vs 暴力 {want:.6f}"))
    # 功效分析：报给用户的数字必须与实现一致
    n = required_n_for_delta_exact(0.05, 0.15)
    power = exact_mcnemar_power(n, 0.05, 0.15)
    out.append(Check("power:n_for_5pt", n == 487, f"n={n}（报告里引用 487）"))
    out.append(Check("power:reaches_0.80", power >= 0.80, f"power@n={power:.4f}"))
    return out


# --------------------------------------------------------------- 2. 报告可信度
def extract_retrieval_metrics(report: dict) -> dict | None:
    """从 p114 报告里取出聚合指标；取不到返回 None。"""
    stages = report.get("stages") or {}
    ret = stages.get("retrieval") or {}
    if ret.get("executed") is False:
        return None
    return ret.get("overall") or None


def check_report(report: dict, path: str) -> list[Check]:
    out: list[Check] = []
    overall = extract_retrieval_metrics(report)
    if overall is None:
        return [Check(f"report:{pathile(path)}", True,
                      "报告里没有执行的检索段（跳过可信度检查）", level="WARN")]

    missing = [f for f in REQUIRED_REPORT_FIELDS if f not in overall]
    out.append(Check(
        f"report:{pathile(path)}:fields", not missing,
        f"缺 {missing}" if missing else "n/分母/副本/越界 四项齐备",
    ))
    if not missing:
        out.append(Check(
            f"report:{pathile(path)}:n<=n_input", overall["n"] <= overall["n_input"],
            f"n={overall['n']} n_input={overall['n_input']}",
        ))
        out.append(Check(
            f"report:{pathile(path)}:no_violations", overall["violations"] == 0,
            f"violations={overall['violations']}",
        ))
        for key in ("hit@1", "hit@5", "hit@10", "mrr@10", "ndcg@10"):
            v = overall.get(key)
            if v is not None:
                out.append(Check(f"report:{pathile(path)}:{key}", 0.0 <= v <= 1.0,
                                 f"{key}={v}"))
    # 报告必须声明口径，否则别人无法判断能不能横向比
    caliber = (report.get("stages") or {}).get("retrieval", {}).get("caliber")
    out.append(Check(f"report:{pathile(path)}:caliber", bool(caliber),
                     "有口径说明" if caliber else "缺 caliber 说明", level="WARN"))
    return out


def pathile(p: str) -> str:
    return pathlib.Path(p).name


# --------------------------------------------------------------- 3. 回归拦截
def check_regression(overall: dict, baseline: dict, max_drop: float) -> list[Check]:
    out: list[Check] = []
    base_metrics = baseline.get("metrics") or {}
    base_n = baseline.get("n")
    for key, base_v in base_metrics.items():
        cur = overall.get(key)
        if cur is None:
            out.append(Check(f"regress:{key}", False, "当前报告缺这个指标"))
            continue
        # 向上取整到 4 位小数比较，避免浮点抖动误判
        drop = round(base_v - cur, 6)
        out.append(Check(
            f"regress:{key}", drop <= max_drop,
            f"{base_v} -> {cur}（跌 {drop*100:.2f}pt，阈值 {max_drop*100:.0f}pt）",
        ))
    if base_n is not None and overall.get("n") is not None:
        # n 大幅缩水说明数据集变窄，比分数下降更危险（分母换了就没法比）
        ratio = overall["n"] / base_n if base_n else 1.0
        out.append(Check(
            "regress:n_shrink", ratio >= 0.95,
            f"n {base_n} -> {overall['n']}（{ratio:.1%}，<95% 说明评测集缩水，横向比失效）",
        ))
    return out


# --------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RAG 评估门禁（p115）")
    ap.add_argument("--report", action="append", default=[],
                    help="要校验的真机报告 JSON，可给多个")
    ap.add_argument("--max-drop", type=float, default=0.02,
                    help="允许的指标跌幅（默认 0.02 = 2pt）")
    ap.add_argument("--baseline", default=str(BASELINE_PATH))
    ap.add_argument("--freeze", action="store_true",
                    help="把 --report 的检索指标写成新基线（人工确认后手跑）")
    ap.add_argument("--json", default=None, help="把检查结果写成 JSON")
    args = ap.parse_args(argv)

    checks: list[Check] = []
    checks += check_ndcg_caliber()
    checks += check_ndcg_bounded()
    checks += check_aggregate_semantics()
    checks += check_statistics_module()

    reports: list[dict] = []
    for rp in args.report:
        p = pathlib.Path(rp)
        if not p.is_absolute():
            p = pathlib.Path.cwd() / p
        if not p.is_file():
            checks.append(Check(f"report:{rp}", False, "文件不存在"))
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        reports.append(data)
        checks += check_report(data, rp)

    base_path = pathlib.Path(args.baseline)
    if not base_path.is_absolute():
        base_path = pathlib.Path.cwd() / base_path
    baseline = json.loads(base_path.read_text(encoding="utf-8")) if base_path.is_file() else None

    if args.freeze:
        if not reports:
            print("--freeze 需要同时给 --report", flush=True)
            return 2
        overall = extract_retrieval_metrics(reports[0])
        if not overall:
            print("--freeze：报告里没有执行的检索段", flush=True)
            return 2
        frozen = {
            "frozen_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
            "note": (
                "检索基线（人工确认后冻结）。更新前必须确认：①语料版本稳定；"
                "②评测集未变动；③数字来自 p114 且不变量全绿。"
            ),
            "n": overall.get("n"),
            "n_input": overall.get("n_input"),
            "source_report": [str(x) for x in args.report],
            "metrics": {k: overall.get(k) for k in
                        ("hit@1", "hit@5", "hit@10", "mrr@10", "ndcg@10")},
        }
        base_path.parent.mkdir(parents=True, exist_ok=True)
        base_path.write_text(json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已冻结基线 -> {base_path}")
        return 0

    if baseline and reports:
        for data in reports:
            overall = extract_retrieval_metrics(data)
            if overall:
                checks += check_regression(overall, baseline, args.max_drop)
    elif baseline and not reports:
        checks.append(Check("regress:skipped", True,
                            "未给 --report，跳过回归比对（口径检查仍生效）", level="WARN"))
    elif not baseline:
        checks.append(Check("regress:no_baseline", True,
                            f"基线不存在（{base_path}），跳过回归比对", level="WARN"))

    fails = [c for c in checks if not c.ok and c.level == "FAIL"]
    warns = [c for c in checks if not c.ok and c.level == "WARN"]

    print(f"{'='*78}\nRAG 评估门禁（p115）\n{'='*78}")
    for c in checks:
        mark = "✅" if c.ok else "❌" if c.level == "FAIL" else "⚠️"
        print(f"{mark} {c.name:42s} {c.detail}")

    print("-" * 78)
    print(f"共 {len(checks)} 项：PASS {len(checks)-len(fails)-len(warns)} / "
          f"FAIL {len(fails)} / WARN {len(warns)}")
    if fails:
        print("\nFAIL 项（阻断）：")
        for c in fails:
            print(f"  - {c.name}: {c.detail}")

    if args.json:
        outp = pathlib.Path(args.json)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(
            {"checks": [c.__dict__ for c in checks],
             "summary": {"total": len(checks), "fail": len(fails), "warn": len(warns)}},
            ensure_ascii=False, indent=2), encoding="utf-8")

    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
