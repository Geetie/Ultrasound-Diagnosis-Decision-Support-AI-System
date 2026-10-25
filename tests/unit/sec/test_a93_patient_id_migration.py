"""A.93 守卫：历史明文 `patient_id` 迁移——**三处必须同步**，且必须幂等可回滚。

## 为什么这件事必须三处一起改

`dicom_images.patient_id` 的明文不在一处：

| 处 | 若不改的后果 |
|---|---|
| **DB 列** `dicom_images.patient_id` | 按假名查不到历史影像 |
| **磁盘 DICOM 头** `(0010,0020)` | 文件本身仍含明文，拷走即泄露 |
| **Qdrant payload** `ultrasound_chunks.patient_id` | 影像向量的病人隔离按明文过滤，与 DB 断链 |

只改一处会直接破坏两条**已修好**的能力：
* 相似病例 `scope="self"`（同患者历次检查聚合，A.88）；
* 影像向量病人隔离（A.78：按 session 过滤，session 又按 patient 归属）。

## 真机执行结果（2026-10-25）

| 处 | 迁移前 | 迁移后 |
|---|---|---|
| DB | 583 张明文 / 10 组 | **0 明文 / 584 全部 anon_** |
| Qdrant | 580 点，10 组明文 | **0 明文** |
| 磁盘 | 582 个文件头明文 | **584/584 与 DB 一致** |

`sessions` 断链 = 0；跨检查聚合仍可用（如 `anon_f1a9…` 101 个 session）。
备份：`~/uai/data/eval/patient_id_migration_20261025_232215.jsonl`（10 组，
含 DB 行 / Qdrant 点 id / 盘上头原值）+ `dicom_storage_backup_20261025_232337.tar.gz`（104M）。

## 真机踩到的两个坑（都写进脚本 docstring）

1. **`save_as` 不许传 `explicit_vr`/`implicit_vr`/`little_endian`** ——
   pydicom 3.x 已从 `dcmwrite` 移除，传了直接 `TypeError`。
   第一次执行时 **583 个文件全部失败**，而 DB/Qdrant 已成功 →
   如果重跑完整迁移，`_plaintext_groups()` 查不到明文就会"无需迁移"，
   **磁盘永远是明文**。所以才需要 `--disk-only` 续跑模式。
2. **`storage.replace` 原先不存在** —— `save()` 是"新建"语义（新 id/新路径），
   用它迁移会产生孤儿文件并让 `storage_path` 指向旧文件。

## 本守卫钉什么

1. `replace` 只覆盖已存在路径（路径不存在返回 False，不凭空创建）；
2. local 分支用临时文件 + `os.replace` 原子替换（不留半个 DICOM）；
3. 迁移脚本默认 dry-run、`--apply` 必须先有备份；
4. 假名函数幂等 + 抗碰撞（迁移能分批跑/能重跑的前提）；
5. `--disk-only` 模式存在（否则 DB 改完后磁盘将永远无法补上）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.dicom_storage import pseudonymize_patient_id

pytestmark = [pytest.mark.track_sec]

_REPO = Path(__file__).resolve().parents[3]
_STORAGE = _REPO / "app" / "services" / "dicom_storage.py"
_MIGRATE = _REPO / "scripts" / "ops" / "migrate_plaintext_patient_ids.py"


class TestPseudonymContract:
    """迁移的前提：同原文 → 同假名（幂等）、不同原文 → 不同假名（抗碰撞）。"""

    def test_idempotent(self):
        assert pseudonymize_patient_id("SMOKE-001") == pseudonymize_patient_id("SMOKE-001")

    def test_no_collision(self):
        assert pseudonymize_patient_id("SMOKE-001") != pseudonymize_patient_id("SMOKE-002")

    def test_format_is_anon_16hex(self):
        p = pseudonymize_patient_id("DRILL-PATIENT-001")
        assert re.fullmatch(r"anon_[0-9a-f]{16}", p), p

    def test_not_reversible_by_inspection(self):
        """输出里不含原文字符串（不可逆的最低要求）。"""
        raw = "DRILL-PATIENT-001"
        assert raw not in pseudonymize_patient_id(raw)

    def test_empty_returns_empty(self):
        """空输入返回空串（"没有标识"与"假名"是两件事）。"""
        assert pseudonymize_patient_id("") == ""
        assert pseudonymize_patient_id(None) == ""


class TestStorageReplace:
    """`replace` 是迁移能改盘上字节的前提。"""

    def _src(self) -> str:
        return _STORAGE.read_text(encoding="utf-8")

    def test_replace_exists(self):
        src = self._src()
        assert "async def replace(self, storage_path: str, content: bytes)" in src, (
            "缺 replace —— 用 save() 迁移会产生孤儿文件并让 storage_path 指向旧文件"
        )

    def test_replace_refuses_missing_path(self):
        """反证腿：去掉存在性检查 → 本用例红（会凭空创建文件）。"""
        src = self._src()
        idx = src.index("async def replace")
        body = src[idx : idx + 2000]
        assert "if not os.path.exists(storage_path):" in body, (
            "replace 没检查路径存在 → 迁移可能把记录指向一个其实不存在的文件"
        )
        assert "return False" in body

    def test_replace_is_atomic(self):
        """local 分支必须 临时文件 + os.replace（原子），否则中途失败留半个 DICOM。"""
        src = self._src()
        idx = src.index("async def replace")
        body = src[idx : idx + 2000]
        assert "os.replace(" in body, "没有原子替换"
        assert ".tmp-" in body, "没有临时文件（直接覆写会在中途失败时损坏原文件）"


class TestMigrationScriptSafety:
    def _src(self) -> str:
        return _MIGRATE.read_text(encoding="utf-8")

    def test_default_dry_run(self):
        src = self._src()
        assert 'ap.add_argument("--apply", action="store_true"' in src
        assert "[dry-run] 未修改任何数据" in src

    def test_apply_requires_existing_backup(self):
        src = self._src()
        assert "is_file()" in src
        assert "[FATAL] --apply 要求先写备份" in src

    def test_backup_covers_three_places(self):
        """备份必须含 DB 行 / Qdrant 点 / 盘上头原值（否则无法完整回滚）。"""
        src = self._src()
        assert '"db_rows"' in src
        assert '"qdrant_point_ids"' in src
        assert '"dicom_patient_id"' in src

    def test_disk_only_mode_exists(self):
        """反证腿：删掉 --disk-only → 本用例红。

        为什么必须要它：真机第一次 apply 时 DB/Qdrant 成功、磁盘全失败
        （pydicom 参数错误）。此时重跑完整迁移会因"已无明文"而**直接跳过**，
        磁盘那 583 个文件将永远是明文。
        """
        src = self._src()
        assert '"--disk-only"' in src
        assert "async def _disk_only" in src

    def test_only_touches_non_pseudonymized(self):
        """只处理 `NOT LIKE 'anon_%'` 的行（已假名的不动 = 幂等）。"""
        src = self._src()
        assert "NOT LIKE 'anon_%'" in src

    def test_no_pydicom_forbidden_kwargs(self):
        """反证腿：把 `explicit_vr=` 加回 `save_as` → 本用例红（pydicom 3.x 直接 TypeError）。"""
        src = self._src()
        # 注释里解释"不能传"是允许的，所以只看代码行
        code = "\n".join(
            line for line in src.splitlines() if not line.strip().startswith("#")
        )
        assert "explicit_vr=" not in code, (
            "save_as 又传了 explicit_vr —— pydicom 3.x 会 TypeError，"
            "第一次迁移 583 个文件全失败就是这个"
        )
        assert "implicit_vr=" not in code
        assert "write_like_original=False" in code
