"""A.66 WP1-②：构造两类**难查询集**（自造，仅用于扩边对照裁决，**不并入正式指标**）。

为什么造它（p40 的结论）
------------------------
p40 实测：136 条 golden 里 **129 条命中 top-1**、累计 hit@10 = **0.9853** → `SATURATED`。
也就是说现有评测集**根本没在考**"能不能找到指南知识"（query 全来自对话语料）与
"跨文档邻居是否有用"（命中太容易）。要判断图谱扩边的价值，只能造**更难的题**。

两类难集（各自的"对症假设"）
----------------------------
1. **`crossdoc`（跨文档依赖型）**：取同一 `(organ, section_path)` 分组里的**两篇不同文档** A、B；
   query = A 的患者提问（自然问句），**gold = B**。→ 这正是"跨文档同章节邻居"能不能救的场景；
   不需要 LLM，**完全确定性可复现**。
2. **`guideline`（指南型）**：从 KB 的 `pdf_*` 块生成问句（走本地生成服务），gold = 该指南文档
   → 考"检索能不能找到指南知识"（现有评测从未覆盖）。

规则化校验（硬性，任何一条不过即丢弃并计数）
--------------------------------------------
* **长度**：8 ≤ len(query) ≤ 60（与 golden 构造口径一致：`t1b` 的 `_MIN/MAX_QUERY_CHARS`）；
* **禁止抄原句**：query 与**任意 KB 块**的最长公共子串占 query 的比 ≤ `--max-overlap`（默认 0.6）；
* **gold 唯一且可追溯**：`gold_source_doc_id` 必须存在；`crossdoc` 模式还要求 gold ≠ query 的来源文档；
* **去重**：query 文本完全重复的丢弃。

产物（`tests/reports/hard_query_set_*.json`）**固定带**：
`synthetic: true` + `usage: expansion_ablation_only` + 规则参数 + 丢弃统计 —— 让"这是自造集、
不许当正式指标"这件事随文件走。

用法（服务器，需 DB；`guideline` 模式还需生成服务）：
    ~/uai/venv/bin/python scripts/audit/p41_build_hard_query_set.py --mode crossdoc
    ~/uai/venv/bin/python scripts/audit/p41_build_hard_query_set.py --mode guideline --gen-url http://127.0.0.1:8002

退出码：0 至少产出一个条目；2 前置缺失（DB 不可达 / 一个都没造出来）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 复用 golden 构造侧的"患者提问"抽取口径（同一把尺子，不另写正则）
from t1b_build_qa_golden import _MIN_QUERY_CHARS, _QUERY_MAX_CHARS, extract_question  # noqa: E402

OUT_DIR = ROOT / "tests" / "reports"

#: 对话语料**未脱敏**的原始来源（`t1b_build_qa_golden.py` 用的就是它）。
#: ⚠️ A.66 实测：KB 里的 `content` 已被脱敏 —— `患者提问：` 被替换成 `[REDACTED]：`
#: （1257 块里含"患者提问"的块数 = **0**）→ 不能拿 KB content 抽问句，必须回源文件。
DIALOGUE_SRC = pathlib.Path.home() / "uai" / "data" / "kb_sources" / "dialogue_docs.jsonl"

#: KB 内容里的脱敏前缀（兜底用）
_REDACTED_HEAD = re.compile(r"^\[REDACTED\]：(.+?)。")

DEFAULT_MAX_OVERLAP = 0.6


def longest_common_substring_ratio(query: str, text: str) -> float:
    """query 与 text 的最长公共子串长度 / len(query)。纯函数（守卫用）。

    用滚动 DP（只有两行），中文按字符处理；text 很长时也够快。
    """
    if not query or not text:
        return 0.0
    prev = [0] * (len(text) + 1)
    best = 0
    for ch in query:
        cur = [0] * (len(text) + 1)
        for j, tc in enumerate(text, 1):
            if ch == tc:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best / len(query)


def check_query(
    query: str,
    gold_doc: str,
    chunk_texts: list[str],
    *,
    max_overlap: float = DEFAULT_MAX_OVERLAP,
    gold_doc_must_differ_from: str | None = None,
) -> tuple[bool, str, float]:
    """规则化校验 → (通过?, 拒绝原因, 最大重叠比)。纯函数（守卫用）。"""
    q = (query or "").strip()
    if not (_MIN_QUERY_CHARS <= len(q) <= _QUERY_MAX_CHARS):
        return False, "length_out_of_range", 0.0
    if not gold_doc:
        return False, "missing_gold", 0.0
    if gold_doc_must_differ_from is not None and gold_doc == gold_doc_must_differ_from:
        return False, "gold_equals_source_doc", 0.0
    overlap = max((longest_common_substring_ratio(q, t) for t in chunk_texts), default=0.0)
    if overlap > max_overlap:
        return False, "overlap_too_high", overlap
    return True, "", overlap


def extract_question_from_kb_content(content: str) -> str | None:
    """兜底：KB 内容已脱敏，`患者提问：` 变成 `[REDACTED]：` → 取到第一个句号为止。纯函数。"""
    m = _REDACTED_HEAD.match((content or "").strip())
    return m.group(1).strip() if m else None


def load_dialogue_questions(src: pathlib.Path) -> dict[str, str]:
    """从**未脱敏**的对话源文件里取 `source_doc_id → 患者提问`（同 t1b 的问句口径）。"""
    out: dict[str, str] = {}
    if not src.is_file():
        return out
    parse_failures = 0
    for line in src.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            # 坏行必须计数（零静默纪律）；来源文件损坏时上层会看到 parse_failures > 0
            parse_failures += 1
            continue
        doc = d.get("source_doc_id") or d.get("doc_id") or ""
        q = extract_question(d.get("content") or "")
        if doc and q:
            out[doc] = q.strip()
    return out


def _chat(gen_url: str, model: str, prompt: str, max_tokens: int = 128) -> str:
    """生成服务调用（/no_think 收尾，避免 Qwen3 类模型把 token 花在思考块上）。"""
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{gen_url}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.load(resp)
    return data["choices"][0]["message"]["content"]


async def _build_crossdoc(conn, kb_version: str, limit: int, max_overlap: float) -> tuple[list[dict], dict]:
    """跨文档依赖型：同 (organ, section_path) 组里取 A 的问句、B 作 gold。"""
    groups = await conn.fetch(
        """
        SELECT organ, section_path, array_agg(DISTINCT source_doc_id) AS docs
        FROM knowledge_chunks
        WHERE kb_version = $1 AND COALESCE(section_path, '') <> ''
        GROUP BY organ, section_path
        HAVING count(DISTINCT source_doc_id) >= 2
        ORDER BY count(DISTINCT source_doc_id) DESC
        """,
        kb_version,
    )
    built: list[dict] = []
    rejected: dict[str, int] = {}
    seen_q: set[str] = set()
    questions = load_dialogue_questions(DIALOGUE_SRC)
    if not questions:
        rejected["dialogue_source_missing_or_unparsable"] = 1
    for g in groups:
        if len(built) >= limit:
            break
        docs = [d for d in (g["docs"] or []) if d]
        for i, src_doc in enumerate(docs):
            if len(built) >= limit:
                break
            # 问句优先取**未脱敏来源**（KB 内容里 `患者提问：` 已被脱敏成 `[REDACTED]：`）
            question = questions.get(src_doc)
            if not question:
                rows = await conn.fetch(
                    "SELECT content FROM knowledge_chunks"
                    " WHERE kb_version = $1 AND source_doc_id = $2 ORDER BY chunk_id LIMIT 1",
                    kb_version,
                    src_doc,
                )
                question = (
                    extract_question_from_kb_content(rows[0]["content"]) if rows else None
                )
            if not question:
                rejected["no_question_in_source_doc"] = (
                    rejected.get("no_question_in_source_doc", 0) + 1
                )
                continue
            q = question.strip()
            if q in seen_q:
                rejected["duplicate_query"] = rejected.get("duplicate_query", 0) + 1
                continue
            gold_doc = next((d for j, d in enumerate(docs) if j != i), "")
            if not gold_doc:
                continue
            gold_rows = await conn.fetch(
                "SELECT content FROM knowledge_chunks"
                " WHERE kb_version = $1 AND source_doc_id = $2",
                kb_version,
                gold_doc,
            )
            ok, reason, overlap = check_query(
                q,
                gold_doc,
                [r["content"] or "" for r in gold_rows],
                max_overlap=max_overlap,
                gold_doc_must_differ_from=src_doc,
            )
            if not ok:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            seen_q.add(q)
            built.append(
                {
                    "query_id": f"hard-xd-{len(built) + 1:04d}",
                    "query_text": q,
                    "gold_source_doc_id": gold_doc,
                    "source_doc_of_query": src_doc,
                    "requires_cross_doc": True,
                    "overlap_ratio": round(overlap, 4),
                    "checked": True,
                }
            )
    return built, rejected


async def _build_guideline(
    conn, kb_version: str, limit: int, max_overlap: float, gen_url: str, model: str
) -> tuple[list[dict], dict]:
    """指南型：从 pdf_* 块生成问句，gold = 该指南文档。"""
    rows = await conn.fetch(
        """
        SELECT chunk_id, source_doc_id, content FROM knowledge_chunks
        WHERE kb_version = $1 AND source_doc_id LIKE 'pdf%' AND COALESCE(content,'') <> ''
        ORDER BY source_doc_id, chunk_id
        """,
        kb_version,
    )
    built: list[dict] = []
    rejected: dict[str, int] = {}
    seen_q: set[str] = set()
    by_doc: dict[str, list[str]] = {}
    for r in rows:
        by_doc.setdefault(r["source_doc_id"], []).append(r["content"] or "")

    for r in rows:
        if len(built) >= limit:
            break
        text = (r["content"] or "").strip()
        if len(text) < 80:
            rejected["too_short_source"] = rejected.get("too_short_source", 0) + 1
            continue
        prompt = (
            "你是超声科医生。请根据下面这段指南原文，提出一个**临床医生会问的问题**"
            f"（{_MIN_QUERY_CHARS}~{_QUERY_MAX_CHARS} 字，中文，只输出问题本身，不要抄原文整句）。\n\n"
            f"【指南原文】\n{text[:800]}\n\n问题：/no_think"
        )
        try:
            raw = _chat(gen_url, model, prompt)
        except Exception as exc:
            gen_failure = f"gen_failed:{type(exc).__name__}"
            rejected[gen_failure] = rejected.get(gen_failure, 0) + 1
            continue
        # 生成服务可能吐多行/解释性文字 → 只在"像问句的行"里挑（长度合规且以问号结尾优先）
        candidates: list[str] = []
        for line in (raw or "").splitlines():
            cand = re.sub(r"^[\s\d.、)\-*\"'“”]*|[\s\"'“”]*$", "", line.strip())
            if not cand:
                continue
            if _MIN_QUERY_CHARS <= len(cand) <= _QUERY_MAX_CHARS:
                candidates.append(cand)
        q = next((c for c in candidates if c.endswith(("？", "?"))), "")
        if not q:
            q = candidates[0] if candidates else ""
            if not q:
                rejected["no_question_shaped_line"] = (
                    rejected.get("no_question_shaped_line", 0) + 1
                )
                continue
        if q in seen_q:
            rejected["duplicate_query"] = rejected.get("duplicate_query", 0) + 1
            continue
        ok, reason, overlap = check_query(
            q,
            r["source_doc_id"],
            by_doc.get(r["source_doc_id"], []),
            max_overlap=max_overlap,
        )
        if not ok:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        seen_q.add(q)
        built.append(
            {
                "query_id": f"hard-gl-{len(built) + 1:04d}",
                "query_text": q,
                "gold_source_doc_id": r["source_doc_id"],
                "source_chunk_id": r["chunk_id"],
                "requires_cross_doc": False,
                "overlap_ratio": round(overlap, 4),
                "checked": True,
            }
        )
    return built, rejected


async def _run(args) -> dict:
    import asyncpg

    from app.config.settings import settings

    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    out: dict = {
        "synthetic": True,
        "usage": "expansion_ablation_only",
        "builder": "p41_build_hard_query_set.py",
        "warning": "自造难集：仅用于'图谱扩边是否有用'的对照裁决，**不并入正式指标与门限**",
        "kb_version": args.kb_version,
        "rule_checks": {
            "max_char_overlap": args.max_overlap,
            "length_range": [_MIN_QUERY_CHARS, _QUERY_MAX_CHARS],
            "dedupe": "exact_text",
        },
        "sets": {},
    }
    try:
        if args.mode in ("crossdoc", "all"):
            built, rejected = await _build_crossdoc(
                conn, args.kb_version, args.limit, args.max_overlap
            )
            out["sets"]["crossdoc"] = {
                "n": len(built),
                "rejected": rejected,
                "queries": built,
            }
        if args.mode in ("guideline", "all"):
            built, rejected = await _build_guideline(
                conn, args.kb_version, args.limit, args.max_overlap, args.gen_url, args.gen_model
            )
            out["sets"]["guideline"] = {
                "n": len(built),
                "rejected": rejected,
                "queries": built,
            }
    finally:
        await conn.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=("crossdoc", "guideline", "all"), default="crossdoc")
    ap.add_argument("--kb-version", default="kb_v1.3.0")
    ap.add_argument("--limit", type=int, default=200, help="每类最多构造多少条")
    ap.add_argument("--max-overlap", type=float, default=DEFAULT_MAX_OVERLAP)
    ap.add_argument("--gen-url", default="http://127.0.0.1:8002")
    ap.add_argument("--gen-model", default="Qwen2.5-1.5B-Instruct")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    args = ap.parse_args()

    t0 = time.monotonic()
    try:
        payload = asyncio.run(_run(args))
    except SystemExit as exc:
        print(str(exc))
        return 2
    except Exception as exc:
        print(f"[FATAL] 难集构造失败：{type(exc).__name__}: {exc}")
        return 2

    payload["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    payload["wall_s"] = round(time.monotonic() - t0, 1)
    out_dir = pathlib.Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for name, block in payload["sets"].items():
        total += int(block.get("n") or 0)
        path = out_dir / f"hard_query_set_{name}.json"
        path.write_text(
            json.dumps({**payload, "set_name": name, "queries": block["queries"],
                        "n": block["n"], "rejected": block["rejected"]},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"{name}: 造出 {block['n']} 条，丢弃 {block['rejected']} → {path}")
    if not total:
        print("[FATAL] 一条都没造出来（看各 set 的 rejected 统计）")
        return 2
    print(f"自造难集合计 {total} 条（synthetic=true，仅用于扩边对照）  耗时 {payload['wall_s']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
