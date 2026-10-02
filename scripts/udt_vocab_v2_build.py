"""UDT 词汇表 v2 构建（四层鲁棒管线之 Layer1+2）。

Layer1 现成词表: THUOCL_medical.txt（清华，18k+ 条带文档频率）
  ——按"解剖锚点"过滤到三器官（解剖词是封闭类，比损伤词素鲁棒）
Layer2 语料对齐: 词表术语在 kb_src_v13 分器官语料中的出现频次
  ——双证（词表+语料）= tier A；仅词表 = tier B
Layer3 LLM 抽取（独立脚本 udut_llm_extract.py，vLLM 8002）→ tier C
LayerS 分级体系: 既有 UDAF 225 同义词 → tier S（已在生产）

产出: data/udt/vocab_v2.json
  {organ: [{term, tier, lex_df, corpus_freq, sources}]}, 外加 meta。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

SRC = Path("/mnt/ee16/home/student03/uai/src")
KB = SRC / "data" / "kb_src_v13"
THUOCL = Path("/tmp/THUOCL_medical.txt")
OUT = SRC / "data" / "udt" / "vocab_v2.json"

# 解剖锚点（封闭类，可穷举）——按器官归属
ANCHORS: dict[str, tuple[str, ...]] = {
    "thyroid": ("甲状腺", "峡部", "甲状旁腺", "颈部", "颈前", "气管旁"),
    "liver": ("肝", "胆囊", "胆管", "胆道", "胆总管", "门静脉", "肝动脉", "脾"),
    "cardiac": (
        "心脏", "心肌", "心房", "心室", "心包", "二尖瓣", "三尖瓣",
        "主动脉瓣", "肺动脉瓣", "主动脉", "肺动脉", "室间隔", "房间隔",
        "冠状动脉", "左心", "右心", "全心", "瓣膜", "心律", "射血分数", "EF",
    ),
}

CORPUS_STOP = ("检查", "医院", "报告", "超声", "影像", "科室", "门诊", "住院")


def load_thuocL() -> dict[str, int]:
    lex: dict[str, int] = {}
    for line in THUOCL.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split("\t")
        if len(parts) == 2 and parts[0]:
            try:
                lex[parts[0]] = int(parts[1])
            except ValueError:
                continue
    return lex


def organ_of(term: str) -> str | None:
    hits = [o for o, anchors in ANCHORS.items() if any(a in term for a in anchors)]
    if len(hits) == 1:
        return hits[0]
    return None  # 多器官命中或无命中：不强行归层（留给 LLM 层裁决）


def corpus_freq(organ: str, terms: list[str]) -> dict[str, int]:
    """词表术语在分器官语料中的出现频次（一次扫描，AC 自动机思路的朴素版）。"""
    freq: Counter[str] = Counter()
    for p in (KB / organ).glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        text = d.get("content", "") or d.get("text", "")
        for t in terms:
            if t in text:
                freq[t] += text.count(t)
    return freq


def main() -> int:
    lex = load_thuocL()
    print(f"[vocab-v2] THUOCL 加载 {len(lex)} 条")

    # Layer1: 解剖锚点过滤
    by_organ: dict[str, dict[str, int]] = {o: {} for o in ANCHORS}
    for term, df in lex.items():
        if any(s in term for s in CORPUS_STOP):
            continue
        o = organ_of(term)
        if o:
            by_organ[o][term] = df
    for o, d in by_organ.items():
        print(f"[vocab-v2] Layer1 {o}: {len(d)} 条（解剖锚点命中）")

    # Layer2: 语料对齐分级
    result: dict[str, list[dict]] = {}
    for organ, terms in by_organ.items():
        cf = corpus_freq(organ, list(terms))
        rows = []
        for term, df in terms.items():
            f = cf.get(term, 0)
            rows.append({
                "term": term,
                "tier": "A" if f >= 3 else "B",
                "lex_df": df,
                "corpus_freq": f,
                "sources": ["THUOCL"] + (["corpus"] if f else []),
            })
        rows.sort(key=lambda r: (-r["corpus_freq"], -r["lex_df"]))
        result[organ] = rows
        na = sum(1 for r_ in rows if r_["tier"] == "A")
        print(f"[vocab-v2] Layer2 {organ}: A={na} B={len(rows) - na}")

    meta = {
        "version": "vocab_v2",
        "sources": ["THUOCL_medical.txt (thunlp/THUOCL)"],
        "tier_meaning": {
            "A": "词表+语料双证（freq>=3）",
            "B": "仅词表（解剖锚点命中，语料未现/低频）",
            "C": "LLM 抽取（独立脚本追加）",
            "S": "生产分级同义词（UDAF 既有）",
        },
        "anchors": {k: list(v) for k, v in ANCHORS.items()},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"meta": meta, "organs": result}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print("[vocab-v2] ->", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
