"""为**链路冒烟**建立最小的知识库基线（不是真实知识库！）。

用法（在 `~/uai/src` 下，已 source `~/uai/.env`）：
    ~/uai/venv/bin/python ~/server_bootstrap_kb_smoke.py

## 为什么需要它

真机第 1 例真实诊断卡在 `dense_search_failed [E2003_VEC_COLLECTION_NOT_FOUND]
table vector_index_knowledge_chunks not found` → `self_rag_retries_exhausted`
→ 只能 `needs_review`。向量表由**建库索引路径**创建，而知识库是空的（0 chunk）。

## 这个脚本做什么（三步，全部幂等）

1. 建向量集合（维度**实测**自嵌入服务返回的向量长度，不靠猜）；
2. 灌 3 条**自造条目**：内容是甲状腺超声报告的通用描述/随访表述，
   **不是临床指南、也不是任何真实患者数据**，`metadata.smoke_fixture=true` 明确标注；
3. 同步写入 `knowledge_chunks` 表（稀疏腿 BM25 与"回表取正文"都需要它）。

## 明确不做的事

* 不引入任何真实患者数据；不冒充权威知识（条目里自带"冒烟用自造条目"标记）；
* 不做体检/评测 —— 本脚本只让检索腿**能跑**，不产出任何精度或一致性数值。
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import urllib.request

SRC = pathlib.Path(os.path.expanduser("~/uai/src"))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# 自造条目（**非临床依据**）：只用于打通检索腿。带 "冒烟用自造条目" 前缀，
# 便于事后从知识库中一眼识别并清除。
SMOKE_CHUNKS: list[dict[str, str]] = [
    {
        "organ": "thyroid",
        "content": (
            "【冒烟用自造条目，非临床依据】甲状腺超声报告通用描述：结节以低回声、"
            "边界清晰、形态规则为常见良性征象描述；若出现纵横比大于一、边界模糊、"
            "微钙化、明显低回声等征象，报告应结合随访建议逐条描述。"
        ),
    },
    {
        "organ": "thyroid",
        "content": (
            "【冒烟用自造条目，非临床依据】甲状腺结节随访表述约定：报告需写明"
            "结节位置、大小、回声、边界、钙化与血流信号等观察项，并给出随访间隔建议；"
            "对需要进一步评估的结节，应在建议中提示专科门诊复诊。"
        ),
    },
    {
        "organ": "thyroid",
        "content": (
            "【冒烟用自造条目，非临床依据】报告用语规范：观察描述与诊断结论需分层书写，"
            "避免在观察描述中出现分级结论；测量值需标注测量部位与切面，便于复核。"
        ),
    },
]

KB_VERSION = "kb_smoke_v0"


def _embed(texts: list[str], base_url: str) -> list[list[float]]:
    body = json.dumps({"texts": texts, "return_dense": True}).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/v1/embeddings",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # 只连本机回环的嵌入服务（base_url 来自 settings，默认 127.0.0.1:8004）
    with urllib.request.urlopen(request, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("dense_embeddings") or payload.get("embeddings") or []


async def _upsert_rows(chunks: list[dict], collection: str, dim: int) -> None:
    """写向量集合 + 同步写 knowledge_chunks（列名按实际表结构自适应）。"""
    import asyncpg

    from app.config.dsn import to_asyncpg_dsn
    from app.config.settings import settings
    from app.embedding.protocols import VectorRecord

    dsn = to_asyncpg_dsn(settings.database_url)
    conn = await asyncpg.connect(dsn)
    try:
        columns = {
            row["column_name"]
            for row in await conn.fetch(
                "select column_name from information_schema.columns "
                "where table_name = 'knowledge_chunks'"
            )
        }
        meta_column = "meta" if "meta" in columns else "metadata"

        for chunk in chunks:
            await conn.execute(
                f"""
                INSERT INTO knowledge_chunks
                    (chunk_id, kb_version, organ, content, {meta_column},
                     source_doc_id, section_path, token_count)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8)
                ON CONFLICT (chunk_id) DO UPDATE
                    SET content = EXCLUDED.content,
                        {meta_column} = EXCLUDED.{meta_column}
                """,
                chunk["id"],
                KB_VERSION,
                chunk["organ"],
                chunk["content"],
                json.dumps(
                    {
                        "smoke_fixture": True,
                        "source": "self-authored",
                        "note": "冒烟用自造条目，非临床依据",
                    }
                ),
                f"smoke-doc-{chunk['organ']}",
                chunk["organ"],
                max(1, len(chunk["content"]) // 2),
            )
        print(f"  knowledge_chunks 写入 {len(chunks)} 行（kb_version={KB_VERSION}）")
    finally:
        await conn.close()

    # 向量集合
    from app.embedding.index_factory import create_index_client_from_settings

    client = await create_index_client_from_settings()
    try:
        await client.create_collection(collection, dim, distance="cosine")
        await client.upsert(
            collection,
            [
                VectorRecord(
                    id=chunk["id"],
                    vector=chunk["vector"],
                    metadata={
                        "organ": chunk["organ"],
                        "kb_version": KB_VERSION,
                        "content": chunk["content"],
                        "smoke_fixture": True,
                    },
                )
                for chunk in chunks
            ],
        )
        hits = await client.search(collection, chunks[0]["vector"], top_k=3)
        print(f"  向量集合 {collection}: 写入 {len(chunks)} 条，回查命中 {len(hits)} 条")
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            await close()


async def main() -> int:
    from app.config.settings import settings

    base_url = f"http://{settings.bge_m3_host}:{settings.bge_m3_port}"
    collection = getattr(settings, "rag_collection", "knowledge_chunks")
    print(f"嵌入服务: {base_url} | 集合: {collection}")

    texts = [c["content"] for c in SMOKE_CHUNKS]
    vectors = _embed(texts, base_url)
    if len(vectors) != len(texts):
        print(f"[ERROR] 嵌入条数不匹配: {len(vectors)} != {len(texts)}")
        return 1
    dim = len(vectors[0])
    print(f"  嵌入维度（实测）: {dim}")

    chunks = [
        {
            "id": f"smoke-{KB_VERSION}-{index:03d}",
            "organ": SMOKE_CHUNKS[index]["organ"],
            "content": SMOKE_CHUNKS[index]["content"],
            "vector": vectors[index],
        }
        for index in range(len(SMOKE_CHUNKS))
    ]

    await _upsert_rows(chunks, collection, dim)
    print("完成（自造冒烟条目，非临床依据）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
