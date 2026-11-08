"""第 4 组 4.1：**严格筛选**可用生成质量样本（只读）。

## 为什么要另写一个（不直接用 p100 的 `usable`）

`p100` 的 `usable` 只排除了「空 / 降级串 / 回显」，它不知道：
* 结论取的是 `fusion_result.content` 还是 `recommendations` 兜底（后者不是正式结论）；
* 结论里有没有**实质内容**（分级 / 建议），还是只有一句"请结合临床"；
* 有没有**重复**（同一条结论被多条记录共用 → 不是独立样本）；
* 有没有 **PHI**（姓名 / 手机号 / 身份证 / 病历号 / 真实患者 ID）。

## 准入判据（**跑之前写死**，全部可审计）

| 代号 | 判据 | 不满足时 |
|---|---|---|
| R1 | 结论取自 `fusion_result.content`（正式结论字段） | `reject:not_official_field` |
| R2 | 长度 ≥ 60 字（低于此基本是残句） | `reject:too_short` |
| R3 | 通过 `output_guard.detect_prompt_echo`（生产同一把尺子） | `reject:prompt_echo` |
| R4 | 无降级串（`p49.degraded_kind`） | `reject:degraded` |
| R5 | 含**分级**（`extract_grading_severest` 可解析）或明确处置建议 | `reject:no_substance` |
| R6 | 结论指纹在库内**唯一**（重复的只留一条，其余计 `dedup`） | 计入 `dedup` |
| R7 | 无 PHI（正则：手机号/身份证/病历号/`患者姓名`/中文姓名模式/`PATIENT_`） | `reject:phi` |
| R8 | `image_ids` 与 `clinical_text` 至少有一个（否则无法复现同一输入） | 仅记录，不拒 |

> R8 不拒的理由：生成质量测的是"给定输入 → 输出质量"，没有输入原文时仍能做
> 幻觉/faithfulness（这两项只依赖结论 + knowledge_ids），但**分级一致率**必须有输入。

用法（服务器）：
    ~/uai/venv/bin/python scripts/audit/run_with_env.py scripts/audit/p101_build_clean_gen_set.py \
        --report ~/uai/reports/p101_clean_gen_set.json \
        --out ~/uai/data/eval/gen_quality_clean_20261108.jsonl

退出码：0 出数；2 库不可达。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import pathlib
import re
import sys
from collections import Counter

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_SCRIPTS), str(_SCRIPTS / "audit"), str(_SCRIPTS / "langfuse")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from p49_degraded_cause import degraded_kind  # noqa: E402

from app.inference.grading_definitions import extract_grading_severest  # noqa: E402
from app.inference.output_guard import detect_prompt_echo  # noqa: E402

#: 最小长度（字符）。60 字以下基本是残句 / 单句占位
MIN_CHARS = 60

#: 处置建议的实质关键词（R5 的第二条腿：没有分级时看有没有可执行建议）
_ADVICE = re.compile(r"建议(?:复查|随访|穿刺|活检|手术|进一步|结合临床|定期)|随访|穿刺|活检|复查")

#: PHI 正则（宁可多拒，不许漏）
_PHI_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mobile", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("id_card", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("mrn", re.compile(r"(?:病历号|住院号|门诊号|MRN)\s*[:：]?\s*[A-Za-z0-9\-]{4,}")),
    ("patient_token", re.compile(r"PATIENT_\d+")),
    ("name_field", re.compile(r"(?:患者|病人)?姓名\s*[:：]\s*\S{2,4}")),
    ("age_sex_full", re.compile(r"(?:年龄|性别)\s*[:：]\s*\d{1,3}\s*岁?\s*[,，、/]\s*(?:男|女)")),
)


def phi_hits(text: str) -> list[str]:
    """命中的 PHI 类别列表（空 = 干净）。纯函数（守卫钉住）。"""
    return [name for name, pat in _PHI_PATTERNS if pat.search(text or "")]


def fingerprint(text: str) -> str:
    """结论指纹（sha256 前 16 位）。同一指纹 = 同一段文字。纯函数。"""
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()[:16]


def _official_content(findings: object) -> str:
    """R1：只认 `fusion_result.content`（**不用 `recommendations` 兜底**）。"""
    if not isinstance(findings, dict):
        return ""
    fr = findings.get("fusion_result")
    if isinstance(fr, dict):
        content = fr.get("content")
        if isinstance(content, str):
            return content.strip()
    return ""


def judge(findings: object, organ: str) -> tuple[bool, str, str]:
    """单条判定 → (是否准入, 拒绝码, 结论正文)。纯函数（守卫钉住）。"""
    text = _official_content(findings)
    if not text:
        return False, "not_official_field", ""
    if degraded_kind(text) != "not_degraded":
        return False, "degraded", text
    echo = detect_prompt_echo(text)
    if echo:
        return False, f"prompt_echo:{echo}", text
    hits = phi_hits(text)
    if hits:
        return False, "phi:" + ",".join(hits), text
    if len(text) < MIN_CHARS:
        return False, "too_short", text
    grade = extract_grading_severest(text, organ)
    if not grade and not _ADVICE.search(text):
        return False, "no_substance", text
    return True, ("has_grade" if grade else "advice_only"), text


async def build() -> dict:
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import DiagnosisResult

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    DiagnosisResult.request_id,
                    DiagnosisResult.session_id,
                    DiagnosisResult.organ,
                    DiagnosisResult.created_at,
                    DiagnosisResult.findings,
                    DiagnosisResult.knowledge_ids,
                    DiagnosisResult.image_ids,
                    DiagnosisResult.clinical_text,
                    DiagnosisResult.confidence,
                    DiagnosisResult.status,
                ).order_by(DiagnosisResult.created_at)
            )
        ).all()

    seen: dict[str, str] = {}  # 指纹 → 首个 request_id
    accepted: list[dict] = []
    rejects: Counter[str] = Counter()
    dedup = 0
    for rid, sid, organ, created, findings, kids, imgs, ctext, conf, status in rows:
        ok, why, text = judge(findings, str(organ or ""))
        if not ok:
            rejects[why] += 1
            continue
        fp = fingerprint(text)
        if fp in seen:
            dedup += 1
            continue
        seen[fp] = str(rid)
        accepted.append(
            {
                "request_id": str(rid),
                "session_id": str(sid or ""),
                "organ": str(organ or ""),
                "created_at": created.isoformat() if created else None,
                "conclusion": text,
                "conclusion_fp": fp,
                "grade": extract_grading_severest(text, str(organ or "")),
                "has_recommendation": bool(_ADVICE.search(text)),
                "knowledge_ids": [str(k) for k in (kids or [])],
                "n_knowledge_ids": len(kids or []),
                "image_ids": [str(i) for i in (imgs or [])],
                "clinical_text": str(ctext or ""),
                "confidence": float(conf or 0.0),
                "status": str(status or ""),
            }
        )
    return {
        "total_rows": len(rows),
        "accepted": accepted,
        "rejects": dict(rejects),
        "dedup_dropped": dedup,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", default=str(pathlib.Path.home() / "uai/reports/p101_clean_gen_set.json"))
    ap.add_argument("--out", default=str(pathlib.Path.home() / "uai/data/eval/gen_quality_clean_20261108.jsonl"))
    args = ap.parse_args()

    try:
        data = asyncio.run(build())
    except Exception as exc:
        print(f"[FATAL] 库不可达或查询失败：{exc!r}")
        return 2

    acc = data["accepted"]
    summary = {
        "total_rows": data["total_rows"],
        "accepted_n": len(acc),
        "dedup_dropped": data["dedup_dropped"],
        "rejects": data["rejects"],
        "by_organ": dict(Counter(a["organ"] for a in acc)),
        "with_grade": sum(1 for a in acc if a["grade"]),
        "with_knowledge_ids": sum(1 for a in acc if a["n_knowledge_ids"]),
        "with_image_ids": sum(1 for a in acc if a["image_ids"]),
        "with_clinical_text": sum(1 for a in acc if a["clinical_text"].strip()),
        "reproducible_input": sum(
            1 for a in acc if a["image_ids"] or a["clinical_text"].strip()
        ),
        "len_p50": sorted(len(a["conclusion"]) for a in acc)[len(acc) // 2] if acc else 0,
        "by_month": dict(Counter((a["created_at"] or "")[:7] for a in acc)),
        "grade_dist": dict(Counter(str(a["grade"]) for a in acc)),
    }

    rpt = pathlib.Path(args.report)
    rpt.parent.mkdir(parents=True, exist_ok=True)
    rpt.write_text(
        json.dumps({"summary": summary, "rows": acc}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for a in acc:
            fh.write(json.dumps(a, ensure_ascii=False) + "\n")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n报告：{rpt}\n样本：{out}（{len(acc)} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
