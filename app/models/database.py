from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    TIMESTAMP,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.config.database import Base


class Patient(Base):
    __tablename__ = 'patients'

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(Text)
    birth_date = Column(Date)
    gender = Column(String(10))
    created_at = Column(TIMESTAMP(timezone=True), default=func.current_timestamp())
    updated_at = Column(TIMESTAMP(timezone=True), default=func.current_timestamp(), onupdate=func.current_timestamp())


class ConsentRecord(Base):
    __tablename__ = 'consent_records'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    record_id = Column(BigInteger, unique=True, nullable=False, index=True)
    patient_id = Column(String(64), nullable=False, index=True)
    status = Column(String(16), nullable=False)
    granted_at = Column(TIMESTAMP(timezone=True))
    withdrawn_at = Column(TIMESTAMP(timezone=True))
    consent_version = Column(String(16), nullable=False, default="1.0")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())
    # 2026-09-29 真机（第 1 例真实诊断）暴露：`consent_records` 被**两个服务各自声明**——
    # app 这里是"表的所有者"（迁移建表），而 `safety_gateway/sg_models.py` 用自己的 Base
    # 也声明了同名表，且**多出 updated_at**。SG 的同意查询是 `SELECT *`（ORM 全列），
    # 于是真机报：`UndefinedColumnError: column consent_records.updated_at does not exist`
    # → 503 `consent_check_db_error` → 主服务 fail-closed，诊断必 failed。
    # 共享表必须取**双方所需列的并集**，故补上这一列（迁移 0013）。
    updated_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    __table_args__ = (
        Index('idx_consent_patient_status', 'patient_id', 'status'),
    )


class DiagnosisSession(Base):
    __tablename__ = 'sessions'
    __table_args__ = (
        Index('idx_sessions_patient_status', 'patient_id', 'status'),
    )

    session_id = Column(String(64), primary_key=True)  # 业务主键：外部系统生成的会话标识符，保留String类型
    patient_id = Column(String(64), nullable=False, index=True)
    device_id = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default='pending', index=True)
    target_organs = Column(JSONB, nullable=False)
    organ_statuses = Column(JSONB, nullable=False)
    knowledge_base_version = Column(String(32), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp(), onupdate=func.current_timestamp())
    expired_at = Column(TIMESTAMP(timezone=True))
    consent_status_at_creation = Column(String(16), nullable=False)


class DiagnosisResult(Base):
    __tablename__ = 'diagnosis_results'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    request_id = Column(String(64), unique=True, nullable=False, index=True)
    session_id = Column(String(64), index=True)
    organ = Column(String(32), nullable=False)
    tier = Column(String(16), nullable=False)
    # 医生提交的临床文本（含语音转写）。此前未落库，导致后台管线读取
    # ``request.clinical_text`` 抛 AttributeError → 调度阶段恒失败。
    clinical_text = Column(Text, nullable=True)
    # 请求携带的患者上下文（age/gender/relevant_history/**conditions**）。
    # 2026-09-29 真机暴露：`create_request(patient_context=...)` 收了这个参数却**从不写库**
    # （模型里甚至没有对应列），于是后台管线拿不到患者条件，安全网关的禁忌判定只能走
    # "缺条件数据 → 保守判命中"分支 → 每次诊断恒 requires_manual_review。
    patient_context = Column(JSONB, nullable=True)
    # 请求携带的影像 ID 列表（对应 DICOM 上传时返回的 image_id）。
    # 2026-10-03 接入结节分割时暴露：`create_request(image_ids=...)` 收了参数却
    # **从不写库**（与 patient_context 同构的"收了就丢"），于是后台管线完全拿不到
    # 影像标识——报告里无法输出结节轮廓/面积。先把标识持久化，是取图链路的地基。
    image_ids = Column(ARRAY(Text))
    confidence = Column(Float, nullable=False)
    findings = Column(JSONB)
    recommendations = Column(Text)
    knowledge_ids = Column(ARRAY(Text))
    status = Column(String(32))
    result_pushed = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP(timezone=True), default=func.current_timestamp())


class DicomImage(Base):
    """上传的 DICOM 影像索引（2026-10-03 接入结节分割时补）。

    为什么需要这张表
    ----------------
    此前"哪张影像属于哪个 session"**只存在于 audit_logs 的 JSONB detail**，
    没有表、没有 Repository、没有 API 可查，于是：
      - ``diagnosis_results.image_ids`` 是**客户端自报**，服务端零校验；
      - 后台管线拿到 image_ids 也**取不回字节**（返回的 img_... ID 与落盘
        文件名是两套命名空间，见 DicomStorageService.path_for_id 注释）。

    本表把映射显式化：``image_id``（客户端拿到的 ID）↔ ``session_id`` ↔
    ``storage_path``（可交给 retrieve()）。有了它才能做三件事：
    按 session 拉全部影像、校验 image_ids 归属、管线侧取图做分割。

    存储路径刻意冗余存一份：即便日后布局规则变更，历史行仍指向当时的真实路径
    （不依赖 path_for_id 的推导规则仍然成立）。
    """

    __tablename__ = 'dicom_images'
    __table_args__ = (
        Index('idx_dicom_images_session_created', 'session_id', 'created_at'),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # 与 DicomStorageService.STORAGE_ID_PATTERN 对齐（img_/frame_ + 日期 + hex8）
    image_id = Column(String(64), unique=True, nullable=False, index=True)
    session_id = Column(String(64), nullable=False)
    patient_id = Column(String(64), nullable=True)
    # 可直接交给 DicomStorageService.retrieve() 的路径（local 绝对路径或 minio:// URL）
    storage_path = Column(Text, nullable=False)
    sop_instance_uid = Column(String(128), nullable=True)
    modality = Column(String(8), nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    frame_count = Column(Integer, nullable=True)
    size_bytes = Column(Integer, nullable=True)
    original_filename = Column(String(255), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), default=func.current_timestamp())


class DiagnosisReport(Base):
    """T-GB-API-02：诊断报告（草稿 → 签名 → PACS/HIS 推送）。

    与 ``diagnosis_results`` 一对多：一次诊断可生成草稿、修订后再签发多份
    报告（content 为结构化报告内容，rendered 为签名与推送所依据的文本，
    content_digest 为其 SHA-256，用于签名校验与幂等）。
    """
    __tablename__ = 'diagnosis_reports'
    __table_args__ = (
        Index('idx_reports_request_status', 'request_id', 'status'),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # 业务主键：rep_{date}_{hex8}
    report_id = Column(String(64), unique=True, nullable=False, index=True)
    request_id = Column(String(64), nullable=False, index=True)
    session_id = Column(String(64), index=True)
    patient_id = Column(String(64), index=True)
    organ = Column(String(32))
    # draft | signed | pushed | failed
    status = Column(String(32), nullable=False, default='draft')
    # 结构化报告内容（模板填充结果）
    content = Column(JSONB)
    # 渲染后的报告正文（签名与推送的载荷）
    rendered = Column(Text)
    # rendered 的 SHA-256（十六进制），签名前计算，幂等键的一部分
    content_digest = Column(String(64))
    # 报告模板版本，便于审计与追溯
    template_version = Column(String(32))
    # 数字签名（base64）与签名元信息
    signature = Column(Text)
    signing_key_name = Column(String(64))
    signed_at = Column(TIMESTAMP(timezone=True))
    signed_by = Column(String(64))
    # PACS/HIS 推送结果
    pacs_adapter = Column(String(32))
    pacs_status = Column(String(32))  # pending | success | failed | skipped
    pacs_message = Column(Text)
    pacs_attempts = Column(Integer, default=0)
    pushed_at = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True), default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP(timezone=True),
        default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )


class ColdArchiveManifest(Base):
    """T-GB-SEC-02：冷归档批次清单（热数据被移出 PG 后的冷层索引）。

    一条记录对应冷层的一个对象（JSONL 分片）。热数据**只有在对象上传成功
    后**才会被删除，因此清单是"冷层是否真的存住了"的唯一凭据；``checksum``
    与 ``record_count`` 供恢复/校验时比对。
    """
    __tablename__ = 'cold_archive_manifests'
    __table_args__ = (
        Index('idx_cold_manifest_category_status', 'category', 'status'),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    manifest_id = Column(String(64), unique=True, nullable=False, index=True)
    # audit_logs | session_archives | operation_logs
    category = Column(String(32), nullable=False, index=True)
    object_key = Column(String(512))
    record_count = Column(Integer, default=0)
    first_record_id = Column(BigInteger)
    last_record_id = Column(BigInteger)
    checksum = Column(String(64))
    size_bytes = Column(BigInteger, default=0)
    time_range_start = Column(TIMESTAMP(timezone=True))
    time_range_end = Column(TIMESTAMP(timezone=True))
    # pending | archived | failed | restored
    status = Column(String(32), nullable=False, default='pending', index=True)
    adapter = Column(String(32))
    message = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), default=func.current_timestamp())
    archived_at = Column(TIMESTAMP(timezone=True))
    purged_at = Column(TIMESTAMP(timezone=True))


class KnowledgeBase(Base):
    __tablename__ = 'knowledge_base'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    kb_id = Column(String(64), unique=True, nullable=False, index=True)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(1024))
    organ = Column(String(32))
    tier = Column(String(16))
    version = Column(String(32))
    confidence = Column(Float, default=1.0)
    created_at = Column(TIMESTAMP(timezone=True), default=func.current_timestamp())
    updated_at = Column(TIMESTAMP(timezone=True), default=func.current_timestamp(), onupdate=func.current_timestamp())


class SafetyEvent(Base):
    __tablename__ = 'safety_events'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    event_id = Column(BigInteger, unique=True, nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    severity = Column(String(16), nullable=False)
    kb_version = Column(String(32))
    device_id = Column(String(64))
    patient_id = Column(String(64))
    payload = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())
    acknowledged = Column(Boolean, default=False)

    __table_args__ = (
        Index('idx_safety_event_type_time', 'event_type', 'created_at'),
    )


class AuditLog(Base):
    __tablename__ = 'audit_logs'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    log_id = Column(String(36), unique=True, nullable=False, index=True)
    session_id = Column(String(64), index=True)
    action = Column(String(64), nullable=False, index=True)
    actor = Column(String(64), nullable=False)
    target_type = Column(String(32))
    target_id = Column(String(64))
    detail = Column(JSONB)
    log_type = Column(String(16), nullable=False, default='operation')
    hash_prev = Column(String(64))
    hash_self = Column(String(64), nullable=False)
    is_critical_operation = Column(Boolean, default=False)
    cross_chain_hash = Column(String(64))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())

    __table_args__ = (
        Index('idx_audit_session_time', 'session_id', 'created_at'),
    )


class AuditCrossChain(Base):
    __tablename__ = 'audit_cross_chain'

    id = Column(String(36), primary_key=True)
    period_start = Column(TIMESTAMP(timezone=True), nullable=False)
    period_end = Column(TIMESTAMP(timezone=True), nullable=False)
    prev_cross_hash = Column(String(64))
    cross_hash_self = Column(String(64), nullable=False)
    session_hashes = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())

    __table_args__ = (
        Index('idx_audit_cross_chain_period', 'period_start', 'period_end'),
    )


class SessionArchive(Base):
    __tablename__ = 'sessions_archive'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(String(64), unique=True, nullable=False, index=True)
    patient_id = Column(String(64), nullable=False)
    device_id = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False)
    target_organs = Column(JSONB, nullable=False)
    organ_statuses = Column(JSONB, nullable=False)
    knowledge_base_version = Column(String(32), nullable=False)
    consent_status_at_creation = Column(String(16), nullable=False)
    archive_timestamp = Column(TIMESTAMP(timezone=True), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False)
    archived_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())
    patient_type = Column(String(16), nullable=False, default='outpatient')


class Device(Base):
    __tablename__ = 'devices'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    device_id = Column(String(64), unique=True, nullable=False, index=True)
    device_name = Column(String(128), nullable=False)
    device_type = Column(String(64), nullable=False)
    location = Column(String(256), nullable=False)
    jetpack_version = Column(String(32))
    api_key = Column(String(128), nullable=False)
    status = Column(String(32), nullable=False, default='active')
    created_at = Column(TIMESTAMP(timezone=True), default=func.current_timestamp())
    updated_at = Column(TIMESTAMP(timezone=True), default=func.current_timestamp(), onupdate=func.current_timestamp())


class FeedbackRecord(Base):
    __tablename__ = 'feedback_records'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    feedback_id = Column(String(64), unique=True, nullable=False, index=True)
    request_id = Column(String(64), nullable=False, index=True)
    session_id = Column(String(64), nullable=False, index=True)
    feedback_type = Column(String(32), nullable=False)
    feedback_level = Column(String(8), nullable=False)
    reviewer_id = Column(String(64), nullable=False)
    content = Column(JSONB, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())


class User(Base):
    __tablename__ = 'users'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(256))
    full_name = Column(String(128))
    hashed_password = Column(String(256), nullable=False)
    disabled = Column(Boolean, nullable=False, default=False)
    role = Column(String(64), nullable=False, default='ULTRASOUND_DOCTOR')
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp(), onupdate=func.current_timestamp())


class OrganDiseaseThreshold(Base):
    __tablename__ = 'organ_disease_thresholds'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    organ = Column(String(32), nullable=False, index=True)
    disease_type = Column(String(64), index=True)
    confidence_threshold = Column(Float, nullable=False)
    description = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp(), onupdate=func.current_timestamp())

    __table_args__ = (
        Index('idx_organ_disease', 'organ', 'disease_type'),
    )


# T-GA-DATA-01: Knowledge base index construction pipeline authoritative table.
# Distinct from KnowledgeBase (which serves as the RAG retrieval fast cache);
# this table holds the source-of-truth chunked content produced by
# scripts/init_knowledge_base.py. DDL lives in
# alembic/versions/0006_knowledge_chunks.py.
class KnowledgeChunkModel(Base):
    __tablename__ = 'knowledge_chunks'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    chunk_id = Column(String(128), unique=True, nullable=False, index=True)
    kb_version = Column(String(32), nullable=False, index=True)
    organ = Column(String(32), nullable=False, index=True)
    content = Column(Text, nullable=False)
    meta = Column('metadata', JSONB, nullable=False, default={})
    embedding = Column(Vector(1024))
    source_doc_id = Column(String(128), nullable=False, index=True)
    section_path = Column(Text, nullable=False, default='')
    token_count = Column(Integer, nullable=False, default=0)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp(), onupdate=func.current_timestamp())
    # 二期 §6 衰减（迁移 0012）：时间衰减看 last_referenced_at，反馈衰减看 dispute_count，
    # confidence_score 是算出来的目标值（检索侧按其加权）。
    last_referenced_at = Column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.current_timestamp()
    )
    reference_count = Column(Integer, nullable=False, server_default='0')
    dispute_count = Column(Integer, nullable=False, server_default='0')
    confidence_score = Column(Float, nullable=False, server_default='1.0')

    __table_args__ = (
        Index('idx_kc_kb_organ', 'kb_version', 'organ'),
        Index('idx_kc_last_referenced_at', 'last_referenced_at'),
        Index('idx_kc_confidence_score', 'confidence_score'),
        CheckConstraint(
            'confidence_score >= 0.0 AND confidence_score <= 1.0',
            name='ck_kc_confidence_score_range',
        ),
    )
