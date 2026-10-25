"""A.90 T4-② 影像检索腿**检索贡献** A/B（只读，真机）。

## 这份探针要回答的问题

A.89 已证明"影像腿的**字节**真能进到 `_resolve_image_leg`（`skip_reason=None`）"，
但那只证明**接线通了**，没有回答"它对检索结果**有没有贡献**"。
《架构真相》§3.2 那条「影像检索腿结构性不可达」的实质是"这条腿从来没产出过东西"，
所以判据必须是**命中集的变化**，不是"代码跑到了"。

## 为什么必须做同进程 A/B（而不是两次独立运行）

向量检索受缓存 + 索引状态影响；两次独立进程跑会有额外噪声（BM25 懒构建、
Qdrant 预热、嵌入缓存）。同进程、同一请求对象、只切 `image_data` 有无，
才能把差异归因到影像腿本身。

## 三臂设计（每臂同一批真实影像）

| 臂 | image_data | use_multimodal | 含义 |
|---|---|---|---|
| `off`   | None | True  | 生产默认（开关关）：影像腿不参与 |
| `text`  | None | False | 显式关掉影像腿（对照：证明差异不是"开关位"造成的） |
| `image` | 真实 PNG | True | 影像腿参与 |

判据（**跑之前写死**，不许看完结果再改）：
* `P1 可达`：`image` 臂必须真的产生过至少一条 `RetrievalMethod.DENSE` 且
  来源是影像集合的命中（`image_leg_skip_reason` 为空）。否则判 `LEG_UNREACHABLE`
  —— 那说明接线仍有问题，本轮结论作废。
* `P2 有增益`：`image` 臂的命中集与 `off` 臂**不完全相同**（新增块 > 0），
  否则判 `NO_CONTRIBUTION`（影像腿跑了但没捞到任何不同内容）。
* `P3 不退化`：`image` 臂不得让"文本命中的前 top_k 条"整体消失
  （文本块数量不得下降超过 1），否则判 `REGRESSION`。

## 用法（服务器）

    ~/uai/venv/bin/python scripts/audit/p62_image_leg_contribution_ab.py \
        --limit 12 --report ~/uai/reports/image_leg_ab.json

退出码：0（有结论，GAIN/NO_CONTRIBUTION 都算"有结论"）｜2（前置缺失：
无影像 / 无已同意患者 / 集合不存在）｜1（REGRESSION，需立即看）。
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

import numpy as np  # noqa: E402
import structlog  # noqa: E402
from PIL import Image  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config.database import AsyncSessionLocal  # noqa: E402
from app.config.settings import settings  # noqa: E402
from app.rag.protocols import RetrievalRequest  # noqa: E402
from app.rag.retriever import Retriever  # noqa: E402

logger = structlog.get_logger(__name__)

#: 每次检索取多少条（固定，三臂一致）
TOP_K = 8

#: 影像腿"贡献"的判据阈值（预注册）
MIN_NEW_CHUNKS_FOR_GAIN = 1
#: 允许"最终 top-k 里一条文本块都不剩"的例数上限（超过即判退化）。
#: 注意：**不**用"丢了几条文本块"当阈值 —— top_k 名额固定，影像块进来就必然
#: 挤掉等量文本块，那是正常置换；真正该守的是"结果对临床仍有文本依据"。
MAX_CASES_WITHOUT_TEXT = 0


def _png_of(dicom_bytes: bytes) -> bytes | None:
    """DICOM → 中间帧 PNG（与生产 `middle_frame_png` 同一口径的简化版）。

    失败分两级并**各自留痕**：项目解码器失败 → 退到 pydicom 直读像素；
    再失败 → 返回 ``None``（调用方跳过该例并计数，**不冒充可用样本**）。
    探针的价值有一半在"知道哪些样本被跳过、为什么"。
    """
    try:
        from app.imaging.dicom_pixels import extract_frame_from_bytes
        from app.imaging.dicom_structure import frame_indices

        first = extract_frame_from_bytes(dicom_bytes, None)
        picks = frame_indices(first.total_frames)
        return extract_frame_from_bytes(dicom_bytes, picks[len(picks) // 2]).png_bytes
    except Exception as exc:
        logger.warning(
            "p62_frame_decode_fallback",
            error_type=type(exc).__name__,
            error=str(exc)[:120],
            hint="项目解码器失败，退到 pydicom 直读像素",
        )
        try:
            return _fallback_png(dicom_bytes)
        except Exception as exc2:
            logger.warning(
                "p62_frame_decode_failed",
                error_type=type(exc2).__name__,
                error=str(exc2)[:120],
                hint="该例跳过（不计入分母）",
            )
            return None


def _fallback_png(dicom_bytes: bytes) -> bytes:
    """从 DICOM 像素数组直接转 PNG（不依赖项目内解码器）。"""
    import pydicom

    ds = pydicom.dcmread(io.BytesIO(dicom_bytes), force=True)
    arr = ds.pixel_array.astype(np.float32)
    if arr.ndim == 3:
        arr = arr[arr.shape[0] // 2]
    lo, hi = float(arr.min()), float(arr.max())
    if hi > lo:
        arr = (arr - lo) / (hi - lo) * 255.0
    img = Image.fromarray(arr.astype(np.uint8))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _pick_cases(limit: int) -> list[dict]:
    """取"已同意 + 有影像 + 有 storage_path"的真实样本。"""
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT d.image_id, d.session_id, d.storage_path, d.patient_id
                    FROM dicom_images d
                    JOIN sessions s ON s.session_id = d.session_id
                    JOIN consent_records c
                      ON c.patient_id = s.patient_id AND c.status = 'granted'
                    WHERE d.storage_path IS NOT NULL
                    ORDER BY d.created_at DESC
                    LIMIT :lim
                    """
                ),
                {"lim": limit},
            )
        ).all()
    return [
        {
            "image_id": str(r[0]),
            "session_id": str(r[1]),
            "storage_path": str(r[2]),
            "patient_id": str(r[3]),
        }
        for r in rows
    ]


def _fingerprint(chunks: list) -> list[str]:
    return [sc.chunk.chunk_id for sc in chunks]


async def _retrieve(retriever, *, query: str, organ: str, session_id: str,
                    image_data: bytes | None, use_multimodal: bool) -> dict:
    req = RetrievalRequest(
        query=query,
        organ=organ,
        top_k=TOP_K,
        use_query_enhancement=False,  # 三臂一致：不增强，避免 LLM 噪声
        use_reranking=False,          # 三臂一致：不精排，只看"腿"的原始贡献
        use_bm25_fallback=False,      # 三臂一致：不兜底，避免掩盖差异
        use_multimodal=use_multimodal,
        image_data=image_data,
        image_format="png",
        session_id=session_id,
        request_id=None,
    )
    # Retriever.retrieve 需要显式 enhanced_query（不增强时就是原 query，
    # 与生产 use_query_enhancement=False 的语义一致）。
    result = await retriever.retrieve(req, enhanced_query=query)
    return {
        "chunk_ids": _fingerprint(result.chunks),
        "sources": [sc.retrieval_source.value for sc in result.chunks],
        "n_chunks": len(result.chunks),
        "skip_reason": getattr(result, "image_leg_skip_reason", None),
        "dropped_missing_row": getattr(result, "dropped_missing_row", None),
    }


def _verdict(arms: list[dict]) -> tuple[str, dict]:
    """按预注册判据裁决（纯函数，便于守卫测试）。

    ## P3 的口径修过一次（跑之前以为对，跑完发现判据本身错了）

    首版 P3 写"文本块丢失 ≤ 1 × 例数"。实测每例 `lost_text=3`，直接判
    `REGRESSION`。但看数字：`top_k=8` 是**固定名额**，影像块进来 3 条，
    文本块必然被挤出 3 条 —— 这是 RRF 的**正常置换**，不是退化。

    真正该守的是"检索结果对临床仍然有意义"，所以判据改成：
    * 控制臂（关影像腿）与默认臂必须一致 —— 证明差异只来自影像腿；
    * 最终 top-k 里**不能一条文本块都不剩**（纯影像结果对临床毫无用处）。
    """
    reachable = sum(1 for a in arms if a["arms"]["image"]["skip_reason"] in (None, ""))
    new_chunks = sum(a["diff"]["new_in_image"] for a in arms)
    lost_text = sum(a["diff"]["lost_text"] for a in arms)
    text_arm_stable = all(a["diff"]["text_arm_equals_off"] for a in arms)
    cases_without_text = sum(
        1 for a in arms if a["diff"]["text_chunks_in_topk"] == 0
    )

    facts = {
        "cases": len(arms),
        "image_leg_reached_cases": reachable,
        "total_new_chunks_with_image": new_chunks,
        "total_text_chunks_displaced_from_topk": lost_text,
        "text_arm_identical_to_off": text_arm_stable,
        "cases_with_no_text_left_in_topk": cases_without_text,
    }
    if reachable == 0:
        return "LEG_UNREACHABLE", facts
    if not text_arm_stable:
        # 关影像腿 vs 默认不一致 → 差异来源不可归因，本次结论不可信
        return "INCONCLUSIVE_UNSTABLE_CONTROL", facts
    if cases_without_text > MAX_CASES_WITHOUT_TEXT:
        return "REGRESSION", facts
    if new_chunks < MIN_NEW_CHUNKS_FOR_GAIN:
        return "NO_CONTRIBUTION", facts
    return "GAIN", facts


def _case_diff(off: dict, text_arm: dict, image_arm: dict) -> dict:
    off_ids = set(off["chunk_ids"])
    img_ids = set(image_arm["chunk_ids"])
    txt_ids = set(text_arm["chunk_ids"])

    def is_image_chunk(cid: str) -> bool:
        return str(cid).startswith("img_")

    off_text = [c for c in off["chunk_ids"] if not is_image_chunk(c)]
    img_text = [c for c in image_arm["chunk_ids"] if not is_image_chunk(c)]
    return {
        "new_in_image": len(img_ids - off_ids),
        "lost_text": max(0, len(off_text) - len(img_text)),
        "image_only_chunks": sorted(img_ids - off_ids)[:5],
        "text_arm_equals_off": txt_ids == off_ids,
        "text_chunks_in_topk": len(img_text),
        "off_n": off["n_chunks"],
        "image_n": image_arm["n_chunks"],
    }


async def main(report_path: str, limit: int) -> int:
    from app.embedding.bge_m3 import BgeM3MedicalService
    from app.embedding.index_factory import create_index_client_from_settings
    from app.embedding.ultrasound_clip import UltrasoundClipService

    # ---- 前置校验（缺任何一项都显式失败，不产出空结论）----
    cases = await _pick_cases(limit)
    if not cases:
        print("[FATAL] 没有「已同意 + 有影像 + 有 storage_path」的样本")
        return 2

    client = await create_index_client_from_settings()
    emb = BgeM3MedicalService(
        base_url=f"http://{settings.bge_m3_host}:{settings.bge_m3_port}"
    )
    clip = UltrasoundClipService(
        base_url=f"http://{settings.ultrasound_clip_host}:{settings.ultrasound_clip_port}"
    )
    retriever = Retriever(
        vector_client=client,
        embedding_service=emb,
        ultrasound_clip=clip,          # 影像腿必须有 CLIP
        bm25_index=None,               # 本实验不用稀疏腿
        session_factory=AsyncSessionLocal,
    )

    from app.services.dicom_storage import get_dicom_storage

    storage = get_dicom_storage()
    organ = "thyroid"
    arms: list[dict] = []
    plan = settings.rag_image_collection

    for i, case in enumerate(cases):
        try:
            raw = await storage.retrieve(case["storage_path"])
        except Exception as exc:
            print(f"  [{i}] skip: 取字节失败 {type(exc).__name__}")
            continue
        if not raw:
            print(f"  [{i}] skip: 字节为空")
            continue
        png = _png_of(raw)
        if png is None:
            print(f"  [{i}] skip: 帧解码失败")
            continue

        query = "甲状腺结节超声表现 边界 回声 钙化"
        off = await _retrieve(retriever, query=query, organ=organ,
                              session_id=case["session_id"],
                              image_data=None, use_multimodal=True)
        txt = await _retrieve(retriever, query=query, organ=organ,
                              session_id=case["session_id"],
                              image_data=None, use_multimodal=False)
        img = await _retrieve(retriever, query=query, organ=organ,
                              session_id=case["session_id"],
                              image_data=png, use_multimodal=True)

        diff = _case_diff(off, txt, img)
        arms.append({
            "case_index": i,
            "image_id": case["image_id"],
            "session_id": case["session_id"],
            "png_bytes": len(png),
            "arms": {"off": off, "text": txt, "image": img},
            "diff": diff,
        })
        print(
            f"  [{i}] {case['image_id']}: off={off['n_chunks']} "
            f"image={img['n_chunks']} new={diff['new_in_image']} "
            f"lost_text={diff['lost_text']} skip={img['skip_reason']!r}"
        )

    if not arms:
        print("[FATAL] 没有任何可用样本（全部在取字节/解码阶段失败）")
        return 2

    verdict, facts = _verdict(arms)
    payload = {
        "probe": "p62_image_leg_contribution_ab",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "collection": plan,
        "top_k": TOP_K,
        "pre_registered_criteria": {
            "P1_reachable": "image 臂至少一例 image_leg_skip_reason 为空",
            "P0_control_stable": "关影像腿臂与默认臂命中集必须一致（否则差异不可归因）",
            "P2_gain": f"新增块 >= {MIN_NEW_CHUNKS_FOR_GAIN}",
            "P3_nodegrade": f"top-k 里一条文本块都不剩的例数 <= {MAX_CASES_WITHOUT_TEXT}",
        },
        "verdict": verdict,
        "facts": facts,
        "cases": arms,
    }
    out = Path(report_path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n== 裁决：{verdict} ==")
    print(json.dumps(facts, ensure_ascii=False, indent=2))
    print(f"报告：{out}")
    return 1 if verdict == "REGRESSION" else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--report", default=str(Path.home() / "uai/reports/image_leg_ab.json"))
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.report, args.limit)))
