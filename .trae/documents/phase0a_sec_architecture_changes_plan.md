# Phase 0A SEC轨道架构级变更实施计划

## 0. 仓库研究结论

### 现有代码状态

| 模块 | 文件路径 | 现状 |
|---|---|---|
| 三层脱敏 | `app/security/three_layer_desensitization.py` | 已实现，单例模式 |
| HSM管理 | `app/security/hsm.py` | 已实现，721行，无密钥槽分离、无应急签发 |
| Refresh Token | `app/security/refresh_token.py` | 已实现，内存+Redis双后端 |
| 同意仓储 | `app/repositories/consent_repository.py` | 已实现，含`_publish_consent_withdrawal()`广播 |
| 同意校验 | `safety_gateway/consent_checker.py` | 已实现，熔断器+重试 |
| 审计服务 | `app/services/audit_service.py` | 已实现，哈希链+asyncio.Lock |
| 脱敏还原 | — | **不存在**，仅`settings.py`有`desensitization_restore_rate_limit=100`占位 |
| 同意撤回消费侧 | — | **不存在**，仅有生产侧广播(`_publish_consent_withdrawal`)，无消费侧 |
| 调度智能体 | — | **不存在**，无`agents/`目录 |
| 测试总数 | `tests/` | **1467个测试函数**，已超过CI≥1000基线 |

### 关键发现

1. **脱敏还原服务**：零实现，需从零构建
2. **HSM密钥槽**：当前使用`_cached_keys: Dict[str, Dict]`扁平字典，无槽位概念
3. **HSM应急签发**：当前RESTRICTED/EMERGENCY阶段完全禁止签名，无旁路机制
4. **同意撤回消费侧**：仅有生产侧（`_publish_consent_withdrawal`写入Redis Stream），无消费侧监听
5. **调度智能体**：不存在，需做无侵入预留设计
6. **测试计数**：1467 > 1000，L5基线已满足，但需确保CI全绿

---

## 1. 变更项总览与依赖关系

```
C1（脱敏-还原映射表）
 ├── 依赖 H1（HSM密钥槽分离）— 映射表加密密钥需独立槽位
 └── 独立新增 DesensitizationRestoreService

C2（HSM应急签发）
 ├── 依赖 H1（密钥槽分离）— 应急签发密钥需独立槽位
 ├── 依赖 H2（降级行为完善）— 应急签发为降级模式的增强
 └── 修改 hsm.py sign()/sign_jwt()

C3（同意撤回消费侧）
 ├── 独立新增 consent_withdrawal_consumer.py
 ├── 无侵入预留：ConsentWithdrawalHandler 抽象接口
 └── 调度智能体集成点：预留回调钩子

H1（HSM密钥槽分离）— 修改 hsm.py
H2（降级行为完善）— 修改 hsm.py
H3（SoftHSM告警增强）— 修改 hsm.py
H4（旧密钥自动清理）— 修改 hsm.py

L5（测试计数CI≥1000）— 已满足基线，需补充新变更测试
```

### 实施顺序（拓扑排序）

```
Phase 1: H1 → H2 → H3 → H4（HSM基础设施，无外部依赖）
Phase 2: C1（依赖H1的密钥槽分离）
Phase 3: C2（依赖H1+H2）
Phase 4: C3（独立，可与Phase 2/3并行）
Phase 5: L5（补充所有新变更的测试）
```

---

## 2. 详细实施步骤

### Phase 1: HSM基础设施增强（H1-H4）

#### H1 — HSM密钥槽分离

**目标**：将`_cached_keys`扁平字典改为按用途分离的密钥槽位，映射表加密密钥与JWT签名密钥使用不同槽位。

**修改文件**：`app/security/hsm.py`

**步骤**：

1. 新增`KeySlot`枚举：
   ```python
   class KeySlot(str, Enum):
       JWT_SIGNING = "jwt_signing"
       DATA_ENCRYPTION = "data_encryption"
       DESENSITIZATION_MAPPING = "desensitization_mapping"
       EMERGENCY_SIGNING = "emergency_signing"
   ```

2. 将`_cached_keys: Dict[str, Dict]`重构为`_key_slots: Dict[KeySlot, Dict[str, Dict]]`，内部字典仍以`key_name`为键

3. 新增方法`get_key_slot(key_name: str) -> KeySlot`：根据key_name前缀自动路由到对应槽位（`jwt_*` → JWT_SIGNING, `desens_*` → DESENSITIZATION_MAPPING, `emergency_*` → EMERGENCY_SIGNING, 其余 → DATA_ENCRYPTION）

4. 修改`generate_key_pair()`：接受`slot: KeySlot`参数，将密钥存入对应槽位

5. 修改`sign()`/`sign_jwt()`/`encrypt()`/`decrypt()`：从对应槽位查找密钥

6. 修改`rotate_key()`/`cleanup_expired_keys()`：按槽位操作

7. 修改`_persist_key_to_redis()`/`_restore_keys_from_redis()`：Redis key增加槽位前缀（`hsm:keys:{slot}:{key_name}`）

8. **向后兼容**：提供`_migrate_legacy_keys()`方法，启动时检测旧格式Redis key并迁移到新槽位结构

**验证**：
- 现有测试全部通过（密钥查找逻辑不变，仅存储结构变化）
- JWT签名密钥与脱敏映射表加密密钥在不同Redis key前缀下存储

#### H2 — 降级行为完善

**目标**：细化降级阶段的行为定义，RESTRICTED阶段允许特定操作（如脱敏映射表解密），EMERGENCY阶段增加应急签发入口。

**修改文件**：`app/security/hsm.py`

**步骤**：

1. 新增`DegradedBehavior`数据类：
   ```python
   @dataclass
   class DegradedBehavior:
       allow_jwt_signing: bool
       allow_data_encryption: bool
       allow_desensitization_restore: bool
       allow_emergency_signing: bool
       max_signature_count: Optional[int]  # None=无限制
   ```

2. 定义各阶段行为映射：
   ```python
   DEGRADED_BEHAVIORS = {
       DegradedPhase.NORMAL: DegradedBehavior(True, True, True, False, None),
       DegradedPhase.CACHED: DegradedBehavior(True, True, True, False, None),
       DegradedPhase.RESTRICTED: DegrededBehavior(False, True, True, False, None),
       DegradedPhase.EMERGENCY: DegradedBehavior(False, False, False, True, 50),
   }
   ```

3. 修改`sign()`/`sign_jwt()`：检查当前阶段行为映射，而非硬编码if-else

4. 新增`check_degraded_behavior(operation: str) -> bool`方法：统一降级行为检查入口

5. RESTRICTED阶段新增：允许脱敏映射表解密（`allow_desensitization_restore=True`），允许数据加密操作（`allow_data_encryption=True`）

**验证**：
- RESTRICTED阶段签名被拒绝，但解密操作允许
- CACHED阶段所有操作正常
- EMERGENCY阶段仅应急签发允许

#### H3 — SoftHSM告警增强

**目标**：SoftHSM超期告警从启动时检查增强为持续性周期检查+Critical告警升级。

**修改文件**：`app/security/hsm.py`

**步骤**：

1. 新增`_soft_hsm_check_interval`配置项（默认24小时）

2. 新增`_start_soft_hsm_periodic_check()`方法：启动后台定时任务，每24小时检查一次SoftHSM过期状态

3. 超期后行为增强：
   - 0-90天：正常运行
   - 90-120天：每次签名操作记录Warning日志 + 管理界面弹出不可关闭警告
   - >120天：每次签名操作记录Critical日志 + 触发Critical告警通知科室主任

4. 新增`get_soft_hsm_status() -> Dict`方法：返回SoftHSM运行天数、是否超期、超期等级

**验证**：
- SoftHSM运行91天时签名仍可执行但产生Warning日志
- SoftHSM运行121天时产生Critical告警

#### H4 — 旧密钥自动清理

**目标**：`cleanup_expired_keys()`同步清理Redis中的持久化密钥数据，而非仅清理内存缓存。

**修改文件**：`app/security/hsm.py`

**步骤**：

1. 修改`cleanup_expired_keys()`：清理内存缓存后，同步删除Redis中对应key（`hsm:keys:{slot}:{key_name}`）

2. 新增`_start_key_cleanup_timer()`方法：启动后台定时任务，每24小时自动执行一次清理

3. 清理前记录审计日志（含被清理密钥的名称、版本、过期时间）

4. 新增配置项`HSM_KEY_CLEANUP_INTERVAL_HOURS`（默认24）

**验证**：
- 过期密钥从内存和Redis中同步删除
- 清理操作记录审计日志

---

### Phase 2: C1 — 脱敏-还原映射表

**目标**：新增`DesensitizationRestoreService`，实现脱敏-还原映射表的加密存储与安全还原。

**新增文件**：`app/security/desensitization_restore.py`

**步骤**：

1. 新增`DesensitizationMappingRecord`数据类：
   ```python
   @dataclass
   class DesensitizationMappingRecord:
       record_id: str
       session_id: str
       original_value: str  # 加密存储
       desensitized_value: str
       field_type: str  # "patient_name" | "id_card" | "phone" | ...
       key_version: int
       created_at: datetime
   ```

2. 新增`DesensitizationRestoreService`类：
   - `store_mapping(session_id, field_type, original, desensitized) -> str`：存储映射记录
     - 使用AES-256-GCM加密original_value
     - 加密密钥从HSM的`KeySlot.DESENSITIZATION_MAPPING`槽位获取
     - 记录`key_version`
     - 写入PostgreSQL `desensitization_mappings`表
     - 记录审计日志
   - `restore_value(record_id: str) -> str`：还原脱敏值
     - 速率限制检查（`desensitization_restore_rate_limit`）
     - 从HSM获取解密密钥
     - AES-256-GCM解密
     - 记录审计日志（含调用方、时间）
     - 返回原始值
   - `batch_restore(record_ids: List[str]) -> Dict[str, str]`：批量还原
   - `re_encrypt_with_new_key(new_key_version: int) -> int`：密钥轮换时后台异步重加密
     - 按`key_version`分批查询旧记录
     - 旧密钥解密 → 新密钥加密 → 更新`key_version`
     - 重加密吞吐 ≥ 1000条/秒
   - `get_restore_stats() -> Dict`：还原统计（频率、最近访问时间等）

3. AES-256-GCM加密实现：
   - 使用`cryptography`库的`AESGCM`
   - 每条记录独立IV（12字节随机）
   - 密钥从HSM `KeySlot.DESENSITIZATION_MAPPING`获取
   - 密钥缓存至内存（TTL 5分钟），减少HSM调用

4. 数据库模型（新增`app/models/desensitization_mapping.py`）：
   ```python
   class DesensitizationMapping(Base):
       __tablename__ = "desensitization_mappings"
       id = Column(String, primary_key=True)
       session_id = Column(String, index=True)
       encrypted_original = Column(LargeBinary)  # AES-256-GCM密文
       iv = Column(LargeBinary)  # 12字节IV
       desensitized_value = Column(String)
       field_type = Column(String)
       key_version = Column(Integer)
       created_at = Column(DateTime)
   ```

5. 修改`three_layer_desensitization.py`：在`process_standardization_layer()`中，脱敏后调用`DesensitizationRestoreService.store_mapping()`存储映射

6. 修改`app/config/settings.py`：新增配置项
   - `desensitization_mapping_encryption_key_slot: str = "desensitization_mapping"`
   - `desensitization_key_rotation_days: int = 180`
   - `desensitization_re_encrypt_batch_size: int = 1000`

**验证**：
- 脱敏后可正确还原原始值
- 还原操作记录审计日志
- 速率限制生效
- 密钥轮换后重加密正确

---

### Phase 3: C2 — HSM应急签发方案

**目标**：HSM不可用超过5分钟时，管理员可启用应急签发模式，使用本地缓存密钥签发报告。

**修改文件**：`app/security/hsm.py`，新增`app/security/emergency_signing.py`

**步骤**：

1. 新增`app/security/emergency_signing.py`：
   - `EmergencySigningManager`类：
     - `enable_emergency_signing(operator_id: str, reason: str) -> None`：启用应急签发
       - 检查前置条件：HSM降级超过5分钟 + 管理员权限
       - 从`KeySlot.EMERGENCY_SIGNING`加载应急签名密钥
       - 设置Redis标记`hsm:emergency_signing:enabled`（TTL 24小时）
       - 记录审计日志（含操作人、原因、启用时间）
     - `disable_emergency_signing(operator_id: str) -> None`：禁用应急签发
     - `is_emergency_signing_active() -> bool`：检查应急签发是否激活
     - `sign_emergency(key_name: str, data: bytes) -> Tuple[bytes, Dict]`：应急签名
       - 返回签名 + 元数据（含`emergency: True`标记、操作人、时间戳）
       - 签名计数器递增（EMERGENCY阶段上限50次/天）
     - `sign_jwt_emergency(key_name, header, payload) -> Tuple[str, Dict]`：应急JWT签名
     - `get_pending_verification_reports() -> List[Dict]`：获取待补验证报告列表
     - `verify_emergency_signature(signature_data: bytes, report_id: str) -> bool`：HSM恢复后补验证
       - 使用HSM主密钥重新验证应急签名
       - 验证通过后标记报告为"已验证"
       - 验证失败触发Critical告警

2. 修改`hsm.py`的`sign()`/`sign_jwt()`：
   - EMERGENCY阶段：检查`EmergencySigningManager.is_emergency_signing_active()`
   - 若激活，委托给`EmergencySigningManager.sign_emergency()`
   - 若未激活，仍抛出RuntimeError

3. 新增配置项（`settings.py`）：
   - `hsm_emergency_signing_enabled: bool = False`（默认关闭）
   - `hsm_emergency_signing_max_daily: int = 50`
   - `hsm_emergency_signing_verification_hours: int = 24`

4. 应急签发密钥管理：
   - 应急签名密钥存储在`KeySlot.EMERGENCY_SIGNING`槽位
   - 应急密钥与主签名密钥独立轮换（120天周期）
   - 应急密钥仅在应急签发激活时加载到内存

**验证**：
- 应急签发启用/禁用流程正确
- 应急签名包含`emergency: True`标记
- HSM恢复后补验证流程正确
- 签名计数限制生效

---

### Phase 4: C3 — 同意撤回消费侧（无侵入预留设计）

**目标**：新增同意撤回事件消费侧，监听Redis Streams，执行SEC轨道自主可控的逻辑，为调度智能体预留无侵入集成点。

**核心设计原则**：
- **SEC轨道100%自主可控**：撤回事件的消费、验证、安全动作执行全部在SEC轨道内完成
- **无侵入预留**：通过抽象接口+回调钩子，调度智能体开发完成后仅需实现接口即可集成
- **不依赖AGT轨道**：SEC轨道的核心安全动作（拦截输出、停止导航标注）不依赖调度智能体

**新增文件**：`app/security/consent_withdrawal_consumer.py`

**步骤**：

1. 新增`ConsentWithdrawalHandler`抽象基类（预留接口）：
   ```python
   class ConsentWithdrawalHandler(ABC):
       """同意撤回事件处理器抽象接口。
       
       SEC轨道内置实现：SafetyActionHandler（安全动作）
       AGT轨道后续实现：SchedulingAgentHandler（调度任务取消）
       """
       
       @abstractmethod
       async def handle_withdrawal(self, event: ConsentWithdrawalEvent) -> None:
           """处理同意撤回事件"""
           ...
       
       @abstractmethod
       def get_handler_name(self) -> str:
           """返回处理器名称，用于日志和审计"""
           ...
   ```

2. 新增`ConsentWithdrawalEvent`数据类：
   ```python
   @dataclass
   class ConsentWithdrawalEvent:
       patient_id: str
       operator_id: str
       withdrawn_at: datetime
       consent_version: str
       event_id: str  # Redis Stream消息ID
   ```

3. 新增`SafetyActionHandler(ConsentWithdrawalHandler)`（SEC轨道内置实现）：
   - `handle_withdrawal()`：
     - 在Redis中标记该患者所有活跃会话的同意状态为`withdrawn`
     - 通知SafetyGateway刷新同意状态缓存
     - 通知边缘端停止AI标注（通过Redis发布`edge:stop_ai_annotation:{session_id}`）
     - 记录审计日志
   - `get_handler_name() -> "safety_action"`

4. 新增`ConsentWithdrawalConsumer`类：
   - `__init__(handlers: List[ConsentWithdrawalHandler])`：接受处理器列表
   - `start()`：启动Redis Stream消费者组
   - `stop()`：优雅停止
   - `_consume_loop()`：持续读取Redis Stream（`consent:withdrawal`）
   - `_process_event()`：解析事件 → 依次调用所有handler的`handle_withdrawal()`
   - `_handle_processing_error()`：处理失败的事件写入补偿队列
   - 消费者组：`consent_withdrawal_consumers`
   - 消费策略：`XREADGROUP` + `ACK`，确保至少一次消费

5. **无侵入预留设计**（调度智能体集成点）：
   ```python
   # 预留的调度智能体处理器（AGT轨道后续实现）
   class SchedulingAgentHandler(ConsentWithdrawalHandler):
       """调度智能体同意撤回处理器 — 预留实现。
       
       AGT轨道开发完成后，仅需实现此接口并注册到Consumer即可。
       集成方式：在应用启动时将SchedulingAgentHandler添加到handlers列表。
       """
       
       async def handle_withdrawal(self, event: ConsentWithdrawalEvent) -> None:
           # 预留：取消该患者所有在途诊断任务
           # 调度智能体开发后实现：await scheduling_agent.cancel_tasks(event.patient_id)
           logger.info(f"SchedulingAgentHandler: withdrawal event received for patient {event.patient_id}, "
                       f"scheduling agent integration pending")
       
       def get_handler_name(self) -> str:
           return "scheduling_agent_pending"
   ```

6. 新增`ConsentWithdrawalConsumer`的启动集成：
   - 在应用启动时（`app/main.py`或lifespan事件中）初始化Consumer
   - 默认注册`SafetyActionHandler`
   - `SchedulingAgentHandler`默认**不注册**（预留，AGT轨道开发后注册）
   - 通过配置项`CONSENT_WITHDRAWAL_HANDLERS`控制启用的处理器列表

7. 新增配置项（`settings.py`）：
   - `consent_withdrawal_consumer_enabled: bool = True`
   - `consent_withdrawal_consumer_group: str = "consent_withdrawal_consumers"`
   - `consent_withdrawal_consumer_name: str = "sec_consumer_01"`
   - `consent_withdrawal_handlers: List[str] = ["safety_action"]`（默认仅SEC内置处理器）
   - `consent_withdrawal_batch_size: int = 10`
   - `consent_withdrawal_poll_timeout_ms: int = 5000`

**验证**：
- 消费侧正确读取Redis Stream中的撤回事件
- SafetyActionHandler正确执行安全动作
- 调度智能体处理器为空实现但可注册
- 消费失败的事件进入补偿队列

**AGT轨道集成方式（后续）**：
```python
# AGT轨道开发完成后，仅需：
# 1. 实现 SchedulingAgentHandler.handle_withdrawal()
# 2. 在配置中添加 "scheduling_agent" 到 consent_withdrawal_handlers
# 无需修改SEC轨道任何代码
```

---

### Phase 5: L5 — 测试补充

**目标**：确保所有新变更的测试覆盖，CI全绿，总测试数 ≥ 1000。

**当前状态**：1467个测试已超过1000基线，但需补充新变更测试。

**新增测试文件**：

1. `tests/unit/test_desensitization_restore.py`：
   - `TestDesensitizationRestoreService`：存储映射、还原值、批量还原、速率限制、密钥轮换重加密
   - `TestDesensitizationMappingEncryption`：AES-256-GCM加密/解密、IV唯一性、密钥版本
   - 估计 ~25个测试

2. `tests/unit/test_emergency_signing.py`：
   - `TestEmergencySigningManager`：启用/禁用、签名、JWT签名、计数限制、补验证
   - `TestEmergencySigningIntegration`：与HSM降级流程集成
   - 估计 ~20个测试

3. `tests/unit/test_consent_withdrawal_consumer.py`：
   - `TestConsentWithdrawalConsumer`：消费事件、处理器调用、错误处理、补偿队列
   - `TestSafetyActionHandler`：安全动作执行、Redis标记、SafetyGateway通知
   - `TestSchedulingAgentHandlerStub`：预留处理器空实现验证
   - `TestConsentWithdrawalHandlerInterface`：抽象接口契约验证
   - 估计 ~20个测试

4. `tests/unit/test_hsm_key_slot.py`：
   - `TestKeySlotSeparation`：密钥槽位路由、槽位隔离、Redis前缀
   - `TestDegradedBehavior`：各阶段行为映射、操作权限检查
   - `TestSoftHSMPeriodicCheck`：周期检查、告警升级
   - `TestKeyCleanupSync`：内存+Redis同步清理、审计日志
   - 估计 ~25个测试

5. 修改现有测试文件以适配H1-H4变更：
   - `tests/unit/test_hsm_basic.py`：适配密钥槽位参数
   - `tests/unit/test_security_compliance_complete.py`：适配降级行为变更
   - `tests/unit/test_sec_audit.py`：适配应急签发审计

**验证**：
- `pytest tests/ --co -q` 计数 ≥ 1500
- `pytest tests/` 全绿
- 新增测试覆盖所有新代码路径

---

## 3. 文件变更清单

| 操作 | 文件路径 | 变更项 |
|---|---|---|
| **修改** | `app/security/hsm.py` | H1密钥槽分离 + H2降级行为 + H3 SoftHSM告警 + H4旧密钥清理 + C2应急签发集成 |
| **新增** | `app/security/desensitization_restore.py` | C1脱敏-还原映射表服务 |
| **新增** | `app/security/emergency_signing.py` | C2应急签发管理器 |
| **新增** | `app/security/consent_withdrawal_consumer.py` | C3同意撤回消费侧 |
| **新增** | `app/models/desensitization_mapping.py` | C1数据库模型 |
| **修改** | `app/security/three_layer_desensitization.py` | C1集成映射存储 |
| **修改** | `app/config/settings.py` | 所有新配置项 |
| **修改** | `app/main.py` | C3消费者启动 |
| **新增** | `tests/unit/test_desensitization_restore.py` | C1测试 |
| **新增** | `tests/unit/test_emergency_signing.py` | C2测试 |
| **新增** | `tests/unit/test_consent_withdrawal_consumer.py` | C3测试 |
| **新增** | `tests/unit/test_hsm_key_slot.py` | H1-H4测试 |
| **修改** | `tests/unit/test_hsm_basic.py` | 适配H1变更 |
| **修改** | `tests/unit/test_security_compliance_complete.py` | 适配H2变更 |

---

## 4. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| H1密钥槽重构破坏现有签名流程 | 高 | `_migrate_legacy_keys()`自动迁移 + 全量回归测试 |
| AES-256-GCM加密性能不达标（P99<10ms） | 中 | 密钥内存缓存TTL 5分钟 + 批量操作pipeline |
| Redis Stream消费侧与生产侧消息格式不一致 | 中 | 共享`ConsentWithdrawalEvent`数据类，生产侧和消费侧共用 |
| 应急签发密钥泄露 | 高 | 应急密钥独立槽位 + 120天轮换 + 启用/禁用审计日志 + 24小时补验证 |
| 调度智能体集成点设计不足 | 低 | 抽象接口+回调钩子，AGT轨道仅需实现接口，无需修改SEC代码 |

---

## 5. 无侵入预留设计总结

### C3 调度智能体集成方案

```
┌─────────────────────────────────────────────────┐
│              SEC轨道（自主可控）                    │
│                                                   │
│  ConsentWithdrawalConsumer                        │
│    ├── SafetyActionHandler（SEC内置，已实现）       │
│    │     ├── Redis标记会话同意状态                  │
│    │     ├── 通知SafetyGateway刷新缓存             │
│    │     ├── 通知边缘端停止AI标注                   │
│    │     └── 记录审计日志                          │
│    │                                               │
│    └── [预留] SchedulingAgentHandler              │
│          └── handle_withdrawal() → 空实现          │
│                                                   │
└─────────────────────────────────────────────────┘
                        │
                        │ 后续AGT轨道实现后注册
                        ▼
┌─────────────────────────────────────────────────┐
│              AGT轨道（后续集成）                    │
│                                                   │
│  SchedulingAgentHandler（实现ConsentWithdrawalHandler）│
│    └── handle_withdrawal()                        │
│          ├── 取消该患者所有在途诊断任务             │
│          └── 中断正在执行的诊断流程                 │
│                                                   │
│  集成方式：                                        │
│  1. 实现 ConsentWithdrawalHandler 接口             │
│  2. 配置 consent_withdrawal_handlers 添加          │
│     "scheduling_agent"                            │
│  3. 无需修改SEC轨道任何代码                        │
│                                                   │
└─────────────────────────────────────────────────┘
```

### 关键设计决策

1. **SEC轨道核心安全动作不依赖AGT轨道**：SafetyActionHandler独立完成所有安全关键操作（拦截输出、停止标注），即使调度智能体未集成，安全底线仍有保障
2. **抽象接口定义在SEC轨道**：`ConsentWithdrawalHandler`由SEC轨道定义，AGT轨道实现，依赖方向正确（AGT依赖SEC，非反向）
3. **配置驱动注册**：通过`consent_withdrawal_handlers`配置项控制处理器列表，无需硬编码导入
4. **空实现预留**：`SchedulingAgentHandler`默认不注册，AGT轨道开发完成后仅需实现接口+修改配置
