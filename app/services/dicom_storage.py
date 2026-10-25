"""DICOM文件存储服务 - 支持本地文件系统和MinIO后端"""
import asyncio
import hashlib
import hmac
import os
import re
import shutil
from datetime import datetime, timezone
from io import BytesIO

import aiofiles
import structlog

from app.config.settings import settings

logger = structlog.get_logger()

# ----------------------------------------------------------------------
# DICOM PatientID → 带盐不可逆假名（A.88）
#
# ## 为什么必须有这一步（真实缺陷）
#
# ``strip_dicom_phi`` 的语义是"把 (0010,0020) PatientID **写成传进来的那个值**"
# （见 :func:`strip_dicom_phi`，行为已被
# ``tests/unit/services/test_dicom_storage_service.py::test_rewrites_patient_id``
# 钉住）。而上传接口原先把 DICOM 原始 PatientID 原样传进来 → 落盘文件里
# PatientID **等于原文**，"脱敏"形同虚设：拿到 dcm 文件即可直接读到患者标识。
# 修法刻意**不动 strip_dicom_phi 的语义**（它是通用写入原语，传什么写什么），
# 而是在调用侧决定"传什么"—— 传假名。
#
# ## 为什么是"带盐不可逆假名"而不是删掉 PatientID
#
# * 删掉（或写死常量）会**打断同患者聚合**：相似病例 ``scope="self"``
#   与影像向量的病人隔离都靠 patient_id 相等来归并同一患者的历次检查；
# * 因此需要"同一原始 ID → 同一假名"（幂等），"不同原始 ID → 不同假名"
#   （不可混同），且**不可逆**（不能反推原文）。
# * 用 HMAC-SHA256 而不是裸 sha256(salt+raw)：裸 sha256 有长度扩展问题，
#   且仓库既有脱敏机制（``DesensitizationEngine._hash_patient_name``）用的就是
#   HMAC，这里保持同一手法，不新造第三套。
#
# ## 盐从哪来（不新引入硬编码常量）
#
# 唯一部署级稳定密钥 ``settings.jwt_secret_key``（.env 配置，两个服务共见）。
# 该字段**不承担 JWT 签名**（JWT 由 HSM 持钥签发，见 00b 统一配置参考手册），
# 所以拿它做 HMAC 盐不会削弱任何现有安全属性。
#
# 回退条件（写死在这里，便于审计）：
#   ① ``jwt_secret_key`` 为空（CI / 裸机未配 .env）→ 用仓库既有的脱敏 HMAC 密钥
#      （``DesensitizationEngine._name_hmac_key``，Redis 持久化，重启后同值）；
#   ② 两者都取不到（Redis 未载入且无配置）→ 仍产出不可逆假名（**绝不回退成明文**），
#      但跨进程不可关联，并打 warning。这是刻意的降级：**宁可断链，不可泄 PHI**。
# ----------------------------------------------------------------------

#: 假名前缀：让"这是脱敏标识"在磁盘路径 / DB / 向量 payload 里一眼可辨。
PSEUDONYM_PREFIX = "anon_"

#: 派生上下文标签：把本用途与其它 HMAC 用途隔开（同一把钥匙不产生同构输出）。
_PSEUDONYM_CONTEXT = b"dicom-patient-id-pseudonym-v1"


def _pseudonym_key() -> bytes:
    """解析假名盐（部署级稳定密钥）。回退链见本节文件头注释。"""
    secret = (getattr(settings, "jwt_secret_key", "") or "").strip()
    if secret:
        return _PSEUDONYM_CONTEXT + secret.encode("utf-8")

    try:
        from app.security.desensitization import desensitization_engine

        key = getattr(desensitization_engine, "_name_hmac_key", None)
    except Exception as exc:  # 取不到盐不能阻止上传，但必须留痕（零静默约定）
        logger.warning(
            "dicom_patient_pseudonym_key_unavailable",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        key = None
    if isinstance(key, (bytes, bytearray)) and key:
        return _PSEUDONYM_CONTEXT + bytes(key)

    logger.warning(
        "dicom_patient_pseudonym_salt_missing",
        hint="jwt_secret_key 与脱敏 HMAC 密钥均不可用：假名不可逆但跨进程不可关联",
    )
    return _PSEUDONYM_CONTEXT


def pseudonymize_patient_id(raw_patient_id: str | None) -> str:
    """把 DICOM 原始 PatientID 换成**带盐不可逆假名**（``anon_<16 hex>``）。

    语义（单测钉死这三条，别改口径）：
      * **幂等**：同一原始 ID → 同一假名（否则同患者历次检查断链，
        相似病例 ``scope="self"`` 与影像向量的病人隔离全部失效）；
      * **抗碰撞**：不同原始 ID → 不同假名（否则跨患者串档，是数据泄露不是精度问题）；
      * **不可逆**：输出里不含原文字符串，无密钥无法反推。

    空输入返回空串（不上假名——"没有患者标识"和"假名"是两件事，别混淆）。

    调用侧口径（A.88）：**落盘 + 索引 + 向量 payload 一律用假名**，
    响应体里的 ``X******`` 展示形态由 ``app/api/v1/imaging.py`` 另行生成，
    两者互不替代。
    """
    raw = str(raw_patient_id or "").strip()
    if not raw:
        return ""
    digest = hmac.new(_pseudonym_key(), raw.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{PSEUDONYM_PREFIX}{digest[:16]}"


_PHI_TAGS = [
    (0x0010, 0x0010),
    (0x0010, 0x0030),
    (0x0010, 0x1000),
    (0x0010, 0x1001),
    (0x0010, 0x1040),
    (0x0010, 0x2154),
    (0x0008, 0x0050),
    (0x0008, 0x0080),
    (0x0008, 0x0081),
    (0x0008, 0x0090),
    (0x0008, 0x1070),
    (0x0010, 0x1020),
    (0x0010, 0x1021),
    (0x0010, 0x1030),
    (0x0010, 0x2155),
    (0x0010, 0x2160),
    (0x0010, 0x2180),
    (0x0010, 0x21B0),
    (0x0010, 0x21D0),
    (0x0010, 0x21F0),
    (0x0010, 0x2201),
    (0x0010, 0x2202),
    (0x0010, 0x2203),
    (0x0008, 0x009C),
    (0x0008, 0x009D),
    (0x0008, 0x1060),
    (0x0008, 0x1072),
    (0x0008, 0x1080),
    (0x0008, 0x1084),
    (0x0008, 0x1110),
    (0x0008, 0x1120),
]


def strip_dicom_phi(content: bytes, patient_id: str) -> tuple[bytes, bool]:
    """DICOM PHI 去标识化。失败即抛错（fail-closed）——调用方拒绝存储。

    P0-5（15 号计划）：原实现 except 分支 `return content, False` 会把
    **未脱敏原文**继续落盘（fail-open），患者 PHI 直接入库，真机审计判定
    为红线缺陷。
    """
    stripped = False
    try:
        import pydicom

        ds = pydicom.dcmread(BytesIO(content), force=True)

        for group, elem in _PHI_TAGS:
            if (group, elem) in ds:
                del ds[(group, elem)]
                stripped = True

        ds.remove_private_tags()
        stripped = True

        if (0x0010, 0x0020) in ds:
            ds[0x0010, 0x0020].value = patient_id
            stripped = True

        buf = BytesIO()
        ds.save_as(buf)
        return buf.getvalue(), stripped
    except Exception as e:
        logger.error("dicom_phi_strip_failed_fail_closed", error=str(e))
        raise ValueError(
            f"DICOM PHI 去标识化失败，拒绝存储（fail-closed）: {e}"
        ) from e


class DicomStorageService:

    def __init__(self):
        self._backend = settings.dicom_storage_backend
        self._base_path = settings.dicom_storage_path
        self._max_file_size = settings.dicom_storage_max_file_size
        self._minio_client = None
        if self._backend == "local":
            os.makedirs(self._base_path, exist_ok=True)

    def _get_minio_client(self):
        if self._minio_client is None:
            from minio import Minio
            self._minio_client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
            )
            if not self._minio_client.bucket_exists(settings.minio_bucket):
                self._minio_client.make_bucket(settings.minio_bucket)
        return self._minio_client

    def _build_storage_path(self, patient_id: str, session_id: str, sop_instance_uid: str) -> str:
        date_prefix = datetime.now(timezone.utc).strftime("%Y%m%d")
        return os.path.join(
            self._base_path,
            date_prefix,
            patient_id,
            session_id,
            f"{sop_instance_uid}.dcm",
        )

    # ------------------------------------------------------------------
    # 按存储 ID 存取（供 WADO 回读用）
    #
    # 背景：上传/推流接口只把 `img_YYYYMMDD_hex8` / `frame_YYYYMMDD_hex8`
    # 这类 ID 返回给客户端，而 `_build_storage_path` 生成的是
    # `{date}/{patient}/{session}/{sop}.dcm` —— 两者之间**没有任何映射记录**，
    # 所以此前无法按 ID 取回字节（浏览器显示 DICOM 缺的就是这条回读路径）。
    #
    # 这里不引入数据库表：ID 本身就带日期（`img_20261002_ab12cd34` 中的
    # `20261002`），因此路径可由 ID **确定性推导**，无需索引、也不会把
    # 文件系统路径暴露给客户端。
    # ------------------------------------------------------------------

    STORAGE_ID_PATTERN = re.compile(r"^(?:img|frame)_(\d{8})_[0-9a-f]{8}$")

    def path_for_id(self, storage_id: str) -> str | None:
        """由存储 ID 反推绝对路径；ID 格式非法或不含日期时返回 None。"""
        m = self.STORAGE_ID_PATTERN.match(storage_id or "")
        if not m:
            return None
        date_prefix = m.group(1)
        if self._backend == "minio":
            return f"minio://{settings.minio_bucket}/{date_prefix}/{storage_id}.dcm"
        candidate = os.path.join(self._base_path, date_prefix, f"{storage_id}.dcm")
        # 双保险：即使 pattern 将来放宽，也必须仍落在 base 之内
        base = os.path.abspath(self._base_path)
        if os.path.abspath(candidate).startswith(base + os.sep):
            return candidate
        return None

    async def save_by_id(self, content: bytes, storage_id: str, patient_id: str) -> str:
        """按存储 ID 落盘（去 PHI 后）。返回可交给 retrieve() 的路径。"""
        if len(content) > self._max_file_size:
            raise ValueError(
                f"DICOM file size {len(content)} exceeds maximum {self._max_file_size}"
            )

        storage_path = self.path_for_id(storage_id)
        if storage_path is None:
            raise ValueError(f"Invalid storage id: {storage_id}")

        # 并发审查 P0-3：strip_dicom_phi 内部 dcmread + remove_private_tags +
        # save_as（同一份数据解析两遍、序列化一遍），超声单帧 1~30MB 时
        # 典型 20~150ms 纯 CPU——必须 offload，否则并发上传会把事件循环打满。
        stripped_content, was_stripped = await asyncio.to_thread(
            strip_dicom_phi, content, patient_id
        )
        if was_stripped:
            logger.info("dicom_phi_stripped_before_save", storage_id=storage_id)

        if self._backend == "local":
            os.makedirs(os.path.dirname(storage_path), exist_ok=True)
            temp_path = storage_path + ".tmp"
            try:
                async with aiofiles.open(temp_path, "wb") as f:
                    await f.write(stripped_content)
                shutil.move(temp_path, storage_path)
            except Exception:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise
            logger.info(
                "dicom_saved_by_id",
                backend="local",
                storage_id=storage_id,
                size=len(stripped_content),
            )
            return storage_path

        if self._backend == "minio":
            client = self._get_minio_client()
            bucket, _, key = storage_path.partition("minio://")[2].partition("/")
            client.put_object(bucket, key, stripped_content, content_type="application/dicom")
            return storage_path

        raise ValueError(f"Unknown storage backend: {self._backend}")

    async def save(
        self,
        content: bytes,
        patient_id: str,
        session_id: str,
        sop_instance_uid: str,
        storage_id: str | None = None,
    ) -> str:
        """落盘一份 DICOM（去 PHI 后）。

        Args:
            storage_id: **传了就按 ID 命名**（``{date}/{storage_id}.dcm`` 扁平布局），
                与 :meth:`path_for_id` 严格自洽——这是"用 image_ids 取回字节"的前提。
                不传则沿用旧的 ``{date}/{patient}/{session}/{sop}.dcm`` 三层布局
                （保留给既有调用方与历史数据）。
                传 ID 还顺带消除一个坑：旧布局的日期取 ``datetime.now(UTC)``，
                与 ``generate_image_id()`` 的日期在**跨零点**时会不一致，
                导致 path_for_id 的确定性推导再错一列。

        Returns:
            可直接传给 :meth:`retrieve` 的 storage_path。
        """
        if storage_id:
            # 走按 ID 落盘：路径由 ID 推导，天然与 path_for_id 一致
            return await self.save_by_id(content, storage_id, patient_id)

        if len(content) > self._max_file_size:
            raise ValueError(
                f"DICOM file size {len(content)} exceeds maximum {self._max_file_size}"
            )

        stripped_content, was_stripped = await asyncio.to_thread(
            strip_dicom_phi, content, patient_id
        )
        if was_stripped:
            logger.info("dicom_phi_stripped_before_save", patient_id=patient_id, session_id=session_id)

        if self._backend == "local":
            return await self._save_local(stripped_content, patient_id, session_id, sop_instance_uid)
        elif self._backend == "minio":
            return await self._save_minio(stripped_content, patient_id, session_id, sop_instance_uid)
        else:
            raise ValueError(f"Unknown storage backend: {self._backend}")

    async def _save_local(
        self,
        content: bytes,
        patient_id: str,
        session_id: str,
        sop_instance_uid: str,
    ) -> str:
        storage_path = self._build_storage_path(patient_id, session_id, sop_instance_uid)
        os.makedirs(os.path.dirname(storage_path), exist_ok=True)

        temp_path = storage_path + ".tmp"
        try:
            async with aiofiles.open(temp_path, "wb") as f:
                await f.write(content)
            shutil.move(temp_path, storage_path)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

        logger.info(
            "dicom_file_saved",
            backend="local",
            path=storage_path,
            size=len(content),
            patient_id=patient_id,
            session_id=session_id,
        )
        return storage_path

    async def _save_minio(
        self,
        content: bytes,
        patient_id: str,
        session_id: str,
        sop_instance_uid: str,
    ) -> str:
        object_key = self._build_minio_key(patient_id, session_id, sop_instance_uid)
        client = self._get_minio_client()
        client.put_object(
            settings.minio_bucket,
            object_key,
            BytesIO(content),
            length=len(content),
            content_type="application/dicom",
        )
        logger.info(
            "dicom_file_saved",
            backend="minio",
            object_key=object_key,
            size=len(content),
            patient_id=patient_id,
            session_id=session_id,
        )
        return f"minio://{settings.minio_bucket}/{object_key}"

    def _build_minio_key(self, patient_id: str, session_id: str, sop_instance_uid: str) -> str:
        date_prefix = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"{date_prefix}/{patient_id}/{session_id}/{sop_instance_uid}.dcm"

    async def retrieve(self, storage_path: str) -> bytes | None:
        if self._backend == "local":
            if os.path.exists(storage_path):
                async with aiofiles.open(storage_path, "rb") as f:
                    return await f.read()
            return None
        elif self._backend == "minio":
            if storage_path.startswith("minio://"):
                _, _, path = storage_path.partition("minio://")
                bucket, _, key = path.partition("/")
                if bucket and key:
                    client = self._get_minio_client()
                    response = client.get_object(bucket, key)
                    return response.read()
            return None
        return None

    async def delete(self, storage_path: str) -> bool:
        if self._backend == "local":
            if os.path.exists(storage_path):
                os.remove(storage_path)
                return True
            return False
        elif self._backend == "minio":
            if storage_path.startswith("minio://"):
                _, _, path = storage_path.partition("minio://")
                bucket, _, key = path.partition("/")
                if bucket and key:
                    client = self._get_minio_client()
                    client.remove_object(bucket, key)
                    return True
            return False
        return False

    async def replace(self, storage_path: str, content: bytes) -> bool:
        """**原地替换**已有影像的字节（A.93：历史患者标识迁移需要改 DICOM 头）。

        ## 为什么需要这个方法

        `save()` 是"新建"语义（生成新 id / 新路径）；迁移要的是"同一条记录、
        同一个 `storage_path`、只换文件内容" —— 用 `save()` 会产生**孤儿文件**
        且让 `dicom_images.storage_path` 指向旧文件，影像与记录当场断链。

        ## 安全约束

        * **只覆盖已存在的路径**（`os.path.exists` / MinIO 对象存在）；
          路径不存在返回 ``False`` 而不是创建 —— 防止"迁移把记录指向一个
          其实不存在的文件"，那会让影像永久丢失且不易发现；
        * 先写同目录临时文件再 `os.replace`（原子替换）：中途失败不会留下
          半个 DICOM 文件（旧文件仍完整）。

        Args:
            storage_path: 已存在的存储路径（与 `retrieve` 同一形态）。
            content: 新的完整文件字节。

        Returns:
            是否成功替换。
        """
        if self._backend == "local":
            if not os.path.exists(storage_path):
                return False
            tmp_path = f"{storage_path}.tmp-{os.getpid()}"
            async with aiofiles.open(tmp_path, "wb") as f:
                await f.write(content)
            os.replace(tmp_path, storage_path)  # 原子替换
            return True
        elif self._backend == "minio":
            if storage_path.startswith("minio://"):
                _, _, path = storage_path.partition("minio://")
                bucket, _, key = path.partition("/")
                if bucket and key:
                    client = self._get_minio_client()
                    # 先确认对象存在（同 local 分支的语义：不凭空创建）
                    try:
                        client.stat_object(bucket, key)
                    except Exception as exc:
                        logger.warning(
                            "dicom_replace_object_missing",
                            bucket=bucket,
                            error_type=type(exc).__name__,
                        )
                        return False
                    client.put_object(
                        bucket, key, BytesIO(content), len(content),
                        content_type="application/dicom",
                    )
                    return True
            return False
        return False


_dicom_storage: DicomStorageService | None = None


def get_dicom_storage() -> DicomStorageService:
    global _dicom_storage
    if _dicom_storage is None:
        _dicom_storage = DicomStorageService()
    return _dicom_storage
