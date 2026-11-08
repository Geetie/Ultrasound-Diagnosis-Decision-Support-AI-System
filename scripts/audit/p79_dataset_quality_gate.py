"""A.166：评测数据集质量闸 —— 造完的自造数据集必须过四道检查才能进评测。

## 为什么要有这个脚本

2026-11-08 全量核查 14 个自造测试集，发现**无一能用于验收**：
`consistency_test_cases` 156/780 条重复、BND 组含同句自相矛盾病例；
`kb_qa_golden_v1` 含影像术语只有 11.8%。这些集被用了半年、报出了
98.53% / 79.56% 等数字，**却从没有人检查过它们本身能不能测**。

本脚本把"能不能测"变成可执行判据，防止同类问题再发生。

## 四道检查（阈值写死，与 docs/合规/评测数据集/README.md 一致）

| 检查 | 阈值 | 判据 |
|---|---|---|
| 重复率 | **必须 0** | 完全相同的文本不得出现两次（同句重复会系统性抬高一致率类指标） |
| 模板污染率 | **≤10%** | 前缀（前 18 字）重复 ≥3 次的组，其条目数占比 |
| 影像术语覆盖 | **≥50%**（检索/临床文本类） | 命中超声术语关键词的条目占比 |
| 自相矛盾 | **必须 0** | 同一条文本内出现对立词对（清晰/欠清晰 等） |

## 用法

    python scripts/audit/p79_dataset_quality_gate.py --dataset path/to/x.jsonl --field query_text
    python scripts/audit/p79_dataset_quality_gate.py --dataset dir --manifest dir/manifest.json

退出码：0 = 全过；1 = 有检查未过（可直接用于 CI 阻断）。
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

#: 影像/临床术语（用于"业务场景匹配度"检查）。
#:
#: **只收"只有医生写报告才会用的描述性术语"**，不收通用病名词。
#: 教训（2026-11-08 首次跑闸）：最初把「结节」「超声」也放进来了，
#: 结果患者问诊「哪个医院治疗甲状腺结节病比较好」也命中 → 覆盖率假高到 0.5。
#: 判据应该是"这句话像不像医生写的影像描述"，所以只认描述性词汇：
#: 回声性质（低回声/高回声/无回声）、形态特征（纵横比/边界/形态/钙化）、
#: 检查手段（CDFI/弹性/造影）、分级体系（TI-RADS/LI-RADS/TR1-5）。
IMAGING_TERMS = [
    # 分级体系
    "TI-RADS", "TI RADS", "LI-RADS", "TR1", "TR2", "TR3", "TR4", "TR5",
    # 回声性质
    "低回声", "高回声", "无回声", "等回声", "混合回声", "回声均匀", "回声不均",
    # 形态/边界特征
    "纵横比", "边界清", "边界欠清", "边界模糊", "形态规则", "形态不规则",
    "微钙化", "粗钙化", "点状强回声", "后方回声",
    # 检查手段/征象
    "CDFI", "血流信号", "弹性成像", "超声造影", "彩色多普勒",
    # 结构描述
    "实性结节", "囊性结节", "囊实性", "病灶", "占位性病变",
]

#: 对立词对（同一句里同时出现 = 自相矛盾）
CONTRADICTION_PAIRS = [
    ("边界清晰", "边界欠清晰"),
    ("边界清晰", "边界模糊"),
    ("形态规则", "形态欠规则"),
    ("形态规则", "形态不规则"),
    ("未见钙化", "可见钙化"),
    ("无钙化", "可见微钙化"),
    ("边界清", "边界欠清"),
]

#: 阈值（与 README 一致，改这里必须同时改 README）
THRESHOLDS = {
    "duplicate_rate": 0.0,
    "template_pollution_rate": 0.10,
    "imaging_term_coverage": 0.50,
    "self_contradiction_count": 0,
}

TEMPLATE_PREFIX_LEN = 18
TEMPLATE_MIN_GROUP = 3


def load_texts(path: pathlib.Path, field: str | None) -> list[str]:
    """读数据集，抽出文本列表。支持 .jsonl / .json（list 或 {queries|samples|items}）。"""
    if path.suffix == ".jsonl":
        rows = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = []
            for key in ("queries", "samples", "items", "cases", "data"):
                if isinstance(data.get(key), list):
                    rows = data[key]
                    break
        else:
            rows = []

    candidates = [field] if field else []
    candidates += ["query_text", "query", "clinical_text", "prompt", "text", "conclusion"]
    for key in candidates:
        if not key:
            continue
        vals = [r.get(key) for r in rows if isinstance(r, dict)]
        texts = [v for v in vals if isinstance(v, str) and v.strip()]
        if texts:
            return texts
    return []


def check_duplicates(texts: list[str]) -> tuple[float, list[str]]:
    """重复率：完全相同的文本重复出现。返回 (重复率, 重复样例)。"""
    counter = collections.Counter(texts)
    duplicated = [t for t, n in counter.items() if n > 1]
    dup_items = sum(n - 1 for n in counter.values() if n > 1)
    rate = dup_items / len(texts) if texts else 0.0
    return rate, [t[:60] for t in duplicated[:3]]


def check_template_pollution(texts: list[str]) -> tuple[float, list[tuple[str, int]]]:
    """模板污染率：前缀重复 ≥3 次的组所占条目比例。"""
    prefixes = collections.Counter(t[:TEMPLATE_PREFIX_LEN] for t in texts)
    polluted = sum(n for n in prefixes.values() if n >= TEMPLATE_MIN_GROUP)
    rate = polluted / len(texts) if texts else 0.0
    top = [(p, n) for p, n in prefixes.most_common(3) if n >= TEMPLATE_MIN_GROUP]
    return rate, top


def check_imaging_coverage(texts: list[str]) -> float:
    """影像术语覆盖率。"""
    if not texts:
        return 0.0
    hit = sum(1 for t in texts if any(term in t for term in IMAGING_TERMS))
    return hit / len(texts)


def check_contradictions(texts: list[str]) -> list[dict]:
    """自相矛盾：同一条文本内出现对立词对。"""
    found = []
    for t in texts:
        for a, b in CONTRADICTION_PAIRS:
            if a in t and b in t:
                found.append({"text": t[:80], "pair": f"{a} / {b}"})
                break
    return found


def run_gate(path: pathlib.Path, field: str | None) -> dict:
    """跑完整闸门，返回报告 dict。"""
    texts = load_texts(path, field)
    if not texts:
        return {
            "dataset": str(path),
            "verdict": "ERROR",
            "error": "no_texts_extracted",
            "hint": "用 --field 指定文本字段名",
        }

    dup_rate, dup_samples = check_duplicates(texts)
    tmpl_rate, tmpl_top = check_template_pollution(texts)
    img_cov = check_imaging_coverage(texts)
    contradictions = check_contradictions(texts)

    checks = [
        {
            "name": "duplicate_rate",
            "value": round(dup_rate, 4),
            "threshold": THRESHOLDS["duplicate_rate"],
            "passed": dup_rate <= THRESHOLDS["duplicate_rate"],
            "samples": dup_samples,
            "why": "同句重复会系统性抬高一致率类指标（consistency_test_cases 教训：156/780 重复）",
        },
        {
            "name": "template_pollution_rate",
            "value": round(tmpl_rate, 4),
            "threshold": THRESHOLDS["template_pollution_rate"],
            "passed": tmpl_rate <= THRESHOLDS["template_pollution_rate"],
            "samples": tmpl_top,
            "why": "模板拼装会让模型答得更好看，但测不出真实能力（double_engine_eval_set 教训：86% 来自 3 个模板）",
        },
        {
            "name": "imaging_term_coverage",
            "value": round(img_cov, 4),
            "threshold": THRESHOLDS["imaging_term_coverage"],
            "passed": img_cov >= THRESHOLDS["imaging_term_coverage"],
            "why": "业务是超声影像辅助诊断，不含影像术语的 query 测不出本系统能力（kb_qa_golden_v1 教训：仅 11.8%）",
        },
        {
            "name": "self_contradiction_count",
            "value": len(contradictions),
            "threshold": THRESHOLDS["self_contradiction_count"],
            "passed": len(contradictions) <= THRESHOLDS["self_contradiction_count"],
            "samples": contradictions[:3],
            "why": "同一句既说清晰又说欠清晰，无法判读（consistency_test_cases BND 组教训）",
        },
    ]

    all_passed = all(c["passed"] for c in checks)
    return {
        "dataset": str(path),
        "n_items": len(texts),
        "verdict": "PASS" if all_passed else "FAIL",
        "checks": checks,
        "note": "PASS 才允许进评测；FAIL 必须重建，不得以『先跑起来』为由放行",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="评测数据集质量闸（A.166）")
    ap.add_argument("--dataset", required=True, help="数据集文件路径")
    ap.add_argument("--field", default=None, help="文本字段名（自动探测失败时指定）")
    ap.add_argument("--report", default=None, help="报告输出路径（可选）")
    args = ap.parse_args(argv)

    path = pathlib.Path(args.dataset).expanduser()
    if not path.exists():
        print(f"文件不存在: {path}", file=sys.stderr)
        return 2

    report = run_gate(path, args.field)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.report:
        out = pathlib.Path(args.report).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return 0 if report.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
