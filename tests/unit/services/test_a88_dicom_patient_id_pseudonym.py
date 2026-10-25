"""A.88 守卫：DICOM PatientID **不再写回原值**（落盘字节 / 索引列都只能是假名）。

## 真实缺陷（修复前）

``app/api/v1/imaging.py`` 使用名为 ``raw_patient_id`` 的值（来自
``extract_metadata_from_dataset`` 的第 3 个返回值，即 DICOM ``(0010,0020)``
**原文**）直接传给 ``DicomStorageService.save(patient_id=...)`` 与
``DicomImageRepository.create(patient_id=...)``；而 ``dicom_storage.py`` 的
``strip_dicom_phi`` 会把 ``(0010,0020)`` **写成传进来的那个 patient_id**。

于是落盘 .dcm 文件里 ``PatientID`` 等于原文，``dicom_images.patient_id``
也是原文 —— 所谓"脱敏"只删了姓名/生日/机构，最关键的病人主索引留了明文。
拿到 dcm 文件或读一次 DB 即可得到患者标识（PHI 泄露）。

## 修法（不动 ``strip_dicom_phi`` 的语义）

``strip_dicom_phi`` 是**通用写入原语**（传什么写什么），行为已被
``test_dicom_storage_service.py::test_rewrites_patient_id`` 钉住；问题在**调用侧
传了原文**。因此新增纯函数 ``pseudonymize_patient_id``（带盐 HMAC-SHA256
→ ``anon_<16 hex>``），落盘与索引都改传假名。

## 这里钉的四件事

1. **落盘字节里没有原文**（拿 pydicom 读回落盘文件断言）；
2. **索引列里没有原文**（同时断言它是假名形态）；
3. **同一原始 ID → 同一假名**（幂等）：否则同患者历次检查断链，
   相似病例 ``scope="self"`` / 影像向量的病人隔离全部失效；
4. **不同原始 ID → 不同假名**（抗碰撞）：否则跨患者串档。

## 反证腿

``TestRevertIsRed`` 直接把修复前的形状（传 ``raw_patient_id``）跑一遍，
断言"原文出现在落盘字节里"——即**这条用例在旧实现下必然红**。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.api.v1.imaging import extract_metadata_from_dataset
from app.services import dicom_storage as dicom_module
from app.services.dicom_storage import (
    PSEUDONYM_PREFIX,
    DicomStorageService,
    pseudonymize_patient_id,
    strip_dicom_phi,
)

#: 贯穿本文件的患者标识原文。刻意做成"人一眼能认出来"的形态，
#: 便于断言"它没出现在落盘字节 / 索引值 / 日志里"。
RAW_PATIENT_ID = "P-REAL-ID-778812"


def _dicom_bytes(*, patient_id: str = RAW_PATIENT_ID) -> bytes:
    """构造一份含 PHI 的内存 DICOM（无外部依赖）。"""
    from io import BytesIO

    from pydicom.dataset import FileDataset, FileMetaDataset

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.6"
    file_meta.MediaStorageSOPInstanceUID = "1.2.3.4"
    file_meta.TransferSyntaxUID = "1.2.840.10008.1.2"

    dataset = FileDataset(None, {}, file_meta=file_meta, preamble=b"\0" * 128)
    dataset.SpecificCharacterSet = "ISO_IR 192"
    dataset.PatientName = "张三"
    dataset.PatientID = patient_id
    dataset.PatientBirthDate = "19800101"
    dataset.StudyID = "STUDY-1"
    # 上传端点的 REQUIRED_DICOM_TAGS 校验需要这些（见 app/api/v1/imaging.py）
    dataset.StudyInstanceUID = "1.2.3"
    dataset.SeriesInstanceUID = "1.2.3.4"
    dataset.SOPInstanceUID = "1.2.3.4.5"
    dataset.Modality = "US"
    dataset.Rows = 2
    dataset.Columns = 2
    dataset.BitsAllocated = 8
    dataset.PixelData = b"\x00" * 4

    buffer = BytesIO()
    dataset.save_as(buffer)
    return buffer.getvalue()


def _read_patient_id(content: bytes) -> str:
    from io import BytesIO

    import pydicom

    return str(pydicom.dcmread(BytesIO(content), force=True)[0x0010, 0x0020].value)


@pytest.fixture
def service(tmp_path, monkeypatch):
    """本地后端 + tmp_path：绝不碰配置里的真实存储目录。"""
    monkeypatch.setattr(
        "app.services.dicom_storage.settings.dicom_storage_backend", "local"
    )
    monkeypatch.setattr(
        "app.services.dicom_storage.settings.dicom_storage_path", str(tmp_path)
    )
    instance = DicomStorageService()
    instance._max_file_size = 10 * 1024 * 1024
    return instance


class TestPseudonymShape:
    """假名本身的形态与三条语义（幂等 / 抗碰撞 / 不可逆）。"""

    def test_shape_is_prefixed_hex(self):
        name = pseudonymize_patient_id(RAW_PATIENT_ID)

        assert name.startswith(PSEUDONYM_PREFIX)
        digest = name[len(PSEUDONYM_PREFIX) :]
        assert len(digest) == 16
        assert all(c in "0123456789abcdef" for c in digest)

    def test_idempotent_same_raw_same_pseudonym(self):
        """同一原始 ID → 同一假名（否则同患者聚合断链）。"""
        assert pseudonymize_patient_id(RAW_PATIENT_ID) == pseudonymize_patient_id(
            RAW_PATIENT_ID
        )

    def test_distinct_raw_gives_distinct_pseudonym(self):
        """不同原始 ID → 不同假名（否则跨患者串档 = 数据泄露）。"""
        names = {
            pseudonymize_patient_id(f"P-{i:04d}") for i in range(200)
        }
        assert len(names) == 200

    def test_non_invertible_no_raw_substring(self):
        """输出里不含原文字符串（不可逆的最直白检验）。"""
        name = pseudonymize_patient_id(RAW_PATIENT_ID)

        assert RAW_PATIENT_ID not in name
        # 也不含任何长度 >= 4 的原文片段
        for start in range(len(RAW_PATIENT_ID) - 3):
            assert RAW_PATIENT_ID[start : start + 4] not in name

    def test_empty_and_whitespace_return_empty(self):
        """"没有患者标识" ≠ "假名"：空输入返回空串，别造一个看起来像标识的东西。"""
        assert pseudonymize_patient_id("") == ""
        assert pseudonymize_patient_id(None) == ""
        assert pseudonymize_patient_id("   ") == ""

    def test_salt_comes_from_settings_not_hardcoded(self, monkeypatch):
        """盐来自配置（换盐 → 换假名），不是写死的常量。"""
        monkeypatch.setattr(
            "app.services.dicom_storage.settings.jwt_secret_key", "salt-A"
        )
        first = pseudonymize_patient_id(RAW_PATIENT_ID)
        monkeypatch.setattr(
            "app.services.dicom_storage.settings.jwt_secret_key", "salt-B"
        )
        second = pseudonymize_patient_id(RAW_PATIENT_ID)

        assert first != second
        # 换盐后仍然不含原文
        assert RAW_PATIENT_ID not in second

    def test_falls_back_to_desensitization_hmac_key(self, monkeypatch):
        """``jwt_secret_key`` 为空时回退到既有脱敏 HMAC 密钥（仍是不可逆假名）。

        CI / 裸机可能没配 .env；此时**绝不能**回退成明文，只是盐换了来源。
        """
        monkeypatch.setattr("app.services.dicom_storage.settings.jwt_secret_key", "")

        name = pseudonymize_patient_id(RAW_PATIENT_ID)

        assert name.startswith(PSEUDONYM_PREFIX)
        assert len(name) > len(PSEUDONYM_PREFIX)
        assert RAW_PATIENT_ID not in name
        # 回退路径同样幂等
        assert name == pseudonymize_patient_id(RAW_PATIENT_ID)


class TestStoredBytesCarryNoRawPatientId:
    """落盘字节：``(0010,0020)`` 只能是假名。"""

    async def test_saved_dicom_patient_id_is_pseudonym(self, service):
        pseudonym = pseudonymize_patient_id(RAW_PATIENT_ID)

        path = await service.save(
            _dicom_bytes(), pseudonym, "sess_1", "1.2.3", storage_id=None
        )

        with open(path, "rb") as handle:
            assert _read_patient_id(handle.read()) == pseudonym

    async def test_saved_bytes_contain_no_raw_patient_id(self, service):
        """最直白的红线：磁盘上那份文件的字节里不许出现原文。"""
        pseudonym = pseudonymize_patient_id(RAW_PATIENT_ID)

        path = await service.save_by_id(
            _dicom_bytes(), "img_20260101_abcdef12", pseudonym
        )

        with open(path, "rb") as handle:
            written = handle.read()
        assert RAW_PATIENT_ID.encode() not in written
        assert _read_patient_id(written) == pseudonym

    async def test_same_patient_two_uploads_share_pseudonym(self, service):
        """同患者两次上传 → 落盘文件里的 PatientID 完全相同（可关联）。"""
        pseudonym = pseudonymize_patient_id(RAW_PATIENT_ID)

        first = await service.save_by_id(
            _dicom_bytes(), "img_20260101_aaaaaaaa", pseudonym
        )
        second = await service.save_by_id(
            _dicom_bytes(), "img_20260102_bbbbbbbb", pseudonym
        )

        with open(first, "rb") as f1, open(second, "rb") as f2:
            assert _read_patient_id(f1.read()) == _read_patient_id(f2.read())

    async def test_different_patients_do_not_collide(self, service):
        other_raw = "P-OTHER-990011"
        first = await service.save_by_id(
            _dicom_bytes(patient_id=RAW_PATIENT_ID),
            "img_20260101_aaaaaaaa",
            pseudonymize_patient_id(RAW_PATIENT_ID),
        )
        second = await service.save_by_id(
            _dicom_bytes(patient_id=other_raw),
            "img_20260101_cccccccc",
            pseudonymize_patient_id(other_raw),
        )

        with open(first, "rb") as f1, open(second, "rb") as f2:
            id_a, id_b = _read_patient_id(f1.read()), _read_patient_id(f2.read())
        assert id_a != id_b


class TestStripPrimitiveSemanticsUnchanged:
    """``strip_dicom_phi`` 仍是"传什么写什么"的通用原语（不许被本次改动带偏）。"""

    def test_strip_writes_exactly_the_given_value(self):
        stripped, _ = strip_dicom_phi(_dicom_bytes(), "WHATEVER-CALLER-PASSES")

        assert _read_patient_id(stripped) == "WHATEVER-CALLER-PASSES"

    def test_strip_does_not_pseudonymize_by_itself(self):
        """原语不偷偷换值 —— 假名化是**调用侧**的决定（否则会与索引侧不一致）。"""
        stripped, _ = strip_dicom_phi(_dicom_bytes(), RAW_PATIENT_ID)

        assert _read_patient_id(stripped) == RAW_PATIENT_ID


class TestUploadEndpointPassesPseudonym:
    """接线腿：上传端点传给存储/索引的 patient_id 必须是假名，不是原文。"""

    @pytest.mark.asyncio
    async def test_upload_wires_pseudonym_into_storage_and_index(self):
        """源码级 + 行为级双重钉住（端点函数体长，直调代价大）。

        行为级：用桩件跑一遍完整端点，捕获 storage/index 收到的 patient_id。
        """
        from unittest.mock import AsyncMock, patch

        from app.api.v1 import imaging as imaging_api
        from app.models.database import DiagnosisSession

        _ds, error, missing = imaging_api.parse_and_validate_dicom(_dicom_bytes())
        assert error is None, f"DICOM 构造失败：{error} {missing}"

        captured: dict = {}

        class _Storage:
            async def save(self, *, content, patient_id, session_id, sop_instance_uid, storage_id=None):
                captured["storage_patient_id"] = patient_id
                captured["storage_content"] = content
                return "/tmp/fake.dcm"

        class _Repo:
            def __init__(self, db):
                pass

            async def create(self, **kwargs):
                captured["index_patient_id"] = kwargs.get("patient_id")
                return MagicMock()

        session_repo = AsyncMock()
        session_repo.get_session.return_value = DiagnosisSession(
            session_id="sess_1",
            patient_id="P001",
            device_id="dev_1",
            status="active",
            target_organs=["thyroid"],
            organ_statuses={"thyroid": "in_progress"},
            knowledge_base_version="kb_v1.0.0",
            consent_status_at_creation="granted",
        )
        audit = AsyncMock()
        upload = MagicMock()
        upload.read = AsyncMock(return_value=_dicom_bytes())
        upload.filename = "test.dcm"

        with patch.object(imaging_api, "get_dicom_storage", lambda: _Storage()), patch.object(
            imaging_api, "DicomImageRepository", _Repo
        ):
            resp = await imaging_api.upload_dicom(
                file=upload,
                session_id="sess_1",
                priority=1,
                session_repo=session_repo,
                audit_service=audit,
                db=AsyncMock(),
                current_user={"user_id": "doc_1"},
            )

        expected = pseudonymize_patient_id(RAW_PATIENT_ID)
        # ① 落盘与索引都拿到假名
        assert captured["storage_patient_id"] == expected
        assert captured["index_patient_id"] == expected
        # ② 两者都不是原文
        assert captured["storage_patient_id"] != RAW_PATIENT_ID
        assert captured["index_patient_id"] != RAW_PATIENT_ID
        # ③ 对外展示形态仍是 X******（两种脱敏互不替代）
        assert resp.metadata["patient_id"] == "P******"

    def test_upload_source_does_not_pass_raw_patient_id(self):
        """源码级反证：端点里不许再把 ``raw_patient_id`` 传给存储/索引。"""
        import inspect

        from app.api.v1 import imaging as imaging_api

        source = inspect.getsource(imaging_api.upload_dicom)
        assert "patient_id=raw_patient_id" not in source
        assert "pseudonymize_patient_id(raw_patient_id)" in source

    def test_audit_detail_has_no_patient_id_plaintext(self):
        """审计 detail 不得出现 patient_id 明文（AGENTS.md #10）。

        只允许一个布尔标记 ``patient_id_anonymized``（"这条影像有没有病人归属"），
        便于运维核对而不留任何可反推线索。
        """
        import inspect

        from app.api.v1 import imaging as imaging_api

        source = inspect.getsource(imaging_api.upload_dicom)
        assert 'internal_metadata["patient_id_anonymized"]' in source
        # detail 里不许再塞原文或假名本身
        assert 'internal_metadata["patient_id"]' not in source

    def test_internal_metadata_has_no_patient_id_key(self):
        """``extract_metadata_from_dataset`` 的 internal_metadata 不含 PatientID。"""
        import pydicom

        ds = pydicom.Dataset()
        ds.PatientID = RAW_PATIENT_ID
        ds.PatientName = "张三"
        ds.Modality = "US"
        ds.StudyInstanceUID = "1.2.3"
        ds.SeriesInstanceUID = "1.2.3.4"
        ds.SOPInstanceUID = "1.2.3.4.5"
        ds.Rows = 2
        ds.Columns = 2
        ds.BitsAllocated = 8

        _response, internal, raw = extract_metadata_from_dataset(ds, b"x" * 16)

        assert raw == RAW_PATIENT_ID  # 原文只在返回值里（供调用侧假名化）
        assert "patient_id" not in internal
        assert RAW_PATIENT_ID not in str(internal)


class TestRevertIsRed:
    """**反证腿**：把修复前的形状（传原文）跑一遍 → 断言必然失败。

    这条用例本身是**通过**的（它断言"旧形状会泄露明文"），
    所以一旦有人把 ``pseudonymize_patient_id(raw_patient_id)`` 改回
    ``raw_patient_id``，上面 ``TestUploadEndpointPassesPseudonym`` 与
    ``TestStoredBytesCarryNoRawPatientId`` 立刻转红。
    """

    async def test_passing_raw_id_leaks_plaintext_into_stored_bytes(self, service):
        """旧实现（传 raw）→ 明文出现在落盘字节里；这正是被修掉的缺陷形态。"""
        path = await service.save_by_id(
            _dicom_bytes(), "img_20260101_deadbeef", RAW_PATIENT_ID
        )

        with open(path, "rb") as handle:
            written = handle.read()
        assert RAW_PATIENT_ID.encode() in written, (
            "若这条不再成立，说明 strip_dicom_phi 已改语义（本文件的假名方案要重新评估）"
        )
        assert _read_patient_id(written) == RAW_PATIENT_ID

    async def test_pseudonym_route_does_not_leak(self, service):
        """新实现（传假名）→ 同一份断言反过来了。两条并排，回归一眼可见。"""
        path = await service.save_by_id(
            _dicom_bytes(), "img_20260101_deadbeef",
            pseudonymize_patient_id(RAW_PATIENT_ID),
        )

        with open(path, "rb") as handle:
            written = handle.read()
        assert RAW_PATIENT_ID.encode() not in written


class TestSimilarCasesStaysConsistent:
    """同患者聚合不能断：写入侧与查询侧必须用**同一套**假名。"""

    def test_scope_patient_id_matches_index_side(self):
        from app.services.similar_cases import scope_patient_id

        assert scope_patient_id("self", RAW_PATIENT_ID) == pseudonymize_patient_id(
            RAW_PATIENT_ID
        )

    def test_scope_patient_id_none_for_missing(self):
        from app.services.similar_cases import scope_patient_id

        assert scope_patient_id("self", None) is None
        assert scope_patient_id("self", "") is None
        assert scope_patient_id("self", "   ") is None

    def test_deidentified_scope_is_not_pseudonymized(self):
        """跨病人通道按器官过滤，不用 patient_id —— 不要多此一举改变语义。"""
        from app.services.similar_cases import scope_patient_id

        assert scope_patient_id("deidentified", RAW_PATIENT_ID) == RAW_PATIENT_ID

    def test_filter_uses_pseudonym_not_raw(self):
        """``find_similar_cases`` 送给向量库的 filter 值必须是假名。"""
        import asyncio
        from unittest.mock import patch

        from app.services import similar_cases

        captured: dict = {}

        class _Index:
            def __init__(self, **kwargs):
                pass

            async def search(self, *, collection, query_vector, top_k, filter):
                captured["filter"] = filter
                return []

            async def close(self):
                return None

        class _Clip:
            def __init__(self, **kwargs):
                pass

            async def embed_image(self, request):
                return MagicMock(embeddings=[[0.1, 0.2]])

            async def close(self):
                return None

        with patch.object(similar_cases, "frame_png_of", lambda content: b"png"), patch(
            "app.embedding.ultrasound_clip.UltrasoundClipService", _Clip
        ), patch("app.embedding.qdrant_client.QdrantVectorIndex", _Index):
            asyncio.run(
                similar_cases.find_similar_cases(
                    image_content=b"dcm",
                    patient_id=RAW_PATIENT_ID,
                    organ="thyroid",
                    scope="self",
                )
            )

        assert captured["filter"] == {
            "patient_id": pseudonymize_patient_id(RAW_PATIENT_ID)
        }
        assert RAW_PATIENT_ID not in str(captured["filter"])


class TestPseudonymModuleContract:
    """模块级契约：假名化函数从存储模块直接取用（调用侧不必碰 pydicom）。"""

    def test_importable_from_storage_module(self):
        assert dicom_module.pseudonymize_patient_id is pseudonymize_patient_id

    def test_imaging_endpoint_imports_it(self):
        """上传端点必须从存储模块引入，而不是在 API 层另写一套哈希。"""
        import inspect

        from app.api.v1 import imaging as imaging_api

        source = inspect.getsource(imaging_api)
        assert "from app.services.dicom_storage import" in source
        assert "pseudonymize_patient_id" in source
        # 不许在 API 层自己拼 HMAC（那样会出现第二套口径，聚合必然断）
        assert "hmac.new" not in source
