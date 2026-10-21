"""A.77：**复查已删除记录的分类**，把被误判的真结论还原成可用样本（只读备份，不写库）。

**背景（我造成的错误）**：A.70.2 的回显规则把 `PATIENT_001` 单独出现当成"模板回显"，
于是 A.70.8 删掉的 660 条里有 **415 条其实是真结论**（有分级、有鉴别诊断、有患者解释），
只是它们**引用了 `chunk_1` 这类假 ID**（另一个缺陷：引用编造），并且把语料脱敏标记
`PATIENT_001` 抄进了患者解释（第三个缺陷：语料标记外泄）。

**本脚本做三件事**（不写数据库，只产出文件 + 统计）：
1. 按**确凿证据**重新分类每条备份记录：
   * `template_echo`：含 `chunk_[a-z0-9]{4}`（如 `chunk_a1b2`）或 `# 输出` ≥2 次；
   * `fake_citation`：含 `chunk_\\d+` 这类假编号（真 ID 形如 `kb_v1.3.0:...#0000`）；
   * `patient_token_leak`：含 `PATIENT_\\d+`；
   * `clean`：以上都没有。
2. 输出 `restored` JSONL（`template_echo` 之外的全部）—— 供评测/复核重新使用；
3. 打印分类统计，便于判断"到底删错了多少"。

用法（服务器）：
    ~/uai/venv/bin/python scripts/audit/run_with_env.py \
        scripts/audit/p58_reclassify_deleted_backups.py
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections import Counter

_DEFAULT_BACKUPS = (
    "~/uai/data/eval/deleted_dirty_20261020.jsonl",
    "~/uai/data/eval/deleted_dirty_round2_20261020.jsonl",
)

_CHUNK_PLACEHOLDER = re.compile(r"chunk_[a-z0-9]{4}\b")
_CHUNK_FAKE_NUM = re.compile(r"chunk_\d+\b")
_PATIENT_TOKEN = re.compile(r"PATIENT_\d+")
_OUTPUT_HEADER = re.compile(r"#\s*输出")


def conclusion_of(findings: object) -> str:
    """从备份的 findings 里取结论正文（与 `_conclusion_of` 同口径）。纯函数（守卫钉住）。"""
    if not isinstance(findings, dict):
        return ""
    fr = findings.get("fusion_result")
    if isinstance(fr, dict):
        return str(fr.get("content") or "")
    rec = findings.get("recommendations")
    return rec if isinstance(rec, str) else ""


def classify(text: str) -> list[str]:
    """一条记录命中的缺陷类（可多选）。纯函数（守卫钉住）。"""
    t = text or ""
    tags: list[str] = []
    if _CHUNK_PLACEHOLDER.search(t) or len(_OUTPUT_HEADER.findall(t)) >= 2:
        tags.append("template_echo")
    if _CHUNK_FAKE_NUM.search(t):
        tags.append("fake_citation")
    if _PATIENT_TOKEN.search(t):
        tags.append("patient_token_leak")
    return tags or ["clean"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backup", action="append", default=None, help="备份 JSONL（可多次）")
    ap.add_argument(
        "--out",
        default=str(pathlib.Path.home() / "uai/data/eval/restored_non_echo_20261021.jsonl"),
        help="还原清单（template_echo 之外的记录）",
    )
    args = ap.parse_args()

    paths = [pathlib.Path(p).expanduser() for p in (args.backup or _DEFAULT_BACKUPS)]
    rows: list[dict] = []
    for path in paths:
        if not path.is_file():
            print(f"[跳过] 不存在：{path}")
            continue
        rows.extend(
            json.loads(line)
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip()
        )

    if not rows:
        print("[FATAL] 备份为空/不存在")
        return 2

    counts: Counter = Counter()
    multi: Counter = Counter()
    restored: list[dict] = []
    for r in rows:
        text = conclusion_of(r.get("findings"))
        tags = classify(text)
        counts["total"] += 1
        for tag in tags:
            counts[tag] += 1
        multi["+".join(sorted(tags))] += 1
        if "template_echo" not in tags:
            restored.append(
                {
                    "request_id": r.get("request_id"),
                    "organ": r.get("organ"),
                    "created_at": r.get("created_at"),
                    "conclusion": text,
                    "defects": tags,
                    "source": "deleted_backup",
                }
            )

    out = pathlib.Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for row in restored:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"备份总数 {counts['total']}")
    print(f"  template_echo（确凿回显）    {counts.get('template_echo', 0)}")
    print(f"  fake_citation（假 chunk_N）  {counts.get('fake_citation', 0)}")
    print(f"  patient_token_leak（PATIENT_）{counts.get('patient_token_leak', 0)}")
    print(f"  clean                        {counts.get('clean', 0)}")
    print(f"\n组合分布（前 6）：{dict(multi.most_common(6))}")
    print(f"\n**非回显记录 {len(restored)} 条 → {out}**")
    print("（这些是 A.70.8 删掉的，其中大部分应为真结论；是否重新入库需人工确认）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
