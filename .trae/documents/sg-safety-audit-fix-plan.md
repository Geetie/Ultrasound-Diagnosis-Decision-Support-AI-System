# SafetyGateway 安全审查修复实施方案

## 修复总览

| 阶段 | 修复项 | 优先级 | 涉及文件 |
|------|--------|--------|---------|
| 阶段1 | SG-FIX-001~010 | P0 | 核心安全缺陷 |
| 阶段2 | SG-FIX-011~020 | P1 | 重要缺陷 |
| 阶段3 | SG-FIX-021~027 | P2 | 改进建议 |
| 阶段4 | T1~T6 | P0 | 测试修复 |

---

## 阶段1: P0安全关键修复

### SG-FIX-001: 脱敏深度超限返回未脱敏原始数据
- **文件**: `safety_gateway/desensitization.py` L170-173, L200-203
- **修改**: `desensitize_dict` 和 `_desensitize_list` 深度超限时返回 `[REDACTED:DEPTH_EXCEEDED]` 而非原始数据

### SG-FIX-002: 禁忌校验patient_conditions缺失时静默通过
- **文件**: `safety_gateway/contraindication.py` L206-216
- **修改**: `_match_condition` 在 `patient_conditions` 缺失且 `clinical_context` 也缺失时返回 `True`（保守策略：视为存在风险）

### SG-FIX-003: 条件别名映射不完整
- **文件**: `safety_gateway/contraindication.py` L202-204
- **修改**: 补全 `_CONDITION_ALIASES`，添加 anticoagulant/coagulopathy/contrast_allergy 等别名

### SG-FIX-004: ThresholdPhase组合故障检测死代码
- **文件**: `safety_gateway/checker/threshold_phase.py` L47-51
- **修改**: 从 `CheckContext.contraindication_matches` 获取禁忌结果，而非从 `threshold_results` 中查找不存在的字段

### SG-FIX-005: ConsistencyPhase EVIDENCE_INSUFFICIENT逻辑补全
- **文件**: `safety_gateway/checker/consistency_phase.py` L170-199
- **修改**: `_classify_consistency` 添加证据不足判定：当有专科结论但缺少关键字段(grade/finding/confidence)时返回 EVIDENCE_INSUFFICIENT

### SG-FIX-006: 一致性降级场景缺少错误码和降级标记
- **文件**: `safety_gateway/degraded_output.py` L69-121
- **修改**: 在 `_DEGRADED_MARKS` 中添加 `CONSISTENCY_CONTRADICTORY` 和 `CONSISTENCY_PARTIAL` 的映射

### SG-FIX-007: Stream消费者失败时仍ACK
- **文件**: `safety_gateway/redis_subscriber.py` L184-192
- **修改**: handler 抛异常时不执行 xack，让消息保持 pending 状态等待重新分配

### SG-FIX-008: consistency/contraindication端点无OOM保护
- **文件**: `safety_gateway/main.py` L372-431
- **修改**: 在 `perform_consistency_check` 和 `perform_contraindication_check` 入口添加 MemoryMonitor 检查

### SG-FIX-009: 阈值持久化异常被吞掉
- **文件**: `safety_gateway/threshold_persistence.py` L65-71
- **修改**: 持久化失败时重新抛出异常，让调用方感知

### SG-FIX-010: 生产者-消费者消息格式不匹配
- **文件**: `safety_gateway/threshold_subscriber.py` L113, `safety_gateway/redis_subscriber.py` L175-178
- **修改**: 统一消息格式，生产者写入 `{"action": ..., "payload": ...}`，消费者解析 `payload` 字段

---

## 阶段2: P1重要缺陷修复

### SG-FIX-011: 降级路径额外查询融合层可用性
- **文件**: `safety_gateway/main.py` L355-358
- **修改**: 将融合层可用性缓存到 CheckContext，避免降级路径额外查询

### SG-FIX-012: 未知器官使用宽松默认阈值
- **文件**: `safety_gateway/sg_config.py` L59
- **修改**: `sg_default_unknown_organ_threshold` 从 0.90 提高到 0.95

### SG-FIX-013: ***替换可被上下文推断
- **文件**: `safety_gateway/degraded_output.py` L59
- **修改**: 替换为 `[REDACTED:GRADING]`

### SG-FIX-014: 错误码映射不完整
- **文件**: `safety_gateway/errors.py` L37-46
- **修改**: 补全 SG_VALIDATION_ERROR→E4005, SG_SENSITIVE_DATA→E4005A1, SG_CONTRAINDICATION→E3006, SG_THRESHOLD→E4005

### SG-FIX-015: database_url空值校验不阻止启动
- **文件**: `safety_gateway/sg_config.py` L103-109
- **修改**: 在 model_validator 中直接 raise ValueError

### SG-FIX-016: Redis maxmemory未按db隔离
- **文件**: `docker-compose.dev.yml`
- **修改**: 添加注释说明 + 考虑为SG预留配额

### SG-FIX-017: 禁忌规则缺少肾脏/乳腺/胆囊等器官
- **文件**: `contraindication_rules_safety.yaml`
- **修改**: 补全常见超声器官禁忌规则

### SG-FIX-018: Patch脱敏规则无异构性标注
- **文件**: `desensitization_rules_safety_patch.yaml`
- **修改**: 为每条规则添加异构性说明

### SG-FIX-019: ContraindicationEngine无线程安全保护
- **文件**: `safety_gateway/contraindication.py`
- **修改**: 添加 threading.Lock 保护懒加载

### SG-FIX-020: DegradedReason缺少枚举值
- **文件**: `safety_gateway/validation_result.py`
- **修改**: 添加 CIRCUIT_BREAKER_OPEN / HEALTH_CHECK_FAILURE / MEMORY_PRESSURE

---

## 阶段3: P2改进建议

### SG-FIX-021: 无worker数量运行时校验
- **文件**: `safety_gateway/main.py` lifespan
- **修改**: 启动时检查环境变量或命令行参数

### SG-FIX-022: sg_validator与sg_config校验逻辑重复
- **文件**: `safety_gateway/sg_validator.py`
- **修改**: 统一为一处校验

### SG-FIX-023: ConsentRecord.status无DB层枚举约束
- **文件**: `safety_gateway/sg_models.py`
- **修改**: 添加 CheckConstraint

### SG-FIX-024: retry.py无jitter
- **文件**: `safety_gateway/retry.py`
- **修改**: 添加随机抖动因子

### SG-FIX-025: Redis get/setex静默吞异常
- **文件**: `safety_gateway/redis_pool.py`
- **修改**: 安全关键路径失败时抛异常

### SG-FIX-026: XPENDING/XCLAIM未实现
- **文件**: `safety_gateway/redis_subscriber.py`
- **修改**: 添加 pending 消息重新分配逻辑

### SG-FIX-027: Dockerfile与docker-compose healthcheck不一致
- **文件**: `Dockerfile.safety-gateway.dev`
- **修改**: 统一 start-period 为 15s
