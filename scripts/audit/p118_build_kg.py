#!/usr/bin/env python3
"""构建评测用知识图谱（按 Ragas 方法，规则抽取 + 关系构建）。

## 为什么需要

Ragas 的测试集生成核心是**知识图谱驱动的场景遍历**：

```
文档 → 分块成节点 → 抽取器抽属性（实体/关键短语/分级码）
     → 关系构建器连边（同实体、同类目、数值邻近）
     → 遍历图生成「单跳/多跳 × 具体/抽象」四象限的 query
```

**人能保证每条 query 合理，但保证不了覆盖矩阵**；图遍历能。

## 为什么用规则抽取，不调 LLM

1. Ragas 也支持 `Extractor` 基类（规则）与 `LLMBasedExtractor`
2. 本项目有 3748 个 chunk，逐一调 LLM 成本高、且结果不稳定
3. **医疗文档的属性高度结构化**：分级码、阈值、解剖部位、征象名
   都能用正则稳定抽出，比 LLM 更可复现

## 抽取的属性（对应 Ragas 的 node.properties）

| 属性 | 正则依据 | 用于生成什么 query |
|---|---|---|
| `grades` | `C-TIRADS 4A` / `TR4` / `LR-5` | 分级类问题 |
| `thresholds` | `>15mm` / `<2%` / `≥10mm` | 阈值类问题 |
| `signs` | 实性/微钙化/低回声… | 征象计数类问题 |
| `anatomy` | 甲状腺/肝/心脏 + 部位 | 定位类问题 |
| `numbers` | 所有数值 | 数值邻近关系 |

## 关系类型（对应 relationship_builder）

| 关系 | 判据 | 用于生成什么 query |
|---|---|---|
| `same_grade` | 同一分级码（如都是 4A） | **多跳对比跨语料** |
| `same_sign` | 共享 ≥2 个征象 | 征象组合类 |
| `same_organ` | 同器官 | 组织切片 |
| `cross_source` | 同 grade 但不同 source_set | **跨语料口径对比（本项目核心）** |

## 用法

```bash
PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p118_build_kg.py \
    --kb-version kb_v2.1.0 --out /tmp/kg.json
```
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

#: 分级码（三器官）
_GRADE_RE = re.compile(
    r"(?:C-?TIRADS\s*([1-6])\s*([A-Ca-c]?)|TI-?RADS\s*([1-6])\s*([A-Ca-c]?)"
    r"|\bTR\s?([1-5])\b|LI-?RADS\s*(?:LR-?)?\s*([1-5M])\b|\bLR-?([1-5M])\b)",
    re.IGNORECASE,
)

#: 阈值（>15mm / <2% / ≥10mm / ≤35%）
_THRESHOLD_RE = re.compile(
    r"([>≥<≤＞＜]\s*\d+(?:\.\d+)?\s*(?:mm|cm|%|分|岁|ml|m/s|mmHg|kPa))",
    re.IGNORECASE,
)

#: 超声征象词表（与 p79 的 IMAGING_TERMS 对齐，只取「征象」类）
_SIGNS = (
    "实性", "囊性", "囊实性", "海绵状",
    "低回声", "高回声", "无回声", "等回声", "极低回声", "混合回声",
    "边界清", "边界模糊", "边界欠清", "形态规则", "形态不规则", "纵横比",
    "微钙化", "粗钙化", "点状强回声", "后方回声", "声影", "声晕",
    "分叶", "边缘不规则", "甲状腺外侵犯", "彗星尾",
    "血流信号", "CDFI", "弹性成像", "超声造影",
)

#: 解剖部位
_ANATOMY = (
    "甲状腺", "右叶", "左叶", "峡部", "淋巴结", "被膜",
    "肝", "肝右叶", "肝左叶", "门静脉", "胆囊",
    "左室", "右室", "左房", "右房", "二尖瓣", "三尖瓣", "主动脉瓣", "肺动脉瓣",
    "室间隔", "房缺", "卵圆孔",
)


def extract_properties(content: str) -> dict:
    """从 chunk 正文抽属性。纯函数。"""
    text = content or ""

    grades: list[str] = []
    for m in _GRADE_RE.finditer(text):
        g = m.group(0).strip()
        # 归一化：只留核心码
        key = re.sub(r"(?i)c-?tirads|ti-?rads|li-?rads|\s|[类]", "", g).upper()
        if key and key not in grades:
            grades.append(key)

    thresholds = []
    seen = set()
    for m in _THRESHOLD_RE.finditer(text):
        t = m.group(1).replace(" ", "")
        if t not in seen:
            seen.add(t)
            thresholds.append(t)

    signs = [s for s in _SIGNS if s in text]
    anatomy = [a for a in _ANATOMY if a in text]

    return {
        "grades": grades,
        "thresholds": thresholds[:8],   # 上限防爆
        "signs": signs,
        "anatomy": anatomy,
    }


async def load_nodes(kb_version: str) -> list[dict]:
    from app.config.database import AsyncSessionLocal
    from app.knowledge.source_set import source_set_of
    from sqlalchemy import text

    async with AsyncSessionLocal() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT chunk_id, organ, source_doc_id, content, metadata "
                    "FROM knowledge_chunks WHERE kb_version = :v"
                ),
                {"v": kb_version},
            )
        ).fetchall()

    nodes = []
    for cid, organ, doc_id, content, meta in rows:
        props = extract_properties(content or "")
        if not (props["grades"] or props["thresholds"] or props["signs"]):
            continue    # 无属性的节点对场景生成没用
        nodes.append(
            {
                "id": cid,
                "organ": organ,
                "source_set": source_set_of(source_doc_id=doc_id, metadata=meta),
                "content_len": len(content or ""),
                **props,
            }
        )
    return nodes


def build_relationships(nodes: list[dict]) -> dict[str, list]:
    """建四类关系。返回 {rel_type: [[node_id_a, node_id_b], ...]}。

    **只建对生成 query 有用的关系**，不做全连接（3748² 会爆）。
    """
    rels: dict[str, list] = defaultdict(list)

    # 索引：grade → nodes；sign → nodes；organ → nodes
    by_grade: dict[str, list[int]] = defaultdict(list)
    by_sign: dict[str, list[int]] = defaultdict(list)
    by_organ: dict[str, list[int]] = defaultdict(list)

    for i, n in enumerate(nodes):
        for g in n["grades"]:
            by_grade[g].append(i)
        for s in n["signs"]:
            by_sign[s].append(i)
        by_organ[n["organ"]].append(i)

    # ---- 关系 1：same_grade（同分级码）→ 用于「多跳对比」
    for g, idxs in by_grade.items():
        # 每对只在跨 source_set 时保留（cross_source 更值钱），
        # 同 source 内保留少量（≤3）避免同质
        by_src: dict[str, list[int]] = defaultdict(list)
        for i in idxs:
            by_src[nodes[i]["source_set"]].append(i)
        srcs = list(by_src)
        for a in range(len(srcs)):
            for b in range(a + 1, len(srcs)):
                for i in by_src[srcs[a]][:3]:
                    for j in by_src[srcs[b]][:3]:
                        rels["cross_source_same_grade"].append([nodes[i]["id"], nodes[j]["id"]])
        # 同 source 内少量
        for s, ii in by_src.items():
            for a in range(min(len(ii), 4)):
                for b in range(a + 1, min(len(ii), 4)):
                    rels["same_grade"].append([nodes[ii[a]]["id"], nodes[ii[b]]["id"]])

    # ---- 关系 2：shared_signs（≥2 个共同征象）→ 用于「征象组合」
    for organ, idxs in by_organ.items():
        if organ not in ("thyroid",):    # 征象词表主要针对甲状腺，其它器官跳过
            continue
        for a in range(len(idxs)):
            si = set(nodes[idxs[a]]["signs"])
            if len(si) < 2:
                continue
            for b in range(a + 1, min(a + 30, len(idxs))):
                sb = set(nodes[idxs[b]]["signs"])
                if len(si & sb) >= 2:
                    rels["shared_signs"].append([nodes[idxs[a]]["id"], nodes[idxs[b]]["id"]])

    # ---- 关系 3：same_threshold_value（同阈值）→ 用于「跨语料阈值对比」
    by_thr: dict[str, list[int]] = defaultdict(list)
    for i, n in enumerate(nodes):
        for t in n["thresholds"]:
            by_thr[t].append(i)
    for t, idxs in by_thr.items():
        srcs = {nodes[i]["source_set"] for i in idxs}
        if len(srcs) >= 2:
            for a in range(min(len(idxs), 6)):
                for b in range(a + 1, min(len(idxs), 6)):
                    if nodes[idxs[a]]["source_set"] != nodes[idxs[b]]["source_set"]:
                        rels["cross_source_same_threshold"].append(
                            [nodes[idxs[a]]["id"], nodes[idxs[b]]["id"]]
                        )

    return {k: v[:500] for k, v in rels.items()}   # 每类上限 500 防爆


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="构建评测知识图谱（p118）")
    ap.add_argument("--kb-version", default="kb_v2.1.0")
    ap.add_argument("--out", default="/tmp/kg.json")
    args = ap.parse_args(argv)

    nodes = await load_nodes(args.kb_version)
    print(f"=== 节点 ===")
    print(f"  有属性的 chunk: {len(nodes)}")
    from collections import Counter
    cnt_g = Counter(len(n["grades"]) for n in nodes)
    print(f"  含分级码的节点: {sum(v for k, v in cnt_g.items() if k > 0)}")
    print(f"  含阈值的节点:   {sum(1 for n in nodes if n['thresholds'])}")
    print(f"  含征象的节点:   {sum(1 for n in nodes if n['signs'])}")

    rels = build_relationships(nodes)
    print(f"\n=== 关系 ===")
    for k, v in rels.items():
        print(f"  {k:32s} {len(v)}")

    out = Path(args.out)
    out.write_text(
        json.dumps({"nodes": nodes, "relationships": rels}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n写出 → {out} ({out.stat().st_size} 字节)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
