"""4.4 分级一致率：**同一输入跑 N 次，看分级是否稳定**。

## 怎么做到"与生产同一条生成路径"（这是本脚本的关键设计）

不自己拼 prompt、不自己调 LLM 端点，而是**直接实例化生产专科智能体**
`ThyroidSpecialistAgent` / `LiverSpecialistAgent` / `CardiacSpecialistAgent`，
只把它的 `knowledge_agent` 换成一个**返回固定知识块的替身**：

    真实生产：AgentContext → specialist.invoke() → RAG 检索 → _build_prompt
              → VLLMEngine(chat/completions + enable_thinking=False + 剥 <think>)
              → _parse_response（含分级失败后的结构化重试）→ AgentResponse.content
    本脚本：  AgentContext → specialist.invoke() → 【替身返回记录时的块】 → 完全同上

这样写的好处：
* prompt 拼装、思考剥除、分级解析、结构化重试**全部复用生产代码**（零重写）；
* 唯一被我控制的变量是"检索结果固定"（这是"同一输入"的定义），
  每一次采样之间**唯一变化的只有随机性**。

## 为什么不去打 /api/v1/diagnosis/request

① 限流 10/min；② 每次都会新建 DB 记录（评测不该往生产写数据）；
③ 那条路径还含融合层/安全网关，一致率就不再是"生成侧稳定性"这一件事。

## 输入与分档（跑之前写死）

* `--inputs production`（默认）：生产库 distinct (clinical_text, organ)，且其
  `knowledge_ids` 在库里能解析出至少 1 块 → 用**记录当时的块快照**；
* `--inputs synthetic_high_risk`：**自造**高风险甲状腺输入（由 TI-RADS 高风险征象
  组合而成，标注 `synthetic=true`），用于补高风险档样本量——生产库里高风险输入只有个位数。

分档：甲状腺 ≥2 个高风险征象词 → `high_risk`，否则 `regular`（按**输入**判，不看输出）。

## 一致率定义

同一输入跑 N 次 → N 个分级；**一致 = N 个全部相同且都能解析出分级**。
`consistency = 一致输入数 / 总输入数`。另报 `unparsed_samples`（解析失败的采样次数），
以及"仅在两次都解析成功时"的条件一致率（作对照，不作主口径）。

用法（服务器，需 8002 + 8004/8005 可达；不需要 18000）：
    ~/uai/venv/bin/python scripts/audit/run_with_env.py scripts/audit/p107_grading_consistency.py \
        --inputs production --limit 70 --samples 2 \
        --report ~/uai/reports/p107_grading_consistency_prod.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys
import time
from collections import Counter

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_SCRIPTS), str(_SCRIPTS / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

#: 甲状腺高风险征象词（输入侧）
THY_HIGH = ("微钙化", "点状强回声", "纵横比", "高>宽", "边缘不规则", "边界不清",
            "边界欠清", "形态不规则", "极低回声", "甲状腺外侵犯", "不规则")
#: 肝脏高风险征象词
LIV_HIGH = ("APHE", "廓清", "washout", "包膜", "阈值增长", "动脉期强化")

#: 自造高风险甲状腺输入（用 TI-RADS 明确的高风险征象组合；标注 synthetic）
SYNTH_HIGH_RISK = [
    "甲状腺右叶实性低回声结节，纵横比>1，边缘不规则，内见微钙化，大小约 11x9mm。",
    "甲状腺左叶极低回声结节，边界不清，形态不规则，伴点状强回声，约 13x10mm。",
    "甲状腺右叶低回声结节，边缘分叶状，纵横比>1，粗大钙化，大小约 18x12mm。",
    "甲状腺左叶实性结节，低回声，边界不清，内见微钙化，伴颈部淋巴结肿大。",
    "甲状腺右叶极低回声结节，形态不规则，边缘模糊，纵横比>1，约 9x12mm。",
    "甲状腺左叶实性低回声结节，边缘不规则，微钙化，甲状腺外侵犯可能。",
    "甲状腺右叶低回声结节，纵横比>1，边缘不规则，内部点状强回声，约 10x14mm。",
    "甲状腺左叶低回声实性结节，边界不清，形态不规则，粗大钙化。",
    "甲状腺右叶结节，极低回声，边缘不规则，纵横比>1，伴微钙化。",
    "甲状腺左叶低回声结节，边界欠清，形态不规则，内见点状强回声。",
    "甲状腺右叶实性结节，低回声，边缘模糊，纵横比>1，微钙化，约 12x15mm。",
    "甲状腺左叶实性低回声结节，边缘不规则，内见微钙化，血流丰富。",
]


def tier_of_input(ctext: str, organ: str) -> str:
    """按**输入**判档（纯函数）。"""
    t = ctext or ""
    if organ == "cardiac" or "射血分数" in t or "LVEF" in t.upper():
        return "high_risk" if re.search(r"(EF|射血分数)\s*[:：=]?\s*\d+\s*%", t) else "regular"
    if organ == "liver":
        return "high_risk" if sum(1 for x in LIV_HIGH if x in t) >= 1 else "regular"
    return "high_risk" if sum(1 for x in THY_HIGH if x in t) >= 2 else "regular"


async def gather_production(limit: int) -> list[dict]:
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import DiagnosisResult, KnowledgeChunkModel

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    DiagnosisResult.request_id,
                    DiagnosisResult.organ,
                    DiagnosisResult.clinical_text,
                    DiagnosisResult.knowledge_ids,
                    DiagnosisResult.created_at,
                ).order_by(DiagnosisResult.created_at.desc())
            )
        ).all()
        picked: dict[tuple[str, str], dict] = {}
        want: set[str] = set()
        for rid, organ, ctext, kids, created in rows:
            ct = (ctext or "").strip()
            if not ct or not kids:
                continue
            key = (ct, str(organ))
            if key in picked:
                picked[key]["repeat"] += 1
                continue
            picked[key] = {
                "request_id": str(rid),
                "organ": str(organ),
                "clinical_text": ct,
                "knowledge_ids": [str(k) for k in kids],
                "repeat": 1,
                "created_at": created.isoformat() if created else None,
                "synthetic": False,
            }
            want.update(str(k) for k in kids)
        have = (
            await session.execute(
                select(
                    KnowledgeChunkModel.chunk_id,
                    KnowledgeChunkModel.kb_version,
                    KnowledgeChunkModel.content,
                    KnowledgeChunkModel.source_doc_id,
                    KnowledgeChunkModel.section_path,
                ).where(KnowledgeChunkModel.chunk_id.in_(list(want)))
            )
        ).all()
    meta = {
        str(cid): {
            "chunk_id": str(cid),
            "kb_version": str(ver),
            "content": str(txt or ""),
            "source_doc_id": str(src or ""),
            "section_path": str(sec or ""),
        }
        for cid, ver, txt, src, sec in have
    }
    out: list[dict] = []
    for v in picked.values():
        chunks = [meta[k] for k in v["knowledge_ids"] if k in meta]
        if not chunks:
            continue
        v["chunks"] = chunks
        v["tier"] = tier_of_input(v["clinical_text"], v["organ"])
        out.append(v)
    out.sort(key=lambda x: (-x["repeat"], x["tier"], x["request_id"]))
    return out[:limit]


def synthetic_high_risk() -> list[dict]:
    out = []
    for i, text in enumerate(SYNTH_HIGH_RISK):
        out.append(
            {
                "request_id": f"synthetic-hr-{i:02d}",
                "organ": "thyroid",
                "clinical_text": text,
                "knowledge_ids": [],
                "repeat": 1,
                "created_at": None,
                "tier": "high_risk",
                "synthetic": True,
            }
        )
    return out


class FixedKnowledge:
    """返回固定知识块的 KnowledgeAgent 替身（**故意不实现检索**）。

    这是"同一输入"的定义：检索结果在这一轮里是常量，采样之间的差异只来自生成。
    """

    def __init__(self, chunks: list[dict], organ: str) -> None:
        from app.knowledge.protocols import KnowledgeChunk

        self._chunks = [
            KnowledgeChunk(
                chunk_id=c["chunk_id"],
                kb_version=c.get("kb_version") or "kb_v1.4.0",
                organ=organ,
                content=c["content"],
                source_doc_id=c.get("source_doc_id") or c["chunk_id"],
                section_path=c.get("section_path") or "",
            )
            for c in chunks
        ]
        self.queries = 0

    async def query(self, query, organ, top_k=10, profile=None, **kwargs):  # noqa: ANN001, ANN201
        self.queries += 1
        return list(self._chunks)

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inputs", choices=("production", "synthetic_high_risk", "both"),
                    default="production")
    ap.add_argument("--limit", type=int, default=70)
    ap.add_argument("--samples", type=int, default=2)
    ap.add_argument("--timeout", type=float, default=0.0,
                    help="单次推理超时秒数；0 = 用 settings（生产 30s）。评测需要时显式放宽并在报告里标注")
    ap.add_argument("--max-retries", type=int, default=-1,
                    help="引擎重试次数；-1 = 用 settings（3）。评测固定 1，避免超时被重试放大")
    ap.add_argument("--concurrency", type=int, default=4,
                    help="并发输入数（每条内部仍串行跑 N 次采样，保证同一条内不乱序）")
    ap.add_argument("--temperature", type=float, default=None,
                    help="留给调试；默认 None = 引擎默认档（生产就是这一档）")
    ap.add_argument("--report",
                    default=str(pathlib.Path.home() / "uai/reports/p107_grading_consistency.json"))
    args = ap.parse_args()

    cases: list[dict] = []
    if args.inputs in ("production", "both"):
        prod = await gather_production(args.limit)
        print(f"[输入] 生产 distinct 输入 {len(prod)} 条（档位 {dict(Counter(c['tier'] for c in prod))}）")
        cases.extend(prod)
    if args.inputs in ("synthetic_high_risk", "both"):
        syn = synthetic_high_risk()
        print(f"[输入] 自造高风险 {len(syn)} 条（synthetic=true，仅补高风险档；不作生产口径）")
        cases.extend(syn)
    if len(cases) < 10:
        print(f"[FATAL] 可用输入仅 {len(cases)} 条（<10）")
        return 2
    await fill_default_chunks(cases)
    if any(not c.get("chunks") for c in cases):
        print("[WARN] 有输入填不出知识块 → 这些样本会被 specialist 判 evidence_insufficient")

    from app.agents.protocols import AgentContext
    from app.agents.specialist.cardiac import CardiacSpecialistAgent
    from app.agents.specialist.liver import LiverSpecialistAgent
    from app.agents.specialist.thyroid import ThyroidSpecialistAgent
    from app.inference.factory import create_inference_engine_from_settings
    from app.inference.grading_definitions import extract_grading_severest
    from app.models.schemas import Organ

    engine = create_inference_engine_from_settings(
        **({"timeout_seconds": args.timeout} if args.timeout else {}),
        **({"max_retries": args.max_retries} if args.max_retries >= 0 else {}),
    )
    from app.config.settings import settings as _settings

    effective_timeout = args.timeout or _settings.inference_timeout_seconds
    effective_retries = args.max_retries if args.max_retries >= 0 else _settings.inference_max_retries
    print(f"[engine] timeout={effective_timeout}s retries={effective_retries} "
          f"（生产 .env 的 INFERENCE_TIMEOUT_SECONDS={_settings.inference_timeout_seconds}）")
    cls = {"thyroid": ThyroidSpecialistAgent, "liver": LiverSpecialistAgent,
           "cardiac": CardiacSpecialistAgent}
    organ_enum = {"thyroid": Organ.THYROID, "liver": Organ.LIVER, "cardiac": Organ.CARDIAC}

    rows: list[dict] = []
    t0 = time.time()
    done = 0
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(max(1, args.concurrency))

    async def run_case(i: int, case: dict) -> dict:
        nonlocal done
        async with sem:
            chunks = case.get("chunks") or []
            specialist = cls[case["organ"]](
                inference_engine=engine,
                knowledge_agent=FixedKnowledge(chunks, case["organ"]),
                rag_top_k=5,
                max_tokens=1024,
            )
            grades: list[str] = []
            contents: list[str] = []
            confs: list[float] = []
            for s in range(args.samples):
                ctx = AgentContext(
                    request_id=f"p107-{case['request_id']}-{s}",
                    session_id="p107-consistency",
                    organ=organ_enum[case["organ"]],
                    user_query=case["clinical_text"],
                )
                try:
                    resp = await specialist.invoke(ctx)
                    text = resp.content or ""
                    confs.append(float(resp.confidence or 0.0))
                except Exception as exc:  # noqa: BLE001
                    text = ""
                    confs.append(0.0)
                    print(f"  [{i}/{s}] invoke 异常 {type(exc).__name__}: {exc}")
                contents.append(text[:800])
                g = extract_grading_severest(text, case["organ"])
                grades.append(str(g) if g else "")
            agree = len(set(grades)) == 1 and all(grades)
            row = {
                "request_id": case["request_id"],
                "organ": case["organ"],
                "tier": case["tier"],
                "synthetic": bool(case.get("synthetic")),
                "clinical_text": case["clinical_text"],
                "repeat_in_prod": case.get("repeat", 1),
                "n_chunks": len(chunks),
                "grades": grades,
                "confidences": confs,
                "agree": agree,
                "mode": Counter(grades).most_common(1)[0][0] if grades else "",
                "unparsed": sum(1 for g in grades if not g),
                "contents": contents,
            }
            async with lock:
                done += 1
                if done % 5 == 0:
                    print(f"  {done}/{len(cases)}  elapsed={time.time() - t0:.0f}s", flush=True)
            return row

    rows = await asyncio.gather(*(run_case(i, c) for i, c in enumerate(cases)))
    rows = list(rows)

    def summarize(sub: list[dict]) -> dict:
        n = len(sub)
        if not n:
            return {"n": 0, "consistent": 0, "consistency": None}
        agree = sum(1 for r in sub if r["agree"])
        both_ok = [r for r in sub if r["unparsed"] == 0]
        cond = sum(1 for r in both_ok if r["agree"])
        return {
            "n": n,
            "consistent": agree,
            "consistency": round(agree / n, 4),
            "both_parsed_n": len(both_ok),
            "consistency_both_parsed": round(cond / len(both_ok), 4) if both_ok else None,
            "unparsed_samples": sum(r["unparsed"] for r in sub),
            "samples_total": sum(len(r["grades"]) for r in sub),
            "grade_dist": dict(Counter(r["mode"] for r in sub)),
        }

    report = {
        "caliber": {
            "definition": "同一输入跑 N 次，N 个分级全同且均可解析 = 一致；consistency = 一致输入数 / 总输入数",
            "samples_per_case": args.samples,
            "generation_path": "生产专科智能体 invoke()（prompt/思考剥除/解析/结构化重试全部复用生产代码）；唯一固定的是检索结果",
            "model": "settings.inference_engine → 8002 vLLM（见 report.model_info）",
            "timeout_seconds": None,
            "max_retries": None,
            "input": "生产库 distinct (clinical_text, organ) + 该请求当时的 knowledge_ids 快照",
            "tier_rule": "按输入侧征象词分档：甲状腺 ≥2 个高风险征象 → high_risk；心脏有显式 EF% → high_risk",
            "offline_note": "离线：只测生成侧稳定性；不含融合层/安全网关/DB 写入",
        },
        "overall": summarize(rows),
        "overall_production_only": summarize([r for r in rows if not r["synthetic"]]),
        "by_tier": {
            "regular": summarize([r for r in rows if r["tier"] == "regular"]),
            "high_risk": summarize([r for r in rows if r["tier"] == "high_risk"]),
        },
        "by_tier_production_only": {
            "regular": summarize([r for r in rows if r["tier"] == "regular" and not r["synthetic"]]),
            "high_risk": summarize([r for r in rows if r["tier"] == "high_risk" and not r["synthetic"]]),
        },
        "by_organ": {
            o: summarize([r for r in rows if r["organ"] == o]) for o in sorted({r["organ"] for r in rows})
        },
        "rows": rows,
    }
    report["caliber"]["timeout_seconds"] = effective_timeout
    report["caliber"]["max_retries"] = effective_retries
    try:
        info = await engine.get_model_info()
        report["model_info"] = {
            "name": getattr(info, "name", None), "version": getattr(info, "version", None),
            "quantization": getattr(info, "quantization", None),
        }
    except Exception as exc:  # noqa: BLE001
        report["model_info"] = {"error": f"{type(exc).__name__}: {exc}"}

    path = pathlib.Path(args.report).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n== 汇总 ==")
    print(json.dumps({k: report[k] for k in
                      ("overall", "overall_production_only", "by_tier", "by_tier_production_only",
                       "by_organ", "model_info")}, ensure_ascii=False, indent=2))
    print(f"\n报告：{path}")
    await engine.close()
    return 0


async def fill_default_chunks(cases: list[dict]) -> None:
    """自造输入没有"记录时的块" → 用该器官 KB 的固定 3 块（在报告里标注）。"""
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import KnowledgeChunkModel

    need = {c["organ"] for c in cases if not c.get("chunks")}
    if not need:
        return
    pool: dict[str, list[dict]] = {}
    async with AsyncSessionLocal() as s:
        for organ in need:
            rows = (
                await s.execute(
                    select(
                        KnowledgeChunkModel.chunk_id,
                        KnowledgeChunkModel.kb_version,
                        KnowledgeChunkModel.content,
                        KnowledgeChunkModel.source_doc_id,
                        KnowledgeChunkModel.section_path,
                    )
                    .where(KnowledgeChunkModel.kb_version == "kb_v1.4.0")
                    .where(KnowledgeChunkModel.organ == organ)
                    .limit(3)
                )
            ).all()
            pool[organ] = [
                {"chunk_id": str(a), "kb_version": str(b), "content": str(c),
                 "source_doc_id": str(d or ""), "section_path": str(e or "")}
                for a, b, c, d, e in rows
            ]
    for c in cases:
        if not c.get("chunks"):
            c["chunks"] = pool.get(c["organ"], [])


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
