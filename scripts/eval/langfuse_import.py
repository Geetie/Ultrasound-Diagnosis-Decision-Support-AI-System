"""PG 判读结果 -> Langfuse Dataset Items（A.48：19 号 P1 采集的落地形态）。

Langfuse v3.225.11 自托管实测（A.48）：
- OTLP trace ingest 队列 500（"Failed to instantiate otel ingestion queue"，
  升级 v4 解决，已知问题不阻塞评测管理）
- datasets/dataset-items 的 CRUD API 完全可用 —— 评测集采集改走
  「PG 事实源（diagnosis_results）→ Dataset API 导入」：
  19 号 P1 想要的字段（organ/tier/clinical_text/findings/timings/SG）PG 全有

用法（服务器上）：
    ~/uai/venv/bin/python scripts/eval/langfuse_import.py [limit]

v3.225 端点注意：dataset items 是 POST /api/public/dataset-items
（body 带 datasetName/datasetId），**不是** 新版文档的
/datasets/{name}/items（该路由在本版本 404）。
"""
import asyncio
import base64
import json
import sys
import urllib.request

DS_API = "http://127.0.0.1:30100/api/public"
AUTH = base64.b64encode(b"pk-lf-uai-prod:sk-lf-uai-prod-9f2b7c41e8a3d65f0b18c7a92e4d3f56").decode()
PG = "postgresql://uai:1682a0cd6b37de723058af8e042f8de6@127.0.0.1:15432/uai"
DATASET_NAME = "uai-eval-set"
DATASET_ID = "cmv2306o50004ld069wulktin"
LIMIT_DEFAULT = 10


def api(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        DS_API + path,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": "Basic " + AUTH, "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


async def main(limit: int) -> None:
    import asyncpg

    conn = await asyncpg.connect(PG)
    rows = await conn.fetch(
        "select request_id, status, organ, tier, clinical_text, findings, confidence "
        "from diagnosis_results where status = 'completed' "
        "order by created_at desc limit $1",
        limit,
    )
    print("pg rows:", len(rows))
    imported = 0
    for r in rows:
        f = r["findings"]
        if isinstance(f, str):
            f = json.loads(f) if f else {}
        fusion = f.get("fusion_result") or {}
        conclusion = fusion.get("conclusion") or json.dumps(fusion, ensure_ascii=False)[:400]
        item = {
            "datasetName": DATASET_NAME,
            "datasetId": DATASET_ID,
            "input": {
                "request_id": r["request_id"],
                "organ": r["organ"],
                "tier": r["tier"],
                "clinical_text": (r["clinical_text"] or "")[:300],
            },
            "expectedOutput": conclusion[:2000],
            "metadata": {
                "timings_ms": f.get("timings_ms", {}),
                "sg_allowed": (f.get("sg_result") or {}).get("allowed"),
                "confidence": r["confidence"],
            },
        }
        try:
            api("POST", "/dataset-items", item)
            imported += 1
        except Exception as exc:
            print("skip", r["request_id"], str(exc)[:80])
    print("imported:", imported)
    await conn.close()


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else LIMIT_DEFAULT
    asyncio.run(main(limit))
