"""T1c：faithfulness 生成评测（RAGAS 官方口径，LLM-as-judge）。

方法论（不自造）：
- RAGAS faithfulness：答案拆原子断言 → 逐条对照检索上下文 NLI 判定 →
  score = 支持断言数 / 总断言数（docs.ragas.io；prompt 照抄官方源码，
  见 scripts/prompts/ragas_faithfulness.py，Apache-2.0）
- 生成腿：QA 问题 → 等权 RRF(k=60) Top-5 上下文 → 8001 生成（仅基于给定知识）
- 判官：8002 vLLM（temperature=0，JSON 输出）
- 启发式交叉校验：答案中的数值必须能在上下文中找到（医疗数值幻觉零容忍）

门控对齐：faithfulness ≥ 95%（对应整体幻觉率 ≤ 5%）；
"verdict=0 且含分级/数值改写"的断言近似严重幻觉（≤ 0.5% 门控为未来扩展）。

用法（真机 ~/uai/src，需 8001/8002/8004 就绪）：
    PYTHONPATH=~/uai/src python scripts/t1_eval_faithfulness.py \
        --golden-set data/golden/kb_qa_golden_v1.json \
        --sample 100 --output reports/t1_faithfulness_report.json
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

from prompts.ragas_faithfulness import (
    NLI_STATEMENT_EXAMPLE,
    NLI_STATEMENT_INSTRUCTION,
    STATEMENT_GENERATOR_EXAMPLE,
    STATEMENT_GENERATOR_INSTRUCTION,
)
from t1_eval_retrieval import bm25_topk, cosine_topk, embed_texts, rrf_fuse

_CONTEXT_TOP_K = 5
_CANDIDATE_DEPTH = 100


def _chat(url: str, model: str, messages: list[dict], temperature: float = 0.0) -> str:
    body = json.dumps(
        {"model": model, "messages": messages, "temperature": temperature}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.load(resp)
    return data["choices"][0]["message"]["content"]


def _extract_json(text: str) -> dict | list:
    """从判官输出中提取 JSON（分层容错：围栏 → json_repair）。

    7B 判官的 JSON 常不严格（断言内含未转义引号、尾逗号），真机首轮实测
    84/100 例因纯 json.loads 失败——json_repair 是业界标准修复库。
    """
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else text
    match = re.search(r"[\[{].*[\]}]", raw, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON in judge output: {text[:200]!r}")
    snippet = match.group(0)
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        import json_repair

        repaired = json_repair.loads(snippet)
        if not isinstance(repaired, (dict, list)):
            raise ValueError(
                f"judge output unrepairable: {text[:200]!r}"
            ) from None
        return repaired


_GENERATION_SYSTEM = (
    "你是一名超声医学知识助手。请严格只依据【参考知识】回答用户问题；"
    "若参考知识不足以回答，请明确说明『根据现有知识无法回答』。"
    "禁止编造参考知识之外的数值、分级或结论。"
)

_GENERATION_USER_TMPL = "【参考知识】\n{context}\n\n【问题】\n{question}"


def _render_statement_prompt(question: str, answer: str) -> str:
    ex = STATEMENT_GENERATOR_EXAMPLE
    return (
        f"{STATEMENT_GENERATOR_INSTRUCTION}\n\n"
        f"Example:\nQuestion: {ex['question']}\nAnswer: {ex['answer']}\n"
        f"Output(JSON): {json.dumps({'statements': ex['statements']}, ensure_ascii=False)}\n\n"
        f"Question: {question}\nAnswer: {answer}\n"
        "Output(JSON only):"
    )


def _render_nli_prompt(context: str, statements: list[str]) -> str:
    ex = NLI_STATEMENT_EXAMPLE
    return (
        f"{NLI_STATEMENT_INSTRUCTION}\n\n"
        f"Example:\nContext: {ex['context']}\n"
        f"Statements: {json.dumps(ex['statements'], ensure_ascii=False)}\n"
        f"Output(JSON): {json.dumps({'statements': ex['verdicts']}, ensure_ascii=False)}\n\n"
        f"Context: {context}\n"
        f"Statements: {json.dumps(statements, ensure_ascii=False)}\n"
        "Output(JSON only):"
    )


_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%?")
# markdown/纯文本列表序号（"1." "2、"）不是内容性数值，不算幻觉（真机首轮实测误杀）
_LIST_INDEX_RE = re.compile(r"^\d+[.、)]$")
# 元语句：拒答/说明性话术不是对事实的断言（RAGAS 空断言=1.0 口径的推广）
_META_STATEMENT_RE = re.compile(
    r"^(根据|基于|以上|参考知识|无法回答|无法确定|抱歉)"
)


def heuristic_numeric_check(answer: str, context: str) -> tuple[bool, list[str]]:
    """答案中的内容性数值必须出现在上下文中（医疗数值幻觉交叉校验）。"""
    missing = []
    for n in _NUMBER_RE.findall(answer):
        if _LIST_INDEX_RE.match(n):
            continue
        if n not in context and n.rstrip("%") not in context:
            missing.append(n)
    return (not missing), missing


def _is_refusal(answer: str) -> bool:
    """生成腿明确拒答（无法基于参考知识回答）→ 忠实度记 1.0。"""
    return "无法回答" in answer or "无法确定" in answer


def _filter_meta_statements(statements: list[str]) -> list[str]:
    """剔除元语句（拒答话术被判官拆成'断言'的噪声）。"""
    return [s for s in statements if not _META_STATEMENT_RE.match(s.strip())]


def main() -> int:
    parser = argparse.ArgumentParser(description="faithfulness 评测（RAGAS 口径）")
    parser.add_argument("--golden-set", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--sample", type=int, default=100)
    parser.add_argument("--generator-url", default="http://127.0.0.1:8001")
    parser.add_argument("--judge-url", default="http://127.0.0.1:8002")
    parser.add_argument("--judge-model", default="Qwen2.5-7B-Instruct")
    args = parser.parse_args()

    golden = json.loads(Path(args.golden_set).read_text(encoding="utf-8"))
    corpus: list[dict] = golden["corpus"]
    queries: list[dict] = golden["queries"][: args.sample]
    print(f"corpus={len(corpus)} queries={len(queries)}")

    # 检索：等权 RRF(k=60) Top-5（与生产融合方式同构）
    corpus_texts = [c["text"] for c in corpus]
    query_texts = [q["query_text"] for q in queries]
    print("[1/3] 检索 Top-5...")
    corpus_vecs = embed_texts(corpus_texts, "http://127.0.0.1:8004")
    query_vecs = embed_texts(query_texts, "http://127.0.0.1:8004")
    dense_pairs = [[(i, 1.0) for i, _ in h] for h in cosine_topk(query_vecs, corpus_vecs, _CANDIDATE_DEPTH)]
    fused = rrf_fuse(dense_pairs, bm25_topk(query_texts, corpus_texts, _CANDIDATE_DEPTH), _CANDIDATE_DEPTH, k_const=60)
    # rrf_fuse 返回 (idx, score) 对 → 归一化为 bare int（类型防御）
    fused = [[i for i, _s in ranking] for ranking in fused]

    results: list[dict] = []
    t_start = time.perf_counter()
    for qi, q in enumerate(queries):
        top5 = fused[qi][:_CONTEXT_TOP_K]
        context = "\n\n".join(corpus_texts[i] for i in top5)
        item: dict = {
            "query_id": q["query_id"],
            "question": q["query_text"],
            "context_chunk_ids": [corpus[i]["chunk_id"] for i in top5],
        }
        try:
            # ① 生成
            answer = _chat(
                args.generator_url,
                "qwen2.5-7b-instruct",
                [
                    {"role": "system", "content": _GENERATION_SYSTEM},
                    {
                        "role": "user",
                        "content": _GENERATION_USER_TMPL.format(
                            context=context, question=q["query_text"]
                        ),
                    },
                ],
            )
            item["answer"] = answer

            # ② RAGAS 断言分解
            stmt_out = _extract_json(
                _chat(
                    args.judge_url,
                    args.judge_model,
                    [
                        {
                            "role": "user",
                            "content": _render_statement_prompt(
                                q["query_text"], answer
                            ),
                        }
                    ],
                )
            )
            statements = stmt_out.get("statements", []) if isinstance(stmt_out, dict) else []
            statements = _filter_meta_statements(statements)
            item["num_statements"] = len(statements)

            # ③ 拒答与空断言：官方口径（无断言 → score=1.0）
            if _is_refusal(answer) or not statements:
                item["faithfulness"] = 1.0
                item["no_statements"] = True
                if _is_refusal(answer):
                    item["refusal"] = True
            else:
                nli_out = _extract_json(
                    _chat(
                        args.judge_url,
                        args.judge_model,
                        [
                            {
                                "role": "user",
                                "content": _render_nli_prompt(context, statements),
                            }
                        ],
                    )
                )
                verdicts = (
                    nli_out.get("statements", [])
                    if isinstance(nli_out, dict)
                    else []
                )
                supported = sum(
                    1
                    for v in verdicts
                    if isinstance(v, dict) and v.get("verdict") == 1
                )
                item["faithfulness"] = (
                    supported / len(statements) if statements else 1.0
                )
                item["verdicts"] = verdicts

            # ④ 启发式交叉校验
            ok, missing = heuristic_numeric_check(answer, context)
            item["numeric_consistent"] = ok
            item["missing_numbers"] = missing
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
        results.append(item)
        if (qi + 1) % 10 == 0:
            done = [r for r in results if "faithfulness" in r]
            cur = sum(r["faithfulness"] for r in done) / len(done) if done else 0
            print(f"  {qi + 1}/{len(queries)} faithfulness_so_far={cur:.4f}")

    elapsed = time.perf_counter() - t_start
    scored = [r for r in results if "faithfulness" in r]
    failed = [r for r in results if "error" in r]
    scores = [r["faithfulness"] for r in scored]
    scores_sorted = sorted(scores)
    n = len(scores)
    numeric_ok = sum(1 for r in scored if r.get("numeric_consistent"))

    report = {
        "golden_set": args.golden_set,
        "sample": len(queries),
        "scored": n,
        "failed": len(failed),
        "context_top_k": _CONTEXT_TOP_K,
        "methodology": "RAGAS faithfulness（prompt 照抄官方源码 vibrantlabsai/ragas@main collections/faithfulness，Apache-2.0）",
        "gate": {"faithfulness_target": 0.95, "对应": "整体幻觉率 ≤ 5%"},
        "results_summary": {
            "mean_faithfulness": sum(scores) / n if n else None,
            "median": scores_sorted[n // 2] if n else None,
            "min": min(scores) if scores else None,
            "pct_below_0.5": (sum(1 for s in scores if s < 0.5) / n) if n else None,
            "zero_score_count": sum(1 for s in scores if s == 0),
            "no_statement_count": sum(1 for r in scored if r.get("no_statements")),
            "refusal_count": sum(1 for r in scored if r.get("refusal")),
            "numeric_consistency_rate": numeric_ok / n if n else None,
        },
        "total_latency_s": round(elapsed, 1),
        "results": results,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"完成: scored={n} failed={len(failed)} elapsed={elapsed:.0f}s")
    print(json.dumps(report["results_summary"], ensure_ascii=False, indent=2))
    print(f"报告写出: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
