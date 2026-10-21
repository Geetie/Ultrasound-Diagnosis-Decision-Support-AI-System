"""A.79：**把库里 580 张影像灌成向量**（建 `ultrasound_chunks`，payload 带病人隔离字段）。

**为什么必须做**：库里 580 张图从未算过向量、`ultrasound_chunks` 集合从未创建 →
影像检索腿每次都"空跑"（A.68 只能把它关掉并报 `ready=false`）。

**三条硬约束（写进代码，不靠自觉）**
1. **payload 必须带 `session_id` + `patient_id`** —— 检索侧 A.78 已强制按 session 过滤，
   没有这两个字段的向量**一律不写**（否则等于绕过病人隔离）；
2. **幂等**：point id 由 `image_id` 确定性生成（`uuid5`），重复跑不会重复写入；
3. **默认 dry-run**：只盘点"多少张图、文件在不在、缺哪些"，`--apply` 才真嵌入 + 写入。

用法（服务器）：
    # 先盘点
    ~/uai/venv/bin/python scripts/audit/run_with_env.py scripts/ops/build_image_vectors.py
    # 小批冒烟（8 张）
    ~/uai/venv/bin/python scripts/audit/run_with_env.py scripts/ops/build_image_vectors.py --apply --limit 8
    # 全量
    ~/uai/venv/bin/python scripts/audit/run_with_env.py scripts/ops/build_image_vectors.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

#: 影像向量维度（ULC 输出 512；与 `app/config/settings.py` 的 image_dim 一致）
IMAGE_DIM = 512


def payload_for(row: dict) -> dict | None:
    """构造向量 payload；**缺 session_id 或 patient_id 时返回 None（拒绝写入）**。纯函数（守卫钉住）。

    这是病人隔离的最后一道闸：检索侧按 `session_id` 过滤，
    如果写入时没这个字段，那条向量就永远查不到（写了等于白写），
    更糟的是有人可能改成"不过滤"来找它 —— 所以宁可拒写。
    """
    session_id = str(row.get("session_id") or "").strip()
    patient_id = str(row.get("patient_id") or "").strip()
    image_id = str(row.get("image_id") or "").strip()
    if not (session_id and patient_id and image_id):
        return None
    payload = {
        "image_id": image_id,
        "session_id": session_id,
        "patient_id": patient_id,
        "modality": str(row.get("modality") or ""),
        "storage_path": str(row.get("storage_path") or ""),
    }
    if row.get("organ"):
        payload["organ"] = str(row["organ"])
    return payload


def image_format_of(path: str) -> str:
    """从文件名猜格式（ULC 需要 mime）。纯函数（守卫钉住）。"""
    suffix = pathlib.Path(path).suffix.lower().lstrip(".")
    return suffix or "dicom"


def needs_frame_conversion(path: str) -> bool:
    """是否要把 DICOM **渲染成 PNG 帧**再送嵌入。纯函数（守卫钉住）。

    ⚠️ 真机实测：把 DICOM 原始字节直接 POST 给 ULC（8003）会 **500**；
    同一接口喂 PNG 正常返回 512 维。ULC 只吃图片、不吃 DICOM
    → 必须先 `extract_frame_from_bytes()` 取中间帧的 PNG 字节（项目里已有该函数）。
    """
    return image_format_of(path) in ("dcm", "dicom", "ima")


async def _load_rows(limit: int) -> list[dict]:
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import DiagnosisResult, DicomImage

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    DicomImage.image_id,
                    DicomImage.session_id,
                    DicomImage.patient_id,
                    DicomImage.storage_path,
                    DicomImage.modality,
                ).order_by(DicomImage.created_at)
            )
        ).all()

        # organ：取该 session 最近一次判读的器官（没有就留空）
        organs = (
            await session.execute(
                select(DiagnosisResult.session_id, DiagnosisResult.organ).order_by(
                    DiagnosisResult.created_at
                )
            )
        ).all()
    organ_of: dict[str, str] = {}
    for sid, organ in organs:
        if sid and organ:
            organ_of[str(sid)] = str(organ)

    out = [
        {
            "image_id": str(r[0]),
            "session_id": str(r[1]),
            "patient_id": str(r[2] or ""),
            "storage_path": str(r[3] or ""),
            "modality": str(r[4] or ""),
            "organ": organ_of.get(str(r[1]), ""),
        }
        for r in rows
    ]
    return out[:limit] if limit else out


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真的嵌入并写库（默认只盘点）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 张（冒烟）")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument(
        "--report", default=str(pathlib.Path.home() / "uai/reports/image_vectors_build.json")
    )
    args = ap.parse_args()

    from app.config.settings import settings

    rows = await _load_rows(args.limit)
    stats: dict = {
        "total": len(rows),
        "no_payload_fields": 0,
        "missing_file": 0,
        "embedded": 0,
        "upserted": 0,
        "failed": 0,
        "collection": settings.rag_image_collection,
        "dim": IMAGE_DIM,
        "applied": bool(args.apply),
        "failures": [],
    }

    usable: list[dict] = []
    for row in rows:
        if payload_for(row) is None:
            stats["no_payload_fields"] += 1
            continue
        if not row["storage_path"] or not pathlib.Path(row["storage_path"]).is_file():
            stats["missing_file"] += 1
            if len(stats["failures"]) < 5:
                stats["failures"].append(
                    {"image_id": row["image_id"], "reason": "file_missing",
                     "path": row["storage_path"]}
                )
            continue
        usable.append(row)

    stats["usable"] = len(usable)
    print(
        f"盘点：库内影像 {stats['total']}，可建向量 {stats['usable']}"
        f"（缺 session/patient {stats['no_payload_fields']}、文件不存在 {stats['missing_file']}）"
    )
    if not args.apply:
        print("dry-run：未写入。加 --apply 才会嵌入并写库。")
        _write(args.report, stats)
        return 0

    from app.embedding.protocols import ImageEmbeddingRequest, VectorRecord
    from app.embedding.qdrant_client import QdrantVectorIndex
    from app.embedding.ultrasound_clip import UltrasoundClipService

    vector = QdrantVectorIndex(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
        api_key=settings.qdrant_api_key or None,
    )
    clip = UltrasoundClipService(base_url=f"http://127.0.0.1:{settings.ultrasound_clip_port}")
    await vector.create_collection(
        collection=settings.rag_image_collection, dimension=IMAGE_DIM, distance="cosine"
    )

    try:
        for start in range(0, len(usable), args.batch):
            chunk = usable[start : start + args.batch]
            records: list[VectorRecord] = []
            for row in chunk:
                try:
                    raw = pathlib.Path(row["storage_path"]).read_bytes()
                    if needs_frame_conversion(row["storage_path"]):
                        # DICOM → PNG 中间帧（ULC 不吃 DICOM，会 500）
                        from app.imaging.dicom_pixels import extract_frame_from_bytes

                        frame = extract_frame_from_bytes(raw)
                        data, fmt = frame.png_bytes, "png"
                    else:
                        data, fmt = raw, image_format_of(row["storage_path"])
                    resp = await clip.embed_image(
                        ImageEmbeddingRequest(
                            image_data=data,
                            image_format=fmt,
                            request_id=row["image_id"],
                            session_id=row["session_id"],
                        )
                    )
                except Exception as exc:  # 单张失败不阻断整批，但必须计数留痕
                    stats["failed"] += 1
                    if len(stats["failures"]) < 10:
                        stats["failures"].append(
                            {"image_id": row["image_id"], "reason": f"{type(exc).__name__}: {exc}"[:120]}
                        )
                    continue
                if not resp.embeddings:
                    stats["failed"] += 1
                    continue
                stats["embedded"] += 1
                records.append(
                    VectorRecord(
                        id=row["image_id"],
                        vector=list(resp.embeddings[0]),
                        metadata=payload_for(row) or {},
                    )
                )
            if records:
                await vector.upsert(collection=settings.rag_image_collection, vectors=records)
                stats["upserted"] += len(records)
            print(f"  进度 {min(start + args.batch, len(usable))}/{len(usable)}"
                  f"：已嵌入 {stats['embedded']}、已写入 {stats['upserted']}、失败 {stats['failed']}")
    finally:
        for obj in (clip, vector):
            closer = getattr(obj, "close", None)
            if closer is not None:
                await closer()

    _write(args.report, stats)
    print(f"完成：写入 {stats['upserted']} 条（集合 {stats['collection']}，维度 {stats['dim']}）")
    return 0


def _write(path: str, stats: dict) -> None:
    out = pathlib.Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{out}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
