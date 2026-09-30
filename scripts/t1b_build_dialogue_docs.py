"""T1b v3：按器官关键词从中文医疗对话筛 QA 生成 KB 文档（JSONL）。

v3（2026-10-01，P1-KB 扩展）：原 v2 硬编码甲状腺；参数化 organ 关键词与
源科别，支持 liver/cardiac 等新器官 KB 构建（kb_v1.1.0）。
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

DEFAULT_SOURCES = {  # 服务器路径：科别 label -> csv
    "肿瘤科": "~/uai/data/chinese_medical_dialogue/Data_数据/Oncology_肿瘤科/肿瘤科5-10000.csv",
    "外科": "~/uai/data/chinese_medical_dialogue/Data_数据/Surgical_外科/外科5-14000.csv",
    "内科": "~/uai/data/chinese_medical_dialogue/Data_数据/IM_内科/内科5000-33000.csv",
}
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
    parser = argparse.ArgumentParser(description="按器官关键词筛对话 QA 生成 KB 文档")
    parser.add_argument("--organ", required=True, help="器官标识（用于 source_doc_id 前缀，如 liver）")
    parser.add_argument("--keyword", required=True, help="筛选关键词（如 肝脏；多词用逗号分隔）")
    parser.add_argument("--max-docs", type=int, default=200)
    parser.add_argument(
        "--sources",
        default=",".join(f"{k}:{v}" for k, v in DEFAULT_SOURCES.items()),
        help="科别:csv路径 逗号分隔（路径支持 ~）",
    )
    parser.add_argument("--out", required=True, help="输出 JSONL 路径")
    args = parser.parse_args()

    keywords = [k.strip() for k in args.keyword.split(",") if k.strip()]
    sources: list[tuple[str, Path]] = []
    for pair in args.sources.split(","):
        label, _, path = pair.partition(":")
        if label and path:
            sources.append((label, Path(path).expanduser()))

    title_hits: list[dict] = []
    body_hits: list[dict] = []
    seen_titles: set[str] = set()
    seq = 0
    stats: dict[str, str] = {}

    def hit(blob: str) -> bool:
        return any(k in blob for k in keywords)

    for label, path in sources:
        rows = read_csv_rows(path)
        t_count = b_count = 0
        for row in rows:
            title = (row.get("title") or "").strip()
            ask = (row.get("ask") or "").strip()
            answer = (row.get("answer") or "").strip()
            if len(answer) < MIN_ANSWER_LEN or title in seen_titles:
                continue
            blob = title + ask + answer
            if not hit(blob):
                continue
            seq += 1
            doc = {
                "source_doc_id": f"dialogue_{args.organ}_{label}_{seq:04d}",
                "title": title[:80],
                "source_kind": "case_reports",
                "section_path": f"医疗对话/{label}",
                "content": f"患者提问：{title}。{ask}\n医生回答：{answer}",
            }
            seen_titles.add(title)
            if hit(title):
                title_hits.append(doc)
                t_count += 1
            else:
                body_hits.append(doc)
                b_count += 1
        stats[label] = f"标题命中 {t_count} / 正文命中 {b_count}"

    # 标题命中优先，正文补足
    docs = (title_hits + body_hits)[: args.max_docs]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"[{args.organ}] 共 {len(docs)} 文档 → {out}")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
