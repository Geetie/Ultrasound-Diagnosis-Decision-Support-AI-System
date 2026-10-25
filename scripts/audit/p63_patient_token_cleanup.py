"""A.90 历史 `患者PATIENT_00N` 语料标记清理（**只读盘点 / dry-run 默认**）。

## 为什么要单独立项（不是顺手删）

A.77 的教训（2026-10-21，16 号附录）：我曾把"答案里出现 `PATIENT_001`"当成
"模板回显铁证"→ **误删 415 条真结论**（它们有分级、有鉴别诊断，只是把语料
脱敏标记抄进了患者解释；prompt 里根本没有该串，KB 里倒有 22 块）。
之后的规则改成：`PATIENT_` **单独出现只算"语料标记外泄"，处置是清洗而非拒收**。

A.89 又发现：这个标记还会让安全网关把它误判成"患者姓名"→ 整条结论降级抑制
（已在 A.89.4 修掉规则，但**历史结论里的标记本身还在**）。

所以本轮做的是**清洗**（把 `患者PATIENT_001:` 之类改成 `患者:`），
不是拒收、不是删除整条。且严格遵守 AGENTS.md 硬规则 14：
**先备份、只处理已打标的、默认 dry-run**。

## 判据（两条独立证据，缺一不处理）

1. **形态证据**：文本里出现 `PATIENT_\\\\d+` 且**紧跟在**"患者/病人"之后，
   或被当作称谓前缀（`PATIENT_010医生`）—— 即"语料脱敏占位符被当成人名用"；
2. **上下文证据**：该串**不是**真实证据引用（真实引用形如
   `kb_v1.3.0:pdf_x_0001#0000`），也不是 `chunk_a1b2` 这类模板假 ID
   （那属于 A.70 的模板回显，处置不同：拒收）。

不满足两条 → **不动**（宁可留着，也不重演 A.77）。

## 清洗规则（幂等、可逆）

| 原始形态 | 清洗后 |
|---|---|
| `患者PATIENT_001:` / `患者PATIENT_001：` | `患者：` |
| `PATIENT_010医生`（称谓前缀） | `医生` |
| `患者PATIENT_001`（无冒号，句中出现） | `患者` |

清洗**只改正文里的占位符串**，不改分级、不改置信度、不改 status、
不改 `knowledge_ids`；原值全部写进备份 JSONL（含完整 findings）。

## 用法

    # 1) 盘点（默认，只读）
    ~/uai/venv/bin/python scripts/audit/p63_patient_token_cleanup.py

    # 2) 备份（不删不改，只导出）
    ... --backup ~/uai/data/eval/patient_token_cleanup_20261025.jsonl

    # 3) 真正清洗（必须显式 --apply，且必须先有备份文件存在）
    ... --apply --backup <同一个备份路径>

退出码：0 正常｜2 前置缺失（未备份就 --apply / 库不可达）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import text  # noqa: E402

from app.config.database import AsyncSessionLocal  # noqa: E402

#: 语料脱敏占位符（对话语料脱敏时用它顶替患者称呼）
_TOKEN = r"PATIENT_\d+"

#: 形态证据：占位符被当成人名使用（"患者X" 或 "X医生/主任/…"）
_RE_HUMAN_LIKE = re.compile(
    rf"(?:患者|病人|被检者)\s*{_TOKEN}|{_TOKEN}\s*(?:医生|主任|教授|护士|技师|大夫|院长)"
)

#: 模板假证据 ID（属于 A.70 的"模板回显"，处置是拒收，不是清洗）
_RE_TEMPLATE_CHUNK = re.compile(r"chunk_[a-z0-9]{4}\b")

#: 真实证据引用（形如 kb_v1.3.0:xxx#0000）
_RE_REAL_EVIDENCE = re.compile(r"kb_v[\d.]+:[^\s,，]*(?:#\d+)?")


def clean_text(s: str) -> tuple[str, list[dict]]:
    """把"被当成人名用的语料占位符"替换掉。纯函数（守卫钉住）。

    Returns:
        ``(cleaned, hits)``，``hits`` 记录每处替换的 `kind` 与偏移
        （便于审计；**不含替换前原文**，避免 PHI/占位符再次落日志）。
    """
    if not s:
        return s, []
    hits: list[dict] = []
    out = s

    # ① "患者PATIENT_001:" / "患者PATIENT_001：" → "患者："
    pattern_a = re.compile(rf"(?:患者|病人|被检者)\s*{_TOKEN}\s*[：:]")
    for m in pattern_a.finditer(out):
        hits.append({"kind": "patient_prefix_with_colon", "start": m.start()})
    out = pattern_a.sub("患者：", out)

    # ② "PATIENT_010医生"（占位符当前缀）→ "医生"
    pattern_b = re.compile(rf"{_TOKEN}\s*(?=(?:医生|主任|教授|护士|技师|大夫|院长))")
    for m in pattern_b.finditer(out):
        hits.append({"kind": "token_as_title_prefix", "start": m.start()})
    out = pattern_b.sub("", out)

    # ③ 剩下裸出现（句中）→ "患者"
    pattern_c = re.compile(rf"{_TOKEN}")
    for m in pattern_c.finditer(out):
        hits.append({"kind": "bare_token", "start": m.start()})
    out = pattern_c.sub("患者", out)

    return out, hits


def classify(conclusion: str) -> dict:
    """判定一条结论该怎么处置（纯函数，两条独立证据）。

    Returns:
        ``{"action": "clean" | "skip_template_echo" | "skip_no_token" | "skip_real_evidence",
           "human_like": int, "template_chunk": int, "real_evidence": int}``
    """
    text_ = conclusion or ""
    human_like = len(_RE_HUMAN_LIKE.findall(text_))
    template_chunk = len(_RE_TEMPLATE_CHUNK.findall(text_))
    real_evidence = len(_RE_REAL_EVIDENCE.findall(text_))

    facts = {
        "human_like": human_like,
        "template_chunk": template_chunk,
        "real_evidence": real_evidence,
    }
    if human_like == 0:
        return {"action": "skip_no_token", **facts}
    # 证据 1（形态）已满足；再用证据 2 排除"模板回显"（那是拒收不是清洗）
    if template_chunk > 0:
        return {"action": "skip_template_echo", **facts}
    return {"action": "clean", **facts}


async def _load_rows(limit: int | None) -> list[dict]:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT request_id, organ, status, findings
                    FROM diagnosis_results
                    WHERE findings->'fusion_result'->>'content' LIKE '%PATIENT_%'
                    ORDER BY created_at
                    """
                )
            )
        ).all()
    out = []
    for rid, organ, status, findings in rows:
        payload = findings if isinstance(findings, dict) else {}
        fr = payload.get("fusion_result") if isinstance(payload.get("fusion_result"), dict) else {}
        content = str(fr.get("content") or "")
        if not content:
            continue
        out.append(
            {
                "request_id": str(rid),
                "organ": str(organ or ""),
                "status": str(status or ""),
                "findings": payload,
                "content": content,
            }
        )
    return out[:limit] if limit else out


async def main(
    *, apply: bool, backup: str | None, limit: int | None, report: str | None
) -> int:
    rows = await _load_rows(limit)
    print(f"扫描：含 `PATIENT_` 的判读记录 = {len(rows)} 条")
    if not rows:
        print("（无需处理）")
        return 0

    to_clean: list[dict] = []
    skipped: dict[str, int] = {}
    for r in rows:
        verdict = classify(r["content"])
        if verdict["action"] == "clean":
            cleaned, hits = clean_text(r["content"])
            r["cleaned"] = cleaned
            r["hits"] = hits
            to_clean.append(r)
        else:
            skipped[verdict["action"]] = skipped.get(verdict["action"], 0) + 1

    print(f"  判定可清洗 = {len(to_clean)} 条")
    for k, v in sorted(skipped.items()):
        print(f"  跳过 [{k}] = {v} 条")

    if to_clean[:3]:
        print("\n清洗样例（前 3 条，只显示替换前后各 60 字）：")
        for r in to_clean[:3]:
            before = r["content"]
            after = r["cleaned"]
            print(f"  - {r['request_id']} ({r['organ']})")
            print(f"      前: {before[:60]!r}")
            print(f"      后: {after[:60]!r}")
            print(f"      替换 {len(r['hits'])} 处")

    if report:
        rp = Path(report).expanduser()
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "scanned": len(rows),
                    "cleanable": len(to_clean),
                    "skipped": skipped,
                    "applied": False,
                    "items": [
                        {
                            "request_id": r["request_id"],
                            "organ": r["organ"],
                            "hits": r["hits"],
                            "before_len": len(r["content"]),
                            "after_len": len(r["cleaned"]),
                        }
                        for r in to_clean
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n盘点报告：{rp}")

    # ---- 备份（硬规则 14：删除/改动历史数据必须先备份）----
    if backup and to_clean:
        bp = Path(backup).expanduser()
        bp.parent.mkdir(parents=True, exist_ok=True)
        with bp.open("w", encoding="utf-8") as fh:
            for r in to_clean:
                fh.write(
                    json.dumps(
                        {
                            "request_id": r["request_id"],
                            "organ": r["organ"],
                            "status": r["status"],
                            # 完整 findings（含原正文），可完整还原
                            "findings": r["findings"],
                            "hits": r["hits"],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        print(f"备份已写：{bp}（{len(to_clean)} 行）")

    if not apply:
        print("\n[dry-run] 未修改数据库。要真正清洗，请加 --apply 并同时给 --backup。")
        return 0

    # ---- 安全闸：apply 必须已有备份文件存在 ----
    if not backup or not Path(backup).expanduser().is_file():
        print("[FATAL] --apply 要求先写备份（--backup <path>），且文件必须已存在")
        return 2

    applied = 0
    async with AsyncSessionLocal() as db:
        for r in to_clean:
            new_findings = dict(r["findings"])
            fr = dict(new_findings.get("fusion_result") or {})
            fr["content"] = r["cleaned"]
            fr["patient_token_cleaned"] = True
            fr["patient_token_clean_hits"] = len(r["hits"])
            new_findings["fusion_result"] = fr
            await db.execute(
                text(
                    "UPDATE diagnosis_results SET findings = CAST(:f AS jsonb) "
                    "WHERE request_id = :r"
                ),
                {"f": json.dumps(new_findings, ensure_ascii=False), "r": r["request_id"]},
            )
            applied += 1
        await db.commit()
    print(f"\n[APPLIED] 已清洗 {applied} 条（备份可完整还原）")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真的写库（默认 dry-run）")
    ap.add_argument("--backup", default=None, help="备份 JSONL 路径（--apply 时必填且需已存在）")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--report", default=None, help="盘点报告 JSON 路径")
    a = ap.parse_args()
    sys.exit(
        asyncio.run(
            main(apply=a.apply, backup=a.backup, limit=a.limit, report=a.report)
        )
    )
