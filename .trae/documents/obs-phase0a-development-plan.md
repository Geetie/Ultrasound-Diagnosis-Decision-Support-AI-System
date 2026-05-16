# OBS可观测性轨道 Phase 0A 开发计划

## 任务来源

`各阶段并发任务.md#L37`: `| **OBS可观测性** | 指标监控、审计哈希链、分布式追踪 | 多任务并行 |`

对应 `开发计划.md` 中的 Phase 0A OBS 轨道任务：
- T-GA-OBS-01: 指标监控框架(VictoriaMetrics compact/Prometheus full)
- T-GA-OBS-02: 审计日志哈希链(会话链+每小时跨链+关键操作独立审计)
- T-GA-OBS-03: OpenTelemetry分布式追踪框架(诊断链路全链路追踪)
- T-GA-OBS-04: 缓存防护监控指标(穿透/击穿/雪崩)
- T-GA-OBS-05: CUSUM参数校准(SOP标准操作程序)

## 现有代码基线

| 模块 | 文件 | 状态 |
|------|------|------|
| 结构化日志 | `app/observability/logging.py` | ✅ 已完成(Phase 0) |
| VM内存监控 | `app/observability/metrics.py` | ✅ 已完成(Phase 0) |
| 枚举定义 | `app/observability/enums.py` | ✅ 已完成(Phase 0) |
| 审计哈希链 | `app/services/audit_service.py` | ⚠️ 基础版已有，需增强(跨链+关键操作独立审计) |
| 指标采集 | — | ❌ 缺失 |
| 分布式追踪 | — | ❌ 缺失 |
| 缓存防护监控 | — | ❌ 缺失 |
| CUSUM校准 | — | ❌ 缺失 |
| Docker配置 | `docker-compose.dev.yml` | ❌ 缺VictoriaMetrics容器 |
| 配置项 | `app/config/settings.py` | ❌ 缺OBS相关配置 |

## 开发目录

所有代码在 `app/observability/` 目录下完成，测试在 `tests/unit/obs/` 下完成。

## 实施步骤

### 步骤1: 添加OBS配置项到settings.py

**文件**: `app/config/settings.py`

新增配置项（对齐 06-开发环境与进度规划.md §5）：
```python
# --- 可观测性配置 ---
obs_metrics_enabled: bool = True
obs_metrics_port: int = 9090
obs_metrics_scrape_interval: float = 15.0
obs_victoriametrics_url: str = 'http://victoriametrics:8428'
obs_tracing_enabled: bool = False
obs_tracing_otlp_endpoint: str = 'http://jaeger:4317'
obs_tracing_sample_rate: float = 0.1
obs_audit_cross_chain_interval_hours: int = 1
obs_audit_critical_operations: List[str] = Field(default_factory=lambda: [
    'threshold_adjustment', 'model_version_change',
    'kb_update', 'desensitization_rule_update'
])
obs_cache_monitor_enabled: bool = True
obs_cusum_target_rate: float = 0.005
obs_cusum_decision_threshold: float = 3.0
obs_cusum_calibration_samples: int = 300
obs_cusum_calibration_simulations: int = 1000
```

### 步骤2: 添加Python依赖

**文件**: `requirements.txt`

新增：
```
prometheus_client==0.20.0
opentelemetry-api==1.24.0
opentelemetry-sdk==1.24.0
opentelemetry-exporter-otlp-proto-grpc==1.24.0
opentelemetry-instrumentation-fastapi==0.45b0
opentelemetry-instrumentation-asyncpg==0.45b0
opentelemetry-instrumentation-redis==0.45b0
```

### 步骤3: 添加VictoriaMetrics到docker-compose.dev.yml

**文件**: `docker-compose.dev.yml`

新增VictoriaMetrics服务（对齐 05-可观测性与运维.md §2 compact模式监控方案）：
```yaml
victoriametrics:
  image: victoriametrics/victoria-metrics:v1.93.0
  container_name: ultrasound-victoriametrics
  ports:
    - "8428:8428"
  volumes:
    - vmdata:/victoria-metrics-data
  networks:
    - ultrasound-network
  oom_score_adj: -500
  deploy:
    resources:
      limits:
        cpus: '0.5'
        memory: 200M
      reservations:
        cpus: '0.1'
        memory: 50M
```

### 步骤4: T-GA-OBS-01 指标监控框架

**新建文件**: `app/observability/metrics_collector.py`

核心设计：
- 使用 `prometheus_client` 定义所有指标（Counter/Gauge/Histogram/Summary）
- Profile感知：compact模式推送到VictoriaMetrics，full模式由Prometheus拉取
- 指标分类（对齐 05-可观测性与运维.md §2）：

**性能指标**:
- `ultrasound_nav_latency_seconds` (Histogram) - 实时导航端到端延迟
- `ultrasound_assessment_latency_seconds` (Histogram) - 辅助评估延迟
- `ultrasound_diagnosis_latency_seconds` (Histogram) - 完整诊断延迟
- `ultrasound_multi_organ_latency_seconds` (Histogram) - 多器官联合诊断延迟

**质量指标**:
- `ultrasound_severe_hallucination_rate` (Gauge) - 严重幻觉率
- `ultrasound_overall_hallucination_rate` (Gauge) - 整体幻觉率
- `ultrasound_high_risk_hallucination_rate` (Gauge) - 高风险病种幻觉率
- `ultrasound_retrieval_empty_rate` (Gauge) - 检索空结果率

**可用性指标**:
- `ultrasound_service_health_rate` (Gauge) - 服务健康率

**资源指标**:
- `ultrasound_gpu_memory_utilization` (Gauge) - GPU显存利用率
- `ultrasound_gpu_fragmentation_rate` (Gauge) - GPU显存碎片化率
- `ultrasound_gpu_temperature_celsius` (Gauge) - GPU温度
- `ultrasound_model_hot_reload_success_rate` (Gauge) - 模型热重载成功率
- `ultrasound_model_hot_reload_duration_seconds` (Histogram) - 模型热重载耗时

**业务指标**:
- `ultrasound_cache_hit_rate` (Gauge) - 知识缓存命中率
- `ultrasound_crag_supplement_rate` (Gauge) - CRAG补充检索触发率
- `ultrasound_self_rag_retry_rate` (Gauge) - Self-RAG重试率
- `ultrasound_adaptive_rag_route_distribution` (Counter) - Adaptive RAG路由分布

**安全指标**:
- `ultrasound_consent_query_latency_seconds` (Histogram) - 同意状态查询延迟
- `ultrasound_safety_gateway_health` (Gauge) - SafetyGateway健康状态
- `ultrasound_desensitization_sync_delay_seconds` (Gauge) - 脱敏规则同步延迟

**基础设施指标**:
- `ultrasound_circuit_breaker_state` (Gauge) - 熔断器状态
- `ultrasound_bM25_fallback_duration_seconds` (Gauge) - BM25兜底持续时间
- `ultrasound_dual_write_queue_backlog` (Gauge) - 双写补偿队列积压

**新增类**:
- `MetricsCollector` - 单例，管理所有指标注册和采集
- `MetricsServer` - HTTP指标暴露服务器（/metrics端点）
- `VictoriaMetricsPusher` - compact模式推送到VM

### 步骤5: T-GA-OBS-02 审计日志哈希链增强

**新建文件**: `app/observability/audit_chain.py`

**增强内容**（对齐 05-可观测性与运维.md §3 + 开发计划 T-GA-OBS-02）：

1. **每小时跨链（HourlyCrossChain）**:
   - 每小时将所有活跃会话链的最新哈希汇总，生成一条跨链记录
   - 跨链哈希 = SHA256(前一小时跨链哈希 + 所有会话链最新哈希排序拼接)
   - 跨链记录存储在 `audit_cross_chain` 表中
   - 篡改任一会话链中的记录，将导致跨链校验失败

2. **关键操作独立审计（CriticalOperationAudit）**:
   - 关键操作列表：阈值调整、模型版本变更、知识库更新、脱敏规则更新
   - 关键操作审计日志独立于会话链，拥有自己的哈希链
   - 关键操作审计日志同时链接到相关会话链（若存在）
   - 关键操作审计日志记录：operator, operation, before_value, after_value, timestamp

3. **完整性校验增强**:
   - `verify_session_chain()` - 单会话链校验
   - `verify_cross_chain()` - 跨链校验
   - `verify_critical_operation_chain()` - 关键操作链校验
   - `verify_all()` - 全量校验

4. **数据库迁移**:
   - 新增 `audit_cross_chain` 表
   - `audit_logs` 表增加 `is_critical_operation` 布尔字段
   - `audit_logs` 表增加 `cross_chain_hash` 字段

### 步骤6: T-GA-OBS-03 OpenTelemetry分布式追踪

**新建文件**: `app/observability/tracing.py`

核心设计（对齐 05-可观测性与运维.md §3）：

1. **TracerProvider配置**:
   - Profile感知：compact模式采样率更低（10%），full模式更高（50%）
   - OTLP gRPC exporter发送到Jaeger/Tempo
   - Resource属性：service.name, service.version, deployment.profile

2. **诊断链路全链路追踪**:
   ```
   请求入口 → 标准化解析 → 调度分配 → 专科智能体 → 知识检索校验 → 融合 → 脱敏校验 → 输出
      |            |            |            |            |       |      |       |
    trace_id    span_1      span_2      span_3      span_4   span_5  span_6  span_7
   ```

3. **Span定义**:
   - `diagnosis.request` - 诊断请求入口
   - `diagnosis.parse` - 标准化解析
   - `diagnosis.dispatch` - 调度分配
   - `diagnosis.specialist.{organ}` - 专科智能体推理
   - `diagnosis.retrieval` - 知识检索校验
   - `diagnosis.fusion` - 融合层处理
   - `diagnosis.desensitization` - 脱敏校验
   - `diagnosis.output` - 输出生成

4. **FastAPI集成**:
   - 使用 `opentelemetry-instrumentation-fastapi` 自动埋点
   - 中间件注入 trace_id 到响应头

5. **上下文传播**:
   - 使用 contextvars 在异步调用链中传播 trace_id / span_id
   - 日志自动绑定 trace_id（structlog processor）

### 步骤7: T-GA-OBS-04 缓存防护监控指标

**新建文件**: `app/observability/cache_monitor.py`

核心设计（对齐 05-可观测性与运维.md §86-123）：

1. **缓存穿透监控**:
   - `cache_bloom_filter_interception_rate` (Gauge) - 布隆过滤器拦截率，目标≥95%
   - `cache_null_value_hit_rate` (Gauge) - 空值缓存命中率，目标≥80%
   - `cache_db_penetration_rate` (Gauge) - 穿透至数据库查询率，目标≤1%

2. **缓存击穿监控**:
   - `cache_mutex_lock_success_rate` (Gauge) - 互斥锁获取成功率，目标≥98%
   - `cache_mutex_lock_timeout_total` (Counter) - 互斥锁等待超时次数，目标≤5次/小时
   - `cache_hotkey_permanent_coverage` (Gauge) - 热点key永不过期覆盖率，目标100%

3. **缓存雪崩监控**:
   - `cache_ttl_randomization_offset_percent` (Gauge) - TTL随机化偏移率，目标±10%
   - `cache_simultaneous_expiry_5min` (Gauge) - 5分钟窗口内同时失效数量，目标≤10
   - `cache_warmup_completion_rate` (Gauge) - 缓存预热完成率，目标100%
   - `cache_warmup_duration_seconds` (Histogram) - 缓存预热耗时，目标<30s

4. **缓存整体指标**:
   - `cache_overall_hit_rate` (Gauge) - 综合缓存命中率，目标≥70%
   - `cache_write_latency_seconds` (Histogram) - 缓存写入延迟P99，目标<10ms
   - `cache_read_latency_seconds` (Histogram) - 缓存读取延迟P99，目标<5ms

5. **日志收集**:
   - 布隆过滤器加载完成 → INFO
   - 空值缓存写入 → DEBUG
   - 互斥锁获取失败 → WARN
   - 缓存预热完成/失败 → INFO/ERROR
   - TTL随机化配置检查 → INFO

6. **告警升级流程**（对齐 05-可观测性与运维.md §104-111）:
   - 缓存命中率 < 40% Critical → 检查Redis → 检查布隆过滤器 → 手动预热
   - 穿透至数据库 > 10% Critical → 检查布隆过滤器 → 检查空值缓存
   - 缓存同时失效 > 100 Critical → 确认TTL随机化 → 紧急预热
   - 互斥锁超时 > 50次/小时 Critical → 检查热点key加载耗时

### 步骤8: T-GA-OBS-05 CUSUM参数校准

**新建文件**: `app/observability/cusum.py`

核心设计（对齐 05-可观测性与运维.md §159-172）：

1. **CUSUM累积和控制图**:
   - 目标值 p₀ = 0.5%（严重幻觉率基线）
   - 决策阈值 h = 3（默认值，可通过校准调整）
   - 偏移量 δ 模拟 1%/2%/3%
   - 上界 CUSUM: S₊ᵢ = max(0, S₊ᵢ₋₁ + (xᵢ - p₀ - δ))
   - 下界 CUSUM: S₋ᵢ = max(0, S₋ᵢ₋₁ + (p₀ - xᵢ - δ))
   - 当 S₊ᵢ > h 或 S₋ᵢ > h 时触发告警

2. **SOP校准程序**:
   - 准备阶段：收集历史标注数据（≥300例，三器官各≥100例）
   - 参数设置：p₀=0.5%, δ∈{1%, 2%, 3%}
   - 蒙特卡洛模拟：1000次重复实验，每次30天日检查量（~50例/日）
   - 指标计算：ARL₀（基线下平均运行长度）、ARL₁（偏移下平均运行长度）
   - 判定规则：
     - ARL₀ < 100 → h值过低，调至h=4或h=5
     - ARL₁ > 50 → h值过高，调至h=2
     - ARL₀ ≥ 100 且 ARL₁ ≤ 50 → 参数适用
   - 记录存档：校准结果记录至Gate 0A验证报告

3. **CUSUM运行时监控**:
   - 每次诊断完成后更新CUSUM统计量
   - 超阈值时自动触发告警
   - compact模式：周统计 + CUSUM双机制
   - full模式：周统计为主

### 步骤9: 更新observability包__init__.py

**文件**: `app/observability/__init__.py`

导出所有新增模块的公共接口。

### 步骤10: 编写单元测试

**新建/更新文件**:
- `tests/unit/obs/test_metrics_collector.py` - 指标采集框架测试
- `tests/unit/obs/test_audit_chain.py` - 审计哈希链增强测试
- `tests/unit/obs/test_tracing.py` - 分布式追踪测试
- `tests/unit/obs/test_cache_monitor.py` - 缓存防护监控测试
- `tests/unit/obs/test_cusum.py` - CUSUM校准测试

### 步骤11: 数据库迁移脚本

**新建文件**: `alembic/versions/0004_audit_cross_chain.py`

新增 `audit_cross_chain` 表，`audit_logs` 表增加字段。

### 步骤12: 集成验证

- 启动 docker-compose，验证 VictoriaMetrics 容器正常运行
- 验证 /metrics 端点暴露所有指标
- 验证审计哈希链跨链校验
- 验证追踪 span 传播
- 验证缓存防护指标采集
- 验证 CUSUM 统计量计算

## 依赖关系

```
步骤1(settings配置) ─┐
步骤2(依赖安装) ─────┤
步骤3(Docker配置) ───┤
                      ├→ 步骤4(指标监控) → 步骤10(测试)
                      ├→ 步骤5(审计链增强) → 步骤10(测试)
                      ├→ 步骤6(分布式追踪) → 步骤10(测试)
                      ├→ 步骤7(缓存防护) → 步骤10(测试)
                      └→ 步骤8(CUSUM) → 步骤10(测试)

步骤9(__init__.py更新) ← 步骤4-8全部完成
步骤11(数据库迁移) ← 步骤5
步骤12(集成验证) ← 步骤9-11全部完成
```

## 验收标准

| 任务ID | 验收标准 |
|--------|---------|
| T-GA-OBS-01 | 关键指标可视化；/metrics端点暴露所有指标；VictoriaMetrics容器内存<200MB |
| T-GA-OBS-02 | 篡改检测窗口≤1分钟；跨链校验正确；关键操作独立审计链完整 |
| T-GA-OBS-03 | 单请求全链路延迟追踪可用；trace_id贯穿日志；span覆盖8个诊断阶段 |
| T-GA-OBS-04 | 布隆过滤器拦截率≥95%+热点key永不过期覆盖率100%监控可用 |
| T-GA-OBS-05 | ARL₀≥100且ARL₁≤50；CUSUM超阈值自动告警 |

## 风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| prometheus_client与FastAPI集成冲突 | 使用独立HTTP服务器暴露指标（端口9090） |
| OpenTelemetry异步上下文传播问题 | 使用contextvars + structlog processor |
| VictoriaMetrics容器内存超200MB | 超限检测机制已实现(metrics.py) |
| CUSUM蒙特卡洛模拟耗时 | 使用numpy向量化计算，限制模拟次数1000 |
