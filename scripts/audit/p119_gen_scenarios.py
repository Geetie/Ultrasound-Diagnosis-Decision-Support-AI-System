#!/usr/bin/env python3
"""从知识图谱生成评测场景（按 Ragas 的 Scenario / QuerySynthesizer 方法）。

## Ragas 的 Scenario 定义

```
Scenario = Nodes（知识图节点）+ Query Length（short/medium/long）
          + Query Style（web search / chat / …）+ Persona（用户角色）
```

## 本项目的四个 persona（决定措辞与深度）

| persona | 角色描述 | 典型问法 |
|---|---|---|
| `resident` | 超声科住院医师，正在学分级规则 | 「这个征象算不算可疑？」 |
| `attending` | 超声科主治医师，处理边界与例外 | 「这情况指南有例外吗？」 |
| `clinician` | 临床科室医生（非超声），看不懂报告 | 「报告上这个分级我该怎么处理病人？」 |
| `qa` | 医务处质控，关心体系一致性 | 「我们医院用的哪套？和另一套差在哪？」 |

## 四象限（Ragas 的 hop × specificity）

| | 具体（specific） | 抽象（abstract） |
|---|---|---|
| **单跳** | 一个节点答得出 | 一个节点但要解释 |
| **多跳** | 两个节点拼起来 | 两个节点要综合 |

**每种组合 × 每个 persona × 三种长度 = 场景格。**

## 关键设计：场景只生成「骨架」，query 由后续 LLM 或人工填充

本脚本输出的是 **scenario 描述**（含 nodes / hop / specificity / persona / length / 意图模板），
不直接生成成品 query —— 因为：
1. 规则生成 query 会模板化（撞 p79 的「模板 ≤10%」闸）
2. Ragas 也是 `_generate_scenarios` 与 `_generate_sample` 分离的

## 用法

```bash
PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p119_gen_scenarios.py \
    --kg /tmp/kg.json --out /tmp/scenarios.jsonl --per-cell 12
```
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

#: 四个 persona（对应 Ragas 的 Persona 类）
PERSONAS = {
    "resident": "超声科住院医师，正在学 C-TIRADS/LI-RADS 分级规则，常问征象判定与计分",
    "attending": "超声科主治医师，熟悉常规，关注边界情况、例外与两套体系的取舍",
    "clinician": "临床科室医生（非超声专业），拿到超声报告后关心分级含义与下一步处置",
    "qa": "医务处质控人员，关心本院采用哪套指南、两套口径冲突如何处理",
}

#: 四象限：hop × specificity
QUADRANTS = list(itertools.product(("single", "multi"), ("specific", "abstract")))

#: 三种长度
LENGTHS = ("short", "medium", "long")

#: 每象限 × persona 的意图模板（这是「骨架」，不是最终 query）
_INTENT: dict[tuple[str, str], dict[str, str]] = {
    ("single", "specific"): {
        "resident": "问某个征象/计分规则的具体判定",
        "attending": "问某个阈值或分档的准确数值",
        "clinician": "问某个分级码对应什么临床含义",
        "qa": "问某套指南对某项的具体规定",
    },
    ("single", "abstract"): {
        "resident": "问某个规则为什么这样定（理解性）",
        "attending": "问某条规则的适用边界与理由",
        "clinician": "问为什么报告要给这个分级（解释性）",
        "qa": "问某套体系的设计逻辑",
    },
    ("multi", "specific"): {
        "resident": "把两个征象/规则组合起来算一个结果",
        "attending": "在两份来源里找同一事实的不同数值",
        "clinician": "把影像表现与处置建议连起来",
        "qa": "对比两套指南对同一情形的规定",
    },
    ("multi", "abstract"): {
        "resident": "综合多处规则判断一个复杂病例",
        "attending": "综合两份体系给出取舍建议",
        "clinician": "综合影像与指南给出病人处置思路",
        "qa": "评估两套体系并存的临床影响",
    },
}


def _pick_node_indices(kg: dict) -> dict[str, list[int]]:
    """按器官分组节点索引。"""
    by_organ: dict[str, list[int]] = defaultdict(list)
    for i, n in enumerate(kg["nodes"]):
        by_organ[n["organ"]].append(i)
    return by_organ


def _candidate_pairs(kg: dict) -> dict[str, list[tuple[str, str]]]:
    """可按关系类型取节点对。"""
    rels = kg["relationships"]
    id2idx = {n["id"]: i for i, n in enumerate(kg["nodes"])}
    out: dict[str, list[tuple[str, str]]] = {}
    for kind, pairs in rels.items():
        valid = []
        for a, b in pairs:
            if a in id2idx and b in id2idx:
                valid.append((a, b))
        out[kind] = valid
    return out


def generate(kg: dict, per_cell: int, seed: int = 42) -> list[dict]:
    rnd = random.Random(seed)
    by_organ = _pick_node_indices(kg)
    pairs = _candidate_pairs(kg)
    id2node = {n["id"]: n for n in kg["nodes"]}

    scenarios: list[dict] = []
    sid = 0
    used_pairs: set[tuple[str, str]] = set()   # 已用过的节点对，防多跳 repeating

    for hop, spec in QUADRANTS:
        for persona in PERSONAS:
            for length in LENGTHS:
                intent = _INTENT[(hop, spec)][persona]
                # 单跳用单个节点；多跳用关系对
                for k in range(per_cell):
                    sid += 1
                    if hop == "single":
                        organ = ("thyroid", "liver", "cardiac")[k % 3]
                        cand = by_organ.get(organ) or []
                        if not cand:
                            continue
                        node = kg["nodes"][rnd.choice(cand)]
                        nodes_used = [node["id"]]
                        rel_kind = "single"
                    else:
                        # 多跳优先用跨语料关系（本项目核心）
                        #
                        # ## 2026-11-09 实测修正：**同一节点对不许重复使用**
                        #
                        # 原来每次 `rnd.choice(plist)` 独立随机 → 小关系池反复被抽中。
                        # 实测后果：20 条多跳场景里出现 3 条一模一样的
                        # 「这个 5 分结节按两套指南分别归哪一类？该听谁的？」
                        # （因为 cross_source_same_grade 反复选到同一对节点）
                        #
                        # 修法：用 `used_pairs` 记录已用过的 (a,b)，优先取未用过的；
                        # 池子耗尽时才允许复用。
                        kind = rnd.choice(
                            [k for k in pairs if k.startswith("cross_source")] or list(pairs)
                        )
                        plist = pairs.get(kind) or []
                        if not plist:
                            continue
                        fresh = [pr for pr in plist if pr not in used_pairs]
                        pool = fresh if fresh else plist
                        a, b = rnd.choice(pool)
                        used_pairs.add((a, b))
                        na, nb = id2node[a], id2node[b]
                        if na["organ"] != nb["organ"]:
                            continue    # 跨器官的多跳不合临床逻辑
                        nodes_used = [a, b]
                        rel_kind = kind

                    scenarios.append(
                        {
                            "scenario_id": f"sc-{sid:04d}",
                            "nodes": nodes_used,
                            "organ": id2node[nodes_used[0]]["organ"],
                            "hop": hop,
                            "specificity": spec,
                            "persona": persona,
                            "persona_role": PERSONAS[persona],
                            "length": length,
                            "relationship": rel_kind,
                            "intent": intent,
                        }
                    )
    return scenarios


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成评测场景（p119）")
    ap.add_argument("--kg", default="/tmp/kg.json")
    ap.add_argument("--out", default="/tmp/scenarios.jsonl")
    ap.add_argument("--per-cell", type=int, default=12, help="每个 (象限×persona×长度) 格生成几条")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)

    kg = json.loads(Path(args.kg).read_text(encoding="utf-8"))
    scenarios = generate(kg, args.per_cell, args.seed)

    out = Path(args.out)
    with out.open("w", encoding="utf-8") as f:
        for s in scenarios:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    # 覆盖矩阵自检
    from collections import Counter
    print(f"=== 生成 {len(scenarios)} 个场景 ===")
    print(f"  格子数: {len(QUADRANTS) * len(PERSONAS) * len(LENGTHS)}")
    print(f"  每格目标: {args.per_cell}")
    cell = Counter((s["hop"], s["specificity"], s["persona"], s["length"]) for s in scenarios)
    print(f"  实际填充格子: {len(cell)}")
    gaps = [
        (h, sp, p, l)
        for (h, sp), p, l in itertools.product(QUADRANTS, PERSONAS, LENGTHS)
        if cell[(h, sp, p, l)] < args.per_cell
    ]
    if gaps:
        print(f"  ⚠️ 未填满的格子 {len(gaps)} 个，前 5:")
        for g in gaps[:5]:
            print(f"    {g} -> {cell[g]}")
    else:
        print("  ✅ 全部格子填满")

    print(f"\n=== 器官分布 ===")
    for o, c in Counter(s["organ"] for s in scenarios).most_common():
        print(f"  {o:10s} {c}")

    print(f"\n=== 关系类型 ===")
    for r, c in Counter(s["relationship"] for s in scenarios).most_common():
        print(f"  {r:32s} {c}")

    print(f"\n写出 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
