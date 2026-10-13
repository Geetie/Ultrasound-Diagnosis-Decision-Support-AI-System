"""L2 忠实度判官：对 Langfuse 评测集跑 **RAGAS 官方 faithfulness**（19 号门控的 L2 层）。

为什么要单独一个脚本
--------------------
`scripts/t1_eval_faithfulness.py` 是**端到端**评测（问题 → 检索 → 生成 → 判官），判的是
"生成腿"；而评测集里已经有**真实判读**的结论与它当时的**检索上下文**（采集器从
`diagnosis_results.knowledge_ids` → `knowledge_chunks` 取回）。本脚本只做**判**这一步：

    结论（答案）→ 拆原子断言 → 逐条对照当时的检索上下文做 NLI → score = 支持数 / 总数

**prompt 与判分公式一律复用官方原文**（`scripts/prompts/ragas_faithfulness.py`，
Apache-2.0，摘自 ragas 官方仓库），渲染/容错/数值启发式**直接复用**
`scripts/t1_eval_faithfulness.py` 的既有实现（`_render_statement_prompt` /
`_render_nli_prompt` / `_extract_json` / `_filter_meta_statements`）—— 不重写、不自造，
避免"两套判官口径"。

关于 **答案相关性（answer_relevance ≥0.90）**：RAGAS 官方 `answer_relevancy` 的 prompt
**本仓库未收录**（`scripts/prompts/` 只有 faithfulness 一份），所以本脚本**不算相关性**，
报告里如实写 `answer_relevance: null`；门控据此记 `not_measured`（**不按 0 判红**）。
要补这一层，先抄官方 prompt 再实现，别自造。

用法（服务器，需判官服务就绪）：
    PYTHONPATH=~/uai/src python scripts/langfuse/judge_eval_set.py \
        --eval-set ~/uai/data/eval/double_engine_eval_set.jsonl \
        --report ~/uai/reports/langfuse_eval_judge.json \
        --judge-url http://127.0.0.1:8006 --model Qwen3-8B --limit 24

退出码：0 判完（含未达标）；1 无可判样本（缺上下文）；2 判官不可用/输入错。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# 复用既有判官实现（同目录兄弟脚本）——`scripts/` 加到 sys.path 才能 import
_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from t1_eval_faithfulness import (  # noqa: E402  (需先改 sys.path)
    _extract_json,
    _filter_meta_statements,
    _is_refusal,
    _render_nli_prompt,
    _render_statement_prompt,
)

#: 判官单次输出上限。**必须显式给**：`t1_eval_faithfulness._chat` 不带 max_tokens，
#: 而判官（Qwen3-8B）会先吐 `<think>` 再吐 JSON —— 实测 24 条里 **13 条**因输出被截断
#: 而 `_extract_json` 报 "no JSON"（假失败：JSON 明明在，只是没有闭合括号）。
_MAX_TOKENS = 2048


def _judge_chat(url: str, model: str, messages: list[dict], max_tokens: int = _MAX_TOKENS) -> str:
    """带 max_tokens 的判官调用（其余与 `t1_eval_faithfulness._chat` 同款：temperature=0）。"""
    import urllib.request

    body = json.dumps(
        {"model": model, "messages": messages, "temperature": 0.0, "max_tokens": max_tokens}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.load(resp)
    return data["choices"][0]["message"]["content"]


def _answer_of(item: dict, mode: str) -> str:
    """取"被判的答案"。

    `raw`：结论全文。
    `reasoning`：只用**临床理由/叙述**部分 —— 结论形如
        「甲状腺 · TR3 — 分级: TR3 总分: 3 置信度: 0.85 理由: 实性(3) + 低回声(2) …」，
    其中「分级/总分/置信度」是**系统派生出的决策**（一个具体结节的分类），检索到的
    指南块里**不会有**这句话；RAGAS faithfulness 判的是"答案能否由上下文推出"，
    把派生决策一起判会**必然**得低分（这是口径问题，不是幻觉证据）。
    """
    text = (item.get("conclusion") or "").strip()
    if mode == "raw":
        return text
    for marker in ("理由:", "理由："):
        if marker in text:
            return text.split(marker, 1)[1].strip() or text
    return text


def _faithfulness_of(item: dict, judge_url: str, model: str, answer_mode: str = "raw") -> dict:
    """单条样本的 faithfulness（RAGAS 官方两阶段；无断言时官方记 1.0）。"""
    question = (item.get("clinical_text") or "").strip() or "（无临床文本；按影像判读）"
    answer = _answer_of(item, answer_mode)
    context = "\n\n".join(item.get("context") or [])
    if not answer or not context:
        return {"score": None, "reason": "缺答案或缺检索上下文"}

    # ① 答案 → 原子断言
    raw = _judge_chat(
        judge_url,
        model,
        [{"role": "user", "content": _render_statement_prompt(question, answer)}],
    )
    data = _extract_json(raw)
    statements = [
        str(s).strip()
        for s in (data.get("statements", []) if isinstance(data, dict) else data)
        if str(s).strip()
    ]
    statements = _filter_meta_statements(statements)
    if not statements:
        # 官方口径：分解不出断言 → score = 1.0；这里单独标出便于审阅
        return {"score": 1.0, "statements": 0, "note": "no_statements_official_score_1"}

    # ② 逐条 NLI 判定
    raw2 = _judge_chat(
        judge_url,
        model,
        [{"role": "user", "content": _render_nli_prompt(context, statements)}],
    )
    vdata = _extract_json(raw2)
    # ⚠️ 判官把逐条判定放在 **`statements`** 键下（元素含 `verdict`），不是 `verdicts` ——
    # 与 `t1_eval_faithfulness.py:240-253` 的读法**逐字一致**。首版写 `vdata["verdicts"]`
    # 拿不到任何元素 → 静默算成 `0/len(statements)` = **假 0 分**（12/12 全 0 的真相）。
    verdicts = (
        vdata.get("statements", [])
        if isinstance(vdata, dict)
        else (vdata if isinstance(vdata, list) else [])
    )
    supported = sum(
        1 for v in verdicts if isinstance(v, dict) and v.get("verdict") == 1
    )
    # 仪器自检：有断言却一条 verdict 都没解析出来 → 结构对不上，**必须报错**而不是给 0 分
    if statements and not verdicts:
        return {
            "score": None,
            "statements": len(statements),
            "error": "verdict_parse_empty（判官返回结构不含逐条判定 → 不当作 0 分）",
            "raw_head": raw2[:200],
        }
    score = supported / len(statements) if statements else 1.0
    unsupported = [
        str(v.get("statement", ""))[:80]
        for v in verdicts
        if isinstance(v, dict) and v.get("verdict") == 0
    ]
    return {
        "score": round(score, 4),
        "statements": len(statements),
        "supported": supported,
        "unsupported_samples": unsupported[:3],
        "refusal": _is_refusal(answer),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--eval-set", default=str(Path.home() / "uai/data/eval/double_engine_eval_set.jsonl")
    )
    ap.add_argument(
        "--report", default=str(Path.home() / "uai/reports/langfuse_eval_judge.json")
    )
    ap.add_argument("--judge-url", default="http://127.0.0.1:8006")
    ap.add_argument("--model", default="Qwen3-8B")
    ap.add_argument("--limit", type=int, default=0, help=">0 时只判前 N 条（冒烟/控时）")
    ap.add_argument("--since", default=None, help="只判 created_at >= 该时刻的样本（同门控口径）")
    ap.add_argument(
        "--answer-mode",
        choices=("raw", "reasoning"),
        default="raw",
        help="raw=判结论全文（口径 A）；reasoning=只判「理由:」之后的临床叙述（口径 B，"
        "把系统派生的分级/总分/置信度排除在外 —— 见 _answer_of 的说明）",
    )
    args = ap.parse_args()

    path = Path(args.eval_set).expanduser()
    if not path.is_file():
        print(f"缺评测集文件：{path}（先跑 collect_eval_set.py）")
        return 2
    rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    if args.since:
        rows = [r for r in rows if str(r.get("created_at") or "") >= args.since]
    judgeable = [r for r in rows if (r.get("conclusion") or "").strip() and r.get("context")]
    skipped_no_context = len(rows) - len(judgeable)
    if args.limit:
        judgeable = judgeable[: args.limit]
    if not judgeable:
        print(f"没有可判样本（{len(rows)} 条中 0 条同时有结论与检索上下文）—— 没样本不等于达标")
        return 1

    # 判官可用性预检（fail-fast：判官不通时不要跑一串 500 当成"低分"）
    try:
        _judge_chat(args.judge_url, args.model, [{"role": "user", "content": "ping"}], max_tokens=8)
    except Exception as exc:
        print(f"判官不可用：{args.judge_url} model={args.model} → {type(exc).__name__}: {exc}")
        return 2

    per_sample: list[dict] = []
    scores: list[float] = []
    t0 = time.time()
    for i, item in enumerate(judgeable, 1):
        try:
            result = _faithfulness_of(item, args.judge_url, args.model, args.answer_mode)
        except Exception as exc:
            # 单条失败必须**当场可见**（零静默）：既进报告，也立刻打屏一行
            # （棘轮 `test_no_silent_catch` 只认 except 体内的留痕/raise/error=，光赋值不给过）
            print(f"  [{i}/{len(judgeable)}] 判官失败：{type(exc).__name__}: {exc}")
            result = {"score": None, "error": f"{type(exc).__name__}: {exc}"}
        result["request_id"] = item.get("request_id")
        result["organ"] = item.get("organ")
        per_sample.append(result)
        if result.get("score") is not None:
            scores.append(float(result["score"]))
        print(f"  [{i}/{len(judgeable)}] {item.get('organ')} score={result.get('score')}"
              f"{' err=' + result['error'] if result.get('error') else ''}")

    mean = round(sum(scores) / len(scores), 4) if scores else None
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "engine": "ragas_faithfulness(official prompts, scripts/prompts/ragas_faithfulness.py)",
        "judge_url": args.judge_url,
        "model": args.model,
        "metric_scope": "faithfulness only（answer_relevancy 的官方 prompt 本仓库未收录 → null）",
        "answer_mode": args.answer_mode,
        "eval_set": str(path),
        "n": len(judgeable),
        "skipped_no_context": skipped_no_context,
        "faithfulness": mean,
        "answer_relevance": None,
        "failed": sum(1 for x in per_sample if x.get("score") is None),
        "wall_s": round(time.time() - t0, 1),
        "per_sample": per_sample,
    }
    rp = Path(args.report).expanduser()
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"== faithfulness（RAGAS 官方口径）：{mean}（n={len(scores)}，缺上下文跳过 "
          f"{skipped_no_context}，失败 {report['failed']}）报告：{rp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
