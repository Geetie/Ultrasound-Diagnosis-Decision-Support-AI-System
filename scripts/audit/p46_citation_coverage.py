"""A.70 / R17 第 2 步：**引用覆盖率**与**引用正确率**（不需要拍板就能先出的两个数）。

**为什么做这个**：faithfulness（资料支持率）在"影像所见 + 知识建议"的混合结论上结构性偏低
（A.69：中文断言下影像所见占 90.9%，剥派生字段后 60 条不支持里只有 5% 是判官偏严）。
相比之下，下面两个数不依赖"判什么"的口径，也不依赖判官：

1. **引用覆盖率**：结论里的句子，有多大比例带了"来源"标记（chunk ID 或 [n] 编号）；
2. **引用正确率**：带标记的引用里，有多大比例**确实来自本次检索到的证据**
   （不在证据里的引用 = 编出来的出处，这是医疗场景最不能容忍的一类）。

**为什么它们能当主指标**（待拍板，但先给数）：
* 覆盖率 **0%** 说明系统压根不给出处 → 该修生成侧（提示词已要求「证据来源：chunk ID」）；
* 覆盖率 **高但正确率低** → 该修"引用纪律"（模型在编出处）；
* 覆盖率 **高且正确率高** → 才谈"每句是否被引用的段支持"（那才需要判官）。

**零成本**：只读评测集文本，不调判官、不调嵌入，秒级。

用法（服务器）：
    ~/uai/venv/bin/python scripts/audit/p46_citation_coverage.py \
        --limit 40 --report ~/uai/reports/citation_coverage.json

退出码：0 出数；2 前置缺失（评测集不存在）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

#: 仓库根（`--simulate-guard` 要 import 生产守卫 `app.inference.output_guard`）
ROOT = pathlib.Path(__file__).resolve().parents[2]

#: 结论里的句子边界（中文句号/问号/叹号/分号/换行）
_SENT_SPLIT = re.compile(r"[。！？；\n]+")

#: chunk ID 形态：`prefix_...#0000` / `pdf_..._0001#0000`（KB 里真实存在的形态）
_CHUNK_ID = re.compile(r"[A-Za-z0-9_\u4e00-\u9fff\.\-]+#\d{3,4}")

#: 数字编号引用：[1] 【1】 ［1］
#: ⚠️ **不能吃圆括号**：A.70 实测甲状腺结论的评分算式是
#: `理由: 成分 3 + 回声 2 = 2 分`、`实性/低回声 (3) + 边界光滑 (0)` —— 括号里是**分值**，
#: 首版把 `(3)`/`(0)` 当引用，算出"覆盖率 58.7%、正确率 50.9%"两个**假数**。
_NUM_CITE = re.compile(r"[\[【［]\s*(\d{1,2})\s*[\]】］]")

#: 提示词模板回显的痕迹（A.70 实测：结论里出现 `chunk_a1b2`、`患者PATIENT_001`、
#: 以及重复多份 `# 输出` 块 —— 说明模型把 prompt 里的示例当答案吐出来了）。
_ECHO_MARKERS = (
    re.compile(r"chunk_[a-z0-9]{4}"),
    re.compile(r"PATIENT_\d+"),
)
_ECHO_HEADER = re.compile(r"#\s*输出")

#: 显式来源标记
_SRC_LABEL = re.compile(r"(证据来源|资料来源|参考(文献|资料)?|来源)\s*[:：]")


def split_sentences(text: str) -> list[str]:
    """把结论切成句子（去空白、丢弃长度 <4 的碎片）。纯函数。"""
    out: list[str] = []
    for raw in _SENT_SPLIT.split(text or ""):
        s = raw.strip().strip("*").strip()
        if len(s) >= 4:
            out.append(s)
    return out


def extract_citations(text: str) -> list[str]:
    """抽取一段文本里的所有引用标记（chunk ID 与数字编号去重后按出现顺序）。纯函数。"""
    t = text or ""
    cites = [m.group(0) for m in _CHUNK_ID.finditer(t)]
    cites += [f"[{m.group(1)}]" for m in _NUM_CITE.finditer(t)]
    seen: set[str] = set()
    out: list[str] = []
    for c in cites:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def sentence_has_citation(sentence: str) -> bool:
    """一句里是否带引用标记（含"证据来源："这类显式标签）。纯函数。"""
    return bool(extract_citations(sentence)) or bool(_SRC_LABEL.search(sentence or ""))


def coverage_of(conclusion: str) -> dict:
    """单条结论的引用覆盖率。纯函数（守卫钉住）。"""
    sentences = split_sentences(conclusion)
    cited = [s for s in sentences if sentence_has_citation(s)]
    cites = extract_citations(conclusion)
    return {
        "sentences": len(sentences),
        "sentences_with_citation": len(cited),
        "coverage": round(len(cited) / len(sentences), 4) if sentences else None,
        "citations": cites,
        "n_citations": len(cites),
    }


def correctness_of(citations: list[str], evidence_ids: list[str]) -> dict:
    """引用里有多少"站得住"。纯函数（守卫钉住）。

    两种引用形态分别核对：
    * **chunk ID 形态**（`xxx#0000`）：与本次证据 ID 做**精确匹配**；
    * **数字编号**（`[n]`）：按**位置**核对 —— 本次证据是**有序列表**，`[n]` 合法当且仅当
      `1 <= n <= 证据条数`（越界 = 编出来的编号）。

    ⚠️ 证据 ID 抽不到时正确率记 ``None``（不写 1.0，同 A.69 的空桶纪律）。
    """
    chunk_like = [c for c in (citations or []) if "#" in c]
    nums = [int(c.strip("[【（(］] ").strip("】）)]")) for c in (citations or []) if "#" not in c
            and c.strip("[【（(［]").strip("]】）)］").isdigit()]
    if not evidence_ids:
        return {"checked_chunk": 0, "checked_numbering": 0, "valid": 0, "invalid": 0,
                "chunk_like": len(chunk_like), "numbering": len(nums), "correctness": None}
    id_set = {str(x) for x in evidence_ids}
    valid = sum(1 for c in chunk_like if c in id_set)
    valid += sum(1 for n in nums if 1 <= n <= len(evidence_ids))
    checked = len(chunk_like) + len(nums)
    return {
        "checked_chunk": len(chunk_like),
        "checked_numbering": len(nums),
        "valid": valid,
        "invalid": checked - valid,
        "chunk_like": len(chunk_like),
        "numbering": len(nums),
        "correctness": round(valid / checked, 4) if checked else None,
    }


def evidence_ids_of(item: dict) -> list[str]:
    """取"本次检索到的证据 chunk ID"，**保持顺序**（数字编号引用要按位置核对）。

    真机字段名是 ``context_ids``（A.70 实测）；其余形态作兼容兜底。
    """
    for key in ("context_ids", "evidence_chunk_ids", "chunk_ids", "retrieved_chunk_ids"):
        val = item.get(key)
        if isinstance(val, list) and val:
            return [str(x) for x in val]
    ids: list[str] = []
    ctx = item.get("context")
    if isinstance(ctx, list):
        for c in ctx:
            if isinstance(c, dict):
                cid = c.get("chunk_id") or c.get("id")
                if cid:
                    ids.append(str(cid))
            else:
                ids.extend(extract_citations(str(c)))
    return ids


def is_degraded(conclusion: str) -> bool:
    """结论是否只是**降级提示**（不是真结论）。纯函数。

    A.70 实测：40 条切片里存在 ``[thyroid] llm_unavailable: InferenceError; manual review
    required`` 这类样本 —— 把它们算进"引用覆盖率"会把分母灌脏（它们本来就没有结论）。
    """
    t = (conclusion or "").lower()
    return "llm_unavailable" in t or "manual review required" in t or "inferenceerror" in t


def is_prompt_echo(conclusion: str) -> bool:
    """结论里是否混入了**提示词模板的回显**（示例被当答案吐出来）。纯函数。

    A.70 实测：非降级样本里出现
    ``... 分级: TR2 总分: 2 ... 证据: chunk_a1b2, chunk_c3d4 ... 患者PATIENT_001: ...``
    并重复 **4 份** `# 输出` 块 —— `chunk_a1b2` / `PATIENT_001` 是 prompt 示例里的**占位符**，
    真实证据 ID 形如 `kb_v1.3.0:pdf_...#0000`。这类样本算进任何指标都是**脏样本**。
    """
    t = conclusion or ""
    if _ECHO_MARKERS[0].search(t):  # 假 chunk ID（chunk_a1b2）→ 铁证
        return True
    # ⚠️ A.77：`PATIENT_001` **单独出现不算回显**——它来自语料脱敏标记（KB 里 22 块含 `PATIENT_`），
    # 模型在患者解释段落抄了出来；prompt 里没有这个串。只有当它同时伴随 `# 输出`/`chunk_` 才算回显。
    headers = len(_ECHO_HEADER.findall(t))
    if _ECHO_MARKERS[1].search(t) and (headers >= 1 or "chunk_" in t):
        return True
    return headers >= 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--eval-set", default=str(pathlib.Path.home() / "uai/data/eval/double_engine_eval_set.jsonl")
    )
    ap.add_argument(
        "--report", default=str(pathlib.Path.home() / "uai/reports/citation_coverage.json")
    )
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--samples", type=int, default=5, help="报告里保留多少条明细")
    ap.add_argument("--diagnose", action="store_true", help="打印样本字段名与一条结论原文")
    ap.add_argument(
        "--simulate-guard",
        action="store_true",
        help="用**生产守卫**（app.inference.output_guard.detect_prompt_echo）回放这批样本，"
        "统计会被拒收多少条（验证拦截规则与真实脏样本是否对得上）",
    )
    args = ap.parse_args()

    path = pathlib.Path(args.eval_set).expanduser()
    if not path.is_file():
        print(f"[FATAL] 评测集不存在：{path}")
        return 2
    rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    items = [r for r in rows if (r.get("conclusion") or "").strip()][: args.limit]
    if not items:
        print("[FATAL] 没有带结论的样本")
        return 2

    if args.simulate_guard:
        # 用**生产守卫**（同一条规则）回放，避免"探针一套、生产一套"
        sys.path.insert(0, str(ROOT))
        from app.inference.output_guard import detect_prompt_echo

        reasons: dict[str, int] = {}
        rejected = 0
        total = len(rows)
        for r in rows:
            text = r.get("conclusion") or ""
            if not text.strip():
                continue
            reason = detect_prompt_echo(text)
            if reason:
                rejected += 1
                reasons[reason] = reasons.get(reason, 0) + 1
        print(f"生产守卫回放：{rejected}/{total} 条会被拒收（{rejected / total:.1%}）")
        for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"  {k}: {v}")
        return 0

    if args.diagnose:
        print("样本字段：", sorted(items[0].keys()))
        print("结论原文（前 300 字）：", (items[0].get("conclusion") or "")[:300].replace("\n", " / "))
        print("context 类型：", type(items[0].get("context")).__name__)
        print("证据 ID 抽取结果（前 3）：", list(evidence_ids_of(items[0]))[:3])

    per: list[dict] = []
    cov_sum = 0.0
    cov_n = 0
    degraded = 0
    echo = 0
    for it in items:
        concl = it.get("conclusion") or ""
        cov = coverage_of(concl)
        ev = evidence_ids_of(it)
        degraded_flag = is_degraded(concl)
        echo_flag = (not degraded_flag) and is_prompt_echo(concl)
        if degraded_flag:
            degraded += 1
        if echo_flag:
            echo += 1
        cor = correctness_of(cov["citations"], ev)
        per.append(
            {
                "request_id": it.get("request_id"),
                "organ": it.get("organ"),
                "degraded": degraded_flag,
                "prompt_echo": echo_flag,
                **{k: cov[k] for k in ("sentences", "sentences_with_citation", "coverage", "n_citations")},
                "citations": cov["citations"][:8],
                "evidence_ids": len(ev),
                **cor,
            }
        )
        # 降级 / 模板回显样本都不是干净结论 → 不进覆盖率均值（否则分母被灌脏）
        if cov["coverage"] is not None and not degraded_flag and not echo_flag:
            cov_sum += cov["coverage"]
            cov_n += 1

    checked = sum(p["checked_chunk"] + p["checked_numbering"] for p in per)
    valid = sum(p["valid"] for p in per)
    total_cites = sum(p["n_citations"] for p in per)
    report = {
        "probe": "p46_citation_coverage",
        "why": (
            "faithfulness 在混合结论上结构性偏低；引用覆盖率/正确率不依赖判官口径，"
            "且直接对应医疗场景最怕的『编出处』。"
        ),
        "generated_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "eval_set": str(path),
        "eval_set_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "n": len(per),
        "n_degraded": degraded,
        "n_prompt_echo": echo,
        "prompt_echo_rate": round(echo / (len(per) - degraded), 4)
        if len(per) - degraded
        else None,
        "n_evaluated": cov_n,
        "mean_coverage": round(cov_sum / cov_n, 4) if cov_n else None,
        "n_with_any_citation": sum(1 for p in per if p["n_citations"] > 0),
        "total_citations": total_cites,
        "citation_correctness": round(valid / checked, 4) if checked else None,
        "citations_checked": checked,
        "citations_invalid": checked - valid,
        "numbering_citations": sum(p["numbering"] for p in per),
        "per_sample": per,
        "samples": per[: args.samples],
    }
    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"样本 {report['n']} 条：降级答案 {degraded} 条、"
          f"**提示词模板回显 {echo} 条（{report['prompt_echo_rate']}）** —— 两者都不进指标")
    print(f"干净样本 {report['n_evaluated']} 条：平均引用覆盖率 **{report['mean_coverage']}**"
          f"（有引用的样本 {report['n_with_any_citation']}/{report['n']}）")
    print(f"引用总数 {total_cites}：chunk ID 形态 {sum(p['chunk_like'] for p in per)} 条、"
          f"数字编号 {sum(p['numbering'] for p in per)} 条；"
          f"核对通过 {valid}/{checked} → 引用正确率 **{report['citation_correctness']}**")
    print(f"报告：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
