"""A.69 / R17（用户选 A）：忠实度**拆口径重算** —— 只对「知识断言」计分。

**为什么拆**：现有 faithfulness 把结论拆成断言后，逐条问"能否由检索到的资料推出"。
但本系统的结论是**两段拼接**：**影像所见**（来自上传影像，永远不在知识库里）+ **知识建议**
（来自知识库）。于是即使知识部分 100% 有据，只要影像所见占比高，总分也上不去 ——
实测 **0.0906**，离门限 0.95 差一个数量级。这不是模型在编，而是**尺子量错了对象**。

**A 口径（本探针）**：
1. 断言分三桶（**规则透明、可审计**，不用第二个模型去判）：
   * `image` —— 描述本次影像所见的断言（回声/边界/形态/大小/血流/钙化…）
   * `advice` —— 建议与流程（建议复查/穿刺/随访/就医…）
   * `knowledge` —— 其余：来自指南/规范的知识性断言（**这才是该被资料支持的部分**）
2. `faith_all`（现口径，用于与 0.0906 对表自证）与
   `faith_knowledge`（A 口径 = 知识断言里有据的比例）**同时给出**；
3. 知识断言为 0 时 `faith_knowledge = null`（**不写 1.0**，否则会把"没东西可查"读成满分）。

**复用（不自造第二把尺子）**：断言生成/NLI/prompt 全部 import
`t1_eval_faithfulness`（RAGAS 官方 prompt），判官调用复用 `judge_eval_set._judge_chat`
（带 max_tokens，避免 `<think>` 吃光输出）。

用法（服务器，需判官 8006）：
    ~/uai/venv/bin/python scripts/audit/p44_faithfulness_caliber_a.py \
        --limit 40 --report ~/uai/reports/faithfulness_caliber_a.json

退出码：0 出数；2 前置缺失（评测集/判官不可用）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
_LANGFUSE = _SCRIPTS / "langfuse"
for _p in (str(ROOT), str(_SCRIPTS), str(_LANGFUSE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from answer_modes import ANSWER_MODES, answer_of  # noqa: E402
from judge_eval_set import _judge_chat  # noqa: E402
from t1_eval_faithfulness import (  # noqa: E402
    _extract_json,
    _filter_meta_statements,
    _is_refusal,
    _render_nli_prompt,
    _render_statement_prompt,
)

#: 影像所见断言的关键词（命中即视为 "描述本次影像"，不该用知识库去核对）
IMAGE_TERMS = (
    "回声",
    "边界",
    "形态",
    "边缘",
    "纵横比",
    "钙化",
    "血流",
    "信号",
    "后方",
    "包膜",
    "晕",
    "内部",
    "实质",
    "腺体",
    "大小",
    "直径",
    "厚",
    "cm",
    "mm",
    "厘米",
    "毫米",
    "结节",
    "囊肿",
    "淋巴结",
    "光滑",
    "毛刺",
    "分叶",
    "囊性",
    "实性",
    "弥漫",
    "局灶",
    "低回声",
    "高回声",
    "等回声",
    "无回声",
    "强回声",
    # 英文（A.69 实测：判官会把中文结论拆成英文断言，分桶必须认两种语言，
    # 否则 "The right lobe has a low echo nodule." 这类影像句会被误分进 knowledge）
    "echo",
    "hypoechoic",
    "hyperechoic",
    "anechoic",
    "isoechoic",
    "boundary",
    "margin",
    "shape",
    "calcification",
    "blood flow",
    "vascularity",
    "nodule",
    "cyst",
    "lymph node",
    "smooth",
    "spiculated",
    "lobulated",
    "solid",
    "cystic",
    "posterior",
    "capsule",
    "lobe",
    "diameter",
)

#: 建议/流程断言（不是"知识事实"，也不该被资料支持性判定拖累）
ADVICE_TERMS = (
    "建议",
    "推荐",
    "随访",
    "复查",
    "穿刺",
    "活检",
    "手术",
    "治疗",
    "就诊",
    "就医",
    "进一步",
    "必要时",
    "请结合",
    "咨询",
    "观察",
)

BUCKETS = ("knowledge", "image", "advice")


def classify_statement(text: str) -> str:
    """把一条断言分到 knowledge / image / advice。纯函数（守卫钉住）。

    判定顺序：**先看是否为影像所见**（含解剖/回声等描述），再看是否为建议，
    其余归为知识断言。顺序不能反：很多建议句里也含"结节"，先判建议会把影像句误分。
    """
    t = (text or "").strip()
    if not t:
        return "knowledge"
    has_image = any(k in t for k in IMAGE_TERMS)
    has_advice = any(k in t for k in ADVICE_TERMS)
    if has_image and not has_advice:
        return "image"
    if has_advice and not has_image:
        return "advice"
    if has_image and has_advice:
        # 两者都有：以"是否在描述本次影像"为主 —— 出现具体测量/回声词优先算 image
        return "image"
    return "knowledge"


def aggregate(rows: list[tuple[str, int]]) -> dict:
    """[(bucket, verdict)] → 每桶计数与支持率。纯函数（守卫钉住）。

    ``faith`` 为 None 表示该桶没有断言（**不写 1.0**）。
    """
    out: dict = {}
    for b in BUCKETS:
        items = [v for k, v in rows if k == b]
        n = len(items)
        sup = sum(1 for v in items if v == 1)
        out[b] = {
            "n": n,
            "supported": sup,
            "faith": round(sup / n, 4) if n else None,
        }
    all_items = [v for _, v in rows]
    out["all"] = {
        "n": len(all_items),
        "supported": sum(1 for v in all_items if v == 1),
        "faith": round(sum(1 for v in all_items if v == 1) / len(all_items), 4)
        if all_items
        else None,
    }
    return out


def _context_text(item: dict) -> str:
    """把评测集里的 context 统一成字符串。纯函数（守卫钉住）。

    ⚠️ **实测踩坑**：评测集的 `context` 是**列表**（`["段1", "段2", ...]`），
    `judge_eval_set._faithfulness_of` 用 `"\\n\\n".join(...)` 拼成一段。
    本探针首版直接把它当字符串传给判官（f-string 渲染成 Python 列表字面量），
    判官看到的是 `['...', '...']` 这种东西 → 几乎全判"不支持"，
    原口径复算 **0.0304** 而不是 0.0906。**自证腿就是用来抓这种事的。**
    """
    ctx = item.get("context")
    if isinstance(ctx, list):
        return "\n\n".join(str(x) for x in ctx if str(x).strip())
    return str(ctx or "").strip()


def _statement_prompt(item_question: str, answer: str, lang: str = "zh") -> str:
    """官方断言生成 prompt + **语言约束**（A.69 实测的关键修正）。

    ⚠️ **为什么必须加**：官方 prompt 是英文的，判官（Qwen3-8B）会顺手把**中文结论**
    拆成**英文断言**（实测样本："The right lobe has a low echo nodule."），
    再拿英文断言去对**中文资料**做 NLI → 跨语言判定大面积"不支持"，
    分数被系统性压低（实测支持率仅 ~20%）。这不是模型在编，是**语言错配**。
    语言约束不改变指标定义（断言仍是"原子断言"），只是要求用与答案相同的语言输出。
    """
    base = _render_statement_prompt(item_question, answer)
    if lang != "zh":
        return base
    hint = "用与答案相同的语言（中文）写出每条断言，不要翻译成英文。"
    return base.replace("Output(JSON only): /no_think", f"{hint}\nOutput(JSON only): /no_think")


def _sample_of(
    item: dict,
    judge_url: str,
    model: str,
    statements_lang: str = "zh",
    answer_mode: str = "raw",
) -> dict:
    """单条样本：取答案（按口径）→ 生成断言 → 分桶 → 一次 NLI → 汇总。

    ⚠️ **A.69 实测**：默认 `raw` 时，被判"不支持"的知识断言里混着大量
    **系统派生字段**（`分级=TR1` / `总分=0` / `置信度=0.0`）—— 它们由系统算出，
    本来就不可能出现在检索资料里。仓库里早有 `derived_stripped`（口径 C）专门剥掉这些，
    所以「口径」这一维必须一起对照，否则会把"派生字段"错记成"知识断言不支持"。
    """
    answer, mode_applied = answer_of(item, answer_mode)
    # 与 `judge_eval_set._faithfulness_of` 逐字一致（否则两口径不可比）
    question = (item.get("clinical_text") or "").strip() or "（无临床文本；按影像判读）"
    context = _context_text(item)
    if not answer or not context:
        return {"error": "缺答案或缺检索上下文（conclusion / context）"}

    raw = _judge_chat(
        judge_url,
        model,
        [{"role": "user", "content": _statement_prompt(question, answer, statements_lang)}],
    )
    data = _extract_json(raw)
    statements = [
        str(s).strip()
        for s in (data.get("statements", []) if isinstance(data, dict) else data)
        if str(s).strip()
    ]
    statements = _filter_meta_statements(statements)
    if _is_refusal(answer) or not statements:
        # 官方口径：拒答/无断言 → 总分 1.0；但 A 口径下"没有知识断言"就是 null
        return {
            "refusal": _is_refusal(answer),
            "no_statements": not statements,
            "buckets": aggregate([]),
            "faith_all": 1.0,
            "faith_knowledge": None,
            "answer_mode": answer_mode,
            "mode_applied": mode_applied,
            "notes": "拒答或无断言：按官方口径总分 1.0；A 口径无可评断言",
        }

    nli_raw = _judge_chat(
        judge_url,
        model,
        [{"role": "user", "content": _render_nli_prompt(context, statements)}],
    )
    nli = _extract_json(nli_raw)
    verdicts = nli.get("statements", []) if isinstance(nli, dict) else []
    by_stmt = {
        str(v.get("statement", "")).strip(): int(v.get("verdict", 0) or 0)
        for v in verdicts
        if isinstance(v, dict)
    }
    pairs: list[tuple[str, int]] = []
    detail: list[dict] = []
    for s in statements:
        v = by_stmt.get(s, 0)
        b = classify_statement(s)
        pairs.append((b, v))
        detail.append({"statement": s, "bucket": b, "verdict": v})

    agg = aggregate(pairs)
    return {
        "buckets": agg,
        "faith_all": agg["all"]["faith"],
        "faith_knowledge": agg["knowledge"]["faith"],
        "n_statements": len(statements),
        "answer_mode": answer_mode,
        "mode_applied": mode_applied,
        "detail": detail,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--eval-set", default=str(pathlib.Path.home() / "uai/data/eval/double_engine_eval_set.jsonl")
    )
    ap.add_argument(
        "--report", default=str(pathlib.Path.home() / "uai/reports/faithfulness_caliber_a.json")
    )
    ap.add_argument("--judge-url", default="http://127.0.0.1:8006")
    ap.add_argument("--model", default="Qwen3-8B")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument(
        "--samples-in-report",
        type=int,
        default=5,
        help="报告里保留多少条明细（含每条断言的桶与判定），便于人工抽查",
    )
    ap.add_argument(
        "--statements-lang",
        choices=("zh", "auto"),
        default="zh",
        help="断言输出语言：zh=要求中文（默认，修跨语言错配）；auto=官方原样（英文 prompt）",
    )
    ap.add_argument(
        "--answer-mode",
        choices=ANSWER_MODES,
        default="raw",
        help="判什么：raw=结论全文；derived_stripped=剔除分级/总分/置信度等系统派生字段（口径 C）",
    )
    args = ap.parse_args()

    path = pathlib.Path(args.eval_set).expanduser()
    if not path.is_file():
        print(f"[FATAL] 评测集不存在：{path}")
        return 2
    rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    judgeable = [r for r in rows if (r.get("conclusion") or "").strip() and r.get("context")]
    judgeable = judgeable[: args.limit]
    if not judgeable:
        print("[FATAL] 没有可判样本（需要 conclusion 与 context）")
        return 2

    t0 = time.time()
    per_sample: list[dict] = []
    faith_all_scores: list[float] = []
    faith_k_scores: list[float] = []
    bucket_totals = {b: {"n": 0, "supported": 0} for b in BUCKETS}
    for i, item in enumerate(judgeable, 1):
        try:
            res = _sample_of(
                item, args.judge_url, args.model, args.statements_lang, args.answer_mode
            )
        except Exception as exc:
            print(f"  [{i}/{len(judgeable)}] 判官失败：{type(exc).__name__}: {exc}")
            res = {"error": f"{type(exc).__name__}: {exc}"}
        res["request_id"] = item.get("request_id")
        res["organ"] = item.get("organ")
        per_sample.append(res)
        if res.get("faith_all") is not None:
            faith_all_scores.append(float(res["faith_all"]))
        if res.get("faith_knowledge") is not None:
            faith_k_scores.append(float(res["faith_knowledge"]))
        for b in BUCKETS:
            slot = (res.get("buckets") or {}).get(b) or {}
            bucket_totals[b]["n"] += int(slot.get("n") or 0)
            bucket_totals[b]["supported"] += int(slot.get("supported") or 0)
        print(
            f"  [{i}/{len(judgeable)}] all={res.get('faith_all')} "
            f"knowledge={res.get('faith_knowledge')} "
            f"n_k={(res.get('buckets') or {}).get('knowledge', {}).get('n')}"
        )

    def _mean(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 4) if xs else None

    total_stmt = sum(s["n"] for s in bucket_totals.values())
    report = {
        "caliber": "A（只算知识断言；影像所见与建议单列，不参与分母）",
        "why": (
            "结论 = 影像所见 + 知识建议；影像所见永远不在知识库里 → 原口径结构性偏低"
            "（实测 0.0906），不是模型在编。"
        ),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "eval_set": str(path),
        "eval_set_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "judge_url": args.judge_url,
        "model": args.model,
        "statements_lang": args.statements_lang,
        "answer_mode": args.answer_mode,
        "mode_applied": sum(1 for x in per_sample if x.get("mode_applied") is True),
        "n": len(per_sample),
        "faithfulness_original_caliber": _mean(faith_all_scores),
        "faithfulness_caliber_a": _mean(faith_k_scores),
        "n_measured_caliber_a": len(faith_k_scores),
        "bucket_totals": {
            b: {
                **v,
                "faith": round(v["supported"] / v["n"], 4) if v["n"] else None,
                "share": round(v["n"] / total_stmt, 4) if total_stmt else None,
            }
            for b, v in bucket_totals.items()
        },
        "failed": sum(1 for x in per_sample if x.get("error")),
        "classification_rules": {
            "image_terms": list(IMAGE_TERMS),
            "advice_terms": list(ADVICE_TERMS),
            "note": "规则透明可审计；命中 image 优先于 advice（避免建议句里的「结节」误分）",
        },
        "samples": [x for x in per_sample if x.get("detail")][: args.samples_in_report],
        "per_sample": per_sample
        if args.samples_in_report >= len(per_sample)
        else [
            {k: v for k, v in x.items() if k != "detail"} for x in per_sample
        ],
        "wall_s": round(time.time() - t0, 1),
    }
    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"== 原口径 faithfulness = {report['faithfulness_original_caliber']}（应接近 0.0906，用于自证）"
    )
    print(
        f"== A 口径（只算知识断言）= {report['faithfulness_caliber_a']}"
        f"（n={report['n_measured_caliber_a']}/{report['n']}）"
    )
    for b, v in report["bucket_totals"].items():
        print(f"   {b:<10} n={v['n']:>4} 占比={v['share']} 支持率={v['faith']}")
    print(f"报告：{out}  耗时 {report['wall_s']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
