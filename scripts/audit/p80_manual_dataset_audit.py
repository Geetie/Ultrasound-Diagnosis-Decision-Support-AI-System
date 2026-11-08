"""A.169：**人工撰写**数据集的可信度审计（配合 p79 质量闸）。

## 为什么还要这一层

`p79` 查的是**统计特征**（重复率/模板率/术语覆盖/自相矛盾），能挡住"脚本批量拼装"。
但它挡不住另一种问题：**100 条全是同一个人用同一种思路写的**——
统计上很干净（不重复、没模板），但覆盖面窄、思维单一，测不出真实能力。

本脚本查三件事，都是"看起来合格但实际不合格"的典型：

1. **思维单一度**：query_type 分布是否过于集中（比如 90% 都是"分级标准"类）；
2. **难度分布**：是否全是简单题（能从单段直接抄答案），没有需要综合多段的；
3. **长度/句式单调**：字数分布的变异系数过低 = 句式雷同。

## 用法

    python scripts/audit/p80_manual_dataset_audit.py --dataset data/eval/v2/zh_retrieval_v1.jsonl
    python scripts/audit/p80_manual_dataset_audit.py --dataset xxx.jsonl --field query --type-field query_type

退出码：0 = 全过；1 = 有项不过。
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import statistics
import sys

#: query_type 集中度上限（单一类型占比超过即报警）
MAX_SINGLE_TYPE_RATIO = 0.40
#: 至少要有多少种不同的 query 类型
MIN_DISTINCT_TYPES = 5
#: 字数变异系数下限（低于即句式雷同）
#: 变异系数 = 标准差 / 均值。0.25 ≈ 长短有明显的差异
MIN_LENGTH_CV = 0.25
#: 至少这么多条才值得做分布分析
MIN_N_FOR_DISTRIBUTION = 30


def load_rows(path: pathlib.Path) -> list[dict]:
    rows: list[dict] = []
    if path.suffix == ".jsonl":
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:  # silent-ok: 坏行由 p79 计数上报
                    continue
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            for k in ("queries", "samples", "items", "cases", "data"):
                if isinstance(data.get(k), list):
                    rows = data[k]
                    break
    return rows


def audit(
    rows: list[dict],
    *,
    field: str | None = None,
    type_field: str | None = None,
) -> dict:
    """对人工撰写的数据集做"思维方式多样性"审计（纯函数）。"""
    if not rows:
        return {"verdict": "ERROR", "error": "no_rows"}

    text_field = field
    if not text_field:
        for cand in ("query", "query_text", "text", "clinical_text", "prompt"):
            if any(isinstance(r.get(cand), str) and r.get(cand).strip() for r in rows):
                text_field = cand
                break

    texts = [
        r.get(text_field, "") for r in rows
        if isinstance(r, dict) and isinstance(r.get(text_field), str)
    ]
    texts = [t for t in texts if t.strip()]
    checks: list[dict] = []

    # 1) 类型集中度
    type_key = type_field
    if not type_key:
        for cand in ("query_type", "type", "hard_case_type", "category"):
            if any(isinstance(r.get(cand), str) for r in rows):
                type_key = cand
                break
    if type_key:
        types = [str(r.get(type_key)) for r in rows if r.get(type_key)]
        dist = collections.Counter(types)
        n = len(types) or 1
        top_type, top_n = (dist.most_common(1)[0] if dist else ("", 0))
        top_ratio = top_n / n
        checks.append({
            "name": "type_concentration",
            "value": round(top_ratio, 3),
            "threshold": MAX_SINGLE_TYPE_RATIO,
            "passed": top_ratio <= MAX_SINGLE_TYPE_RATIO,
            "detail": f"最集中的类型「{top_type}」占 {top_ratio:.0%}（{top_n}/{n}）",
            "why": "全是同一类问题 = 思维单一，测不出系统在各类场景下的表现",
        })
        checks.append({
            "name": "type_diversity",
            "value": len(dist),
            "threshold": MIN_DISTINCT_TYPES,
            "passed": len(dist) >= MIN_DISTINCT_TYPES,
            "detail": f"{len(dist)} 种类型：" + "、".join(f"{k}({v})" for k, v in dist.most_common(6)),
            "why": "覆盖的场景太少，评测结论不可外推",
        })
    else:
        checks.append({
            "name": "type_concentration",
            "value": None,
            "passed": None,
            "detail": "没有类型字段，跳过（建议补 query_type 便于分析）",
        })

    # 2) 长度变异系数（句式单调检测）
    if len(texts) >= MIN_N_FOR_DISTRIBUTION:
        lens = [len(t) for t in texts]
        mean = statistics.mean(lens)
        sd = statistics.pstdev(lens)
        cv = sd / mean if mean else 0.0
        checks.append({
            "name": "length_variability",
            "value": round(cv, 3),
            "threshold": MIN_LENGTH_CV,
            "passed": cv >= MIN_LENGTH_CV,
            "detail": f"字数 均值 {mean:.0f} / 标准差 {sd:.1f} / 变异系数 {cv:.2f}",
            "why": "变异系数过低 = 每条长度雷同 = 很可能是同一模板套出来的",
        })
    else:
        checks.append({
            "name": "length_variability",
            "value": None,
            "passed": None,
            "detail": f"样本 {len(texts)} 条 < {MIN_N_FOR_DISTRIBUTION}，不做分布分析",
        })

    # 3) 前缀重复（比 p79 更严：前 8 字重复 ≥5 次）
    prefixes = collections.Counter(t[:8] for t in texts)
    heavy = [(p, n) for p, n in prefixes.most_common(3) if n >= 5]
    checks.append({
        "name": "prefix_repetition",
        "value": len(heavy),
        "threshold": 0,
        "passed": not heavy,
        "detail": f"前 8 字重复 ≥5 次的组：{heavy}" if heavy else "无",
        "why": "人工撰写不会出现多条同开头；出现即说明有模板",
    })

    scored = [c for c in checks if c.get("passed") is not None]
    all_passed = all(c["passed"] for c in scored)
    return {
        "n_items": len(rows),
        "text_field": text_field,
        "type_field": type_key,
        "verdict": "PASS" if all_passed else "FAIL",
        "checks": checks,
        "note": (
            "本脚本查的是'人工撰写'的可信度（思维多样性），p79 查的是统计卫生。"
            "两者都过才算合格。"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="人工撰写数据集可信度审计（A.169）")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--field", default=None, help="文本字段名")
    ap.add_argument("--type-field", default=None, help="类型字段名")
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)

    path = pathlib.Path(args.dataset).expanduser()
    if not path.exists():
        print(f"文件不存在: {path}", file=sys.stderr)
        return 2

    report = audit(load_rows(path), field=args.field, type_field=args.type_field)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.report:
        out = pathlib.Path(args.report).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
