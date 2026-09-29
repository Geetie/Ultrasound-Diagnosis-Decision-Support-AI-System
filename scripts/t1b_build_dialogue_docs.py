"""T1b v2：按价值优先级筛甲状腺对话 QA（肿瘤科→外科→内科；标题命中优先）。"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

SOURCES = [  # 价值优先级：甲状腺癌(肿瘤科) > 结节手术(外科) > 内科(内分泌/甲功)
    (r"C:\Windows\Temp\medical_dialogue\Data_数据\Oncology_肿瘤科\肿瘤科5-10000.csv", "肿瘤科"),
    (r"C:\Windows\Temp\medical_dialogue\Data_数据\Surgical_外科\外科5-14000.csv", "外科"),
    (r"C:\Windows\Temp\medical_dialogue\Data_数据\IM_内科\内科5000-33000.csv", "内科"),
]
KEYWORD = "甲状腺"
MAX_DOCS = 400
MIN_ANSWER_LEN = 60


def read_csv_rows(path: Path) -> list[dict]:
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            with open(path, encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"无法解码: {path}")


def main() -> int:
    title_hits: list[dict] = []
    body_hits: list[dict] = []
    seen_titles: set[str] = set()
    stats: dict[str, str] = {}

    for src, dept_label in SOURCES:
        path = Path(src)
        rows = read_csv_rows(path)
        t_count = b_count = 0
        for row in rows:
            title = (row.get("title") or "").strip()
            ask = (row.get("ask") or "").strip()
            answer = (row.get("answer") or "").strip()
            if len(answer) < MIN_ANSWER_LEN or title in seen_titles:
                continue
            blob = title + ask + answer
            if KEYWORD not in blob:
                continue
            doc = {
                "source_doc_id": f"dialogue_{dept_label}_{len(title_hits) + len(body_hits) + 1:04d}",
                "title": title[:80],
                "source_kind": "case_reports",
                "section_path": f"医疗对话/{dept_label}",
                "content": f"患者提问：{title}。{ask}\n医生回答：{answer}",
            }
            if KEYWORD in title:
                title_hits.append(doc)
                seen_titles.add(title)
                t_count += 1
            elif b_count + t_count < MAX_DOCS:
                body_hits.append(doc)
                seen_titles.add(title)
                b_count += 1
        stats[dept_label] = f"标题命中 {t_count} / 正文命中 {b_count}"

    docs = (title_hits + body_hits)[:MAX_DOCS]

    # 按科轮转取满（KB 科室多样性）：全部命中先按科分桶，再轮转取 MAX_DOCS
    by_source: dict[str, list[dict]] = {}
    for d in title_hits + body_hits:
        label = d["section_path"].split("/")[-1]
        by_source.setdefault(label, []).append(d)
    docs = []
    while len(docs) < MAX_DOCS and any(by_source.values()):
        for label in list(by_source):
            if by_source[label]:
                docs.append(by_source[label].pop(0))
                if len(docs) >= MAX_DOCS:
                    break
    for k, v in stats.items():
        print(k, "->", v)
    print("total docs:", len(docs))

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/kb_sources/dialogue_docs.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
