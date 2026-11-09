#!/usr/bin/env python3
"""数据集版本化与覆盖矩阵检查（目标交付项 3 + 5）。

## 解决两个问题

### 问题 1：指标无法追溯（交付项 5）

现状：报出「hit@10 = 0.7872」时**不知道是哪个数据集版本算的**。
数据集一改，旧数字就失去意义，但报告里没记版本。

**Ragas 最佳实践 #5**：`Version Control: Track changes to your datasets over time
to ensure reproducibility.`

**做法**：每个数据集文件带一个 `manifest` 旁挂文件，含：
- `dataset_version`（如 `rag-eval-v1.0.0`）
- 内容指纹（`sha256` of sorted ids + queries）
- 条数与分布
- 生成脚本与参数的 git commit

### 问题 2：覆盖矩阵无机器检查（交付项 3）

要求「每格 ≥10 条」，但现在没人查。本脚本查并报告缺格。

## 用法

```bash
# 写 manifest
PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p126_dataset_version.py \
    --dataset /tmp/synth.jsonl --write-manifest

# 只检查覆盖矩阵
... --dataset /tmp/synth.jsonl --check-coverage --min-per-cell 10
```
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

#: 覆盖矩阵的维度（与 p119 的场景生成一致）
DIMS = ("hop", "specificity", "persona", "length")

#: **实际可用的分层维度**（2026-11-09 实测）
#:
#: ## 为什么要把 query_type 单列
#:
#: Ragas 的四维（persona × hop × specificity × length）里，`persona` 在 B2 上是空的 ——
#: 实测 483 条 query 里只有 87 条有 persona 线索词，**400 条完全没有**。
#: （「4A 类结节 13mm 要不要穿刺」这种问题，住院医师和主治医师都会问，
#:   原数据没有记录是谁问的，**编一个就是伪造**。）
#:
#: 而 `query_type` 是 B2 **原数据真实标注**的 8 类：
#:   technical_parameter 111 / grading_standard 89 / procedure_indication 72
#:   boundary_case 51 / followup_strategy 49 / comparison 46 / special_case 43 / report_standard 26
#: **每类都 ≥10 条**，是真正能支撑「按格子报指标」的维度。
#:
#: 结论：分层要用**真实存在的维度**，不是照抄框架里的维度名。
AVAILABLE_DIMS = ("hop", "specificity", "query_type", "length")


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_SRC, capture_output=True, text=True, timeout=5,
        ).stdout.strip() or "unknown"
    except Exception:  # silent-ok: 非 git 环境
        return "unknown"


def compute_fingerprint(rows: list[dict]) -> str:
    """内容指纹：按 id 排序后拼接，保证「同内容 → 同指纹」。

    **不包含** reference_answer/gold（那些会因标注修订而变）——
    指纹标识「这批问题」，不是「这批标注」。标注修订用 manifest 的 revision 记。
    """
    keys = sorted((r.get("query") or "").strip() for r in rows if r.get("query"))
    h = hashlib.sha256("\n".join(keys).encode("utf-8"))
    return h.hexdigest()[:16]


def coverage(rows: list[dict], dims: tuple[str, ...] = DIMS) -> dict:
    """统计覆盖矩阵。返回 {cell_key: n}。

    兼容两种 metadata 布局：
    - 扁平（`{"hop": "single", ...}`）
    - 嵌套（`{"metadata": {"hop": "single", ...}}`）
    """
    cells: Counter = Counter()
    missing_dims = Counter()
    for r in rows:
        md = r.get("metadata") if isinstance(r.get("metadata"), dict) else r
        vals = []
        for d in dims:
            v = md.get(d)
            if v is None:
                missing_dims[d] += 1
                v = "?"
            vals.append(str(v))
        cells[tuple(vals)] += 1
    return {"cells": dict(cells), "missing_dims": dict(missing_dims)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="数据集版本化与覆盖检查（p126）")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--version", default="rag-eval-v1.0.0")
    ap.add_argument("--write-manifest", action="store_true")
    ap.add_argument("--check-coverage", action="store_true")
    ap.add_argument("--min-per-cell", type=int, default=10)
    ap.add_argument(
        "--dims",
        default=",".join(DIMS),
        help=(
            "覆盖矩阵用哪几个维度。默认 hop,specificity,persona,length（16×3=48 格）。"
            "按 persona 分层的数据集建议用 hop,specificity,persona（16 格）——"
            "length 只是 query 字数，不是临床差异，纳入它会把 200 条摊成 48 格而每格不足 10。"
        ),
    )
    args = ap.parse_args(argv)

    p = Path(args.dataset)
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"数据集: {p}  条数 {len(rows)}")

    fp = compute_fingerprint(rows)
    dims = tuple(x.strip() for x in args.dims.split(",") if x.strip())
    cov = coverage(rows, dims)
    cells = cov["cells"]

    print(f"\n=== 版本信息 ===")
    print(f"  dataset_version = {args.version}")
    print(f"  fingerprint     = {fp}")
    print(f"  git_commit      = {_git_commit()}")

    if cov["missing_dims"]:
        print(f"\n⚠️ 缺 metadata 维度: {cov['missing_dims']}")

    # 分布统计
    from collections import Counter as C

    for d in dims:
        vals = C()
        for r in rows:
            md = r.get("metadata") if isinstance(r.get("metadata"), dict) else r
            vals[str(md.get(d, "?"))] += 1
        if len(vals) > 1 or "?" not in vals:
            print(f"  {d:12s}: {dict(vals.most_common())}")

    # 额外报 query_type —— 这是本项目**真实可用**的分层维度
    vals_qt = C()
    for r in rows:
        md = r.get("metadata") if isinstance(r.get("metadata"), dict) else r
        vals_qt[str(md.get("query_type", "?"))] += 1
    if vals_qt and "?" not in vals_qt:
        print(f"\n  【真实可分层的维度】query_type:")
        for k, v in vals_qt.most_common():
            flag = "✅" if v >= args.min_per_cell else "❌"
            print(f"    {flag} {k:22s}: {v}")

    if args.check_coverage:
        print(f"\n=== 覆盖矩阵（要求每格 ≥{args.min_per_cell}）===")
        total_cells = 1
        for d in dims:
            vals_d = {str((r.get("metadata") if isinstance(r.get("metadata"), dict) else r).get(d, "?")) for r in rows}
            total_cells *= max(len(vals_d), 1)
        print(f"  维度: {dims}")
        print(f"  理论格子数: {total_cells}")
        print(f"  实际有样本: {len(cells)}")
        short = {k: v for k, v in cells.items() if v < args.min_per_cell}
        full = len(cells) - len(short)
        print(f"  达到 ≥{args.min_per_cell}: {full}")
        print(f"  不足: {len(short)}")
        if short:
            print(f"\n  缺格明细（前 10）:")
            for k, v in list(short.items())[:10]:
                print(f"    {k}: {v} 条")
        # 未出现的格子
        absent = [k for k in (("single", "specific", "resident", "short"),) if k not in cells]
        if absent:
            print(f"\n  完全无样本的格子示例: {absent[:3]}")

    if args.write_manifest:
        mf = p.with_suffix(".manifest.json")
        manifest = {
            "dataset_version": args.version,
            "dataset_path": str(p),
            "fingerprint": fp,
            "git_commit": _git_commit(),
            "n": len(rows),
            "dims": list(DIMS),
            "distribution": {
                d: dict(
                    Counter(
                        str(
                            (r.get("metadata") if isinstance(r.get("metadata"), dict) else r).get(d, "?")
                        )
                        for r in rows
                    )
                )
                for d in DIMS
            },
            "coverage_cells": len(cells),
            "cells_below_min": len([v for v in cells.values() if v < args.min_per_cell]),
            "note": (
                "指标必须带 dataset_version + fingerprint 一起报，"
                "否则无法追溯是哪个数据集版本算的"
            ),
        }
        mf.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n写出 manifest → {mf}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
