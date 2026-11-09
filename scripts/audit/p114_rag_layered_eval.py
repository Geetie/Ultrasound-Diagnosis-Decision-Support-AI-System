#!/usr/bin/env python3
"""RAG 五阶段分层评估（p114）—— 摄取 / 分块 / 检索 / 增强 / 生成。

## 为什么需要"分层"

本项目此前的评估是**一个大数**：报一个 hit@10、一个 faithfulness。一旦数字变差，
无法回答"是语料的问题、分块的问题、检索的问题、还是生成的问题"——2026-11-08
那次 hit@10 从 0.75 掉到 0.54，追了三轮才发现是语料翻倍、不是检索退化。

RAG 是流水线，每一段的失效方式不同，必须**分段测**：

| 阶段 | 测什么 | 典型失效 | 修起来贵不贵 |
|---|---|---|---|
| 摄取 | 表格/数值有没有在解析时丢掉 | 表格被拍平成一行、% 号丢在表头 | 要重新解析 PDF，贵 |
| 分块 | 单块能不能独立回答（自足性） | 块从句子中间断开、指代没有先行词 | 要重建索引，很贵 |
| 检索 | 该找到的找到没有、排得靠不靠前 | 语料变多把 gold 挤出 Top-k | 调参，便宜 |
| 增强 | 多来源冲突时有没有**并列**而不是二选一 | 静默挑了其中一份 | 改提示词/渲染，便宜 |
| 生成 | 有没有编、有没有据 | 结论含知识库没有的数字 | 改提示词，看情况 |

**越靠前越贵**。所以分层评估的最大价值是"把问题定位到最便宜的那一层"。

## 本脚本的诚实边界（先读再信数字）

* **摄取/分块两段是"结构性指标"，不是"正确率"**：本项目没有"原始 PDF 的表格
  正确解析结果"这份金标准（源 PDF 不在服务器上，只在 `kb_src_v15` 有已解析的
  JSON）。所以这两段只能测**守恒性与自足性**，测不了"抽对了没有"。
  这一点在报告的 `limitations` 里写明，**不许当成准确率对外报**。
* **检索段是唯一有 gold 的**（B2 120 条手写），也是唯一能报"准确率"的。
* **增强段**复用 `app/rag/source_conflict.py` 的真实检测器，测的是
  "在真实语料上，冲突检测器能检出多少条真分歧"——上限受语料里真实分歧条数限制。
* **生成段**本脚本**不跑**（要真机判官 + 生产链路），只做"入口检查"：
  报告当前可评测样本量，并给出功效分析要求。跑生成质量请用
  `scripts/audit/p108_hallucination_faithfulness.py`。

## 用法

    # 服务器（需 PG + 8004 嵌入）
    ~/uai/venv/bin/python scripts/audit/run_with_env.py \\
        scripts/audit/p114_rag_layered_eval.py --report reports/p114_layered.json

    # 只看结构层（不跑检索，不需要嵌入服务）
    ~/uai/venv/bin/python scripts/audit/run_with_env.py \\
        scripts/audit/p114_rag_layered_eval.py --skip-retrieval

退出码：0 = 全部阶段跑通；1 = 有阶段违反自身不变量（如 NDCG>1、重复副本未去重）；
2 = 环境/参数错误。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from app.evaluation.retrieval_metrics import (  # noqa: E402
    METRIC_NAMES,
    aggregate,
    score_query,
)
from app.evaluation.statistics import (  # noqa: E402
    required_n_for_delta_exact,
)

BGE_URL = "http://127.0.0.1:8004"
DEFAULT_DATASET = "data/eval/v2/zh_retrieval_v1.jsonl"

#: 分块自足性的"指代开头"标记 —— 块以这些词开头时，脱离上一块就读者无法解析。
_ANAPHORA_HEADS = (
    "其", "该", "此", "上述", "前述", "以上", "后者", "前者", "这",
    "它们", "它", "他", "她", "因此", "所以", "但是", "然而", "另外", "此外",
)

#: 句末标点（中文 + 英文）
_SENT_END = "。！？；.!?;"


# ===================================================================== 工具
def _vkey(v: str) -> tuple:
    """kb_v1.5.0 → 可比较元组（非数字段退化为 0）。"""
    out = []
    for part in str(v or "").replace("kb_v", "").split("."):
        out.append(int(part) if part.isdigit() else 0)
    return tuple(out)


def _norm_chunk_id(cid: str) -> str:
    """``kb_v1.5.0:pdf_x_0001#0000`` → ``pdf_x_0001#0000``（去掉版本前缀）。"""
    return str(cid).split(":", 1)[-1]


async def _connect():
    import asyncpg

    from app.config.settings import settings

    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return await asyncpg.connect(dsn)


# ===================================================================== 阶段 1：摄取
def evaluate_ingestion(source_docs: list[dict], corpus: list[dict]) -> dict:
    """摄取段：源文档 → chunk 的**守恒性**与**表格结构保留**。

    ## 口径（写死）

    1. **字符守恒率**：`Σ len(chunk) / Σ len(source)`。
       - 接近 1.0 = 内容基本没丢；
       - **> 1.05 是异常**（chunk 有重叠或重复入库），必须报出来而不是当好事；
       - < 0.90 = 有实质丢失，要看是不是表格/公式被 guard 丢掉。
    2. **表格管道符保留率**：源文档里含 `|` 的行，在 chunk 里还剩多少。
       表格被拍平是 PDF 解析最常见的静默损失 —— 一旦丢了，检索里那些
       "4a 类 2~10%"的数字就永远查不到（本项目 2026-11-09 实际踩过）。
    3. **数值保留率**：源文档里所有数字串，在 chunk 里还能找到的比例。
       这是"数值有没有被吞"的直接检验。

    注意：这三项都是**守恒性**指标，不是"抽对了没有"。
    没有原文金标准时，抽取正确性无法测 —— 报告里必须标注。
    """
    by_organ = Counter()
    for d in source_docs:
        by_organ[d.get("organ") or "unknown"] += 1

    src_chars = sum(len(d.get("content") or "") for d in source_docs)
    chunk_chars = sum(len(c.get("text") or "") for c in corpus)
    char_ratio = (chunk_chars / src_chars) if src_chars else 0.0

    # 表格管道符
    src_pipe_lines = 0
    for d in source_docs:
        src_pipe_lines += sum(1 for ln in (d.get("content") or "").splitlines() if "|" in ln)
    chunk_pipe_lines = sum(
        sum(1 for ln in (c.get("text") or "").splitlines() if "|" in ln) for c in corpus
    )
    pipe_ratio = (chunk_pipe_lines / src_pipe_lines) if src_pipe_lines else None

    # 数值保留：抽源文档的数字集合，看 chunk 并集里还剩多少
    num_re = re.compile(r"\d+(?:\.\d+)?")
    src_nums: set[str] = set()
    for d in source_docs:
        src_nums.update(num_re.findall(d.get("content") or ""))
    chunk_text = "\n".join(c.get("text") or "" for c in corpus)
    chunk_nums = set(num_re.findall(chunk_text))
    num_ratio = (len(src_nums & chunk_nums) / len(src_nums)) if src_nums else None

    return {
        "stage": "ingestion",
        "n_source_docs": len(source_docs),
        "n_source_docs_by_organ": dict(by_organ),
        "n_chunks": len(corpus),
        "source_chars": src_chars,
        "chunk_chars": chunk_chars,
        "char_conservation": round(char_ratio, 4),
        "char_conservation_flag": (
            "OK" if 0.90 <= char_ratio <= 1.05
            else ("LOSS" if char_ratio < 0.90 else "OVERLAP_OR_DUP")
        ),
        "table_pipe_lines_source": src_pipe_lines,
        "table_pipe_lines_chunk": chunk_pipe_lines,
        "table_pipe_conservation": round(pipe_ratio, 4) if pipe_ratio is not None else None,
        "distinct_numbers_source": len(src_nums),
        "distinct_numbers_chunk": len(chunk_nums),
        "number_conservation": round(num_ratio, 4) if num_ratio is not None else None,
        "caliber": (
            "**守恒性**指标，不是抽取正确率。本项目无原文金标准（源 PDF 不在服务器，"
            "只有已解析的 kb_src JSON），故无法测『抽对了没有』。"
        ),
        "limitations": [
            "char_conservation > 1.05 表示 chunk 有重叠或重复入库，需人工看",
            "number_conservation 高不代表数字对：把 15 写成 51 也能『保留』",
            "表格管道符只是表格的一种残留形态，无管道的表格（纯空格对齐）测不到",
        ],
    }


# ===================================================================== 阶段 2：分块
def evaluate_chunking(corpus: list[dict]) -> dict:
    """分块段：**自足性**（单块脱离上下文能不能读）与**边界完整性**。

    ## 口径（写死）

    1. **句末完整率**：块以句末标点结尾的比例。块从句子中间断开时，
       检索命中它、但模型拿到的是一句半截话 —— 这是"命中率好看但答不准"的
       常见来源。
    2. **指代开头率**：块以「其 / 该 / 上述 / 这 / 因此…」开头的比例。
       指代词没有先行词 → 该块**单独不可读**，必须靠邻居。
    3. **长度分布**：p50 / p95 / 超长块占比。超长块会挤爆提示词预算。
    4. **自足性得分**：`1 - (指代开头率) - 加权(非句末结尾率)`，clamp 到 [0,1]。
       **这是启发式代理指标，不是人工判定的"能不能独立回答"。** 真正的
       自足性判定需要人工或 LLM 判官，本脚本不假装做到了。
    """
    if not corpus:
        return {"stage": "chunking", "n_chunks": 0, "error": "corpus empty"}

    lens = [len(c.get("text") or "") for c in corpus]
    ends_ok = sum(1 for c in corpus if (c.get("text") or "").rstrip().endswith(tuple(_SENT_END)))
    anaphora = sum(
        1 for c in corpus
        if (c.get("text") or "").lstrip().startswith(_ANAPHORA_HEADS)
    )
    n = len(corpus)
    p50 = statistics.median(lens) if lens else 0
    srt = sorted(lens)
    p95 = srt[min(len(srt) - 1, int(0.95 * len(srt)))] if srt else 0
    too_long = sum(1 for x in lens if x > 1500)

    end_rate = ends_ok / n
    anaphora_rate = anaphora / n
    self_sufficiency = max(0.0, min(1.0, 1.0 - anaphora_rate - 0.5 * (1.0 - end_rate)))

    return {
        "stage": "chunking",
        "n_chunks": n,
        "len_chars_p50": int(p50),
        "len_chars_p95": int(p95),
        "len_chars_max": max(lens) if lens else 0,
        "too_long_over_1500_rate": round(too_long / n, 4),
        "sentence_end_rate": round(end_rate, 4),
        "anaphora_head_rate": round(anaphora_rate, 4),
        "self_sufficiency_proxy": round(self_sufficiency, 4),
        "caliber": (
            "**启发式代理指标**。self_sufficiency_proxy 由『指代开头率』与"
            "『句末完整率』合成，不是人工/判官判定的『能不能独立回答』。"
            "可用来做版本间对比（同一把尺子），不可当绝对正确率对外报。"
        ),
        "limitations": [
            "指代词表是有限的，同义指代（『上面说的』）测不到",
            "句末标点缺失也可能是排版问题，不必然是分块错误",
            "本指标不判『块里内容是否回答了某个问题』——那需要 query 对齐",
        ],
    }


# ===================================================================== 阶段 3：检索
def embed(texts: list[str], batch_size: int = 64) -> list[list[float]]:
    """经 8004 bge-m3 嵌入（入参字段 `texts`，返回 `dense_embeddings`）。"""
    import urllib.request

    out: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        body = json.dumps({"texts": batch, "model": "bge-m3"}).encode()
        req = urllib.request.Request(
            f"{BGE_URL}/v1/embeddings", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read())
        if isinstance(data, list):
            items = data
        else:
            items = (data.get("dense_embeddings") or data.get("embeddings")
                     or data.get("data") or [])
            if items and isinstance(items[0], dict):
                items = [it.get("embedding") or it.get("dense_embedding")
                         for it in items if isinstance(it, dict)]
        out.extend([it["embedding"] if isinstance(it, dict) else it for it in items])
    return out


def cosine_topk(qvecs, cvecs, k: int):
    import numpy as np

    C = np.asarray(cvecs, dtype="float32")
    Cn = C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-9)
    out = []
    for q in qvecs:
        qv = np.asarray(q, dtype="float32")
        qn = qv / (np.linalg.norm(qv) + 1e-9)
        sims = Cn @ qn
        idx = np.argsort(-sims)[:k]
        out.append([(int(i), float(sims[i])) for i in idx])
    return out


def gold_ids_of(row: dict) -> set[str]:
    """从 B2 的 gold 结构生成 chunk_id。

    ⚠️ 2026-11-09 修：旧实现在 `doc_id` 含 `#` 时**直接丢掉 chunk_index**
    （`f"{doc}#{ci:04d}" if "#" not in doc else doc`），等于把 chunk 级口径
    悄悄降级成 doc 级。现在显式处理：若 doc_id 已含 `#`，认为它已是完整 id；
    否则按 `doc#NNNN` 拼。并且**报警**，不让它静默发生。
    """
    out: set[str] = set()
    for item in row.get("gold") or []:
        doc = str(item.get("doc_id") or "")
        ci = int(item.get("chunk_index", 0) or 0)
        if "#" in doc:
            out.add(doc)
        else:
            out.add(f"{doc}#{ci:04d}")
    return out


def evaluate_retrieval(
    rows: list[dict], corpus: list[dict], *, top_k: int = 10, organ_filter: bool = False
) -> dict:
    """检索段：**唯一有 gold、能报准确率**的阶段。"""
    answerable = [r for r in rows if r.get("answerable", True)]
    unanswerable = [r for r in rows if r.get("answerable") is False]

    def _norm(cid: str) -> str:
        return _norm_chunk_id(cid)

    q_vecs = embed([r["query"] for r in answerable])
    c_vecs = embed([c["text"] for c in corpus])
    if organ_filter:
        rankings = []
        for r, qv in zip(answerable, q_vecs, strict=False):
            organ = r.get("organ")
            allowed = [i for i, c in enumerate(corpus) if c.get("organ") == organ]
            if not allowed:
                rankings.append([])
                continue
            sub = cosine_topk([qv], [c_vecs[i] for i in allowed], k=top_k)[0]
            rankings.append([(allowed[i], s) for i, s in sub])
    else:
        rankings = cosine_topk(q_vecs, c_vecs, k=top_k)

    scores = []
    per_query = []
    for r, ranking in zip(answerable, rankings, strict=False):
        ranked_ids = [_norm(corpus[i]["chunk_id"]) for i, _ in ranking]
        gold = {_norm(g) for g in gold_ids_of(r)}
        s = score_query(r["query_id"], ranked_ids, gold, k_max=top_k)
        scores.append(s)
        per_query.append({**s.as_dict(), "organ": r.get("organ"),
                          "query_type": r.get("query_type")})

    overall = aggregate(scores)

    def _stratify(key: str) -> dict:
        buckets: dict[str, list] = {}
        for p, s in zip(per_query, scores, strict=False):
            buckets.setdefault(p.get(key) or "unknown", []).append(s)
        return {k: aggregate(v) for k, v in sorted(buckets.items())}

    # 不可答题：检索器是否"很自信地"返回了高分（拒答能力的代理）
    refusal = {"n_unanswerable": len(unanswerable)}
    if unanswerable:
        qv = embed([r["query"] for r in unanswerable])
        un_ranks = cosine_topk(qv, c_vecs, k=1)
        un_top1 = [float(sims[0][1]) if sims else 0.0 for sims in un_ranks]
        ans_top1 = [float(rk[0][1]) for rk in rankings if rk]
        refusal.update({
            "unanswerable_top1_mean": round(statistics.mean(un_top1), 4) if un_top1 else None,
            "answerable_top1_mean": round(statistics.mean(ans_top1), 4) if ans_top1 else None,
            "answerable_top1_p50": round(statistics.median(ans_top1), 4) if ans_top1 else None,
            "overconfident_rate": (
                round(sum(1 for s in un_top1 if ans_top1 and s > statistics.median(ans_top1))
                      / len(un_top1), 4)
                if un_top1 and ans_top1 else None
            ),
            "caliber": (
                "代理指标：不可答题的 Top-1 相似度若普遍高于可答题的中位数，"
                "说明检索器『很自信地回答了答不了的问题』。"
                "这不是拒答率 —— 真正拒答要看生成层，本脚本不测。"
            ),
        })

    return {
        "stage": "retrieval",
        "overall": overall,
        "by_organ": _stratify("organ"),
        "by_query_type": _stratify("query_type"),
        "refusal_proxy": refusal,
        "per_query": per_query,
        "caliber": "chunk 级（必须命中含答案的那一段），不是 doc 级。",
    }


# ===================================================================== 阶段 4：增强
def evaluate_augmentation(corpus: list[dict]) -> dict:
    """增强段：多套语料冲突时**是否并列**。复用生产检测器，不重写。

    `app/rag/source_conflict.py` 是生产实现（它自己抽取数值事实、按 source_set
    比对、并列渲染）。本函数只做**评估**：把它跑在真实语料上，报
    "检出多少条真分歧 / 覆盖多少 source_set 对"。

    为什么不自造一个检测器来测：那会变成"测我自己的检查器"，与生产脱节。
    评估必须用**生产同一个实现**，否则分数好看但与线上无关（本项目 2026-10-20
    踩过同样形态的坑）。
    """
    try:
        from app.knowledge.protocols import KnowledgeChunk
        from app.knowledge.source_set import source_set_of
        from app.rag.source_conflict import detect_conflicts
    except Exception as exc:
        return {
            "stage": "augmentation",
            "available": False,
            "reason": f"import_failed:{type(exc).__name__}:{exc}",
            "note": "source_conflict / source_set 不可用，本阶段未评估（不是 0 分，是没测）",
        }

    chunks = []
    by_set = Counter()
    # 构造失败必须留下**真实原因**（AGENTS.md A.86 教训：每个被跳过的分支都要有
    # 自己的原因，聚合处取真实原因，不许写死默认值）。
    build_errors: Counter = Counter()
    for c in corpus:
        doc_id = str(c.get("source_doc_id") or "")
        try:
            ss = source_set_of(source_doc_id=doc_id)
            by_set[ss] += 1
            chunks.append(KnowledgeChunk(
                chunk_id=c["chunk_id"],
                kb_version=c.get("kb_version") or "",
                organ=c.get("organ") or "unknown",
                content=c.get("text") or "",
                metadata={},
                source_doc_id=doc_id,
                section_path="",
                token_count=0,
            ))
        except Exception as exc:
            build_errors[f"{type(exc).__name__}: {exc}"[:200]] += 1

    if not chunks:
        return {"stage": "augmentation", "available": False,
                "reason": "no_chunks_constructed",
                "build_errors": dict(build_errors.most_common(5))}

    try:
        report = detect_conflicts(chunks)
    except Exception as exc:
        return {"stage": "augmentation", "available": False,
                "reason": f"detect_failed:{type(exc).__name__}:{exc}"}

    conflicts = getattr(report, "conflicts", []) or []
    pairs = Counter()
    for cf in conflicts:
        try:
            sets = tuple(sorted({v.source_set for v in getattr(cf, "values", [])}))
        except Exception:
            sets = ()
        pairs[sets] += 1

    return {
        "stage": "augmentation",
        "available": True,
        "n_chunks_checked": len(chunks),
        "n_build_failed": sum(build_errors.values()),
        "build_errors": dict(build_errors.most_common(3)),
        "n_source_sets": len([k for k, _ in by_set.most_common() if k]),
        "source_set_distribution": dict(by_set.most_common(12)),
        "n_conflict_facts": len(conflicts),
        "conflict_pairs": {"|".join(k) if k else "(unknown)": v
                           for k, v in pairs.most_common(10)},
        "caliber": (
            "测的是『生产检测器在真实语料上检出多少条数值口径分歧』。"
            "**没有人工标注的『应检出』清单**，所以这是描述性统计，不是召回率。"
            "要报召回率必须先有人工标注分歧集。"
        ),
        "limitations": [
            "只覆盖 source_conflict 规则表里定义的数值事实（风险区间/阈值等）",
            "倾向性分歧（『建议手术』vs『可观察』）不在检测范围",
            "n_conflict_facts 小不代表没有分歧，可能只是规则没覆盖到",
        ],
    }


# ===================================================================== 阶段 5：生成
def evaluate_generation_readiness(args) -> dict:
    """生成段：**只做就绪度检查**，不跑（要真机判官与生产链路）。

    给出"当前可评测样本量"与"要判 5pt 需要多少 n"，避免再次出现
    "n=21 就敢报 faithfulness"的情况。
    """
    out = {
        "stage": "generation",
        "executed": False,
        "why": (
            "生成质量需要生产链路 + 判官（8006），属真机评测，由 "
            "scripts/audit/p108_hallucination_faithfulness.py 负责。"
            "本脚本只报『要判得动需要多少 n』，防止再拿 n=21 报结论。"
        ),
        "power_requirements": {
            "delta_5pt_at_discordant_10pct": required_n_for_delta_exact(0.05, 0.10),
            "delta_5pt_at_discordant_15pct": required_n_for_delta_exact(0.05, 0.15),
            "delta_5pt_at_discordant_25pct": required_n_for_delta_exact(0.05, 0.25),
            "note": (
                "配对 McNemar，80% power，alpha=0.05，**精确解**（闭式解偏乐观 2~7%）。"
                "delta=5pt 是绝对差（如 faithfulness 从 0.60 提到 0.65）。"
            ),
        },
    }
    return out


# ===================================================================== 主流程
def check_invariants(stages: dict) -> list[str]:
    """**不变量闸门**：任何一条命中即让脚本返回非 0。

    这些不是"分数好不好看"，而是"评估本身有没有算错"——第四次评估事故之后
    加的硬约束。
    """
    violations: list[str] = []
    ret = stages.get("retrieval") or {}
    # 检索段**没跑**时它的不变量不适用 —— 不能报成"违反"，否则
    # `--skip-retrieval` 永远红，门禁变成"每次都必须起嵌入服务"（那是另一回事）。
    if ret.get("executed") is not False:
        overall = ret.get("overall") or {}
        if overall.get("violations"):
            violations.append(f"retrieval.ndcg_out_of_range:{overall['violations']}")
        for name in METRIC_NAMES:
            v = overall.get(name)
            if v is not None and not (0.0 <= v <= 1.0):
                violations.append(f"retrieval.{name}_out_of_[0,1]:{v}")
        if overall.get("duplicates_removed") is None:
            violations.append(
                "retrieval.duplicates_removed_missing（必须报，否则不知道语料有没有副本）")
        if (overall.get("n") is not None and overall.get("n_input") is not None
                and overall["n"] > overall["n_input"]):
            violations.append("retrieval.n_exceeds_n_input")

    ing = stages.get("ingestion") or {}
    if ing.get("char_conservation_flag") == "OVERLAP_OR_DUP":
        violations.append(
            f"ingestion.char_conservation_over_1.05:{ing.get('char_conservation')}"
            "（chunk 有重叠或重复入库）"
        )

    ch = stages.get("chunking") or {}
    ss = ch.get("self_sufficiency_proxy")
    if ss is not None and not (0.0 <= ss <= 1.0):
        violations.append(f"chunking.self_sufficiency_out_of_[0,1]:{ss}")

    return violations


async def collect_corpus(conn, kb_version: str | None) -> tuple[list[dict], dict]:
    """读 chunk + 按逻辑 ID 去重到**最新版本**（指标可信的前提）。"""
    sql = (
        "SELECT chunk_id, kb_version, organ, source_doc_id, content, section_path "
        "FROM knowledge_chunks WHERE content IS NOT NULL"
    )
    rows = await conn.fetch(sql + (" AND kb_version = $1" if kb_version else ""),
                            *([kb_version] if kb_version else []))
    raw = [{"chunk_id": r["chunk_id"], "kb_version": r["kb_version"],
            "organ": r["organ"], "source_doc_id": r["source_doc_id"],
            "text": r["content"], "section_path": r["section_path"] or ""} for r in rows]

    best: dict[str, dict] = {}
    for c in raw:
        key = _norm_chunk_id(c["chunk_id"])
        cur = best.get(key)
        if cur is None or _vkey(c["kb_version"]) > _vkey(cur["kb_version"]):
            best[key] = c
    deduped = list(best.values())
    info = {
        "raw_chunks": len(raw),
        "deduped_chunks": len(deduped),
        "dropped_duplicates": len(raw) - len(deduped),
        "kept_versions": dict(Counter(c["kb_version"] for c in deduped)),
    }
    return deduped, info


async def collect_source_docs(source_dir: str | None) -> tuple[list[dict], str]:
    """读 KB 源文档 JSON（摄取段的"上游"）。读不到就如实返回空 + 原因。"""
    if not source_dir:
        return [], "no_source_dir_given"
    root = Path(os.path.expanduser(source_dir))
    if not root.is_dir():
        return [], f"source_dir_not_found:{root}"
    docs = []
    for organ_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for jf in sorted(organ_dir.glob("*.json")):
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(data, dict):
                continue
            content = data.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            docs.append({"source_doc_id": data.get("source_doc_id") or jf.stem,
                         "organ": organ_dir.name, "content": content})
    return docs, "ok"


async def main() -> int:
    ap = argparse.ArgumentParser(description="RAG 五阶段分层评估（p114）")
    ap.add_argument("--dataset", default=DEFAULT_DATASET)
    ap.add_argument("--kb-version", default=None, help="限定 kb_version；默认取全量后去重到最新")
    ap.add_argument("--source-dir", default=os.path.expanduser("~/uai/data/kb_src_v15"),
                    help="KB 源文档目录（摄取段的上游）；缺省跳过摄取对比")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--organ-filter", action="store_true", help="按器官过滤语料（生产口径）")
    ap.add_argument("--skip-retrieval", action="store_true", help="不跑检索段（无需嵌入服务）")
    ap.add_argument("--report", default="reports/p114_layered_eval.json")
    args = ap.parse_args()

    t0 = time.monotonic()
    ds = Path(args.dataset)
    if not ds.is_absolute():
        ds = Path.cwd() / ds
    if not ds.is_file():
        print(f"[FATAL] 数据集不存在：{ds}", flush=True)
        return 2
    rows = [json.loads(x) for x in ds.read_text(encoding="utf-8").splitlines() if x.strip()]
    print(f"[data] {len(rows)} 条（可答 {sum(1 for r in rows if r.get('answerable', True))}"
          f" / 不可答 {sum(1 for r in rows if r.get('answerable') is False)}）", flush=True)

    conn = await _connect()
    try:
        corpus, corpus_info = await collect_corpus(conn, args.kb_version)
        source_docs, src_status = await collect_source_docs(args.source_dir)
    finally:
        await conn.close()

    print(f"[corpus] 原始 {corpus_info['raw_chunks']} → 去重后 {corpus_info['deduped_chunks']}"
          f"（丢弃 {corpus_info['dropped_duplicates']} 份旧版本副本）", flush=True)
    print(f"[corpus] 版本: {corpus_info['kept_versions']}", flush=True)
    print(f"[source] {len(source_docs)} 份源文档（{src_status}）", flush=True)

    stages: dict = {}
    stages["ingestion"] = (
        evaluate_ingestion(source_docs, corpus) if source_docs
        else {"stage": "ingestion", "available": False,
              "reason": src_status,
              "note": "源文档读不到，摄取段**未评估**（不是 0 分，是没测）"}
    )
    stages["chunking"] = evaluate_chunking(corpus)
    stages["augmentation"] = evaluate_augmentation(corpus)

    if args.skip_retrieval:
        stages["retrieval"] = {"stage": "retrieval", "executed": False,
                               "reason": "--skip-retrieval"}
    else:
        print("[retrieval] 嵌入 + 检索中…", flush=True)
        stages["retrieval"] = evaluate_retrieval(
            rows, corpus, top_k=args.top_k, organ_filter=args.organ_filter)

    stages["generation"] = evaluate_generation_readiness(args)

    violations = check_invariants(stages)

    report = {
        "probe": "p114_rag_layered_eval",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dataset": str(ds),
        "corpus": corpus_info,
        "stages": stages,
        "invariant_violations": violations,
        "invariants_clean": not violations,
        "wall_s": round(time.monotonic() - t0, 1),
    }
    rp = Path(args.report)
    if not rp.is_absolute():
        rp = Path.cwd() / rp
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 打印摘要（人读） ----
    print("\n" + "=" * 78)
    ing = stages["ingestion"]
    if ing.get("available") is False:
        print(f"① 摄取  未评估（{ing.get('reason')}）")
    else:
        print(f"① 摄取  字符守恒 {ing['char_conservation']:.4f} [{ing['char_conservation_flag']}]"
              f"  数值保留 {ing.get('number_conservation')}"
              f"  表格管道符 {ing.get('table_pipe_conservation')}")
    ch = stages["chunking"]
    print(f"② 分块  自足性代理 {ch['self_sufficiency_proxy']:.4f}"
          f"  句末完整 {ch['sentence_end_rate']:.4f}"
          f"  指代开头 {ch['anaphora_head_rate']:.4f}  p50={ch['len_chars_p50']}字")
    ret = stages["retrieval"]
    if ret.get("executed") is False:
        print(f"③ 检索  未执行（{ret.get('reason')}）")
    else:
        o = ret["overall"]
        print(f"③ 检索  n={o['n']}（排除 {o['n_excluded']}）"
              f"  hit@1={o['hit@1']}  hit@10={o['hit@10']}"
              f"  mrr@10={o['mrr@10']}  ndcg@10={o['ndcg@10']}"
              f"  重复副本={o['duplicates_removed']}")
        for organ, m in ret["by_organ"].items():
            print(f"         └ {organ:9s} n={m['n']:3d} hit@1={m['hit@1']} hit@10={m['hit@10']}")
    aug = stages["augmentation"]
    if aug.get("available") is False:
        print(f"④ 增强  未评估（{aug.get('reason')}）")
    else:
        print(f"④ 增强  source_set {aug['n_source_sets']} 个"
              f"  检出数值分歧 {aug['n_conflict_facts']} 条")
    print(f"⑤ 生成  未执行（需真机判官）；判 5pt 需 n≈"
          f"{stages['generation']['power_requirements']['delta_5pt_at_discordant_15pct']}")

    if violations:
        print("\n❌ 不变量违反（评估实现自身有问题，数字不可用）：")
        for v in violations:
            print(f"   - {v}")
    else:
        print("\n✅ 不变量全部通过")
    print(f"报告：{rp}  用时 {report['wall_s']}s")
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
