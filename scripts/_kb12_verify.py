import asyncio
import sys

sys.path.insert(0, "/mnt/ee16/home/student03/uai/src")

from dotenv import load_dotenv

load_dotenv("/mnt/ee16/home/student03/uai/.env", override=True)


async def main():
    from app.config.database import AsyncSessionLocal
    from app.config.settings import settings
    from app.embedding.bge_m3 import BgeM3MedicalService
    from app.embedding.index_factory import create_index_client_from_settings
    from app.embedding.protocols import TextEmbeddingRequest

    idx = await create_index_client_from_settings()
    emb = BgeM3MedicalService()
    resp = await emb.embed_text(
        TextEmbeddingRequest(texts=["甲状腺癌的手术治疗方式"]))
    vec = resp.embeddings[0]
    async with AsyncSessionLocal() as db:
        for kb in ("kb_v1.2.0", "kb_v1.0.2"):
            res = await idx.search(
                "knowledge_chunks", query_vector=vec, top_k=3,
                filter={"kb_version": kb})
            print(f"--- {kb}: {len(res)} hits")
            for h in res[:2]:
                cid = getattr(h, "chunk_id", None) or str(h)[:50]
                print("  ", cid)
            if kb == "kb_v1.2.0" and res:
                content = getattr(res[0], "content", None) or ""
                print("   top content:", content[:120].replace("\n", "|"))


asyncio.run(main())
