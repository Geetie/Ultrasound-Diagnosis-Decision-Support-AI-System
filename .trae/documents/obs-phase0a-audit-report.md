# OBS 可观测性轨道 Phase 0A 系统性审查报告

> **审查日期**: 2026-05-13  
> **审查范围**: OBS 可观测性轨道 Phase 0A 实施情况  
> **审查原则**: 独立、客观、深入、批判性  
> **核心参考文档**: 05-可观测性与运维.md (P0)、01-系统架构设计.md (P1)、06-开发环境与进度规划.md (P2)、00-项目总览与核心指标.md (辅助)

---

## 一、审查概述

### 1.1 审查对象

| 模块 | 文件路径 | 代码行数 |
|------|---------|---------|
| CUSUM序贯检验 | app/observability/cusum.py | 325 |
| 缓存防护监控 | app/observability/cache_monitor.py | 328 |
| 指标采集器 | app/observability/metrics_collector.py | 378 |
| VictoriaMetrics内存监控 | app/observability/metrics.py | 236 |
| 结构化日志 | app/observability/logging.py | 843 |
| 链路追踪 | app/observability/tracing.py | 258 |
| 审计链 | app/observability/audit_chain.py | 433 |
| 枚举定义 | app/observability/enums.py | 26 |
| 模块入口 | app/observability/__init__.py | 135 |
| Docker配置 | docker-compose.dev.yml | 173 |
| 全局配置 | app/config/settings.py (obs相关) | ~20 |

### 1.2 测试基线

| 指标 | 结果 |
|------|------|
| 单元测试总数 | 171 |
| 通过 | 170 |
| 失败 | 1 (test_logging.py::TestPostgreSQLLogStorage::test_init_default_pool_size — 默认连接池大小断言不匹配) |
| 通过率 | 99.4% |

---

## 二、审查发现汇总

### 按严重级别统计

| 级别 | 数量 | 说明 |
|------|------|------|
| **P0-阻断** | 5 | 核心安全功能缺失，直接影响系统安全或合规 |
| **P1-严重** | 10 | 重要功能缺失或实现偏差，影响可观测性完整性 |
| **P2-中等** | 8 | 代码质量问题，不影响核心功能但影响可维护性 |
| **P3-建议** | 5 | 改进建议，不影响当前功能正确性 |

---

## 三、P0-阻断级发现

### P0-01: CUSUM算法公式错误

**文件**: [cusum.py:60-61](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/observability/cusum.py#L60-L61)

**设计要求**: 标准CUSUM序贯检验，参考值 k = (p₀ + p₁)/2 = p₀ + δ/2

**实际实现**:
```python
s_plus = max(0.0, s_plus + (x - p0 - delta))    # 应为 (x - p0 - delta/2)
s_minus = max(0.0, s_minus + (p0 - x - delta))   # 应为 (p0 - delta/2 - x)
```

**影响**:
- s_plus 使用 `p0 + delta` 而非 `p0 + delta/2`，等价于检测2δ的漂移而非δ，**降低上升漂移检测灵敏度**
- s_minus 在默认参数(p0=0.005, delta=0.005)下，X=0时增量=p0-delta-0=0，**s_minus几乎无法累积**，下降漂移检测完全失效
- 这直接影响compact模式严重幻觉率CUSUM监控的可靠性，是安全关键缺陷

**修复**: 将delta替换为delta/2

---

### P0-02: CUSUM校准器与监控器数据粒度不一致

**文件**: [cusum.py:213-247](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/observability/cusum.py#L213-L247) vs [cusum.py:51-93](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/observability/cusum.py#L51-L93)

**设计要求**: §161-172 CUSUM参数校准SOP，校准结果须记录至Gate 0A验证报告

**实际实现**:
- `CUSUMMonitor.update()` 接收**单个布尔观测** (X∈{0,1})
- `CUSUMCalibrator._simulate_arl_numpy()` 使用**日聚合率** (x=cases/50, x∈{0,0.02,...,1.0})

**影响**: 校准器估计的ARL值与监控器实际表现会有显著差异，**用校准器结果设置监控器阈值h不可靠**。Gate 0A验证报告中的校准结果可能误导决策。

---

### P0-03: 幻觉检测器与CUSUM监控完全隔离，无集成代码

**文件**: app/inference/hallucination_detector.py vs app/observability/cusum.py

**设计要求**: §2 compact模式采用周统计+CUSUM双机制监控严重幻觉率

**实际实现**: 
- `HallucinationDetector` 和 `CUSUMMonitor` 是两个完全独立的模块
- **没有任何代码**将幻觉检测结果馈入CUSUM的`update()`方法
- `create_cusum_monitor()` 仅被测试代码调用，无业务代码调用
- CUSUM监控器目前只是一个独立的数据结构，缺少数据输入管道

**影响**: CUSUM监控形同虚设——虽然算法已实现，但没有实际数据流入，无法执行任何监控功能。

---

### P0-04: 安全熔断自动执行机制未实现

**设计要求**: §2 "周统计>0.5%或CUSUM超阈值时自动暂停AI辅助功能" + §172 "连续3周出现≥1例或单周≥3例严重幻觉时自动暂停AI辅助"

**实际实现**:
- `CUSUMMonitor` 阈值超限时仅做日志记录和回调通知
- `register_alert_callback()` 机制已预留，但**无任何业务代码注册了暂停AI的回调**
- 搜索 `suspend_ai`、`disable_ai`、`pause_ai` 等关键词，**零匹配**
- `check_compact_rules()` 返回告警字符串列表，但无后续动作

**影响**: compact模式核心安全机制缺失——即使CUSUM检测到幻觉率异常，系统也不会自动暂停AI辅助，违反设计文档的安全要求。

---

### P0-05: 告警升级路径完全未实现

**设计要求**: §195-203 compact模式告警响应流程：
- Warning → 4小时内确认 → 24小时未确认升级至上级医院运维
- Critical → 15分钟内确认 → 15分钟未确认通知上级医院运维 → 1小时未确认通知医院行政值班+设备供应商

**实际实现**: 
- 代码中仅有 `AlertLevel` 枚举(NORMAL/WARNING/CRITICAL)，无时间驱动的升级逻辑
- 搜索 `escalation`、`4hr`、`15min`、`升级`，**零匹配**
- `ALERT_CONTACTS` 环境变量配置：**未实现**
- `PUT /api/v1/admin/alert-contacts` API：**未实现**
- AlertManager Webhook集成：**未实现**

**影响**: 告警发出后无确认跟踪、无自动升级、无通知推送，Critical告警可能在无人响应的情况下被忽略，直接违反医疗系统安全要求。

---

## 四、P1-严重级发现

### P1-01: 缓存防护告警规则覆盖率仅31%

**文件**: [cache_monitor.py:214-281](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/observability/cache_monitor.py#L214-L281)

**设计要求**: §86-102 定义13条缓存防护告警规则

**实际实现**: `check_alerts()` 仅实现4条告警规则：

| 已实现(4/13) | 缺失(9/13) |
|-------------|-----------|
| 缓存命中率 <40% Critical / <60% Warning | 布隆过滤器拦截率 <80% Critical / <90% Warning |
| 穿透至数据库率 >10% Critical / >5% Warning | 空值缓存命中率 <60% Warning |
| 同时失效5min >100 Critical / >50 Warning | 互斥锁成功率 <90% Critical / <95% Warning |
| 互斥锁超时 >50 Critical / >20 Warning | 热点key覆盖率 <100% Warning |
| | TTL随机化偏移 0% Critical |
| | 预热完成率 <80% Critical / <100% Warning |
| | 预热耗时 >120s Critical / >60s Warning |
| | 写入延迟P99 >50ms Warning |
| | 读取延迟P99 >20ms Warning |

---

### P1-02: 缓存防护指标未接入Prometheus

**文件**: [cache_monitor.py:309-314](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/observability/cache_monitor.py#L309-L314)

**设计要求**: 所有监控指标须通过VictoriaMetrics/Prometheus采集

**实际实现**: `_update_prometheus()` 仅同步 `cache_hit_rate` 1个指标，其余12个缓存指标：
- 未在 `_update_prometheus()` 中同步
- 未在 `MetricsCollector` 中定义对应的Prometheus指标和setter方法

**影响**: CacheMonitor内部维护了完整的穿透/击穿/雪崩监控数据，但数据停留在Python内存中，**无法被VictoriaMetrics采集和告警**。这是最大的架构缺口——监控数据存在但不可观测。

---

### P1-03: 互斥锁超时统计为累计值而非每小时速率

**文件**: [cache_monitor.py:122](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/observability/cache_monitor.py#L122)

**设计要求**: "互斥锁等待超时次数 ≤5次/小时，>20次/小时 Warning，>50次/小时 Critical"

**实际实现**: `mutex_lock_timeout_count = lock_timeouts` 是进程启动以来的累计值，未按时间窗口滑动计算。

**影响**: 进程长时间运行后必然误报（累计值持续增长），进程刚启动时可能漏报。

---

### P1-04: VictoriaMetrics内存监控"持续5分钟"条件未实现

**文件**: [metrics.py:137-138](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/observability/metrics.py#L137-L138)

**设计要求**: §2 "持续5分钟超过150MB触发Warning告警"

**实际实现**: RSS超过150MB立即触发Warning，无持续时间判断逻辑。缺少首次超限时间戳字段和持续时间计算。

---

### P1-05: VictoriaMetrics内存监控降频后无法恢复（死代码bug）

**文件**: [metrics.py:177-182](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/observability/metrics.py#L177-L182)

**实际实现**: `_handle_high_memory()` 中NORMAL分支（恢复采集频率）是**死代码**——调用方在[第137行](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/observability/metrics.py#L137)已用`if alert_level in (WARNING, CRITICAL)`守卫，NORMAL分支永远不可达。

**影响**: 采集频率一旦从15s降至60s，**永远不会恢复**，即使内存已回到正常水平。

---

### P1-06: 高风险病种CUSUM监控未实现

**设计要求**: §2.2 "高风险病种幻觉率≤1%，compact双周统计+CUSUM"

**实际实现**: 
- CUSUMMonitor是全局单一实例，无`organ`或`disease_type`参数
- 无按病种/器官维度的独立CUSUM实例
- 无双周(bi-weekly)统计实现，仅有周统计

---

### P1-07: 轻量Web仪表盘未实现

**设计要求**: §186 "集成于主服务进程，基于Prometheus HTTP API展示关键指标"

**实际实现**: 完全缺失，无任何仪表盘代码。

---

### P1-08: VictoriaMetrics数据备份未实现

**设计要求**: §186 "vmbackup每小时自动备份至本地文件，保留7天"

**实际实现**: 完全缺失。docker-compose.dev.yml中无vmbackup服务定义。

---

### P1-09: FULL profile日志存储后端与设计不一致

**文件**: [logging.py:672-677](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/observability/logging.py#L672-L677)

**设计要求**: §1 full profile使用Elasticsearch存储

**实际实现**: `LogStorage.__init__` 中 full profile 使用 `FileLogStorage`，非 `ElasticsearchLogStorage`。ElasticsearchLogStorage虽已实现但未被选择。

---

### P1-10: 多项关键监控指标Prometheus注册缺失

**设计要求**: §2 完整指标表

**缺失的Prometheus指标**:

| 缺失指标 | 设计要求 |
|---------|---------|
| Redis进程存活状态 | 进程不可达Warning，重启>30s Critical |
| Redis容器重启事件 | >0次/天 Warning |
| 审计日志降级写入频率 | >0次/天 Warning |
| PostgreSQL连接池使用率 | >80% Warning, >95% Critical |
| PostgreSQL慢查询 | 5分钟>3次 Warning |
| GPU持续超温计时 | >90°C持续60s Critical |
| GPU热重载会话关联 | 有活跃会话延迟执行 |
| 边缘端导航延迟(独立) | >60ms Warning, >80ms Critical |
| 边缘端GPU温度(独立) | >80°C Warning, >90°C Critical |

---

## 五、P2-中等级发现

### P2-01: CUSUMMonitor线程安全

**文件**: [cusum.py:41-93](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/observability/cusum.py#L41-L93)

`CUSUMState` 无锁保护，`_state`的`total_observations`/`total_severe`/`s_plus`/`s_minus`在多线程并发`update()`时存在竞态条件。Python GIL对简单属性赋值提供原子性保护，但`update()`中连续修改多个属性不是原子操作。

---

### P2-02: CacheMonitor线程安全

**文件**: [cache_monitor.py:66-328](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/observability/cache_monitor.py#L66-L328)

`CacheMonitor` 无锁保护，`_penetration`/`_breakdown`/`_avalanche`/`_overall`的并发读写存在竞态。`_write_latencies`/`_read_latencies` deque的并发append和遍历不安全。

---

### P2-03: P99计算精度偏差

**文件**: [cache_monitor.py:317-324](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/observability/cache_monitor.py#L317-L324)

`_calculate_p99` 使用 `int(0.99 * len(sorted_data))` 索引，与标准nearest-rank P99定义有偏差。标准方法应使用 `ceil(0.99/100 * N)` 或线性插值。

---

### P2-04: VictoriaMetricsPusher无重试/退避机制

**文件**: [metrics_collector.py:343-366](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/observability/metrics_collector.py#L343-L366)

推送失败后仅等待固定间隔重试，无指数退避、无断路器、无最大重试次数。VictoriaMetrics长时间不可达时持续无效推送。

---

### P2-05: 单例模式实现不一致

- `MetricsCollector`: 双重检查锁 + threading.Lock
- `CacheMonitor`: 简单if判断（非线程安全）
- `LogStorage`: 双重检查锁 + threading.Lock
- `VictoriaMetricsMemoryMonitor`: asyncio.Lock（异步单例）

应统一为线程安全的双重检查锁模式。

---

### P2-06: 日志器命名不一致

- `logging.py` 使用 `_internal_logger`
- 其他模块(cusum/cache_monitor/metrics_collector/tracing/audit_chain) 使用 `_logger`

---

### P2-07: 配置引用方式不一致

- `cusum.py` 直接引用 `settings.obs_cusum_target_rate`
- `cache_monitor.py` 通过 `metrics_collector` 间接引用
- `metrics_collector.py` 通过构造函数参数传入

---

### P2-08: VictoriaMetricsPusher优雅关闭不完整

**文件**: [metrics_collector.py:333-341](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/observability/metrics_collector.py#L333-L341)

`stop()` 取消task但未等待当前推送完成，可能导致最后一次指标推送被中断。

---

## 六、P3-建议级发现

### P3-01: 模块文档字符串不统一

- `enums.py`、`metrics.py`、`logging.py` 有英文docstring
- `cusum.py`、`cache_monitor.py`、`metrics_collector.py`、`tracing.py`、`audit_chain.py` 无模块级docstring

### P3-02: 类型注解不够精确

`get_all_metrics()` 返回 `Dict[str, Any]` 过于宽泛，应定义TypedDict或dataclass。

### P3-03: 错误处理策略不一致

- `cache_monitor._update_prometheus()`: 异常静默忽略(pass)
- `tracing.init_tracing()`: ImportError时仅warning
- `cusum._notify_alerts()`: 异常仅日志记录

### P3-04: asyncio事件循环API弃用

`logging.py` 中 `write()` 方法使用 `asyncio.get_event_loop()`，在Python 3.12+已弃用，应使用 `asyncio.get_running_loop()`。

### P3-05: CUSUMCalibrator随机种子固定

蒙特卡洛模拟固定种子42确保可复现性，但可能影响校准结果的统计意义。建议增加种子配置参数。

---

## 七、设计规范一致性矩阵

### 幻觉率监控

| 审查项 | 设计要求 | 状态 | 偏差描述 |
|--------|---------|------|---------|
| 严重幻觉率≤0.5% Gauge注册 | Prometheus指标 | ✅一致 | ultrasound_severe_hallucination_rate已注册 |
| 严重幻觉率CUSUM双机制 | compact周统计+CUSUM | ⚠️部分一致 | CUSUM算法已实现但公式错误(P0-01)，且无数据输入管道(P0-03) |
| 高风险病种幻觉率≤1% | 按病种统计+独立CUSUM | ❌缺失 | 全局单一CUSUM实例，无病种维度(P1-06) |
| 整体幻觉率≤5% | 月统计 | ⚠️部分一致 | Gauge已注册，无月度统计窗口实现 |
| CUSUM参数校准SOP | 蒙特卡洛ARL₀≥100,ARL₁≤50 | ⚠️部分一致 | 校准器已实现但与监控器粒度不一致(P0-02) |
| compact熔断规则 | 连续3周≥1例/单周≥3例 | ⚠️部分一致 | 规则逻辑已实现但无自动暂停(P0-04) |
| 安全熔断自动执行 | 自动暂停AI辅助 | ❌缺失 | 仅日志+回调，无业务回调注册(P0-04) |

### 缓存防护监控

| 审查项 | 设计要求 | 状态 | 偏差描述 |
|--------|---------|------|---------|
| 布隆过滤器拦截率告警 | <90%W/<80%C | ❌缺失 | check_alerts()未包含 |
| 空值缓存命中率告警 | <60%W | ❌缺失 | check_alerts()未包含 |
| 穿透至数据库率告警 | >5%W/>10%C | ✅一致 | |
| 互斥锁成功率告警 | <95%W/<90%C | ❌缺失 | check_alerts()未包含 |
| 互斥锁超时告警 | >20/hrW/>50/hrC | ⚠️部分一致 | 已实现但为累计值非每小时速率(P1-03) |
| 热点key覆盖率告警 | <100%W | ❌缺失 | check_alerts()未包含 |
| TTL随机化偏移告警 | 0%C | ❌缺失 | check_alerts()未包含 |
| 同时失效5min告警 | >50W/>100C | ✅一致 | |
| 预热完成率告警 | <80%C/<100%W | ❌缺失 | check_alerts()未包含 |
| 预热耗时告警 | >60sW/>120sC | ❌缺失 | check_alerts()未包含 |
| 缓存命中率告警 | <60%W/<40%C | ✅一致 | |
| 写入延迟P99告警 | >50msW | ❌缺失 | check_alerts()未包含 |
| 读取延迟P99告警 | >20msW | ❌缺失 | check_alerts()未包含 |
| 缓存指标Prometheus同步 | 全部同步到VM | ❌缺失 | 仅同步cache_hit_rate(P1-02) |
| 缓存防护日志收集 | 6类日志事件 | ⚠️部分一致 | 4/6日志方法已实现，字段基本完整 |
| 缓存防护告警升级 | 4种场景升级策略 | ❌缺失 | 无升级流程实现 |

### 告警升级路径

| 审查项 | 设计要求 | 状态 |
|--------|---------|------|
| Warning 4h确认/24h升级 | 时间驱动升级 | ❌缺失 |
| Critical 15min确认/1h升级 | 时间驱动升级 | ❌缺失 |
| 安全熔断无需人工确认 | 自动执行 | ❌缺失 |
| ALERT_CONTACTS环境变量 | JSON格式配置 | ❌缺失 |
| 告警联系人热更新API | PUT /api/v1/admin/alert-contacts | ❌缺失 |
| AlertManager Webhook | 推送至飞书/钉钉/企业微信 | ❌缺失 |

### compact模式监控方案

| 审查项 | 设计要求 | 状态 |
|--------|---------|------|
| 轻量Web仪表盘 | 集成于主服务进程 | ❌缺失 |
| VictoriaMetrics单机版 | Docker容器v1.93.0 | ✅一致 |
| VM内存超限检测 | >150MB W/>180MB C+降频60s | ⚠️部分一致(缺持续5min条件) |
| VM数据备份 | vmbackup每小时备份 | ❌缺失 |
| VM OOM保护 | oom_score_adj=-500 | ✅一致 |

---

## 八、代码质量评估

### 按模块评估

| 模块 | 健壮性 | 可维护性 | 可扩展性 | 性能 | 综合评级 |
|------|--------|---------|---------|------|---------|
| cusum.py | ⚠️ 低(算法错误+无锁) | ⚠️ 中(数据类清晰但粒度不一致) | ⚠️ 中(回调机制可扩展但无多实例) | ✅ 高(O(1)更新) | C |
| cache_monitor.py | ⚠️ 低(无锁+告警不全) | ⚠️ 中(数据类清晰但Prometheus未接入) | ⚠️ 中(单例限制扩展) | ✅ 高(O(1)更新) | C |
| metrics_collector.py | ✅ 中(双重检查锁) | ✅ 高(指标定义清晰) | ✅ 高(易添加新指标) | ✅ 高(异步推送) | B |
| metrics.py | ⚠️ 低(死代码bug+无持续条件) | ⚠️ 中(配置清晰但逻辑有缺陷) | ✅ 高(回调机制) | ✅ 高(5s检查间隔) | C |
| logging.py | ✅ 高(多后端+降级) | ✅ 高(抽象层清晰) | ✅ 高(易添加新后端) | ✅ 高(批量写入P99<1ms) | A |
| tracing.py | ✅ 中(可选依赖优雅降级) | ✅ 高(标准OTel集成) | ✅ 高(标准接口) | ✅ 高(采样率控制) | B+ |
| audit_chain.py | ✅ 高(哈希链完整性) | ⚠️ 中(直接DB耦合) | ⚠️ 中(全局锁限制) | ⚠️ 中(全表扫描验证) | B- |

---

## 九、修复优先级路线图

### 第一优先级：P0-阻断（须在Gate 0A前修复）

| 编号 | 修复项 | 预估工作量 | 依赖 |
|------|--------|-----------|------|
| P0-01 | 修正CUSUM算法公式(delta→delta/2) | 0.5天 | 无 |
| P0-02 | 统一校准器与监控器数据粒度 | 1天 | P0-01 |
| P0-03 | 实现幻觉检测器→CUSUM数据管道 | 2天 | P0-01 |
| P0-04 | 实现安全熔断自动执行机制 | 3天 | P0-03 |
| P0-05 | 实现告警升级路径(含ALERT_CONTACTS+Webhook) | 5天 | 无 |

### 第二优先级：P1-严重（须在Phase 0A内修复）

| 编号 | 修复项 | 预估工作量 |
|------|--------|-----------|
| P1-01 | 补全缓存防护9条缺失告警规则 | 2天 |
| P1-02 | 缓存指标接入Prometheus | 2天 |
| P1-03 | 互斥锁超时改为滑动窗口统计 | 1天 |
| P1-04+05 | 修复VM内存监控持续5min条件+降频恢复bug | 1天 |
| P1-06 | 实现按病种CUSUM+双周统计 | 3天 |
| P1-07 | 实现轻量Web仪表盘 | 3天 |
| P1-08 | 配置vmbackup自动备份 | 0.5天 |
| P1-09 | 修正FULL profile日志存储后端选择 | 0.5天 |
| P1-10 | 补全缺失的Prometheus指标注册 | 3天 |

### 第三优先级：P2-中等（可在后续迭代修复）

| 编号 | 修复项 |
|------|--------|
| P2-01 | CUSUMMonitor添加线程锁 |
| P2-02 | CacheMonitor添加线程锁 |
| P2-03 | 修正P99计算方法 |
| P2-04 | VictoriaMetricsPusher添加重试/退避 |
| P2-05 | 统一单例模式实现 |
| P2-06 | 统一日志器命名 |
| P2-07 | 统一配置引用方式 |
| P2-08 | 完善Pusher优雅关闭 |

---

## 十、审查结论

### 10.1 总体评估

OBS可观测性轨道Phase 0A的实施在**基础框架层面**已建立了较为完整的模块结构（8个核心模块、171个单元测试、99.4%通过率），日志框架和链路追踪质量较高。但在**设计规范一致性**和**功能完整性**方面存在严重差距：

1. **CUSUM监控链断裂**: 算法公式错误→校准器不一致→无数据输入管道→无自动熔断，整条监控链从底层数学到业务逻辑均存在问题
2. **告警基础设施空白**: 告警升级、联系人管理、通知推送三个核心环节完全缺失
3. **缓存防护可观测性缺口**: 内部数据完整但69%告警规则未实现、92%指标未接入Prometheus

### 10.2 关键风险

- **安全风险**: 安全熔断机制未实现，幻觉率异常时系统不会自动暂停AI辅助
- **合规风险**: 告警升级路径缺失，Critical告警可能无人响应，违反医疗系统安全要求
- **运维风险**: 缓存防护指标不可观测（未接入Prometheus），运维人员无法通过VictoriaMetrics发现缓存问题

### 10.3 Gate 0A建议

**不建议在当前状态下通过Gate 0A OBS轨道验证**。须至少完成P0级5项修复后重新验证。P1级修复可在Gate 0A通过后的迭代中完成，但P1-01(缓存告警补全)和P1-02(Prometheus接入)应优先处理，因为它们直接影响可观测性的基本可用性。
