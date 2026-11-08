"""第 4 组 4.2 + 4.3：**幻觉率**与 **faithfulness**（同一批干净样本、同一套原子事实）。

## 4.2 幻觉率（口径写死）

**定义**：结论里出现了**知识库中不存在的具体事实**，且该事实属于下列四类"硬事实"之一：
1. **百分数/风险区间**（`10-50%`、`约 5%`）；
2. **分级-分值映射**（`4-6 分为 TR4`、`≥7 分为 TR5`）；
3. **阈值类判定**（`≥1cm 建议 FNA`、`>10mm`）；
4. **药物/剂量**（`mg`/`ml`/`μg`）。

**不算幻觉的三类（必须排除，否则分母虚高）**：
* 本次影像所见里的测量值（`大小约 8x6mm` —— 来自输入，不是模型编的）；
* 系统派生字段（`总分`/`置信度`/`评分` —— 由系统算出）；
* 纯定性描述与建议（`建议复查`、`边界清晰`）。

**检测方法（可审计、可复算）**：把结论里的硬事实**原文片段**抽出来，逐条检索：
* ① 该记录**当时送进 prompt 的知识块**（`knowledge_ids` 快照）；
* ② 该器官 `kb_v1.4.0` 全库（`content ILIKE '%<片段>%'`）。
两条都检索不到 → 记为**候选幻觉**，再由**人工逐条判**（自动检测有假阳性：
同义改写、跨块拼接、表格数字被吞都会误报）。

**报出的数**：`幻觉率 = 人工确认的幻觉条数 / 可检测样本数`，并给 `候选/确认` 两级。

## 4.3 faithfulness（口径 = p44 的 A 口径）

* **只算知识断言**：断言先分三桶 `knowledge / image / advice`；
  影像所见（本次影像描述）与建议（随访/穿刺…）**不进分母**——
  这是 A.69 实测结论：结论 = 影像所见 + 知识建议，影像所见永远不在知识库里。
* 断言生成 / NLI 用 **RAGAS 官方 prompt**（`scripts/prompts/ragas_faithfulness.py`），
  判官 = 8006 Qwen3-8B，`/no_think`，要求**中文**输出（否则跨语言错配把分数压死）。
* `answer_mode=derived_stripped`（剥掉 `分级/总分/置信度` 这类系统派生字段）。
* `faithfulness = 有支持的知识断言 / 知识断言总数`；某条样本知识断言为 0 时记 `None`（**不写 1.0**）。

用法（服务器，需 8006 判官）：
    ~/uai/venv/bin/python scripts/audit/run_with_env.py \
        scripts/audit/p108_hallucination_faithfulness.py \
        --eval-set ~/uai/data/eval/gen_quality_assertive_20261108.jsonl \
        --report ~/uai/reports/p108_hf.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import statistics
import sys
import time

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_SCRIPTS), str(_SCRIPTS / "audit"), str(_SCRIPTS / "langfuse")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from p44_faithfulness_caliber_a import BUCKETS, _sample_of  # noqa: E402

# ---------------------------------------------------------------- 幻觉：硬事实抽取

#: 百分数（含区间 `10-50%`）
_RE_PCT = re.compile(r"\d{1,3}(?:\.\d+)?\s*(?:[-~—至]\s*\d{1,3}(?:\.\d+)?\s*)?%")
#: 分级-分值映射（`4-6 分为 TR4` / `≥7 分为 TR5` / `2 分为 TR2`）
_RE_SCORE_MAP = re.compile(
    r"(?:≥|>|<=|<)?\s*\d{1,2}(?:\s*[-~—至]\s*\d{1,2})?\s*分\s*(?:为|→|即|=)\s*(?:TR|LR-?)\s*[1-5][a-c]?",
    re.IGNORECASE,
)
#: 阈值类（`≥1cm` / `>10mm` / `不超过 6 个月`）
_RE_THRESH = re.compile(
    r"(?:≥|>|<=|<|不小于|不大于|不超过|超过)\s*\d{1,3}(?:\.\d+)?\s*(?:cm|mm|厘米|毫米|个月|月|年|周)"
)
#: 药物/剂量
_RE_DOSE = re.compile(r"\d{1,3}(?:\.\d+)?\s*(?:mg|g|ml|mL|μg|ug|IU|U)\b")

#: prompt 里的「分数 → 分级」分档规则（每器官）
_RE_BIN = re.compile(
    r"(?:TR|LR-?)\s*([1-5][a-c]?)\s*[（(]\s*(≥|>|≤|<)?\s*(\d{1,2})"
    r"(?:\s*[-~—至]\s*(\d{1,2}))?\s*分",
    re.IGNORECASE,
)


def parse_grade_bins(template: str) -> dict[str, list[tuple[float, float]]]:
    """从生产 prompt 模板里解析「分级 → 分数区间」。纯函数（守卫钉住）。

    支持 prompt 里实际出现的两种写法：
      * ``**TR2（2 分）**`` / ``**TR4（4-6 分）**`` / ``**TR5（≥ 7 分）**``
      * ``（≤2 分为 TR2）`` 这类反写形式由调用方按"区间并集"处理即可
    返回 ``{"TR2": [(2,2)], "TR4": [(4,6)], "TR5": [(7,inf)]}``。
    """
    bins: dict[str, list[tuple[float, float]]] = {}
    for m in _RE_BIN.finditer(template or ""):
        grade = f"TR{m.group(1).lower()}"
        op, lo, hi = m.group(2), float(m.group(3)), m.group(4)
        if op == "≥":
            rng = (lo, float("inf"))
        elif op == ">":
            rng = (lo + 1e-9, float("inf"))
        elif op == "≤":
            rng = (float("-inf"), lo)
        elif op == "<":
            rng = (float("-inf"), lo - 1e-9)
        elif hi is not None:
            rng = (lo, float(hi))
        else:
            rng = (lo, lo)
        bins.setdefault(grade, []).append(rng)
    return bins


def score_map_violates(claim: dict, bins: dict[str, list[tuple[float, float]]]) -> bool | None:
    """模型给的「N 分 → TRk」是否违反 prompt 的分档表。纯函数（守卫钉住）。

    * ``True``  —— 分数落在该分级区间**之外**（真·算错）；
    * ``False`` —— 落在区间内；
    * ``None``  —— 该分级在 prompt 里没有可解析区间（不判，**不计入分母**）。

    ⚠️ 已知边界：模型偶尔会多写一句"（注：2分为TR2）"，其中 `2分为TR2` 是**取值区间**的
    陈述句，不构成本样本的算错。这类片段的分数与分级往往与主体结论一致；
    为保守起见，本函数对"注："开头的片段同样按区间判定（不一致才算错）。
    """
    m = re.search(
        r"(\d{1,2})(?:\s*[-~—至]\s*(\d{1,2}))?\s*分\s*(?:为|→|即|=)\s*(TR|LR-?)\s*([1-5][a-c]?)",
        claim.get("raw") or "", re.IGNORECASE)
    if not m:
        return None
    lo = float(m.group(1))
    hi = float(m.group(2)) if m.group(2) else lo
    prefix = "TR" if m.group(3).upper().startswith("TR") else "LR"
    grade = f"{prefix}{m.group(4).lower()}"
    ranges = bins.get(grade) or []
    if not ranges:
        return None
    return all(not (lo >= a and hi <= b) for a, b in ranges)


#: 明确不算硬事实的上下文（影像所见/系统派生）
_EXCLUDE_CONTEXT = (
    "大小约", "大小", "约 ", "x", "×", "总分", "置信度", "评分", "面积", "体积",
)


def extract_claims(text: str) -> list[dict]:
    """从结论里抽**硬事实**断言。纯函数（守卫钉住）。

    返回 `[{"kind": ..., "raw": 片段, "context": 前后 24 字}]`。
    排除规则：出现在"总分/置信度/大小约/测量"等派生或输入字段附近的**不算**。
    """
    t = (text or "").replace("\n", " ")
    out: list[dict] = []
    for kind, rx in (
        ("pct", _RE_PCT),
        ("score_map", _RE_SCORE_MAP),
        ("threshold", _RE_THRESH),
        ("dose", _RE_DOSE),
    ):
        for m in rx.finditer(t):
            lo = max(0, m.start() - 24)
            hi = min(len(t), m.end() + 24)
            ctx = t[lo:hi]
            raw = m.group(0).strip()
            # 排除系统派生字段
            if any(k in ctx for k in ("总分", "置信度")):
                continue
            out.append({"kind": kind, "raw": raw, "context": ctx})
    # 去重（同一片段多次出现）
    seen: set[tuple[str, str]] = set()
    uniq: list[dict] = []
    for c in out:
        key = (c["kind"], c["raw"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    return uniq


def _norm(s: str) -> str:
    """比对用的归一化：全角→半角、去空格、百分号/符号统一。"""
    table = str.maketrans("０１２３４５６７８９％－～", "0123456789%-~")
    return re.sub(r"\s+", "", (s or "").translate(table))


def claim_supported(raw: str, pools: list[str]) -> bool:
    """硬事实是否在某段文本里能找到。纯函数（守卫钉住）。

    比对是**子串级**（归一化后）：`10-50%` 在知识块里写成 `10%～50%` 也能命中，
    但写成了别的数就命中不了 —— 这正是"编造数值"要抓的。
    """
    needle = _norm(raw)
    if not needle:
        return True
    variants = {needle}
    # 区间写法归一：10-50% 与 10%～50% 等
    if "-" in needle or "~" in needle:
        a, _, b = needle.partition("-") if "-" in needle else needle.partition("~")
        if a and b:
            variants.add(a + b)
    for pool in pools:
        hay = _norm(pool)
        if any(v and v in hay for v in variants):
            return True
    return False


# ---------------------------------------------------------------- 4.3 复述识别

#: 数值片段（用于判断某条断言是否只是把**输入里的测量值**复述一遍）
_RE_NUM = re.compile(r"\d{1,3}(?:\.\d+)?\s*(?:%|mm|cm|毫米|厘米|岁|分)?")


def _nums(text: str) -> set[str]:
    out = set()
    for m in _RE_NUM.finditer(text or ""):
        token = re.sub(r"\s+", "", m.group(0))
        if any(ch.isdigit() for ch in token):
            out.add(token)
    return out


def rebucket_by_input(statements: list[dict], clinical_text: str) -> list[dict]:
    """把"只是复述输入测量值"的 knowledge 断言改判为 image。纯函数（守卫钉住）。

    ## 为什么必须有这一步（否则 faithfulness 结构性偏低）

    本系统的结论里**必然**含本次影像/测量的数值（`LVEF 60%`、`大小约 8x6mm`）。
    这些值来自**输入**（医生给的临床文本），不可能出现在知识库里；
    但它们不带"回声/边界"这类影像术语，于是 p44 的关键词分类器会把它们判进
    `knowledge` 桶 → 分母里混进"永远不可能有据"的断言 → faithfulness 被压低。

    判据（保守、可审计）：断言里的**每一个**数值 token 都能在 `clinical_text` 里找到
    → 判为"输入复述"（`image` 桶）。只要有一个数值不在输入里，就仍留在 knowledge 桶
    （宁可把它算成知识断言，也不放过可能的编造数值）。
    """
    have = _nums(clinical_text)
    out: list[dict] = []
    for d in statements:
        item = dict(d)
        if item.get("bucket") == "knowledge":
            n = _nums(item.get("statement") or "")
            if n and n <= have:
                item["bucket"] = "image"
                item["rebucket"] = "input_restatement"
        out.append(item)
    return out


# ---------------------------------------------------------------- 主流程


async def load_items(eval_set: pathlib.Path) -> tuple[list[dict], dict]:
    """样本 + 补齐 `context`（从知识块快照），并返回统计。"""
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import KnowledgeChunkModel

    rows = [json.loads(line) for line in eval_set.open(encoding="utf-8") if line.strip()]
    ids: set[str] = set()
    for r in rows:
        ids.update(r.get("knowledge_ids") or [])
    async with AsyncSessionLocal() as s:
        have = (
            await s.execute(
                select(KnowledgeChunkModel.chunk_id, KnowledgeChunkModel.content).where(
                    KnowledgeChunkModel.chunk_id.in_(list(ids))
                )
            )
        ).all()
    text_of = {str(a): str(b or "") for a, b in have}
    kept: list[dict] = []
    no_ctx = 0
    for r in rows:
        ctx = [text_of[k] for k in (r.get("knowledge_ids") or []) if k in text_of]
        if not ctx:
            no_ctx += 1
            continue
        item = dict(r)
        item["context"] = ctx
        item["clinical_text"] = r.get("clinical_text") or ""
        kept.append(item)
    return kept, {"rows": len(rows), "resolved": len(kept), "no_context": no_ctx}


def load_prompt_templates() -> dict[str, str]:
    """生产 prompt 模板（**系统注入的权威依据**）。

    ⚠️ 这是口径上必须的一条腿：模型算分用的是 prompt 里的 TI-RADS 评分表
    （`TR2（2 分）`、`4-6 分 → TR4`），那是**系统给它的规则**，不是它编的知识。
    只查"知识库"会把"按 prompt 规则算分"误判成幻觉（首轮实测 63/65 全中，
    全是 `2 分 → TR2` 这类），所以必须把模板也算来源。
    """
    base = pathlib.Path(__file__).resolve().parents[2] / "app" / "agents" / "prompts"
    out: dict[str, str] = {}
    for organ in ("thyroid", "liver", "cardiac"):
        f = base / f"{organ}.md"
        if f.is_file():
            out[organ] = f.read_text(encoding="utf-8")
    # 通用规则也可能被引用
    for extra in ("dispatcher.md", "knowledge.md"):
        f = base / extra
        if f.is_file():
            out.setdefault("_common", "")
            out["_common"] += "\n" + f.read_text(encoding="utf-8")
    return out


async def load_kb_pool(organ: str, kb_version: str) -> list[str]:
    """该器官全库文本（幻觉判定的第二条检索腿）。"""
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import KnowledgeChunkModel

    async with AsyncSessionLocal() as s:
        rows = (
            await s.execute(
                select(KnowledgeChunkModel.content).where(
                    KnowledgeChunkModel.kb_version == kb_version,
                    KnowledgeChunkModel.organ == organ,
                )
            )
        ).all()
    return [str(r[0] or "") for r in rows]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eval-set", default=str(
        pathlib.Path.home() / "uai/data/eval/gen_quality_assertive_20261108.jsonl"))
    ap.add_argument("--report", default=str(pathlib.Path.home() / "uai/reports/p108_hf.json"))
    ap.add_argument("--judge-url", default="http://127.0.0.1:8006")
    ap.add_argument("--judge-model", default="Qwen3-8B")
    ap.add_argument("--kb-version", default="kb_v1.4.0")
    ap.add_argument("--limit", type=int, default=0, help="0 = 全部")
    ap.add_argument("--answer-mode", default="derived_stripped")
    ap.add_argument("--skip-faithfulness", action="store_true")
    args = ap.parse_args()

    path = pathlib.Path(args.eval_set).expanduser()
    if not path.is_file():
        print(f"[FATAL] 样本不存在：{path}")
        return 2
    items, load_stat = await load_items(path)
    if args.limit:
        items = items[: args.limit]
    print(f"[样本] {load_stat}")
    if not items:
        print("[FATAL] 没有可用样本")
        return 2

    templates = load_prompt_templates()
    bins_by_organ = {o: parse_grade_bins(t) for o, t in templates.items()}
    print(f"[prompt] 分档规则解析：{ {o: sorted(v) for o, v in bins_by_organ.items() if v} }")
    print(f"[prompt] 载入模板 {sorted(templates)}（作为系统注入的权威依据，计入来源腿）")

    kb_pools: dict[str, list[str]] = {}
    for organ in sorted({str(i.get("organ") or "") for i in items}):
        kb_pools[organ] = await load_kb_pool(organ, args.kb_version)
        print(f"[KB] {organ}: {len(kb_pools[organ])} chunks（{args.kb_version}）")

    # ---------------- 4.2 幻觉（本地、确定式） ----------------
    t0 = time.time()
    per_sample: list[dict] = []
    for i, item in enumerate(items, 1):
        organ = str(item.get("organ") or "")
        text = item.get("conclusion") or ""
        claims = extract_claims(text)
        checked: list[dict] = []
        bins = bins_by_organ.get(organ) or {}
        for c in claims:
            if c["kind"] == "score_map":
                # 「N 分 → TRk」是**规则应用**，不是知识断言：
                # 只判它有没有违反 prompt 里给出的分档表（违反 = 真算错）。
                violated = score_map_violates(c, bins)
                if violated is None:
                    continue  # 该分级无区间可解析 → 不计入分母（不猜）
                checked.append({**c, "supported": not violated,
                                "check": "prompt_bin_rule"})
                continue
            pools = (
                list(item.get("context") or [])
                + kb_pools.get(organ, [])
                + [templates.get(organ, ""), templates.get("_common", "")]
            )
            ok = claim_supported(c["raw"], pools)
            checked.append({**c, "supported": ok, "check": "retrieval"})
        cand = [c for c in checked if not c["supported"]]
        per_sample.append(
            {
                "request_id": item.get("request_id"),
                "organ": organ,
                "n_claims": len(checked),
                "n_unsupported": len(cand),
                "claims": checked,
            }
        )
        if i % 20 == 0:
            print(f"  [4.2] {i}/{len(items)}  elapsed={time.time() - t0:.0f}s", flush=True)

    n_with_claims = sum(1 for s in per_sample if s["n_claims"])
    n_with_unsup = sum(1 for s in per_sample if s["n_unsupported"])
    total_claims = sum(s["n_claims"] for s in per_sample)
    total_unsup = sum(s["n_unsupported"] for s in per_sample)

    # ---------------- 4.3 faithfulness ----------------
    faith: list[dict] = []
    if not args.skip_faithfulness:
        t1 = time.time()
        for i, item in enumerate(items, 1):
            try:
                res = _sample_of(
                    item, args.judge_url, args.judge_model,
                    statements_lang="zh", answer_mode=args.answer_mode,
                )
            except Exception as exc:
                res = {"error": f"{type(exc).__name__}: {exc}"}
            res["request_id"] = item.get("request_id")
            res["organ"] = item.get("organ")
            if res.get("detail"):
                reb = rebucket_by_input(res["detail"], item.get("clinical_text") or "")
                agg: dict = {}
                for b in BUCKETS:
                    bucket_items = [x for x in reb if x["bucket"] == b]
                    sup = sum(1 for x in bucket_items if int(x.get("verdict") or 0) == 1)
                    agg[b] = {
                        "n": len(bucket_items),
                        "supported": sup,
                        "faith": round(sup / len(bucket_items), 4) if bucket_items else None,
                    }
                res["buckets"] = agg
                res["faith_knowledge"] = agg["knowledge"]["faith"]
                res["faith_all"] = round(
                    sum(1 for x in reb if int(x.get("verdict") or 0) == 1) / len(reb), 4
                ) if reb else None
                res["n_rebucketed"] = sum(1 for x in reb if x.get("rebucket"))
                res["detail"] = reb
            faith.append(res)
            if i % 5 == 0 or i == len(items):
                ok = sum(1 for r in faith if r.get("faith_knowledge") is not None)
                print(f"  [4.3] {i}/{len(items)}  measured={ok}  elapsed={time.time() - t1:.0f}s",
                      flush=True)

    fk = [float(r["faith_knowledge"]) for r in faith if r.get("faith_knowledge") is not None]
    fa = [float(r["faith_all"]) for r in faith if r.get("faith_all") is not None]
    bucket_tot = {b: {"n": 0, "supported": 0} for b in BUCKETS}
    for r in faith:
        for b in BUCKETS:
            slot = (r.get("buckets") or {}).get(b) or {}
            bucket_tot[b]["n"] += int(slot.get("n") or 0)
            bucket_tot[b]["supported"] += int(slot.get("supported") or 0)

    unsupported_examples: list[dict] = []
    for r in faith:
        for d in (r.get("detail") or []):
            if (
                d.get("bucket") == "knowledge"
                and int(d.get("verdict") or 0) == 0
                and len(unsupported_examples) < 40
            ):
                unsupported_examples.append(
                    {"request_id": r.get("request_id"), "statement": d.get("statement")}
                )

    # ---- 口径 B（对照）：把生产 prompt 模板也算作支持来源 ----
    template_text = {
        o: (templates.get(o, "") + "\n" + templates.get("_common", "")) for o in kb_pools
    }
    caliber_b = {"n": 0, "supportedA": 0, "supportedB": 0}
    for r in faith:
        organ = str(r.get("organ") or "")
        tpl_raw = template_text.get(organ, "")
        for d in (r.get("detail") or []):
            if d.get("bucket") != "knowledge":
                continue
            caliber_b["n"] += 1
            v = int(d.get("verdict") or 0)
            caliber_b["supportedA"] += v
            if v or claim_supported(d.get("statement") or "", [tpl_raw]):
                caliber_b["supportedB"] += 1
    caliber_b["faith_A"] = (
        round(caliber_b["supportedA"] / caliber_b["n"], 4) if caliber_b["n"] else None
    )
    caliber_b["faith_B"] = (
        round(caliber_b["supportedB"] / caliber_b["n"], 4) if caliber_b["n"] else None
    )
    caliber_b["note"] = (
        "口径 A = 判官只对着**检索块**判（主口径）；口径 B = 断言在检索块或**生产 prompt 模板**"
        "（TI-RADS 评分表 / LVEF 分档表）中能找到出处也算支持。B 是确定性下界，不是判官替换。"
    )

    # ---- 知识断言再分三层（供人工抽查，不影响主口径） ----
    _DERIVED = re.compile(
        r"总分|总评分|评分(?:为|范围|达到|对应)|评分总和|各项.{0,6}得分|分值为\s*\d|"
        r"\d+\s*分(?:\s*(?:对应|属于|归为|被归类|→))|\d+\s*分\s*$|"
        r"TR[1-5]\s*(?:等级|类别|分类|标准|的评分)|≥\s*\d+\s*分|≤\s*\d+\s*分|"
        r"\d+\s*[-~至]\s*\d+\s*分为", re.IGNORECASE)
    _SELFREF = re.compile(
        r"鉴别诊断(?:中|的|第)|证据(?:来源|来自|引用)|文献\s*\[|输出示例|"
        r"(?:第一|第二|第三)项|本条|上述|该结论|报告(?:中|里)", re.IGNORECASE)
    tier_cnt = {"derived": [0, 0], "selfref": [0, 0], "pure": [0, 0]}
    for r in faith:
        for d in (r.get("detail") or []):
            if d.get("bucket") != "knowledge":
                continue
            st = d.get("statement") or ""
            sub = "derived" if _DERIVED.search(st) else ("selfref" if _SELFREF.search(st) else "pure")
            tier_cnt[sub][0] += 1
            tier_cnt[sub][1] += int(d.get("verdict") or 0)
    knowledge_tiers = {
        k: {"n": v[0], "supported": v[1],
            "faith": round(v[1] / v[0], 4) if v[0] else None}
        for k, v in tier_cnt.items()
    }

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "eval_set": str(path),
        "load": load_stat,
        "n_samples": len(items),
        "n_distinct_inputs": len({(i.get("clinical_text"), i.get("organ")) for i in items}),
        "hallucination": {
            "caliber": {
                "definition": "结论中出现**具体事实**（百分数/分级-分值映射/阈值/剂量），且三条来源腿都检索不到",
                "excluded": "影像所见测量值（来自输入）、系统派生字段（总分/置信度）、纯定性与建议",
                "detector": "正则抽硬事实 → 归一化子串检索（三条腿：记录当时的 prompt 上下文 / kb_v1.4.0 该器官全库 / 生产 prompt 模板）→ 候选 → 人工复核",
                "sample_level": "一条样本只要含 ≥1 条未支持硬事实即计为候选",
            },
            "n_samples": len(items),
            "n_samples_with_claims": n_with_claims,
            "n_samples_with_unsupported": n_with_unsup,
            "candidate_rate": round(n_with_unsup / n_with_claims, 4) if n_with_claims else None,
            "n_claims_total": total_claims,
            "n_claims_unsupported": total_unsup,
            "claim_rate": round(total_unsup / total_claims, 4) if total_claims else None,
            "per_sample": per_sample,
        },
        "faithfulness": {
            "caliber": {
                "definition": "知识断言（不含影像所见与建议）中，有知识块支持的占比",
                "answer_mode": args.answer_mode,
                "judge": f"{args.judge_model} @ {args.judge_url}（RAGAS 官方 prompt，/no_think，中文断言）",
                "bucket_rule": "image/advice/knowledge 三桶由 p44 classify_statement 判定（规则透明）；再按 rebucket_by_input 把\"只复述输入测量值\"的 knowledge 断言改判为 image（否则分母混入永远无据的断言）",
                "zero_knowledge": "知识断言为 0 的样本记 None，不写 1.0",
            },
            "n_samples": len(items),
            "n_measured": len(fk),
            "n_judge_error": sum(1 for r in faith if r.get("error")),
            "mean_faith_knowledge": round(sum(fk) / len(fk), 4) if fk else None,
            "median_faith_knowledge": round(statistics.median(fk), 4) if fk else None,
            "mean_faith_all": round(sum(fa) / len(fa), 4) if fa else None,
            "bucket_totals": bucket_tot,
            "unsupported_knowledge_examples": unsupported_examples[:20],
            "n_rebucketed_total": sum(int(r.get("n_rebucketed") or 0) for r in faith),
            "knowledge_tiers": knowledge_tiers,
            "caliber_b_with_prompt_template": caliber_b,
            "per_sample": faith,
        },
    }
    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n== 4.2 幻觉（候选） ==")
    print(json.dumps({k: report["hallucination"][k] for k in (
        "n_samples", "n_samples_with_claims", "n_samples_with_unsupported",
        "candidate_rate", "n_claims_total", "n_claims_unsupported", "claim_rate")},
        ensure_ascii=False, indent=2))
    print("\n== 4.3 faithfulness ==")
    print(json.dumps({k: report["faithfulness"][k] for k in (
        "n_samples", "n_measured", "n_judge_error", "mean_faith_knowledge",
        "median_faith_knowledge", "mean_faith_all", "bucket_totals")},
        ensure_ascii=False, indent=2))
    print(f"\n报告：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
