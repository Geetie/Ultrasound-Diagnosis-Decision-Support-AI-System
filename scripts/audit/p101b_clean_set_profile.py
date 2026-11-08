"""第 4 组 4.1 复核：**干净样本集的分布与可测性**（只读，从已生成的 jsonl 统计）。

这份脚本回答"4.1 到底筛出了多少条真正可用的样本"，并把三类样本分开数：
* `clean`      —— 通过严格筛选的（非空/非降级/非回显/无 PHI/唯一/有实质内容）；
* `assertive`  —— 其中**有分级且有理由**（可进幻觉/faithfulness 的）；
* `refusal`    —— 有分级行但**正文在拒答**（"信息不足/无法判断"），只能进幻觉率的分母。

用法：
    ~/uai/venv/bin/python scripts/audit/p101b_clean_set_profile.py \
        --clean ~/uai/data/eval/gen_quality_clean_20261108.jsonl \
        --report ~/uai/reports/p101b_profile.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
import sys
from collections import Counter

#: 拒答/信息不足的标志词（与 p101 的准入判据**不同**：准入只看"有没有实质内容"，
#: 这里看"这条实质内容是不是在说自己判不出来"）
REFUSAL = re.compile(
    r"信息不足|无法判断|无法进行|无法准确|未提供|证据不足|资料不足|不足|不确定|"
    r"请提供|请重新|重新上传|重新获取|屏幕截图|元数据不可用|缺少"
)
GRADE = re.compile(r"TR\s*[-_]?\s*[1-5]|LR\s*[-_]?\s*[1-5]|分级\s*[:：]|LVEF\s*[:：]")
SUBSTANCE = re.compile(r"理由\s*[:：]|鉴别诊断|分\s*→|评分|支持\s*[:：]|排除\s*[:：]")
FAKE_ID = re.compile(r"chunk_[a-z0-9]{1,4}\b", re.IGNORECASE)


def classify(conclusion: str) -> str:
    """一条干净样本的**可测性**分类。纯函数（守卫钉住）。"""
    t = conclusion or ""
    if REFUSAL.search(t):
        return "refusal"
    if GRADE.search(t) and SUBSTANCE.search(t):
        return "assertive"
    return "weak"


def profile(rows: list[dict]) -> dict:
    cat = Counter(classify(r.get("conclusion") or "") for r in rows)
    assertives = [r for r in rows if classify(r.get("conclusion") or "") == "assertive"]
    lens = sorted(len(r.get("conclusion") or "") for r in rows)
    return {
        "n_clean": len(rows),
        "by_category": dict(cat),
        "n_assertive": len(assertives),
        "assertive_by_organ": dict(Counter(r.get("organ") for r in assertives)),
        "assertive_by_month": dict(Counter((r.get("created_at") or "")[:7] for r in assertives)),
        "assertive_distinct_inputs": len(
            {(r.get("clinical_text") or "", r.get("organ")) for r in assertives}
        ),
        "assertive_grade_dist": dict(Counter(str(r.get("grade")) for r in assertives)),
        "assertive_with_fake_chunk_id": sum(
            1 for r in assertives if FAKE_ID.search(r.get("conclusion") or "")
        ),
        "len_p50": statistics.median(lens) if lens else 0,
        "len_min": lens[0] if lens else 0,
        "len_max": lens[-1] if lens else 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clean", default=str(
        pathlib.Path.home() / "uai/data/eval/gen_quality_clean_20261108.jsonl"))
    ap.add_argument("--report", default=str(pathlib.Path.home() / "uai/reports/p101b_profile.json"))
    args = ap.parse_args()

    path = pathlib.Path(args.clean).expanduser()
    if not path.is_file():
        print(f"[沉默风险] 样本文件不存在：{path}")
        return 2
    rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    out = {"file": str(path), **profile(rows)}
    rpt = pathlib.Path(args.report).expanduser()
    rpt.parent.mkdir(parents=True, exist_ok=True)
    rpt.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
