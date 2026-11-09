"""端到端验证：多套权威语料并存时的**口径分歧并列**（2026-11-09）。

## 这个脚本要证明什么

用户的原话需求：

> 某些医院以 A 语料为权威，某些医院以 B 语料为权威 …… 若医院觉得两份语料都得启用，
> 里面的口径不一样，以什么为准？还是说都放进去，但标注语料来源？并且在提示词方面下手？

这个脚本不跑单元测试替身，而是**打真实的 RAG 检索 + 真实 LLM**，走完整链路：

    knowledge.query(source_sets=...)  →  dense(Qdrant) + sparse(BM25) 双过滤
      →  specialist._build_prompt（逐条标来源 + 并列块）
      →  vLLM 生成
      →  抽结论，看它有没有并列

## 五组对照（每一组回答一个具体问题）

| # | 场景 | 要看到什么 |
|---|---|---|
| 1 | 全开（默认） | 两套语料的证据**都进来**，并列块出现 |
| 2 | 只启 C-TIRADS | 只有 C-TIRADS 的证据，**没有**并列块（单来源不该报警） |
| 3 | 只启诊疗规范 | 只有诊疗规范的证据 |
| 4 | 拆掉并列块（``rag_source_conflict_render=false``） | 同样两套证据，但 prompt 里**没有**并列 → 用于对照"提示词层到底有没有用" |
| 5 | 用一句**只有单套语料**能答的问题 | 确认不加并列块时 prompt 逐字不变（零噪声） |

## 用法（远端）

    PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/e2e_source_conflict_demo.py \
        --out reports/e2e_source_conflict_20261109.json

不做任何断言（它是一次**观察**，不是测试）：断言在
``tests/unit/rag/test_source_conflict.py`` 与
``tests/unit/rag/test_source_set_config.py`` 里。这里只把真实输出落盘，供报告引用。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 演示脚本要能独立跑：所有依赖在这里按生产装配方式建起来
from app.config.database import AsyncSessionLocal
from app.config.settings import settings
from app.knowledge.source_set import label_of, source_set_of
from app.rag.protocols import RetrievalRequest
from app.rag.source_conflict import (
    detect_conflicts,
    render,
    summary_of,
)

#: 用户给的典型问题：**4a 类 12mm 要不要穿刺**。
#:
#: 为什么它是最好的检验题：C-TIRADS 说 >15 mm 才建议 FNA（12 mm 不满足），
#: 诊疗规范说 >1cm 且有恶性征象就推荐（12 mm 满足）。**答案会相反**。
#: 如果系统只给一套，"要不要穿刺"就变成掷硬币。
QUESTION = "甲状腺结节 4a 类，大小 12mm，边界清晰，无微钙化。需要做细针穿刺吗？"

#: 对照用的单来源问题（只该命中 C-TIRADS 的分级方法描述）。
SINGLE_SOURCE_QUESTION = "C-TIRADS 计数法里，哪些超声特征算可疑恶性征象？"


def prompt_template_head() -> str:
    """读专科 system prompt（与 ``_build_prompt`` 的第一段同源）。"""
    return (
        Path(__file__).resolve().parents[1]
        / "app" / "agents" / "prompts" / "thyroid.md"
    ).read_text(encoding="utf-8")


async def _build_rag():
    """按 ``app/main.py`` 的生产装配方式构建 RAG 服务（不启动 HTTP）。"""
    from app.embedding.bge_m3 import BgeM3MedicalService
    from app.embedding.index_factory import create_index_client_from_settings
    from app.embedding.ultrasound_clip import UltrasoundClipService
    from app.knowledge.udt_parser import UDTParser
    from app.knowledge.vocabulary import UDAFVocabulary
    from app.rag.bm25_fallback import BM25Fallback, BM25Index
    from app.rag.cache import RetrievalCache
    from app.rag.query_enhancer import QueryEnhancer
    from app.rag.reranker import BgeRerankerService, HeuristicReranker
    from app.rag.retriever import Retriever
    from app.rag.service import RAGServiceImpl

    vector_client = await create_index_client_from_settings()
    embedding_svc = BgeM3MedicalService(
        base_url=f"http://{settings.bge_m3_host}:{settings.bge_m3_port}"
    )
    clip_svc = UltrasoundClipService(
        base_url=(
            f"http://{settings.ultrasound_clip_host}:{settings.ultrasound_clip_port}"
        )
    )
    reranker_svc = BgeRerankerService(
        base_url=f"http://{settings.bge_reranker_host}:{settings.bge_reranker_port}",
        model=settings.bge_reranker_model,
        timeout_seconds=settings.bge_reranker_timeout_seconds,
        health_check_timeout_seconds=(
            settings.bge_reranker_health_check_timeout_seconds
        ),
    )
    bm25 = BM25Index(session_factory=AsyncSessionLocal)
    await bm25.ensure_built(settings.rag_kb_version)
    return RAGServiceImpl(
        query_enhancer=QueryEnhancer(
            vocabulary=UDAFVocabulary(), udt_parser=UDTParser(), llm_enhancer=None
        ),
        retriever=Retriever(
            vector_client=vector_client,
            embedding_service=embedding_svc,
            ultrasound_clip=clip_svc,
            bm25_index=bm25,
            session_factory=AsyncSessionLocal,
        ),
        reranker=reranker_svc,
        heuristic_reranker=HeuristicReranker(),
        bm25_fallback=BM25Fallback(bm25),
        kb_version=settings.rag_kb_version,
        cache=RetrievalCache(),
    )


async def _retrieve(
    rag, question: str, source_sets: list[str] | None, top_k: int = 12
):
    """检索并返回 ``(chunks, 检索元数据)``。"""
    result = await rag.retrieve(
        RetrievalRequest(
            query=question,
            organ="thyroid",
            top_k=top_k,
            use_query_enhancement=True,
            use_reranking=False,       # 生产 full 档实测精排负增益，关掉
            use_bm25_fallback=True,
            use_multimodal=False,
            min_score_threshold=0.0,   # 演示要看全貌，不截断
            source_sets=source_sets,
        )
    )
    chunks = [sc.chunk for sc in result.chunks]
    meta = {
        "n_chunks": len(chunks),
        "retrieval_method": result.retrieval_method.value,
        "triggered_bm25_fallback": result.triggered_bm25_fallback,
        "latency_ms": round(result.latency_ms, 1),
        "dropped_missing_row": result.dropped_missing_row,
        "source_sets_hit": sorted(
            {
                source_set_of(source_doc_id=c.source_doc_id, metadata=c.metadata)
                for c in chunks
            }
        ),
        "chunks": [
            {
                "chunk_id": c.chunk_id,
                "source_doc_id": c.source_doc_id,
                "source_set": source_set_of(
                    source_doc_id=c.source_doc_id, metadata=c.metadata
                ),
                "score": round(float(c.score or 0.0), 4),
                "head": (c.content or "")[:110].replace("\n", " "),
            }
            for c in chunks
        ],
    }
    return chunks, meta


async def _generate(prompt: str, base_url: str | None = None) -> str:
    """直接打 vLLM（OpenAI 兼容端点），返回文本。

    Args:
        base_url: 显式端点（``http://host:port``）。``None`` 时用 settings。
            需要它是参数、而不是每次都读 settings：实测这台共享服务器上
            生成服务会被别的任务换端口（8002 → 8007 → …），
            演示脚本必须能在不改配置的前提下指向当前活着的那个。

    Raises:
        RuntimeError: 端点不可达。**不静默返回空串** —— 空答案会被误读成
            "模型没并列"，而那其实是"模型没跑"。
    """
    import httpx

    base = base_url or f"http://{settings.vllm_host}:{settings.vllm_port}"
    url = f"{base}/v1/chat/completions"
    # 关思考：与 `app/inference/vllm.py` 的生产做法**逐字一致**
    # （``continue_final_message`` + 空思考块预填）。
    # 不这么做的话，II-Medical-8B 的 chat template 里没有 ``enable_thinking`` 分支，
    # token 全花在 ``<think>`` 内、``finish_reason=length``，正文为空 ——
    # 实测第一次跑就是这样：7 段输出全是思考过程，看不到结论。
    from app.inference.vllm import NO_THINK_PREFILL

    payload = {
        "model": settings.vllm_default_model or "II-Medical-8B",
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": NO_THINK_PREFILL},
        ],
        "continue_final_message": True,
        "add_generation_prompt": False,
        "temperature": 0.1,
        "max_tokens": 900,
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
        except Exception as exc:
            raise RuntimeError(
                f"生成端点不可达：{url}（{type(exc).__name__}: {exc}）。"
                f"用 --llm-base-url 指向当前实际在跑的 vLLM。"
            ) from exc
        data = resp.json()
    return data["choices"][0]["message"]["content"] or ""


async def run_case(
    name: str,
    question: str,
    source_sets: list[str] | None,
    *,
    render_conflict: bool = True,
    llm_base_url: str | None = None,
    top_k: int = 12,
) -> dict:
    """跑一个完整用例：检索 → 组 prompt → 生成。"""
    prompt_template = prompt_template_head()

    rag = await _build_rag()
    chunks, meta = await _retrieve(rag, question, source_sets, top_k)
    report = detect_conflicts(chunks)
    conflict_block = render(report) if render_conflict else ""

    # 与 specialist._build_prompt **同一渲染口径**（逐条标来源）
    lines = [prompt_template, "", f"# 检索到的知识（top {len(chunks)}）"]
    for idx, chunk in enumerate(chunks, start=1):
        ss = source_set_of(source_doc_id=chunk.source_doc_id, metadata=chunk.metadata)
        ss_name, ss_org = label_of(ss)
        lines.append(
            f"[{idx}]（来源：《{ss_name}》/{ss_org}；chunk_id={chunk.chunk_id}）"
            f" {chunk.content}"
        )
    lines.append("")
    if conflict_block:
        lines.extend([conflict_block, ""])
    lines.extend(["# 用户查询", question])
    prompt = "\n".join(lines)

    # 生成失败**不中断整轮**：前 4 组要证明的是"检索+组装"层，
    # 第 5 组才需要模型。把它记成 error 而不是伪造空答案。
    answer, gen_error = "", ""
    try:
        answer = await _generate(prompt, llm_base_url)
    except RuntimeError as exc:
        gen_error = str(exc)
    return {
        "generation_error": gen_error,
        "case": name,
        "question": question,
        "requested_source_sets": source_sets,
        "render_conflict_enabled": render_conflict,
        "retrieval": meta,
        "conflict": summary_of(report),
        "conflict_block": conflict_block,
        "prompt_chars": len(prompt),
        "prompt_has_conflict_block": bool(conflict_block),
        "answer": answer,
    }


async def main_async(args) -> int:
    print(f"KB={settings.rag_kb_version}  collection={settings.rag_collection}")
    cases = []

    print("\n[1/6] 全开：两套语料并存 → 应出现并列块")
    cases.append(await run_case("all_enabled", QUESTION, None, llm_base_url=args.llm_base_url))

    print("[2/6] 只启 C-TIRADS → 单来源，不应有并列块")
    cases.append(await run_case("only_ctirads", QUESTION, ["ctirads_2021"], llm_base_url=args.llm_base_url))

    print("[3/6] 只启诊疗规范 → 单来源，不应有并列块")
    cases.append(await run_case("only_norm2018", QUESTION, ["norm_2018"], llm_base_url=args.llm_base_url))

    print("[4/6] 两套都启但**关掉并列渲染** → 对照'提示词层有没有用'")
    cases.append(
        await run_case(
            "all_enabled_no_render", QUESTION, None,
            render_conflict=False, llm_base_url=args.llm_base_url,
        )
    )

    print("[5/6] 单来源问题 → 确认无分歧时 prompt 不被污染")
    cases.append(
        await run_case(
            "single_source_question", SINGLE_SOURCE_QUESTION, None,
            llm_base_url=args.llm_base_url,
        )
    )

    print("[6/6] 加深检索（top_k=60）→ 验证能否绕过 5 条输出上限")
    # ⚠️ 预期**仍然 detected=False**：`app/rag/service.py` 在无精排时用
    # `settings.rag_rerank_output_top_k`（=5）截断输出，`request.top_k` 只影响
    # 检索深度、不影响最终条数。这一组就是为了把这条**上限**显式测出来，
    # 而不是让读者以为"加大 top_k 就能解决"。
    cases.append(
        await run_case(
            "all_enabled_top60", QUESTION, None,
            llm_base_url=args.llm_base_url, top_k=60,
        )
    )

    # ----------------------------------------------------------------
    # 【旁路演示】直接用 BM25 深检（top_k=60）+ 渲染，**绕过 service 层的 5 条上限**，
    # 让"并列块真的长什么样"可以被看见。
    #
    # 为什么必须单独一组：上面 6 组证明的是"按当前生产配置，这条冲突看不见"，
    # 那是**真实缺陷**，不是演示失败。但如果只给那 6 组，读者会以为
    # "并列能力没做出来"。这一组把两种事实分开摆：
    #   · 能力：**做出来了**（本组能渲染出完整并列块）；
    #   · 生效：**当前 top-5 上不触发**（前 6 组）。
    # ----------------------------------------------------------------
    print("\n[旁路] BM25 深检 top_k=60 → 展示并列块的完整形态")
    from app.config.database import AsyncSessionLocal
    from app.rag.bm25_fallback import BM25Index

    bm25 = BM25Index(session_factory=AsyncSessionLocal)
    await bm25.ensure_built(settings.rag_kb_version)
    deep_hits = bm25.search(QUESTION, top_k=60, organ_filter="thyroid")
    deep_chunks = [h.chunk for h in deep_hits]
    deep_report = detect_conflicts(deep_chunks)
    deep_block = render(deep_report)
    # 第一条冲突块对应的 prompt 片段（与 specialist 同一渲染口径）
    deep_prompt = "\n".join(
        [
            prompt_template_head(),
            "",
            f"# 检索到的知识（top {len(deep_chunks)}）",
            *[
                f"[{i}]（来源：《{label_of(source_set_of(source_doc_id=c.source_doc_id, metadata=c.metadata))[0]}》"
                f"/{label_of(source_set_of(source_doc_id=c.source_doc_id, metadata=c.metadata))[1]}；"
                f"chunk_id={c.chunk_id}） {c.content}"
                for i, c in enumerate(deep_chunks, start=1)
            ],
            "",
            deep_block,
            "",
            "# 用户查询",
            QUESTION,
        ]
    )
    deep_answer, deep_gen_error = "", ""
    try:
        deep_answer = await _generate(deep_prompt, args.llm_base_url)
    except RuntimeError as exc:
        deep_gen_error = str(exc)

    # 【对照】同样的 60 块证据，但**去掉并列块** —— 这是判断
    # "提示词层到底有没有用"的唯一干净办法：证据完全一样，只差那一段。
    ctrl_prompt = "\n".join(
        [
            prompt_template_head(),
            "",
            f"# 检索到的知识（top {len(deep_chunks)}）",
            *[
                f"[{i}]（来源：《{label_of(source_set_of(source_doc_id=c.source_doc_id, metadata=c.metadata))[0]}》"
                f"/{label_of(source_set_of(source_doc_id=c.source_doc_id, metadata=c.metadata))[1]}；"
                f"chunk_id={c.chunk_id}） {c.content}"
                for i, c in enumerate(deep_chunks, start=1)
            ],
            "",
            "# 用户查询",
            QUESTION,
        ]
    )
    ctrl_answer, ctrl_gen_error = "", ""
    try:
        ctrl_answer = await _generate(ctrl_prompt, args.llm_base_url)
    except RuntimeError as exc:
        ctrl_gen_error = str(exc)

    out = {
        "bypass_deep_retrieval": {
            "why": "绕过 app/rag/service.py 的 rag_rerank_output_top_k=5 截断，"
                   "展示并列块的实际形态",
            "bm25_top_k": 60,
            "n_chunks": len(deep_chunks),
            "conflict": summary_of(deep_report),
            "conflict_block": deep_block,
            "prompt_chars": len(deep_prompt),
            "generation_error": deep_gen_error,
            "answer": deep_answer,
            "control_same_evidence_no_block": {
                "why": "同样 60 块证据、同一模型、同一温度，唯一差别是没有并列块",
                "prompt_chars": len(ctrl_prompt),
                "generation_error": ctrl_gen_error,
                "answer": ctrl_answer,
            },
        },
        "kb_version": settings.rag_kb_version,
        "collection": settings.rag_collection,
        "llm": settings.vllm_default_model or "II-Medical-8B",
        "cases": cases,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"\n已写 → {args.out}")

    # 终端摘要（报告里要引用的就是这几行）
    print("\n" + "=" * 78)
    for c in cases:
        conf = c["conflict"]
        print(
            f"{c['case']:28s} chunks={c['retrieval']['n_chunks']:2d} "
            f"sets={','.join(c['retrieval']['source_sets_hit']):34s} "
            f"detected={conf['detected']!s:5s} block={c['prompt_has_conflict_block']!s:5s} "
            f"gen={'OK' if c['answer'] else 'FAIL'}"
        )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="多套语料口径分歧端到端演示")
    ap.add_argument(
        "--llm-base-url",
        default=None,
        help="vLLM 端点（如 http://127.0.0.1:8002）；省略则用 settings",
    )
    ap.add_argument(
        "--out",
        default="reports/e2e_source_conflict_20261109.json",
        help="结果 JSON 落盘路径",
    )
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
