"""合并四层来源 -> vocab_v2_final.json（去重 + 溯源 + 分层）。"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

SRC = Path("/mnt/ee16/home/student03/uai/src")
UDT = SRC / "data" / "udt"
sys.path.insert(0, str(SRC))

out: dict[str, dict[str, dict]] = {}
for organ in ("thyroid", "liver", "cardiac"):
    out[organ] = {}

    def add(term: str, tier: str, source: str, extra: dict | None = None) -> None:
        term = term.strip()
        if not 2 <= len(term) <= 12:
            return
        rec = out[organ].setdefault(term, {"tier": tier, "sources": [], "freq": 0})
        rec["sources"].append(source)
        rec["freq"] += extra.get("freq", 0) if extra else 0
        order = {"S": 0, "A": 1, "B": 2, "C": 3}
        if order[tier] < order[rec["tier"]]:
            rec["tier"] = tier

    from app.knowledge.vocabulary import UDAFVocabulary
    v = UDAFVocabulary()
    for s in sorted(v.get_all_synonyms()):
        add(s, "S", "udaf")

    vv = json.load(open(UDT / "vocab_v2.json", encoding="utf-8"))
    for row in vv["organs"].get(organ, []):
        add(row["term"], row["tier"], "THUOCL", {"freq": row["corpus_freq"]})

    draft = json.load(open(UDT / "phase1_draft_v1.json", encoding="utf-8"))
    for term, f in draft.get(organ, {}).items():
        add(term, "C", "corpus_morph", {"freq": f})

    llm = json.load(open(UDT / "llm_terms.json", encoding="utf-8"))
    for term, c in llm.get(organ, {}).items():
        add(term, "C", "llm", {"freq": c})

for organ, terms in out.items():
    tiers = Counter(r["tier"] for r in terms.values())
    print(organ, "总词条:", len(terms), dict(tiers))

final = {
    "meta": {
        "version": "vocab_v2_final",
        "tiers": {
            "S": "UDAF 生产分级同义词",
            "A": "THUOCL+语料双证",
            "B": "THUOCL 解剖锚点（语料低频）",
            "C": "语料词素/LLM 抽取（待复核）",
        },
        "sources": ["UDAF", "THUOCL_medical(thunlp)", "kb_src_v13 corpus",
                    "Qwen2.5-7B extraction"],
        "date": "2026-10-02",
    },
    "organs": {
        o: {t: r for t, r in sorted(rec.items(), key=lambda kv: -kv[1]["freq"])}
        for o, rec in out.items()
    },
}
path = UDT / "vocab_v2_final.json"
path.write_text(json.dumps(final, ensure_ascii=False, indent=1), encoding="utf-8")
print("->", path)
