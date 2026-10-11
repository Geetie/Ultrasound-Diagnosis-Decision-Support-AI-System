
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    env: str = 'development'

    database_url: str = ''
    # 主服务固定 db0（SG 固定 db1，见 sg_validator 对 db 的强制）。
    # 默认值曾指向 db1——裸机部署漏配 REDIS_URL 时主服务会写进 SG 的
    # 逻辑库，共享键（脱敏 HMAC/令牌映射）跨服务不可见（wiring-audit P1-7）。
    redis_url: str = 'redis://localhost:6379/0'

    postgres_password: str = ''

    # 绑定地址的安全默认：**loopback**（AGENTS.md 硬规则 #3——共享 GPU 服务器
    # 不许把临床 API 暴露到校园网，跨机访问走
    # `ssh -N -L 8000:127.0.0.1:18000 student03@10.20.28.21`）。
    #
    # ⚠️ 本字段**不是** uvicorn 实际绑定的来源。真实绑定来自命令行 `--host`：
    #   · 容器：`Dockerfile.dev` 硬编码 `--host 0.0.0.0`（容器内必须，否则
    #     `ports: 8000:8000` 映射不通）—— 所以改这里对 compose 零影响；
    #   · 裸机：一律走 `scripts/start_main.sh`，别再手写启动命令
    #     （历史上 7 个 deploy_*.sh 各自写死 `--host 0.0.0.0`，导致铁律 #3
    #     被反复违反：2026-10-05 真机实测 `ss -ltn` 就是 `0.0.0.0:18000`）。
    # 保留此字段只为给部署与文档一个可读的真值来源，别当成"改了就会生效"。
    api_host: str = '127.0.0.1'
    api_port: int = 8000

    safety_gateway_host: str = 'localhost'
    safety_gateway_port: int = 9001
    # wiring-audit P2-4：删除游离白名单副本 safety_gateway_whitelist_fields——
    # 权威白名单在 edge/safety.py 与 safety_gateway 侧（SG 自带同名配置），
    # 这份 8 项副本零引用且与权威不一致，删除以防误用。
    safety_gateway_enable_degraded_mode: bool = True
    safety_gateway_queue_timeout_seconds: float = 10.0
    safety_gateway_http_client_timeout_seconds: float = Field(default=3.0, alias='sg_http_client_timeout_seconds')
    safety_gateway_client_fast_fail_threshold: int = Field(default=3, alias='sg_client_fast_fail_threshold')
    safety_gateway_health_probe_interval_seconds: float = Field(default=4.0, alias='sg_health_probe_interval_seconds')
    desensitization_rules_safety_path: str = 'desensitization_rules_safety.yaml'
    desensitization_rules_safety_patch_path: str = 'desensitization_rules_safety_patch.yaml'
    db_query_timeout: int = 5
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_idle_in_transaction_timeout: int = 30

    log_db_pool_max_size: int = 3
    log_db_pool_min_size: int = 1

    safety_gateway_db_retry_max_attempts: int = Field(default=3, alias='sg_db_retry_max_attempts')
    safety_gateway_db_retry_delay_base: float = Field(default=0.5, alias='sg_db_retry_delay_base')
    safety_gateway_db_retry_max_total_time_seconds: float = Field(default=10.0, alias='sg_db_retry_max_total_time_seconds')
    safety_gateway_threshold_cache_ttl_seconds: int = Field(default=300, alias='sg_threshold_cache_ttl_seconds')
    safety_gateway_circuit_breaker_failure_threshold: int = Field(default=5, alias='sg_circuit_breaker_failure_threshold')
    safety_gateway_circuit_breaker_success_threshold: int = Field(default=3, alias='sg_circuit_breaker_success_threshold')
    safety_gateway_circuit_breaker_cooldown_period: float = Field(default=30.0, alias='sg_circuit_breaker_cooldown_period')

    safety_gateway_p99_latency_warning_threshold_ms: float = Field(default=4.0, alias='sg_p99_latency_warning_threshold_ms')
    safety_gateway_p99_latency_critical_threshold_ms: float = Field(default=5.0, alias='sg_p99_latency_critical_threshold_ms')

    redis_pool_max_connections: int = 50
    redis_pool_timeout: int = 5
    redis_ssl: bool = False
    redis_ssl_cert_reqs: str = 'required'
    redis_key_prefix: str = 'ultrasound'
    websocket_redis_pubsub_enabled: bool = True
    rate_limit_redis_enabled: bool = False

    jwt_algorithm: str = 'RS256'

    @field_validator("jwt_algorithm")
    @classmethod
    def jwt_algorithm_must_be_rs256(cls, v: str) -> str:
        if v != 'RS256':
            raise ValueError(
                f"jwt_algorithm must be RS256 (asymmetric), got '{v}'. "
                f"HS256 is prohibited per security compliance policy (04-安全合规.md §4)."
            )
        return v
    jwt_secret_key: str = ''
    jwt_expire_minutes: int = 480
    jwt_refresh_expire_days: int = 7

    # 旋转宽限期（秒）：旧 refresh token 在被轮换后 N 秒内再次出示，视为**合法并发
    # 刷新**（多标签页/双击），返回后继 token 而不是按窃取撤销整个 family
    # （feature_list: refresh-token-grace-period；真机 6 进程验证实测合法并发被判
    # family revoked 全设备踢下线）。默认 30s：足以覆盖同一次人工操作触发的并发
    # 刷新，又把被盗旧 token 的可用窗口压到最短。**调大是产品决策** —— 等于放宽
    # reuse 检测；0 = 关闭宽限，回到严格语义。登出/显式吊销（无后继）不吃宽限。
    refresh_token_grace_period_s: int = 30

    log_level: str = 'INFO'

    allowed_origins: str = 'http://localhost:3000,http://localhost:8080,http://127.0.0.1:3000'

    max_concurrent_sessions_per_user: int = 5
    max_queued_requests_per_user: int = 2

    desensitization_restore_rate_limit: int = 100
    desensitization_restore_rate_limit_full: int = 200
    desensitization_key_rotation_days: int = 180
    desensitization_key_rotation_days_compact: int = 60
    desensitization_re_encrypt_batch_size: int = 1000

    hsm_emergency_signing_enabled: bool = False
    hsm_emergency_signing_max_daily: int = 50
    hsm_emergency_signing_verification_hours: int = 24
    hsm_degraded_phase_1_threshold_minutes: int = 5
    hsm_degraded_phase_2_threshold_minutes: int = 30
    jwt_key_rotation_days: int = 120
    jwt_key_rotation_days_compact: int = 60

    consent_withdrawal_consumer_enabled: bool = True
    consent_withdrawal_consumer_group: str = "consent_withdrawal_consumers"
    consent_withdrawal_consumer_name: str = "sec_consumer_01"
    consent_withdrawal_handlers: list[str] = Field(default_factory=lambda: ["safety_action", "data_cleanup"])
    consent_withdrawal_batch_size: int = 10
    consent_withdrawal_poll_timeout_ms: int = 5000
    # T-G1-INT-06：撤回生效端到端时延验收线（撤回动作 -> 各处理器执行完毕）
    consent_withdrawal_e2e_latency_target_ms: int = 500

    # 二期 §9 应用层算法档位：standard（RSA 家族）| gm（国密 SM2/SM4）
    # 默认 standard 而非规格写的"默认国密"：国密需 gmssl，未安装时选 gm 会直接起不来，
    # 部署侧装好依赖后改这一项即可切换。
    security_algorithm_profile: str = "standard"

    # 二期 §20 分布漂移（与既有 SPC 检测器分工：SPC 判单点越限，这里判分布形状变化）
    drift_input_kl_threshold: float = 0.1
    drift_output_kl_threshold: float = 0.15
    drift_output_mean_shift_threshold: float = 0.05
    drift_distribution_min_samples: int = 500

    # 二期 §19 A/B 实验（仅 full Profile 生效）
    ab_experiment_enabled: bool = False
    ab_experiment_name: str = "default"
    ab_experiment_traffic_percent: int = 10
    ab_alpha: float = 0.05
    ab_power: float = 0.80
    ab_mde: float = 0.03
    ab_non_inferiority_margin: float = 0.03
    ab_min_samples_per_variant: int = 200

    # 二期 §7 AI 修正率监控
    correction_rate_window_days: int = 30
    correction_rate_direction_alert_threshold: float = 0.20
    correction_rate_reviewer_alert_threshold: float = 0.50
    correction_rate_peer_baseline_threshold: float = 0.20
    # 低于该样本数只统计不判定：1/1=100% 会直接触发个体告警，把真实信号淹没
    correction_rate_min_samples: int = 20

    # T-GR-INT-01 灰度发布路由
    rollout_enabled: bool = False
    rollout_full_percent: int = 5
    rollout_compact_session_threshold: int = 10
    rollout_counter_ttl_seconds: int = 7 * 24 * 3600

    deployment_profile: str = 'compact'

    inference_engine: str = 'llama.cpp'
    llama_cpp_host: str = 'localhost'
    llama_cpp_port: int = 8001
    llama_cpp_default_max_tokens: int = 1024
    vllm_host: str = 'localhost'
    vllm_port: int = 8002
    vllm_default_model: str = ''
    vllm_require_model: bool = True
    max_tokens: int = Field(default=2048, alias='inference_max_tokens')
    temperature: float = Field(default=0.1, alias='inference_temperature')
    top_p: float = Field(default=0.95, alias='inference_top_p')
    inference_timeout_seconds: float = 30.0
    inference_health_check_timeout_seconds: float = 5.0
    inference_connect_timeout_seconds: float = 10.0

    # 重试配置
    inference_max_retries: int = 3
    inference_retry_delay_base: float = 1.0
    inference_retry_delay_max: float = 10.0

    # 熔断配置
    inference_circuit_breaker_timeout: float = 30.0
    inference_circuit_breaker_failure_threshold: int = 5
    inference_circuit_breaker_success_threshold: int = 3

    # 连接池配置
    inference_pool_max_connections: int = 10
    inference_pool_keepalive_expiry: float = 300.0

    dicom_scp_port: int = 10400
    dicom_storage_backend: str = 'local'
    dicom_storage_path: str = './dicom_storage'
    dicom_storage_max_file_size: int = 524288000

    navigation_ws_heartbeat_interval_seconds: int = 5
    navigation_ws_heartbeat_timeout_seconds: int = 15
    navigation_ws_auth_token_ttl_seconds: int = 300
    navigation_confidence_threshold: float = 0.75
    # T-GB-INT-02：导航延迟埋点的合理性上限（毫秒）。超过该值视为时钟漂移
    # 或单位错误（例如误传秒），丢弃以避免污染 P99（验收：导航 P99 < 200ms）。
    nav_latency_max_plausible_ms: int = 60000
    nav_safety_check_normal_timeout_seconds: float = 0.05
    nav_safety_check_max_timeout_seconds: float = 0.08
    nav_verification_workers: int = 4
    nav_max_connections_per_session: int = 5

    # === M2 快速路径高危标记自动升级 ===
    # 命中 B+ 级（risk_flag=high，已过 SafetyGateway）时自动发起深度诊断。
    nav_auto_upgrade_enabled: bool = True
    # 自动升级使用的响应档位（规格：标准/全量）。
    nav_auto_upgrade_tier: str = "standard"
    # 同一会话的升级冷却窗口（秒）。导航流为 15fps，若不做冷却会逐帧入队，
    # 瞬间打满诊断队列。
    nav_auto_upgrade_cooldown_seconds: float = 60.0
    # 同一会话最多自动升级次数（防止异常模型持续误标导致队列饥饿）。
    nav_auto_upgrade_max_per_session: int = 3

    # === M1 切面标准性监控 ===
    # 连续多少帧非标准切面触发流程监控提醒（规格：连续 5 帧）。
    nav_plane_non_standard_streak_threshold: int = 5
    # 连续多少帧图像质量不达标触发提醒。
    nav_image_quality_poor_streak_threshold: int = 5
    session_max_ttl_seconds: int = 7200
    dicom_max_retransmit_per_frame: int = 3
    dicom_max_retransmit_frame_range: int = 90
    dicom_frame_buffer_capacity: int = 90

    phase0a_mock_processing_time_ms: int = 2500
    phase0a_mock_model_version: str = "thyroid_v1.0.0"
    phase0a_mock_kb_version: str = "kb_v1.0.0"

    # === T-GA-INT-01 端到端集成配置 ===
    # 当前激活的知识库版本（RAG 检索与知识智能体缓存键共用；换版本走
    # .env 配置，建库/灌数见 scripts/init_knowledge_base.py 与
    # server_populate_vector_index.py）
    rag_kb_version: str = 'kb_v1.0.0'
    int_worker_pool_size: int = 4
    # wiring-audit P2-5：删除孤儿配置 int_pipeline_total_timeout_ms
    # （管线各阶段用 time.monotonic 计时，总超时从未被强制执行；如需
    # 总超时门控须先设计降级语义再恢复此字段）。
    int_dlq_max_retries: int = 3
    int_worker_blpop_timeout_seconds: int = 5
    # T-Perf 措施#3：管线各阶段延迟预算（秒，超时降级不挂死）
    pipeline_dispatch_budget_s: float = 6.0
    pipeline_fusion_budget_s: float = 6.0
    pipeline_sg_budget_s: float = 2.0
    # 并发审查 P0-1：worker 槽位被分钟级管线占死的雪崩防线。
    # dispatch/fusion 两段的预算字段（6.0s）自 T-Perf 起就是**孤儿配置**
    # （全仓无读取点），而这两段恰是分钟级 LLM 调用——单阶段最坏可达
    # inference_timeout(30s) × max_retries(3) + 退避 ≈ 120s，dispatch+fusion
    # 串行最坏 ~240s，4 个 worker 槽位会同时被占死，队列积压到
    # int_pending_queue_max_length 后整条诊断链不可用且无法自愈。
    # 这里补一个**端到端**总预算（超时按普通失败处理 → 走既有 _handle_failure
    # 计数/DLQ 路径），不动上面两个 6.0s：实测 dispatch/fusion 常态已达 ~4.7s，
    # 直接把 6.0s 强制生效会造成大面积超时降级（比不设更糟）。
    pipeline_total_budget_s: float = 180.0
    # 并发审查 P2-3：诊断 DLQ 是无 TTL 的 list，下游长时间故障时单调增长
    # （同意撤回 DLQ 早已用 maxlen=1000，此处补齐同口径）。
    int_dlq_max_length: int = 1000

    # === RAG 检索结果缓存（覆盖 fusion 的 consistency / crag 直连检索路径）===
    # 关闭时 RAGServiceImpl 走真实流水线，行为与引入缓存前完全一致。
    rag_cache_enabled: bool = True
    rag_cache_redis_ttl_seconds: int = 3600
    rag_cache_local_capacity: int = 128
    rag_cache_local_ttl_seconds: int = 300
    rag_cache_negative_ttl_seconds: int = 30
    rag_cache_ttl_jitter_ratio: float = 0.1
    int_worker_idle_log_interval_seconds: int = 60
    int_pending_queue_max_length: int = 1000
    int_consumer_recovery_delay_seconds: float = 2.0

    # === T-GB-AGT-01 多器官联合诊断（三器官串行）===
    # 超时预算：单器官 30s（规格验收），全局 90s（三器官串行上限）。
    multi_organ_per_organ_timeout_seconds: float = 30.0
    multi_organ_total_timeout_seconds: float = 90.0
    # 会话级接口未指定档位时的默认响应档位（多器官串行更接近标准路径）。
    multi_organ_default_response_tier: str = "standard"
    # 是否在器官开始/结束时推送 WS organ_switching 进度消息。
    multi_organ_progress_push_enabled: bool = True

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "dicom-storage"
    minio_secure: bool = False

    embedding_model: str = 'ultrasound-clip'
    embedding_dim: int = 768
    embedding_dim_fallback: int = 1024
    ultrasound_clip_host: str = 'localhost'
    ultrasound_clip_port: int = 8003
    bge_m3_host: str = 'localhost'
    bge_m3_port: int = 8004
    embedding_timeout_seconds: float = 30.0
    embedding_health_check_timeout_seconds: float = 5.0
    embedding_connect_timeout_seconds: float = 10.0
    embedding_contradiction_threshold_ultrasound_clip: float = 0.85
    embedding_contradiction_threshold_bge_m3_medical: float = 0.85
    # P1-EMB-03：embedding HTTP 请求的退避重试预算
    # （app/embedding/base.py::_run_with_retry，仅对 retryable=True 的
    #  EmbeddingError 生效，sleep = backoff_base * 2^(attempt-1)）
    embedding_retry_max_attempts: int = 3
    embedding_retry_backoff_base: float = 1.0

    # Vector index backend (EMB-02)
    vector_index_backend: str = 'qdrant'   # 'qdrant' | 'pgvector'
    qdrant_host: str = 'localhost'
    qdrant_port: int = 6333
    qdrant_grpc_port: int = 6334
    qdrant_timeout_seconds: float = 3.0
    qdrant_api_key: str = ''
    vector_index_auto_fallback: bool = True

    # RAG (T-GA-RAG-01/02/03)
    rag_use_query_enhancement: bool = True
    rag_use_reranking: bool = True
    rag_use_bm25_fallback: bool = True
    rag_use_multimodal: bool = True
    rag_use_llm_enhancement: bool = False
    rag_collection: str = 'knowledge_chunks'
    rag_image_collection: str = 'ultrasound_chunks'
    # 实体近似匹配专用 collection（≤1 万向量），与主检索 collection 隔离。
    # 设计规格 §5.5 / 01-系统架构设计「Gate 0A 强制执行项」指定的名称。
    rag_entity_matching_collection: str = 'entity_matching'
    # wiring-audit P2-5：删除孤儿配置 rag_top_k（专科智能体检索档位实际用
    # specialist/base.py 的 DEFAULT_RAG_TOP_K=5，此字段零读者；接线会改变
    # 检索行为 5→20，须先经评测再定）。
    rag_rerank_output_top_k: int = 5
    rag_min_score_threshold: float = 0.5
    rag_bm25_min_top1_score: float = 0.3
    rag_bm25_min_results: int = 3
    rag_bm25_max_chunks: int = 10000
    rag_query_embedding_cache_ttl_seconds: int = 3600
    rag_use_redis_cache: bool = False
    rag_p99_warning_threshold_ms: float = 80.0
    rag_p99_critical_threshold_ms: float = 100.0

    # Knowledge Graph 图谱查询开关（app/knowledge/graph_query.py）。
    # 图谱查询开关（P2-KB-08）：查询实现（PGVector 递归 CTE）已落地，
    # 边数据由 builder 写入 metadata.relations（存量库用
    # scripts/kb_backfill_relations.py 回填）。默认关闭；置 True 后
    # 检索器自动追加 depth-1 图谱邻居（retrieval_source=graph）。
    graph_query_enabled: bool = False
    rag_rrf_k: int = 60

    # 甲状腺结节分割（app/inference/nodule_segmentation.py）
    # 权重来自 scripts/tn3k_seg_unet.py 的官方划分训练（TN3K trainval 2879 张），
    # 官方 test(614 张) 全量评测 DSC 0.7961 / IoU 0.7004（reports/tn3k_seg_unet.json）。
    # 权重为部署产物，默认放在 ~/uai/models/nodule_seg/（仓库不分发二进制）。
    # 依赖缺失/权重缺失时端点返回 503 并带原因，不静默返回空掩码。
    nodule_segmentation_enabled: bool = True
    nodule_segmentation_model_path: str = "~/uai/models/nodule_seg/unet_tn3k.pt"
    nodule_segmentation_threshold: float = 0.5
    # auto | cuda | cpu；auto 在 CUDA_VISIBLE_DEVICES 无效时会退 CPU（慢但可用）
    nodule_segmentation_device: str = "auto"
    # 把分割旁路接进诊断管线（app/services/diagnosis_pipeline.py 的
    # _safe_nodule_contour）。**默认关闭**：分割要读磁盘 + 跑 UNet 推理，是在
    # 分钟级 LLM 诊断之外额外的 IO/算力；开启前应先用甲状腺/心脏分割真机
    # 验证过取图链路（dicom_images 索引 + storage_id 命名落盘）。
    # 开启后结果挂 findings["segmentation"]，缺权重/缺影像一律 available=False
    # 带原因，不影响诊断状态与置信度。
    # A.37 ③：默认开启 —— 分割量化特征必须在 dispatch 前产出才能经 imaging_metadata
    # 注入 LLM 上下文；关闭 = LLM 永远看不到影像信息（结论反向索要影像特征）
    nodule_contour_in_pipeline_enabled: bool = True
    # AI 依赖的**硬离线**闸门（2026-10-04 真机事故驱动）：
    # open_clip / huggingface_hub 在装载模型时会向 huggingface.co 发 HEAD 探测
    # 权重；网络不可达时每次 5 次重试 ~14s，且这段是**同步**代码——跑在请求
    # 路径上会把 event loop 整个卡死（实测 18000 端口全 000、health 无响应）。
    # 这里在进程启动最早期打开离线开关（两个变量都设：hub 端与 transformers 端），
    # 让所有模型装载只认本地缓存/本地路径，找不到就显式报"缺权重"。
    ai_offline_mode: bool = True

    # 结果接口把"还在 processing"判成 timeout 的阈值（秒）。P1-13：原硬编码 30s，
    # 而真实诊断 90s+（LLM 串行链 + 冷启动），会把正常在跑的诊断误报成超时，
    # 前端据此停轮询。默认 180s；换更大模型时必须调大。
    result_read_timeout_seconds: int = 180

    # 诊断属性提示（app/inference/attribute_hints.py，ULC 文本塔 zero-shot）
    # 依据 A.21 全量实测（US-365K 71,919 张，image→attribute）：
    #   ULC 9 维宏平均 0.6894 / BiomedCLIP 0.4092（低于多数类基线=无判别力）
    #   仅启用 6 个显著超基线的维度；Vascularity −0.056 / InternalChar +0.143 /
    #   PosteriorAcoustics +0.045 三维不启用（见 attribute_hints.DISABLED_REASON）
    # 定位是**旁路一致性校验**，不参与诊断结论。默认 cpu：GPU 常被 vLLM 占满。
    attribute_hints_enabled: bool = True
    attribute_hints_clip_weights: str = "~/uai/models/ultrasound_clip/checkpoints.pt"
    attribute_hints_device: str = "auto"

    # bge-reranker (T-GA-RAG-03)
    bge_reranker_host: str = 'localhost'
    bge_reranker_port: int = 8005
    bge_reranker_timeout_seconds: float = 5.0
    bge_reranker_model: str = 'bge-reranker-base'
    bge_reranker_health_check_timeout_seconds: float = 3.0

    # Agnes LLM (T-GA-RAG-01 optional enhancement)
    # API key auto-injected from os.environ['AGNES_API_KEY'] via model_validator.
    agnes_api_key: str = ''
    agnes_api_base: str = 'https://agnes-api.example.com/v1'
    agnes_api_model: str = 'agnes-flash'
    agnes_timeout_seconds: float = 3.0

    speech_engine: str = 'funasr'
    funasr_model_dir: str = 'models/funasr'
    # OTA 包分发根目录（A.50.8 E7）：空=未部署（OTA 端点保持 501 契约位）；
    # 配置后端点从 {dir}/{package}/{version}/{package}-{version}.bin 分发。
    ota_package_dir: str = ''
    funasr_device: str = 'cpu'
    funasr_num_threads: int = 4
    speech_timeout_seconds: float = 10.0

    vllm_gpu_memory_utilization: float = 0.90
    gpu_fragmentation_warning_threshold: float = 0.30
    gpu_fragmentation_critical_threshold: float = 0.40
    compact_memory_budget_mb: float = 22048.0

    # === T-GB-LLM-01 模型热重载 ===
    # 阈值复用上面两个（>0.30 Warning / >0.40 触发），避免同一数字两处定义。
    # 重载适配器：local_fake（本地假实现，仅编排可测）| external（现场注入）。
    hot_reload_adapter: str = 'local_fake'
    # 有活跃会话时最多延迟多久（秒）——规格：延迟至会话结束，最长 10 分钟。
    hot_reload_max_defer_seconds: float = 600.0
    # 等待活跃会话结束时的轮询间隔（秒）。
    hot_reload_poll_interval_seconds: float = 5.0

    # === T-GB-API-02 报告生成与签发 ===
    # 报告模板名（对应 app/templates/report/<name>），缺失时回退内置模板。
    report_template_name: str = 'default.md'
    # 报告签名密钥（前缀 report_ → KeySlot.REPORT_SIGNING）。
    report_signing_key_name: str = 'report_signing_v1'
    report_signing_key_size: int = 2048
    # PACS/HIS 推送适配器：local_fake（本地假实现）| http（现场配置 endpoint）
    pacs_adapter: str = 'local_fake'
    pacs_endpoint: str = ''
    pacs_timeout_seconds: float = 10.0
    pacs_max_attempts: int = 3
    pacs_retry_backoff_seconds: float = 0.5
    # 报告草稿生成的软超时（秒）——规格验收：草稿 ≤ 2 分钟。
    report_draft_budget_seconds: float = 120.0

    # === T-GB-SEC-02 审计/会话冷热分层（对象存储冷归档）===
    # 适配器：local_fake（本地目录/内存假实现）| s3（MinIO/S3，用 boto3）
    object_storage_adapter: str = 'local_fake'
    object_storage_bucket: str = 'ultrasound-archive'
    object_storage_prefix: str = 'cold-archive'
    # local_fake 的落盘目录；留空则退化为进程内内存实现
    object_storage_local_dir: str = './cold_archive_storage'
    object_storage_endpoint: str = ''
    object_storage_access_key: str = ''
    object_storage_secret_key: str = ''
    object_storage_region: str = 'us-east-1'
    object_storage_max_retries: int = 3
    object_storage_retry_backoff_seconds: float = 0.5
    # 单批冷归档的记录数（避免一次性全表扫描）
    cold_archive_batch_size: int = 500
    # 单次任务最多处理的会话数（每个会话的审计链整条归档，避免跨批撕裂）
    cold_archive_max_sessions_per_run: int = 20
    # 审计日志热层保留窗口（天）：超过则移入对象存储冷层，
    # 冷层保留年限由 data_retention_audit_years（30 年）约束。
    audit_hot_retention_days: int = 90
    # 未部署对象存储时是否允许直接清理到期归档（默认 False：先冷归档再清理，
    # 避免把本应入冷层的数据直接删掉）。
    data_retention_purge_without_cold_archive: bool = False

    # === T-G1-SEC-02 脱敏还原频率监控 ===
    # 数据安全官通知 webhook（对接安全运营平台/工单系统）；留空则只落结构化日志
    security_officer_webhook: str = ''

    # === T-G1-INT-04 安全响应头 / CSP ===
    # 留空表示使用 app/security/security_headers.py 中的默认策略
    csp_policy: str = ''
    # 灰度观察期可置 True（只上报不拦截），生产必须为 False
    csp_report_only: bool = False
    hsts_enabled: bool = False
    hsts_max_age_seconds: int = 31536000

    obs_metrics_enabled: bool = True
    obs_metrics_port: int = 9090
    # 指标服务器的**绑定地址**。默认只绑回环：`prometheus_client.start_http_server`
    # 不传 `addr` 时默认 `0.0.0.0`，在共享机/边缘端等于把 `/metrics` 暴露到整个网络
    # （2026-09-29 真机实测 `ss -ltn` 显示 `0.0.0.0:9090`）。
    # 需要被**同机之外的抓取端**拉取时（例如容器编排里由另一个容器抓）
    # 显式设 `OBS_METRICS_HOST=0.0.0.0`；仓库内当前的部署编排没有这种用法。
    obs_metrics_host: str = '127.0.0.1'
    # wiring-audit P2-5：删除孤儿配置 obs_metrics_scrape_interval（零读者，
    # 抓取间隔由 Prometheus/VictoriaMetrics 侧决定）。
    obs_victoriametrics_url: str = 'http://victoriametrics:8428'
    # 指标导出链路的开关（2026-09-28 接线修复新增）。
    # 此前这两条链路**只定义、从未接线**：`start_metrics_server()` 只被测试调用、
    # `VictoriaMetricsPusher` 在 app 内从未实例化，导致运行期 Prometheus 抓不到
    # `/metrics`、VictoriaMetrics 收不到推送 —— 看板面板再正确也没有数据。
    obs_metrics_server_enabled: bool = True
    obs_vm_push_enabled: bool = True
    obs_tracing_enabled: bool = False
    obs_tracing_otlp_endpoint: str = 'http://127.0.0.1:30100'
    # OTLP gRPC headers（Langfuse Basic auth：Authorization=Basic base64(pk:sk)）
    obs_tracing_otlp_headers: str = ''
    obs_tracing_sample_rate: float = 0.1
    obs_audit_cross_chain_interval_hours: int = 1
    obs_audit_critical_operations: list[str] = Field(
        default_factory=lambda: [
            'threshold_adjustment', 'model_version_change',
            'kb_update', 'desensitization_rule_update',
        ]
    )
    obs_cache_monitor_enabled: bool = True
    obs_cusum_target_rate: float = 0.005
    obs_cusum_decision_threshold: float = 3.0
    obs_cusum_calibration_samples: int = 300
    obs_cusum_calibration_simulations: int = 1000
    obs_overall_hallucination_warning_rate: float = 0.03
    obs_overall_hallucination_critical_rate: float = 0.05

    alert_contacts_json: str = ''
    alert_escalation_warning_confirm_seconds: int = 14400
    alert_escalation_critical_confirm_seconds: int = 900
    alert_escalation_warning_escalate_seconds: int = 86400
    alert_escalation_critical_escalate_seconds: int = 3600
    alert_webhook_url: str = ''
    # 内部告警回调（Alertmanager / vmbackup → /api/v1/internal/alerts/webhook）的共享令牌。
    # 为空表示不校验（内网部署）；**一旦配置即强制校验**，缺失/错误返回 401（fail-closed）。
    alert_webhook_token: str = ''

    data_retention_outpatient_years: int = 15
    data_retention_inpatient_years: int = 30
    data_retention_audit_years: int = 30
    data_retention_operation_years: int = 5
    session_archive_after_days: int = 7
    data_retention_task_interval_hours: int = 24

    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        populate_by_name=True,
        extra='ignore',
    )

    @field_validator("deployment_profile")
    @classmethod
    def deployment_profile_must_be_valid(cls, v: str) -> str:
        allowed = {'compact', 'full'}
        if v not in allowed:
            raise ValueError(f"deployment_profile must be one of {allowed}, got '{v}'")
        return v

    @field_validator("nav_auto_upgrade_tier")
    @classmethod
    def nav_auto_upgrade_tier_must_be_valid(cls, v: str) -> str:
        allowed = {'realtime', 'standard', 'full'}
        if v not in allowed:
            raise ValueError(
                f"nav_auto_upgrade_tier must be one of {allowed}, got '{v}'"
            )
        return v

    @model_validator(mode='after')
    def validate_m2_risk_config(self) -> 'Settings':
        """M2 自动升级的参数必须自洽，避免配错导致逐帧入队打满队列。"""
        if self.nav_auto_upgrade_cooldown_seconds < 0:
            raise ValueError(
                "nav_auto_upgrade_cooldown_seconds 不能为负: "
                f"{self.nav_auto_upgrade_cooldown_seconds}"
            )
        if self.nav_auto_upgrade_max_per_session < 1:
            raise ValueError(
                "nav_auto_upgrade_max_per_session 必须 >= 1: "
                f"{self.nav_auto_upgrade_max_per_session}"
            )
        if self.nav_plane_non_standard_streak_threshold < 1:
            raise ValueError(
                "nav_plane_non_standard_streak_threshold 必须 >= 1: "
                f"{self.nav_plane_non_standard_streak_threshold}"
            )
        return self

    @model_validator(mode='after')
    def validate_deployment_config(self) -> 'Settings':
        import logging
        import os
        _log = logging.getLogger(__name__)

        # Auto-inject AGNES_API_KEY from environment if not provided via .env
        # (T-GA-RAG-01: enable Agnes LLM enhancement when env var is set).
        if not self.agnes_api_key:
            env_key = os.environ.get('AGNES_API_KEY', '').strip()
            if env_key:
                self.agnes_api_key = env_key
                _log.info("agnes_api_key_loaded_from_env")
            elif self.rag_use_llm_enhancement:
                _log.warning(
                    "rag_use_llm_enhancement=true but AGNES_API_KEY is empty; "
                    "LLM enhancement will be silently disabled at runtime"
                )

        if self.env == 'production':
            if os.environ.get('USE_REDIS_STORAGE', 'false').lower() != 'true':
                _log.warning(
                    "production模式强制使用Redis存储Refresh Token，已自动启用USE_REDIS_STORAGE=true"
                )
                os.environ['USE_REDIS_STORAGE'] = 'true'

            if not os.environ.get('HSM_PRIVATE_KEY_PASSWORD'):
                raise ValueError(
                    "HSM_PRIVATE_KEY_PASSWORD must be set in production environment"
                )

            if not os.environ.get('HSM_KEY_ENCRYPTION_KEY'):
                raise ValueError(
                    "HSM_KEY_ENCRYPTION_KEY must be set in production environment"
                )

            if not self.redis_url.startswith('rediss://'):
                _log.warning(
                    "production模式建议使用TLS加密Redis连接(rediss://)，当前使用: %s",
                    self.redis_url[:20] + "..."
                )

            if not os.environ.get('REDIS_PASSWORD'):
                raise ValueError(
                    "REDIS_PASSWORD must be set in production environment"
                )

        if self.deployment_profile == 'compact' and self.inference_engine != 'llama.cpp':
            _log.warning(
                "compact模式强制使用llama.cpp引擎，已从%s覆盖为llama.cpp",
                self.inference_engine,
            )
            self.inference_engine = 'llama.cpp'
        elif self.deployment_profile == 'full' and self.inference_engine != 'vllm':
            _log.warning(
                "full模式强制使用vllm引擎，已从%s覆盖为vllm",
                self.inference_engine,
            )
            self.inference_engine = 'vllm'

        if self.deployment_profile == 'compact':
            self.funasr_device = 'cpu'
            if self.desensitization_key_rotation_days > self.desensitization_key_rotation_days_compact:
                _log.info(
                    "compact模式使用缩短的密钥轮换周期，已从%d天覆盖为%d天",
                    self.desensitization_key_rotation_days,
                    self.desensitization_key_rotation_days_compact,
                )
                self.desensitization_key_rotation_days = self.desensitization_key_rotation_days_compact
            if self.jwt_key_rotation_days > self.jwt_key_rotation_days_compact:
                _log.info(
                    "compact模式使用缩短的JWT密钥轮换周期，已从%d天覆盖为%d天",
                    self.jwt_key_rotation_days,
                    self.jwt_key_rotation_days_compact,
                )
                self.jwt_key_rotation_days = self.jwt_key_rotation_days_compact
            if self.vllm_gpu_memory_utilization > 0.80:
                _log.warning(
                    "compact模式下vllm_gpu_memory_utilization超过0.80，已从%.2f覆盖为0.80",
                    self.vllm_gpu_memory_utilization,
                )
                self.vllm_gpu_memory_utilization = 0.80
            if self.vllm_gpu_memory_utilization < 0.50:
                raise ValueError(
                    f"vllm_gpu_memory_utilization must be >= 0.50 for compact mode, got {self.vllm_gpu_memory_utilization}"
                )
        else:
            if self.desensitization_restore_rate_limit < self.desensitization_restore_rate_limit_full:
                _log.info(
                    "full模式使用更高的脱敏还原速率限制，已从%d次/小时覆盖为%d次/小时",
                    self.desensitization_restore_rate_limit,
                    self.desensitization_restore_rate_limit_full,
                )
                self.desensitization_restore_rate_limit = self.desensitization_restore_rate_limit_full
            if self.vllm_gpu_memory_utilization > 0.95:
                raise ValueError(
                    f"vllm_gpu_memory_utilization must be <= 0.95 for full mode, got {self.vllm_gpu_memory_utilization}"
                )
            if self.vllm_gpu_memory_utilization < 0.70:
                raise ValueError(
                    f"vllm_gpu_memory_utilization must be >= 0.70 for full mode, got {self.vllm_gpu_memory_utilization}"
                )

        ports = [
            ('api_port', self.api_port),
            ('safety_gateway_port', self.safety_gateway_port),
            ('llama_cpp_port', self.llama_cpp_port),
            ('vllm_port', self.vllm_port),
            ('ultrasound_clip_port', self.ultrasound_clip_port),
            ('bge_m3_port', self.bge_m3_port),
            ('bge_reranker_port', self.bge_reranker_port),
            ('qdrant_port', self.qdrant_port),
            ('dicom_scp_port', self.dicom_scp_port),
        ]
        seen: dict[int, str] = {}
        for name, port in ports:
            if port in seen:
                raise ValueError(
                    f"Port conflict: {name}={port} conflicts with {seen[port]}={port}"
                )
            seen[port] = name

        return self

    @property
    def effective_max_tokens(self) -> int:
        """Compact模式下返回min(max_tokens, 1024)，full模式返回max_tokens。

        此属性为配置文档性质，供调用方参考compact模式下的推荐上限。
        实际运行时限制由引擎default_max_tokens控制（LlamaCpp=1024, VLLM=2048），
        InferenceRequest.max_tokens=None时引擎默认值生效。
        未来工厂模式可直接消费此属性创建引擎实例。
        """
        if self.deployment_profile == 'compact':
            return min(self.max_tokens, 1024)
        return self.max_tokens


settings = Settings()
