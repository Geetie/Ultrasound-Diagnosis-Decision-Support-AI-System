"""DICOM 存储服务补测（quality-debt，核心模块 app/services；46.9% -> 目标高位）。

本模块的未覆盖部分集中在**PHI 去标识化**与**落盘/回滚**，都是合规关键路径：
漏删一个患者标识标签就是一起合规事故，而"存进去了但内容不对"在功能测试里看不出来。

覆盖要点：
- `strip_dicom_phi` 真的删掉患者标识标签、改写 PatientID、清私有标签，
  同时**保留**非 PHI 的临床字段（删多了会让影像不可用）；
- 解析失败时**抛错拒存（fail-closed，P0-5 15 号计划落地）**——原"失败开放"
  语义（fail-open，未脱敏原文继续落盘）已于 2026-09-30 按产品决策废弃，
  见 `test_parse_failure_is_fail_closed`；
- `_save_local` 先写 `.tmp` 再原子移动，失败时清掉临时文件（不留垃圾、不留半个文件）；
- MinIO 分支只断言命令形状（bucket/key/content_type），真实对象存储读写属真机验证项。
"""
import os
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from app.services import dicom_storage as dicom_module
from app.services.dicom_storage import (
    DicomStorageService,
    get_dicom_storage,
    strip_dicom_phi,
)

MINIO_BUCKET = "ultrasound-dicom"


def _dicom_bytes() -> bytes:
    """构造一份含 PHI 与私有标签的内存 DICOM（无外部依赖）。"""
    from pydicom.dataset import FileDataset, FileMetaDataset

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.6"
    file_meta.MediaStorageSOPInstanceUID = "1.2.3.4"
    file_meta.TransferSyntaxUID = "1.2.840.10008.1.2"

    dataset = FileDataset(None, {}, file_meta=file_meta, preamble=b"\0" * 128)
    # 不声明字符集的话 pydicom 会按 iso8859 编码，中文会变成 "?????"（第一版就栽在这）
    dataset.SpecificCharacterSet = "ISO_IR 192"
    dataset.PatientName = "张三"
    dataset.PatientID = "P-REAL-ID"
    dataset.PatientBirthDate = "19800101"
    dataset.StudyID = "STUDY-1"           # 非 PHI，必须保留
    dataset.SeriesDescription = "甲状腺横切"  # 非 PHI，必须保留
    dataset.add_new(0x00090010, "LO", "private-value")  # 私有标签

    buffer = BytesIO()
    dataset.save_as(buffer)
    return buffer.getvalue()


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


@pytest.fixture
def minio_service(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.dicom_storage.settings.dicom_storage_backend", "minio"
    )
    instance = DicomStorageService()
    fake_client = MagicMock()
    fake_client.bucket_exists.return_value = True
    instance._minio_client = fake_client
    return instance, fake_client


class TestStripDicomPhi:
    def test_removes_patient_identifiers(self):
        import pydicom

        stripped, was_stripped = strip_dicom_phi(_dicom_bytes(), "P-ANON")
        dataset = pydicom.dcmread(BytesIO(stripped), force=True)

        assert was_stripped is True
        assert (0x0010, 0x0010) not in dataset, "患者姓名必须删除"
        assert (0x0010, 0x0030) not in dataset, "出生日期必须删除"
        assert (0x0008, 0x0080) not in dataset, "机构名必须删除"

    def test_rewrites_patient_id(self):
        import pydicom

        stripped, _ = strip_dicom_phi(_dicom_bytes(), "P-ANON")
        dataset = pydicom.dcmread(BytesIO(stripped), force=True)

        assert dataset[0x0010, 0x0020].value == "P-ANON"

    def test_removes_private_tags(self):
        """私有标签是 PHI 常见的漏网入口（厂商会往里塞姓名与备注）。"""
        import pydicom

        stripped, _ = strip_dicom_phi(_dicom_bytes(), "P-ANON")
        dataset = pydicom.dcmread(BytesIO(stripped), force=True)

        assert (0x0009, 0x0010) not in dataset

    def test_keeps_clinical_fields(self):
        """删多了会让影像失去临床价值：非 PHI 字段必须保留。"""
        import pydicom

        stripped, _ = strip_dicom_phi(_dicom_bytes(), "P-ANON")
        dataset = pydicom.dcmread(BytesIO(stripped), force=True)

        assert dataset.StudyID == "STUDY-1"
        assert dataset.SeriesDescription == "甲状腺横切"

    def test_stripped_flag_is_true_whenever_parsing_succeeds(self):
        """`stripped=True` 的含义是"走过脱敏流程"，不是"确实删掉了东西"。

        `ds.remove_private_tags()` 是无条件调用的，因此成功解析就必然置 True
        （见 app/services/dicom_storage.py:61-62）。钉住这一点，避免调用方把它
        误解成"本次必有改动"。
        """
        import pydicom
        from pydicom.dataset import FileDataset, FileMetaDataset

        file_meta = FileMetaDataset()
        file_meta.TransferSyntaxUID = "1.2.840.10008.1.2"
        clean = FileDataset(None, {}, file_meta=file_meta, preamble=b"\0" * 128)
        clean.StudyID = "ONLY-NON-PHI"
        buffer = BytesIO()
        clean.save_as(buffer)

        _, was_stripped = strip_dicom_phi(buffer.getvalue(), "P-ANON")

        assert was_stripped is True
        assert pydicom.dcmread(BytesIO(buffer.getvalue()), force=True).StudyID

    def test_parse_failure_is_fail_closed(self):
        """**失败即拒存（fail-closed，P0-5 落地）**：脱敏失败必须抛错，绝不返回原文。

        历史：原实现 except 分支 `return content, False`（fail-open）——无法
        脱敏的文件带着 PHI 原样落盘。15 号计划 P0-5 产品决策改为 fail-closed。

        注意：要让异常分支真的走到，必须让 dcmread 抛错——`force=True` 极其宽松，
        垃圾字节也会被"解析"成功（见下一条用例），所以这里直接打桩。
        """
        payload = b"not-a-dicom-file"

        with patch("pydicom.dcmread", side_effect=RuntimeError("corrupt header")):
            with pytest.raises(ValueError, match="fail-closed"):
                strip_dicom_phi(payload, "P-ANON")

    def test_garbage_bytes_are_force_parsed_not_rejected(self):
        """`force=True` 的后果：垃圾字节不会被拒绝，而是被改写后照常入库。

        第一版我断言"垃圾字节会走异常分支"，实测是错的——pydicom 用 force 模式给
        这些字节编出了一个数据集（未知 VR 记作 UN）再重新序列化。也就是说
        **输入校验发生在别处**（上传接口/SCP），本函数不承担"这到底是不是 DICOM"的判断。
        把它记下来，免得后续有人误以为这里有一道格式校验。
        """
        payload = b"not-a-dicom-file"

        content, was_stripped = strip_dicom_phi(payload, "P-ANON")

        assert content != payload, "被重新序列化了，说明确实走了正常路径"
        assert was_stripped is True

    def test_empty_content_goes_through_normal_path(self):
        """空字节同样不算"解析失败"：返回空字节且 stripped=True。"""
        content, was_stripped = strip_dicom_phi(b"", "P-ANON")

        assert content == b""
        assert was_stripped is True


class TestSaveDispatch:
    async def test_local_returns_filesystem_path(self, service, tmp_path):
        path = await service.save(_dicom_bytes(), "P-1", "sess_1", "1.2.3")

        assert path.startswith(str(tmp_path))
        assert path.endswith("1.2.3.dcm")

    async def test_saved_bytes_are_stripped(self, service):
        """落盘的必须是脱敏后的内容——这是本模块存在的根本理由。"""
        import pydicom

        path = await service.save(_dicom_bytes(), "P-ANON", "sess_1", "1.2.3")
        dataset = pydicom.dcmread(path, force=True)

        assert (0x0010, 0x0010) not in dataset

    async def test_oversized_file_rejected(self, service):
        service._max_file_size = 8

        with pytest.raises(ValueError, match="exceeds maximum"):
            await service.save(b"x" * 64, "P-1", "sess_1", "1.2.3")

    async def test_unknown_backend_rejected(self, service):
        """后端写错时必须明确报错，而不是把文件悄悄丢掉。"""
        service._backend = "tape"

        with pytest.raises(ValueError, match="Unknown storage backend"):
            await service.save(_dicom_bytes(), "P-1", "sess_1", "1.2.3")


class TestSaveLocal:
    async def test_creates_nested_directories(self, service, tmp_path):
        path = await service._save_local(b"data", "P-1", "sess_1", "1.2.3")

        assert os.path.exists(path)
        assert os.path.dirname(path).startswith(str(tmp_path))
        assert os.path.isdir(os.path.dirname(path)), "父目录必须按日期/患者/会话分级创建"

    async def test_no_temp_file_left_behind(self, service):
        path = await service._save_local(b"data", "P-1", "sess_1", "1.2.3")

        assert not os.path.exists(path + ".tmp")

    async def test_move_failure_removes_temp_file(self, service, tmp_path):
        """移动失败必须清掉 .tmp：否则磁盘上会留下半个文件与一批垃圾。"""
        with patch.object(
            dicom_module.shutil, "move", side_effect=OSError("disk full")
        ), pytest.raises(OSError):
            await service._save_local(b"data", "P-1", "sess_1", "1.2.3")

        assert list(tmp_path.rglob("*.tmp")) == []


class TestLocalRetrieveDelete:
    async def test_roundtrip(self, service):
        path = await service.save(_dicom_bytes(), "P-1", "sess_1", "1.2.3")

        content = await service.retrieve(path)

        assert content and content.startswith(b"\x00" * 128)

    async def test_missing_file_returns_none(self, service, tmp_path):
        assert await service.retrieve(str(tmp_path / "nope.dcm")) is None

    async def test_delete_existing(self, service):
        path = await service.save(_dicom_bytes(), "P-1", "sess_1", "1.2.3")

        assert await service.delete(path) is True
        assert await service.retrieve(path) is None

    async def test_delete_missing_returns_false(self, service, tmp_path):
        assert await service.delete(str(tmp_path / "nope.dcm")) is False


class TestMinioBackend:
    def test_client_is_cached_and_bucket_created(self, monkeypatch):
        """bucket 不存在时要建桶；建好后必须复用客户端（每次新建会泄漏连接）。"""
        fake_cls = MagicMock()
        fake_client = fake_cls.return_value
        fake_client.bucket_exists.return_value = False
        monkeypatch.setattr("minio.Minio", fake_cls)
        instance = DicomStorageService()

        first = instance._get_minio_client()
        second = instance._get_minio_client()

        assert first is second
        assert fake_cls.call_count == 1
        fake_client.make_bucket.assert_called_once()

    async def test_save_returns_minio_uri(self, minio_service):
        service, client = minio_service

        uri = await service.save(_dicom_bytes(), "P-1", "sess_1", "1.2.3")

        assert uri.startswith("minio://")
        assert uri.endswith("/1.2.3.dcm")
        assert client.put_object.call_args.kwargs["content_type"] == "application/dicom"

    async def test_save_key_layout(self, minio_service):
        service, client = minio_service

        await service.save(_dicom_bytes(), "P-1", "sess_1", "1.2.3")

        bucket, key = client.put_object.call_args.args[:2]
        assert bucket
        assert key.endswith("P-1/sess_1/1.2.3.dcm")

    async def test_retrieve_reads_object(self, minio_service):
        service, client = minio_service
        client.get_object.return_value = MagicMock(read=MagicMock(return_value=b"blob"))

        assert await service.retrieve("minio://bucket/20260101/P-1/s/1.dcm") == b"blob"

    async def test_retrieve_without_key_returns_none(self, minio_service):
        """`minio://bucket` 这种缺 key 的路径不能去调对象存储，直接判定取不到。"""
        service, client = minio_service

        assert await service.retrieve("minio://bucket") is None
        client.get_object.assert_not_called()

    async def test_retrieve_with_wrong_scheme_returns_none(self, minio_service):
        service, _ = minio_service

        assert await service.retrieve("/local/path.dcm") is None

    async def test_delete_removes_object(self, minio_service):
        service, client = minio_service

        assert await service.delete("minio://bucket/20260101/P-1/s/1.dcm") is True
        assert client.remove_object.call_args.args[1].endswith("1.dcm")

    async def test_delete_without_key_returns_false(self, minio_service):
        service, client = minio_service

        assert await service.delete("minio://bucket") is False
        client.remove_object.assert_not_called()


class TestSingleton:
    def test_get_dicom_storage_is_singleton(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dicom_module, "_dicom_storage", None)
        monkeypatch.setattr(
            "app.services.dicom_storage.settings.dicom_storage_backend", "local"
        )
        monkeypatch.setattr(
            "app.services.dicom_storage.settings.dicom_storage_path", str(tmp_path)
        )

        assert get_dicom_storage() is get_dicom_storage()

    def test_path_builders_include_date_and_ids(self, service, tmp_path):
        path = service._build_storage_path("P-1", "sess_1", "1.2.3")
        key = service._build_minio_key("P-1", "sess_1", "1.2.3")

        assert str(tmp_path) in path
        assert key.endswith("P-1/sess_1/1.2.3.dcm")
        assert path.split("sess_1")[0].endswith(("\\", "/"))
