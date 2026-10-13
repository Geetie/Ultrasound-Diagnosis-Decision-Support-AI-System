"""p30 证据链核实：数据库里记的 `knowledge_ids`，到底是不是「当时喂给模型的检索上下文」？

**为什么要查（A.61.7 登记的真问题）**：L2 忠实度判官在口径 B 下把「实性特征被赋予 3 分」这类
TI-RADS 评分规则判成「上下文不支持」，而它们**本应在指南块里** —— 要么检索没召回，
要么**我们记下来的证据比模型当时看到的少**。后者会让一切「结论可溯源」的说法打折，
也会让 L2 判官系统性低估（判官看的是我们记的那几条，不是模型看的全部）。

**为什么必须在生产侧留痕（A.62 实测的架构事实）**：
判读**不是**在 API 进程里跑的 —— 端点只做 `enqueue_diagnosis`（Redis 队列），真正的判读由
**worker 进程** BLPOP 取走执行（`app/services/diagnosis_worker.py`）。所以：
- 外部探针进程里给 `RAGServiceImpl.retrieve` 打类级包装 → **一条都捕不到**（判读被别人消费了）；
- 离线"重跑检索"也不可比：生产的 query = `clinical_text` + 影像量化块 + 「患者信息：…」，
  并带器官检索档位（`diagnosis_pipeline.py:591-619`）→ 重建必然不同 → **会得出假 MISMATCH**
  （本探针第一版就这么错过一次，第二版又因给 `retrieve` 多传一个参数把它打坏、被管线优雅降级吞掉）。
- 结论：只有让生产侧把两边都打出来。`app/agents/specialist/base.py::_log_evidence_chain`
  在 `UAI_EVIDENCE_CHAIN_DEBUG=1` 时输出两行（`_build_prompt` 打 prompt 里的块、
  `_post_process` 打落库的块），用同一个 `request_id` 对齐。**本探针读这两行**。

**判定分类**（写进报告的 `verdict`）：
| verdict | 含义 | 要不要动手 |
|---|---|---|
| `PREFIX_TRUNCATED` | 落库 = prompt 里的**前 N 条**（有意截断） | 忠实但**不完整**；L2 口径要按"补全上下文"再判一次 |
| `ORDER_MISMATCH` | 集合相同但顺序不同 | 要查（顺序影响模型注意力，也影响判官） |
| `SUBSET_NONPREFIX` | 是子集但非前缀 | 要查（选择逻辑与顺序不一致） |
| `NOT_RECORDED` | 检索有块、落库为空 | **缺陷**（证据链断了） |
| `MISMATCH` | 落库里有块不在本次检索结果中 | 要查（或检索不可复现） |
| `NO_RETRIEVAL` | prompt 里本来就没有块 | 先看检索，不看记录 |

**真机实测（2026-10-13，`req_20261013_eae7a3ee`）**：prompt 5 块 / 落库 3 块，
落库 = prompt 的**前 3 条**（顺序一致）→ `PREFIX_TRUNCATED`。

用法（服务器，先在带 `UAI_EVIDENCE_CHAIN_DEBUG=1` 的服务上跑一次判读）：
    python scripts/audit/p30_evidence_chain.py [--log ~/uai/logs/api.log] [--judge-check]

退出码：0 = `PREFIX_TRUNCATED`（链路忠实、记录不完整）；1 = 其它（要查/缺陷）；2 = 缺输入。
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_VERDICTS = (
    "PREFIX_TRUNCATED",
    "ORDER_MISMATCH",
    "SUBSET_NONPREFIX",
    "NOT_RECORDED",
    "MISMATCH",
    "NO_RETRIEVAL",
)


def classify(recorded: list[str], retrieved: list[str]) -> str:
    """判定「落库的块」与「prompt 里的块」的关系（纯函数，便于反证自测）。"""
    if not retrieved:
        return "NO_RETRIEVAL"
    if not recorded:
        return "NOT_RECORDED"
    if list(recorded) == list(retrieved):
        return "PREFIX_TRUNCATED"  # 全等也归此类（记录完整）
    if set(recorded) == set(retrieved):
        return "ORDER_MISMATCH"
    if set(recorded) <= set(retrieved):
        return "PREFIX_TRUNCATED" if list(retrieved[: len(recorded)]) == list(recorded) \
            else "SUBSET_NONPREFIX"
    return "MISMATCH"


def _counter_evidence() -> list[tuple[str, bool, str]]:
    """反证腿：判定器不能"永远 PASS" —— 故意喂坏输入必须被判成对应的问题类型。"""
    base = ["c1", "c2", "c3", "c4", "c5"]
    cases = [
        ("全等 → PREFIX_TRUNCATED", ["c1", "c2", "c3", "c4", "c5"], base, "PREFIX_TRUNCATED"),
        ("前 3 条 → PREFIX_TRUNCATED", ["c1", "c2", "c3"], base, "PREFIX_TRUNCATED"),
        ("顺序倒置 → ORDER_MISMATCH", ["c5", "c4", "c3", "c2", "c1"], base, "ORDER_MISMATCH"),
        ("子集非前缀 → SUBSET_NONPREFIX", ["c2", "c4"], base, "SUBSET_NONPREFIX"),
        ("空记录 → NOT_RECORDED", [], base, "NOT_RECORDED"),
        ("记录含未知块 → MISMATCH", ["c1", "cX"], base, "MISMATCH"),
        ("prompt 为空 → NO_RETRIEVAL", ["c1"], [], "NO_RETRIEVAL"),
    ]
    out = []
    for name, recorded, retrieved, expect in cases:
        got = classify(recorded, retrieved)
        out.append((name, got == expect, f"expect={expect} got={got}"))
    return out


def parse_evidence_log(log_path: Path) -> list[dict]:
    """抽「证据链留痕」并按 request_id 配对。

    ⚠️ **一次请求可能有多次调用**（CRAG 恢复会再检索一次、专科可能被重新调用）——
    所以每个 request_id 下要保留**每一次** prompt/recorded 留痕（`*_calls`），
    而不是只留最后一条：本探针第一版按"后写覆盖"聚合，于是把 2 次调用压成 1 条，
    心脏腿因此出现"DB 里记的块不在 prompt 留痕里"的**假 MISMATCH**。
    """
    pairs: dict[str, dict] = {}
    if not log_path.is_file():
        return []
    raw = log_path.read_text(encoding="utf-8", errors="ignore").replace("\x00", "")
    for line in raw.splitlines():
        if "evidence_chain" not in line or "request_id=" not in line:
            continue
        rid = line.split("request_id=")[-1].strip()
        entry = pairs.setdefault(rid, {"request_id": rid})
        for key in ("prompt_chunk_ids", "recorded_ids"):
            if f"{key}=" not in line:
                continue
            frag = line.split(f"{key}=", 1)[1].split(" request_id=", 1)[0].strip()
            # 行尾还有兄弟字段（`prompt_chunks=5` / `recorded_chunks=3`）：必须**截到最后一个 `]`**，
            # 否则 `literal_eval("[...] prompt_chunks=5")` 直接 SyntaxError（本探针踩过：
            # 异常被吞成空 list → 所有条目被过滤 → 报"日志里没有留痕"的假象）。
            end = frag.rfind("]")
            if end < 0:
                entry.setdefault("parse_errors", []).append(f"{key}: 找不到 ']'：{frag[:80]}")
                continue
            try:
                ids = [str(x) for x in ast.literal_eval(frag[: end + 1])]
            except (ValueError, SyntaxError) as exc:
                # 解析失败必须**可见**（不许静默变空 list）
                entry.setdefault("parse_errors", []).append(
                    f"{key}: {type(exc).__name__}: {frag[:80]}"
                )
                continue
            entry.setdefault(f"{key}_calls", []).append(ids)
            entry[key] = ids  # 兼容：保留最后一次（单次调用的场景等价）
    # 汇总：所有调用的**保序并集**（这才是"模型当时看到/落库的全集"）
    for entry in pairs.values():
        for key in ("prompt_chunk_ids", "recorded_ids"):
            union: list[str] = []
            for ids in entry.get(f"{key}_calls", []) or []:
                for cid in ids:
                    if cid not in union:
                        union.append(cid)
            entry[f"{key}_union"] = union
            entry[f"{key}_calls_n"] = len(entry.get(f"{key}_calls", []) or [])
    # 带解析错误的条目**也要留下**（否则"坏行"会被静默丢掉，探针又变成"日志里没有留痕"）
    return [
        e
        for e in pairs.values()
        if e.get("prompt_chunk_ids_union") or e.get("parse_errors")
    ]


async def _row_for(request_id: str) -> dict:
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import DiagnosisResult

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(DiagnosisResult).where(DiagnosisResult.request_id == request_id)
            )
        ).scalars().first()
    if row is None:
        return {}
    findings = getattr(row, "findings", None) or {}
    fusion = findings.get("fusion_result") if isinstance(findings, dict) else None
    return {
        "request_id": str(row.request_id),
        "organ": str(row.organ),
        "clinical_text": str(getattr(row, "clinical_text", "") or ""),
        "knowledge_ids": [str(c) for c in (getattr(row, "knowledge_ids", None) or [])],
        "status": str(getattr(row, "status", "") or ""),
        "conclusion": (
            str(fusion.get("content"))
            if isinstance(fusion, dict) and fusion.get("content")
            else ""
        ),
        "crag_triggered": bool(fusion.get("crag_triggered")) if isinstance(fusion, dict) else None,
        "created_at": (
            row.created_at.isoformat() if getattr(row, "created_at", None) else None
        ),
    }


async def _chunk_texts(ids: list[str]) -> dict[str, str]:
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import KnowledgeChunkModel

    if not ids:
        return {}
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(KnowledgeChunkModel.chunk_id, KnowledgeChunkModel.content).where(
                    KnowledgeChunkModel.chunk_id.in_(ids)
                )
            )
        ).all()
    return {str(cid): (content or "")[:600] for cid, content in rows}


async def _judge_compare(
    row: dict, prompt_ids: list[str], recorded_ids: list[str], args
) -> dict | None:
    """同一结论，用「落库的几条」与「模型当时看到的全部」各判一次 RAGAS faithfulness。

    这是给 00 号拍板 L2 口径用的**偏差数字**：如果补全上下文后分数明显上升，
    说明当前的 L2 红是「我们记少了」造成的伪影，而不是结论不忠实。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "langfuse"))
    try:
        from judge_eval_set import _faithfulness_of

        texts = await _chunk_texts(prompt_ids)
        base = {"clinical_text": row["clinical_text"], "conclusion": row["conclusion"]}
        rec = _faithfulness_of(
            {**base, "context": [texts[c] for c in recorded_ids if c in texts]},
            args.judge_url,
            args.model,
            "raw",
        )
        full = _faithfulness_of(
            {**base, "context": [texts[c] for c in prompt_ids if c in texts]},
            args.judge_url,
            args.model,
            "raw",
        )
        return {
            "faithfulness_recorded": rec.get("score"),
            "faithfulness_full": full.get("score"),
            "recorded_statements": rec.get("statements"),
            "full_statements": full.get("statements"),
            "judge_url": args.judge_url,
            "model": args.model,
        }
    except Exception as exc:
        print(f"  （L2 对照不可用：{type(exc).__name__}: {exc}）")
        return None


async def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log", default=str(Path.home() / "uai/logs/api.log"))
    ap.add_argument("--request-id", default=None, help="指定要核对的判读；缺省取留痕里最新一条")
    ap.add_argument("--judge-url", default="http://127.0.0.1:8006")
    ap.add_argument("--model", default="Qwen3-8B")
    ap.add_argument("--judge-check", action="store_true", help="量出「少记上下文」对 L2 的偏差")
    ap.add_argument("--all", action="store_true", help="把所有留痕条目都过一遍并给判定分布")
    ap.add_argument("--out", default=str(Path.home() / "uai/reports/evidence_chain.json"))
    args = ap.parse_args()

    counter = _counter_evidence()
    checks: list[dict] = [
        {"name": f"反证腿：{name}", "ok": ok, "detail": detail} for name, ok, detail in counter
    ]
    for name, ok, detail in counter:
        print(f"[{'PASS' if ok else 'FAIL'}] 反证腿：{name} — {detail}")
    if not all(ok for _name, ok, _detail in counter):
        print("判定器自身不可信（反证腿失败）→ 停止，不产出结论")
        return 2

    log_path = Path(args.log).expanduser()
    entries = parse_evidence_log(log_path)
    if not entries:
        print(f"日志里没有证据链留痕：{log_path}")
        print("  → 需先在带 `UAI_EVIDENCE_CHAIN_DEBUG=1` 的服务上跑一次判读（见脚本头注释）")
        return 2
    if args.request_id:
        entries = [e for e in entries if e["request_id"] == args.request_id] or entries
    entry = entries[-1]
    if entry.get("parse_errors"):
        # 留痕行解析失败 = 探针读不到事实，必须显式判红（不许绕过）
        print(f"留痕行解析失败：{entry['parse_errors']}")
        checks.append(
            {"name": "留痕行可解析", "ok": False, "detail": str(entry["parse_errors"])[:120]}
        )
        return 1
    rid = entry["request_id"]
    # 用**保序并集**（一次请求可能有多次调用：CRAG 会再检索、专科可能被重调）
    prompt_ids = list(entry.get("prompt_chunk_ids_union") or [])
    logged_recorded = list(entry.get("recorded_ids_union") or [])
    print(f"\n样本：request_id={rid}")
    print(f"  调用次数：prompt 留痕 {entry.get('prompt_chunk_ids_calls_n')} 次 / "
          f"落库留痕 {entry.get('recorded_ids_calls_n')} 次")
    for i, ids in enumerate(entry.get("prompt_chunk_ids_calls") or [], 1):
        print(f"    prompt#{i}（{len(ids)}）={ids}")
    for i, ids in enumerate(entry.get("recorded_ids_calls") or [], 1):
        print(f"    recorded#{i}（{len(ids)}）={ids}")
    print(f"  prompt 里的块（并集 {len(prompt_ids)}）：{prompt_ids}")
    print(f"  日志里落库的块（并集 {len(logged_recorded)}）：{logged_recorded}")

    row = await _row_for(rid)
    if not row:
        print(f"库里查不到该判读（request_id={rid}）→ 无法核对")
        return 2
    db_ids = row["knowledge_ids"]
    print(f"  数据库 knowledge_ids（{len(db_ids)}）：{db_ids}")

    # `--all`：把所有留痕条目都过一遍（一条只能说明一件事，一批才能说明规律）
    distribution: dict[str, int] = {}
    per_entry: list[dict] = []
    if args.all:
        for e in entries:
            if e.get("parse_errors"):
                continue
            r2 = await _row_for(e["request_id"])
            pid = list(e.get("prompt_chunk_ids_union") or [])
            rec = list(e.get("recorded_ids_union") or [])
            db = list(r2.get("knowledge_ids") or [])
            v = classify(db or rec, pid)
            distribution[v] = distribution.get(v, 0) + 1
            per_entry.append(
                {
                    "request_id": e["request_id"],
                    "organ": r2.get("organ"),
                    "calls": e.get("prompt_chunk_ids_calls_n"),
                    "prompt_chunks": len(pid),
                    "recorded_chunks": len(rec),
                    "db_chunks": len(db),
                    "verdict": v,
                    "db_minus_prompt": [c for c in db if c not in pid],
                }
            )
        print(f"\n全部留痕条目（{len(per_entry)} 条）判定分布：{distribution}")
        for item in per_entry:
            extra = (
                f" db中不在prompt的块={item['db_minus_prompt']}"
                if item["db_minus_prompt"]
                else ""
            )
            print(f"  {item['organ']} {item['request_id']} 调用{item['calls']}次 "
                  f"prompt={item['prompt_chunks']} recorded={item['recorded_chunks']} "
                  f"db={item['db_chunks']} → {item['verdict']}{extra}")

    verdict = classify(db_ids, prompt_ids)
    verdict_log = classify(logged_recorded, prompt_ids)
    missing = [c for c in prompt_ids if c not in db_ids]
    print(f"\n[判定] {verdict}（按日志留痕判定 = {verdict_log}）")
    print(f"  模型当时看到 {len(prompt_ids)} 块，落库记 {len(db_ids)} 块；未记录的 {len(missing)} 块：{missing}")

    checks.append(
        {
            "name": "证据链判定（落库 = prompt 的前 N 条）",
            "ok": verdict == "PREFIX_TRUNCATED",
            "detail": f"verdict={verdict}",
        }
    )
    # 数据库那一列记的是**融合层最终响应**的证据；CRAG 合并/多专科时它可能是"最后一次调用"或"并集"，
    # 因此两种都接受（但要如实写清是哪种），不一致才判红。
    last_recorded = list(entry.get("recorded_ids") or [])
    consistent_with = (
        "union" if db_ids == logged_recorded
        else "last_call" if db_ids == last_recorded
        else ""
    )
    checks.append(
        {
            "name": "日志留痕与数据库一致",
            "ok": bool(consistent_with),
            "detail": f"与 {consistent_with or '两者都不'} 一致：db={db_ids} last={last_recorded}",
        }
    )

    judge_result: dict | None = None
    if args.judge_check:
        judge_result = await _judge_compare(row, prompt_ids, db_ids, args)
        if judge_result:
            print(f"  L2 对照（记录版 vs 补全版）：{judge_result['faithfulness_recorded']} vs "
                  f"{judge_result['faithfulness_full']}")

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "log": str(log_path),
        "sample": row,
        "prompt_chunk_ids": prompt_ids,
        "logged_recorded_ids": logged_recorded,
        "verdict": verdict,
        "verdict_from_log": verdict_log,
        "not_recorded_chunks": missing,
        "verdict_distribution": distribution if args.all else None,
        "per_entry": per_entry if args.all else None,
        "judge_check": judge_result,
        "counter_evidence": [{"name": n, "ok": ok, "detail": d} for n, ok, d in counter],
        "checks": checks,
    }
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    failed = [c for c in checks if not c["ok"]]
    print(f"\n== p30 证据链：{'PASS' if not failed else 'FAIL'}"
          f"（{len(checks) - len(failed)}/{len(checks)}）报告：{out}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
