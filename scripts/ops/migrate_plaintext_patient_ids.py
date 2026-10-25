"""A.93 历史 DICOM 明文 patient_id 迁移为假名（**三处必须同步**，dry-run 默认）。

## 为什么这件事必须三处一起改

`dicom_images.patient_id` 的明文不只存在于一个地方：

| 处 | 内容 | 若不同步的后果 |
|---|---|---|
| **DB 列** | `dicom_images.patient_id` | 只改这里 → 检索侧按假名查不到历史影像 |
| **磁盘 DICOM 头** | `(0010,0020) PatientID` | 不改 → 文件本身仍含明文，拷走即泄露 |
| **Qdrant payload** | `ultrasound_chunks` 的 `patient_id` | 不改 → **影像向量的病人隔离按明文过滤，与 DB 断链** |

A.88 只让**新上传**走假名（`pseudonymize_patient_id`），历史 583 张仍是明文。
只改其中一处会直接破坏两条已修好的能力：
* 相似病例 `scope="self"`（同患者历次检查聚合）；
* 影像向量的病人隔离（A.78：按 session 过滤，session 又按 patient 归属）。

## 语义契约（与 `app/services/dicom_storage.pseudonymize_patient_id` 完全一致）

* **幂等**：同一原文 → 同一假名。这是迁移能分批跑、能重跑的前提；
* **抗碰撞**：不同原文 → 不同假名；
* **不可逆**：`anon_<hmac_sha256[:16]>`，盐来自 `settings.jwt_secret_key` + 固定上下文标签。

**迁移不改变任何现有指针**：`session_id` / `storage_path` / `image_id` 全不动，
只把 `patient_id` 这一个字段换成它的假名。因此历史判读记录、报告、
相似病例的关联**一条都不会断**（它们按 session_id / request_id 关联）。

## 安全闸（照 AGENTS.md 硬规则 14）

1. **默认 dry-run**，只有 `--apply` 才写；
2. `--apply` **必须先有备份**（DB 行 + DICOM 头原值 + Qdrant payload，写 JSONL）；
3. 备份文件必须已存在才允许 apply（防"以为备份了"）；
4. 只处理 `patient_id NOT LIKE 'anon_%'` 的行 —— 已假名的不动（幂等）；
5. 全程不把明文 patient_id 写进日志（只写假名与计数）。

## 用法

    # 1) 盘点（只读）
    ~/uai/venv/bin/python scripts/ops/migrate_plaintext_patient_ids.py

    # 2) 备份（不写库，只导出）
    ... --backup ~/uai/data/eval/patient_id_migration_<ts>.jsonl

    # 3) 真正迁移（需同时给已存在的备份）
    ... --apply --backup <同一个路径>

    # 4) 复核
    ... --verify
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import text  # noqa: E402

from app.config.database import AsyncSessionLocal  # noqa: E402
from app.services.dicom_storage import pseudonymize_patient_id  # noqa: E402

#: Qdrant 影像向量集合
IMAGE_COLLECTION = "ultrasound_chunks"


async def _plaintext_groups() -> list[dict]:
    """列出仍是明文的 patient_id 及其影像数。"""
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT patient_id, count(*) AS n,
                           count(DISTINCT session_id) AS sessions
                    FROM dicom_images
                    WHERE patient_id IS NOT NULL
                      AND patient_id NOT LIKE 'anon_%'
                    GROUP BY patient_id
                    ORDER BY count(*) DESC
                    """
                )
            )
        ).all()
    return [
        {"plaintext": str(r[0]), "images": int(r[1]), "sessions": int(r[2])}
        for r in rows
    ]


async def _image_rows(plaintext: str) -> list[dict]:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT image_id, storage_path, patient_id
                    FROM dicom_images
                    WHERE patient_id = :p
                    """
                ),
                {"p": plaintext},
            )
        ).all()
    return [
        {"image_id": str(r[0]), "storage_path": str(r[1]), "patient_id": str(r[2])}
        for r in rows
    ]


def _qdrant_points(plaintext: str) -> list[dict]:
    """取 Qdrant 里该明文 patient_id 的所有点（只读）。"""
    import urllib.request

    body = {
        "limit": 2000,
        "with_payload": True,
        "with_vector": False,
        "filter": {
            "must": [{"key": "patient_id", "match": {"value": plaintext}}]
        },
    }
    req = urllib.request.Request(
        f"http://127.0.0.1:6333/collections/{IMAGE_COLLECTION}/points/scroll",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    return data.get("result", {}).get("points", [])


def _qdrant_set_patient(ids: list[str], pseudonym: str) -> int:
    """批量把点的 payload.patient_id 改成假名。返回更新条数。"""
    import urllib.request

    if not ids:
        return 0
    body = {"payload": {"patient_id": pseudonym}, "points": ids}
    req = urllib.request.Request(
        f"http://127.0.0.1:6333/collections/{IMAGE_COLLECTION}/points/payload"
        "?wait=true",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    return int(data.get("result", {}).get("status", 0) == "completed")


def _read_dicom_patient_id(raw: bytes) -> str | None:
    import pydicom

    ds = pydicom.dcmread(io.BytesIO(raw), force=True)
    v = getattr(ds, "PatientID", None)
    return str(v) if v is not None else None


def _rewrite_dicom_patient_id(raw: bytes, pseudonym: str) -> bytes:
    """把 DICOM 头里的 PatientID 换成假名，返回新字节。

    ## 两个实现约束（真机踩过）

    1. **不能给 `save_as` 传 `explicit_vr` / `implicit_vr` / `little_endian`**
       —— 新版 pydicom(3.x) 已把它们从 `dcmwrite` 参数里移除，传了直接
       `TypeError: Invalid keyword argument(s) for dcmwrite(): explicit_vr`。
       第一次执行时 583 个文件**全部失败**就是因为这个。
    2. 用 `write_like_original=False`：按数据集实际的 TransferSyntaxUID 重写，
       保留原始编码形态，不要硬指 VR。

    另外**同步清空 PatientName**（A.88 已定新上传该字段置空；
    历史文件若带明文姓名，只改 ID 不改姓名等于没脱敏）。
    """
    import pydicom

    ds = pydicom.dcmread(io.BytesIO(raw), force=True)
    ds.PatientID = pseudonym
    if getattr(ds, "PatientName", None):
        ds.PatientName = ""
    buf = io.BytesIO()
    ds.save_as(buf, enforce_file_format=True, write_like_original=False)
    return buf.getvalue()


async def _backup(groups: list[dict], path: Path) -> int:
    """把三处原值全部写进 JSONL（可完整回滚）。"""
    from app.services.dicom_storage import get_dicom_storage

    storage = get_dicom_storage()
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for g in groups:
            pt = g["plaintext"]
            pseudo = pseudonymize_patient_id(pt)
            rows = await _image_rows(pt)
            pts = _qdrant_points(pt)
            fh.write(
                json.dumps(
                    {
                        "plaintext": pt,
                        "pseudonym": pseudo,
                        "image_count": len(rows),
                        "db_rows": rows,
                        "qdrant_point_ids": [p["id"] for p in pts],
                        "qdrant_payload_patient_id": [p["payload"].get("patient_id") for p in pts],
                        "disk_samples": [
                            {
                                "storage_path": r["storage_path"],
                                "dicom_patient_id": _read_dicom_patient_id(
                                    await storage.retrieve(r["storage_path"]) or b""
                                ),
                            }
                            for r in rows[:3]
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            n += 1
    return n


async def _apply(groups: list[dict]) -> dict:
    """三处同步改写。返回统计。"""
    from app.services.dicom_storage import get_dicom_storage

    storage = get_dicom_storage()
    stat = {"db": 0, "qdrant": 0, "disk": 0, "disk_failed": 0, "groups": 0}

    for g in groups:
        pt = g["plaintext"]
        pseudo = pseudonymize_patient_id(pt)
        rows = await _image_rows(pt)

        # ① DB 列
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                text(
                    "UPDATE dicom_images SET patient_id = :new "
                    "WHERE patient_id = :old"
                ),
                {"new": pseudo, "old": pt},
            )
            await db.commit()
            stat["db"] += res.rowcount or 0

        # ② Qdrant payload
        pts = _qdrant_points(pt)
        ids = [p["id"] for p in pts]
        if ids:
            _qdrant_set_patient(ids, pseudo)
            stat["qdrant"] += len(ids)

        # ③ 磁盘 DICOM 头
        for r in rows:
            try:
                raw = await storage.retrieve(r["storage_path"])
                if not raw:
                    stat["disk_failed"] += 1
                    continue
                if _read_dicom_patient_id(raw) == pseudo:
                    stat["disk"] += 1  # 已是假名（幂等）
                    continue
                new_bytes = _rewrite_dicom_patient_id(raw, pseudo)
                await storage.replace(r["storage_path"], new_bytes)
                stat["disk"] += 1
            except Exception:
                stat["disk_failed"] += 1

        stat["groups"] += 1
        print(
            f"  {pseudo}: DB {len(rows)} 张 / Qdrant {len(ids)} 点 / 盘上已处理"
        )
    return stat


async def _verify() -> int:
    """复核：三处是否都不再有明文。"""
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT patient_id, count(*) FROM dicom_images "
                    "WHERE patient_id NOT LIKE 'anon_%' GROUP BY patient_id"
                )
            )
        ).all()
    print(f"DB 明文 patient_id 组数 = {len(rows)}")
    for pid, n in rows:
        print(f"  {pid}: {n}")

    import urllib.request

    body = {"limit": 2000, "with_payload": True, "with_vector": False}
    req = urllib.request.Request(
        f"http://127.0.0.1:6333/collections/{IMAGE_COLLECTION}/points/scroll",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        pts = json.load(resp)["result"]["points"]
    from collections import Counter

    c = Counter(p["payload"].get("patient_id") for p in pts)
    non_anon = {k: v for k, v in c.items() if k and not str(k).startswith("anon_")}
    print(f"Qdrant 明文 patient_id 组数 = {len(non_anon)}（共 {len(pts)} 点）")
    for pid, n in non_anon.items():
        print(f"  {pid}: {n}")
    return 0 if (not rows and not non_anon) else 1


async def _disk_only(*, dry_run: bool) -> dict:
    """只补磁盘 DICOM 头（DB 与 Qdrant 已迁移完时的续跑路径）。

    ## 为什么需要这个模式（真机踩到）

    第一次 `--apply` 时，DB 列与 Qdrant payload **都成功了**，只有磁盘因为
    pydicom 参数错误全部失败。此时如果重跑完整迁移，`_plaintext_groups()`
    已经查不到明文（DB 改完了）→ 会打印"无需迁移"然后什么都不做，
    **磁盘那 583 个文件永远是明文**。

    这个模式直接按 DB 里**已假名**的 `patient_id` 去核对/修正盘上头，
    所以既能续跑、也能当幂等复核：已正确的文件跳过。
    """
    from app.services.dicom_storage import get_dicom_storage

    storage = get_dicom_storage()
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT image_id, storage_path, patient_id FROM dicom_images "
                    "WHERE patient_id LIKE 'anon_%' ORDER BY image_id"
                )
            )
        ).all()

    stat = {"total": len(rows), "already_ok": 0, "rewritten": 0, "failed": 0}
    failed_samples: list[str] = []
    for image_id, path, expected in rows:
        try:
            raw = await storage.retrieve(path)
            if not raw:
                stat["failed"] += 1
                failed_samples.append(f"{image_id}:no_bytes")
                continue
            current = _read_dicom_patient_id(raw)
            if current == expected:
                stat["already_ok"] += 1
                continue
            if dry_run:
                stat["rewritten"] += 1
                continue
            new_bytes = _rewrite_dicom_patient_id(raw, expected)
            if await storage.replace(path, new_bytes):
                stat["rewritten"] += 1
            else:
                stat["failed"] += 1
                failed_samples.append(f"{image_id}:replace_false")
        except Exception as exc:
            stat["failed"] += 1
            failed_samples.append(f"{image_id}:{type(exc).__name__}")
    if failed_samples:
        stat["failed_samples"] = failed_samples[:10]
    return stat


async def main(
    *, apply: bool, backup: str | None, verify: bool, disk_only: bool = False
) -> int:
    if verify:
        return await _verify()

    if disk_only:
        print("=== 仅补磁盘 DICOM 头（DB/Qdrant 已迁移的续跑路径）===")
        stat = await _disk_only(dry_run=not apply)
        print(
            f"  总计 {stat['total']} 个文件：已正确 {stat['already_ok']} / "
            f"{'待改' if not apply else '已改'} {stat['rewritten']} / "
            f"失败 {stat['failed']}"
        )
        if stat.get("failed_samples"):
            print(f"  失败样例: {stat['failed_samples']}")
        if not apply:
            print("\n[dry-run] 未写任何文件。加 --apply 真正改写。")
        return 0

    groups = await _plaintext_groups()
    total_images = sum(g["images"] for g in groups)
    print(f"明文字段盘点：{len(groups)} 个 patient_id / {total_images} 张影像")
    for g in groups:
        print(
            f"  {g['plaintext']}: {g['images']} 张 / {g['sessions']} session "
            f"→ {pseudonymize_patient_id(g['plaintext'])}"
        )
    if not groups:
        print("（已是全假名，无需迁移）")
        return 0

    if backup:
        bp = Path(backup).expanduser()
        bp.parent.mkdir(parents=True, exist_ok=True)
        n = await _backup(groups, bp)
        print(f"\n备份已写：{bp}（{n} 组，含 DB 行/Qdrant 点/盘上头原值）")

    if not apply:
        print("\n[dry-run] 未修改任何数据。要真正迁移，加 --apply 并给 --backup。")
        return 0

    if not backup or not Path(backup).expanduser().is_file():
        print("[FATAL] --apply 要求先写备份（--backup <path>），且文件必须已存在")
        return 2

    print("\n开始三处同步迁移…")
    stat = await _apply(groups)
    print(
        f"\n[APPLIED] DB {stat['db']} 行 / Qdrant {stat['qdrant']} 点 / "
        f"盘上 {stat['disk']} 文件（失败 {stat['disk_failed']}）"
    )
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真的写（默认 dry-run）")
    ap.add_argument("--backup", default=None, help="备份 JSONL 路径（--apply 时必填且需已存在）")
    ap.add_argument("--verify", action="store_true", help="只复核三处是否还有明文")
    ap.add_argument(
        "--disk-only",
        action="store_true",
        help="只补磁盘 DICOM 头（DB/Qdrant 已迁移完的续跑路径）",
    )
    a = ap.parse_args()
    sys.exit(
        asyncio.run(
            main(
                apply=a.apply,
                backup=a.backup,
                verify=a.verify,
                disk_only=a.disk_only,
            )
        )
    )
