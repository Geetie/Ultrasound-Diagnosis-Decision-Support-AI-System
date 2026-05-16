# OBS Phase 0A 审查问题修复计划

> **依据**: obs-phase0a-audit-report.md  
> **原则**: 每个修复步骤独立可验证，修复后运行对应测试确认通过  
> **执行顺序**: P0 → P1 → P2，同级别内按依赖关系排序

---

## P0-01: 修正CUSUM算法公式 (delta → delta/2)

### 问题
[cusum.py:60-61](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/observability/cusum.py#L60-L61) 使用 `p0 + delta` 而非标准公式 `p0 + delta/2`，导致上升漂移检测灵敏度降低，下降漂移检测在默认参数下完全失效。

### 修复步骤

**步骤1**: 修改 `CUSUMMonitor.update()` 中的CUSUM更新公式

文件: `app/observability/cusum.py`

```python
# 第60-61行，将:
self._state.s_plus = max(0.0, self._state.s_plus + (x - p0 - delta))
self._state.s_minus = max(0.0, self._state.s_minus + (p0 - x - delta))

# 改为:
k = p0 + delta / 2
self._state.s_plus = max(0.0, self._state.s_plus + (x - k))
self._state.s_minus = max(0.0, self._state.s_minus + (k - x))
```

**步骤2**: 同步修改 `CUSUMCalibrator._simulate_arl_numpy()` 和 `_simulate_arl_pure()` 中的公式

文件: `app/observability/cusum.py`

```python
# _simulate_arl_numpy 第229-230行，将:
s_plus = max(0.0, s_plus + (x - self._target_rate - delta))
s_minus = max(0.0, s_minus + (self._target_rate - x - delta))

# 改为:
k = self._target_rate + delta / 2
s_plus = max(0.0, s_plus + (x - k))
s_minus = max(0.0, s_minus + (k - x))

# _simulate_arl_pure 第265-266行做同样修改
```

**步骤3**: 更新测试 `tests/unit/obs/test_cusum.py`

- `test_s_minus_increases_without_hallucination`: 原测试用 `target_rate=0.5, delta=0.1` 使 s_minus>0，修正后公式下 s_minus 行为不同，需调整断言
- 新增测试 `test_s_plus_standard_cusum_formula`: 验证 s_plus 增量 = x - (p0 + delta/2)
- 新增测试 `test_s_minus_standard_cusum_formula`: 验证 s_minus 增量 = (p0 + delta/2) - x
- 新增测试 `test_default_params_s_minus_accumulates`: 验证默认参数(p0=0.005, delta=0.005)下 s_minus 在连续非幻觉观测时能累积

**验证**: `python -m pytest tests/unit/obs/test_cusum.py -v`

---

## P0-02: 统一校准器与监控器数据粒度

### 问题
`CUSUMCalibrator._simulate_arl_numpy()` 使用日聚合率(Binomial/50)，而 `CUSUMMonitor.update()` 使用单个布尔观测，两者粒度不一致导致校准结果无法指导监控器阈值设置。

### 修复步骤

**步骤1**: 重写 `_simulate_arl_numpy()` 使用逐个Bernoulli观测

文件: `app/observability/cusum.py`

```python
def _simulate_arl_numpy(self, rate: float, h: float, delta: float) -> float:
    import numpy as np

    rng = np.random.default_rng(42)
    k = self._target_rate + delta / 2
    max_observations = self._simulation_days * self._daily_cases
    run_lengths = np.zeros(self._num_simulations)

    for i in range(self._num_simulations):
        s_plus = 0.0
        triggered = False
        observations = rng.random(max_observations) < rate
        for j, x_bool in enumerate(observations):
            x = 1.0 if x_bool else 0.0
            s_plus = max(0.0, s_plus + (x - k))
            if s_plus > h:
                run_lengths[i] = j + 1
                triggered = True
                break
        if not triggered:
            run_lengths[i] = max_observations * 2

    triggered_runs = run_lengths[run_lengths <= max_observations]
    if len(triggered_runs) == 0:
        return float(max_observations * 2)
    return float(np.mean(triggered_runs))
```

**步骤2**: 同步重写 `_simulate_arl_pure()`

**步骤3**: 更新 `CUSUMCalibrationResult` 和 `generate_calibration_report()` — ARL单位从"天"改为"观测次数"

**步骤4**: 更新测试

- 修改 `test_calibrate_returns_results` 和 `test_calibration_result_validity` 中的ARL值断言（单位变化后数值范围不同）
- 新增测试 `test_calibrator_uses_same_granularity_as_monitor`: 验证校准器与监控器使用相同的逐观测更新逻辑

**验证**: `python -m pytest tests/unit/obs/test_cusum.py -v`

---

## P0-03: 实现幻觉检测器→CUSUM数据管道

### 问题
`HallucinationDetector` 和 `CUSUMMonitor` 完全独立，无集成代码将幻觉检测结果馈入CUSUM。

### 修复步骤

**步骤1**: 在 `app/observability/cusum.py` 中添加 `HallucinationCUSUMBridge` 类

```python
class HallucinationCUSUMBridge:
    def __init__(self, monitor: CUSUMMonitor):
        self._monitor = monitor
        self._disease_monitors: Dict[str, CUSUMMonitor] = {}

    def report_hallucination_result(
        self,
        is_severe: bool,
        disease_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        result = self._monitor.update(is_severe)
        if disease_type and disease_type in self._disease_monitors:
            disease_result = self._disease_monitors[disease_type].update(is_severe)
            result[f"disease_{disease_type}"] = disease_result
        return result

    def register_disease_monitor(self, disease_type: str, config: Optional[CUSUMConfig] = None) -> None:
        if disease_type not in self._disease_monitors:
            self._disease_monitors[disease_type] = CUSUMMonitor(config)

    def get_disease_monitor(self, disease_type: str) -> Optional[CUSUMMonitor]:
        return self._disease_monitors.get(disease_type)
```

**步骤2**: 在 `app/observability/__init__.py` 中导出 `HallucinationCUSUMBridge`

**步骤3**: 在 `app/inference/hallucination_detector.py` 中集成桥接

在 `HallucinationDetector.detect()` 方法末尾，当检测到严重幻觉时调用桥接：

```python
# 在 HallucinationDetector.__init__ 中添加:
self._cusum_bridge: Optional[HallucinationCUSUMBridge] = None

# 添加方法:
def set_cusum_bridge(self, bridge: HallucinationCUSUMBridge) -> None:
    self._cusum_bridge = bridge

# 在 detect() 方法返回前添加:
if self._cusum_bridge is not None:
    is_severe = result.is_hallucination and result.severity in ("critical", "high")
    self._cusum_bridge.report_hallucination_result(
        is_severe=is_severe,
        disease_type=getattr(result, "disease_type", None),
    )
```

**步骤4**: 新增测试文件 `tests/unit/obs/test_cusum_bridge.py`

- `test_bridge_reports_to_global_monitor`
- `test_bridge_reports_to_disease_monitor`
- `test_bridge_without_disease_monitor`
- `test_hallucination_detector_integration_with_bridge`

**验证**: `python -m pytest tests/unit/obs/test_cusum_bridge.py tests/unit/llm/test_hallucination_detector.py -v`

---

## P0-04: 实现安全熔断自动执行机制

### 问题
CUSUM阈值超限仅做日志记录和回调通知，无自动暂停AI辅助的业务逻辑。

### 修复步骤

**步骤1**: 在 `app/observability/cusum.py` 中添加 `AISafetyCircuitBreaker` 类

```python
class AISafetyCircuitBreaker:
    SUSPENDED = "suspended"
    ACTIVE = "active"

    def __init__(self, cusum_monitor: CUSUMMonitor):
        self._monitor = cusum_monitor
        self._state = self.ACTIVE
        self._suspended_at: Optional[float] = None
        self._suspension_reason: Optional[str] = None
        cusum_monitor.register_alert_callback(self._on_cusum_alert)

    def _on_cusum_alert(self, direction: str, value: float) -> None:
        if direction == "s_plus" and value > self._monitor._config.decision_threshold:
            self._suspend(f"cusum_s_plus_exceeded:{value:.2f}")

    def check_weekly_suspension(self) -> Optional[str]:
        alerts = self._monitor.check_compact_rules()
        if alerts:
            self._suspend(f"compact_rules:{','.join(alerts)}")
            return self._suspension_reason
        return None

    def _suspend(self, reason: str) -> None:
        self._state = self.SUSPENDED
        self._suspended_at = time.time()
        self._suspension_reason = reason
        _logger.critical("ai_assist_suspended", reason=reason)

    def resume(self) -> None:
        self._state = self.ACTIVE
        self._suspended_at = None
        self._suspension_reason = None
        self._monitor.reset()
        _logger.info("ai_assist_resumed")

    @property
    def is_suspended(self) -> bool:
        return self._state == self.SUSPENDED

    @property
    def suspension_info(self) -> Dict[str, Any]:
        return {
            "state": self._state,
            "suspended_at": self._suspended_at,
            "reason": self._suspension_reason,
        }
```

**步骤2**: 在 `HallucinationCUSUMBridge` 中集成 `AISafetyCircuitBreaker`

```python
# 在 HallucinationCUSUMBridge.__init__ 中添加:
self._circuit_breaker = AISafetyCircuitBreaker(monitor)

# 添加属性:
@property
def circuit_breaker(self) -> AISafetyCircuitBreaker:
    return self._circuit_breaker
```

**步骤3**: 在 `app/observability/__init__.py` 中导出 `AISafetyCircuitBreaker`

**步骤4**: 新增测试 `tests/unit/obs/test_cusum_bridge.py`

- `test_circuit_breaker_suspends_on_cusum_threshold`
- `test_circuit_breaker_suspends_on_compact_rules`
- `test_circuit_breaker_resume_resets_monitor`
- `test_circuit_breaker_suspension_info`

**验证**: `python -m pytest tests/unit/obs/test_cusum_bridge.py -v`

---

## P0-05: 实现告警升级路径

### 问题
告警升级路径完全未实现，包括ALERT_CONTACTS配置、告警确认跟踪、时间驱动升级、AlertManager Webhook。

### 修复步骤

**步骤1**: 在 `app/config/settings.py` 中添加告警联系人配置

```python
alert_contacts_json: str = ''
alert_escalation_warning_confirm_seconds: int = 14400  # 4小时
alert_escalation_critical_confirm_seconds: int = 900    # 15分钟
alert_escalation_warning_escalate_seconds: int = 86400  # 24小时
alert_escalation_critical_escalate_seconds: int = 3600  # 1小时
alert_webhook_url: str = ''
```

**步骤2**: 创建 `app/observability/alert_escalation.py`

核心类：
- `AlertContact`: dataclass，含name/role/phone/hospital_admin/supplier字段
- `AlertEvent`: dataclass，含alert_id/level/metric/value/created_at/confirmed_at/escalated_at
- `AlertEscalationManager`: 管理告警生命周期和升级逻辑
  - `load_contacts()`: 从ALERT_CONTACTS环境变量加载联系人
  - `emit_alert(level, metric, value)`: 发出告警并启动升级计时
  - `confirm_alert(alert_id)`: 确认告警，停止升级
  - `check_escalations()`: 周期性检查未确认告警是否需要升级
  - `_escalate_warning(alert)`: Warning 24h未确认→通知上级医院运维
  - `_escalate_critical(alert)`: Critical 15min未确认→通知上级医院运维，1h未确认→通知行政值班+供应商
  - `_send_webhook(level, message)`: 发送AlertManager Webhook
- `create_alert_escalation_manager()`: 工厂函数

**步骤3**: 在 `app/observability/__init__.py` 中导出新模块

**步骤4**: 新增测试 `tests/unit/obs/test_alert_escalation.py`

- `test_load_contacts_from_env`
- `test_emit_alert_starts_escalation_timer`
- `test_confirm_alert_stops_escalation`
- `test_warning_escalation_after_24h`
- `test_critical_escalation_after_15min`
- `test_critical_second_escalation_after_1h`
- `test_webhook_sent_on_critical`
- `test_safety_circuit_breaker_alert_integrates_with_escalation`

**验证**: `python -m pytest tests/unit/obs/test_alert_escalation.py -v`

---

## P1-01: 补全缓存防护9条缺失告警规则

### 问题
`check_alerts()` 仅实现4/13条告警规则。

### 修复步骤

**步骤1**: 在 `cache_monitor.py` 的 `check_alerts()` 方法中添加9条缺失规则

文件: `app/observability/cache_monitor.py`

在现有4条规则后追加：

```python
# 布隆过滤器拦截率
if self._penetration.bloom_filter_interception_rate < 0.80:
    alerts.append({"level": "critical", "metric": "bloom_filter_interception_rate", ...})
elif self._penetration.bloom_filter_interception_rate < 0.90:
    alerts.append({"level": "warning", "metric": "bloom_filter_interception_rate", ...})

# 空值缓存命中率
if self._penetration.null_value_hit_rate < 0.60:
    alerts.append({"level": "warning", "metric": "null_value_hit_rate", ...})

# 互斥锁成功率
if self._breakdown.mutex_lock_success_rate < 0.90:
    alerts.append({"level": "critical", "metric": "mutex_lock_success_rate", ...})
elif self._breakdown.mutex_lock_success_rate < 0.95:
    alerts.append({"level": "warning", "metric": "mutex_lock_success_rate", ...})

# 热点key覆盖率
if self._breakdown.hotkey_permanent_coverage < 1.0 and self._breakdown.hotkey_total > 0:
    alerts.append({"level": "warning", "metric": "hotkey_permanent_coverage", ...})

# TTL随机化偏移
if self._avalanche.ttl_randomization_offset_percent == 0 and self._avalanche.total_keys > 0:
    alerts.append({"level": "critical", "metric": "ttl_randomization_offset_percent", ...})

# 预热完成率
if self._avalanche.warmup_completion_rate < 0.80:
    alerts.append({"level": "critical", "metric": "warmup_completion_rate", ...})
elif self._avalanche.warmup_completion_rate < 1.0:
    alerts.append({"level": "warning", "metric": "warmup_completion_rate", ...})

# 预热耗时
if self._avalanche.warmup_duration_seconds > 120:
    alerts.append({"level": "critical", "metric": "warmup_duration_seconds", ...})
elif self._avalanche.warmup_duration_seconds > 60:
    alerts.append({"level": "warning", "metric": "warmup_duration_seconds", ...})

# 写入延迟P99
if self._overall.write_latency_p99_seconds > 0.050:
    alerts.append({"level": "warning", "metric": "write_latency_p99", ...})

# 读取延迟P99
if self._overall.read_latency_p99_seconds > 0.020:
    alerts.append({"level": "warning", "metric": "read_latency_p99", ...})
```

**步骤2**: 更新测试 `tests/unit/obs/test_cache_monitor.py`

新增测试类 `TestCacheAlertsExtended`:
- `test_bloom_filter_interception_rate_warning`
- `test_bloom_filter_interception_rate_critical`
- `test_null_value_hit_rate_warning`
- `test_mutex_lock_success_rate_warning`
- `test_mutex_lock_success_rate_critical`
- `test_hotkey_permanent_coverage_warning`
- `test_ttl_randomization_offset_critical`
- `test_warmup_completion_rate_warning`
- `test_warmup_completion_rate_critical`
- `test_warmup_duration_warning`
- `test_warmup_duration_critical`
- `test_write_latency_p99_warning`
- `test_read_latency_p99_warning`

**验证**: `python -m pytest tests/unit/obs/test_cache_monitor.py -v`

---

## P1-02: 缓存指标接入Prometheus

### 问题
`_update_prometheus()` 仅同步 `cache_hit_rate` 1个指标，其余12个缓存指标未注册Prometheus Gauge。

### 修复步骤

**步骤1**: 在 `MetricsCollector._init_metrics()` 中添加缓存相关Prometheus指标

文件: `app/observability/metrics_collector.py`

```python
# 缓存穿透指标
self.cache_bloom_filter_interception_rate = Gauge(
    "ultrasound_cache_bloom_filter_interception_rate", ..., registry=r)
self.cache_null_value_hit_rate = Gauge(
    "ultrasound_cache_null_value_hit_rate", ..., registry=r)
self.cache_db_penetration_rate = Gauge(
    "ultrasound_cache_db_penetration_rate", ..., registry=r)

# 缓存击穿指标
self.cache_mutex_lock_success_rate = Gauge(
    "ultrasound_cache_mutex_lock_success_rate", ..., registry=r)
self.cache_mutex_lock_timeout_count = Gauge(
    "ultrasound_cache_mutex_lock_timeout_count", ..., registry=r)
self.cache_hotkey_permanent_coverage = Gauge(
    "ultrasound_cache_hotkey_permanent_coverage", ..., registry=r)

# 缓存雪崩指标
self.cache_ttl_randomization_offset_percent = Gauge(
    "ultrasound_cache_ttl_randomization_offset_percent", ..., registry=r)
self.cache_simultaneous_expiry_5min = Gauge(
    "ultrasound_cache_simultaneous_expiry_5min", ..., registry=r)
self.cache_warmup_completion_rate = Gauge(
    "ultrasound_cache_warmup_completion_rate", ..., registry=r)
self.cache_warmup_duration_seconds = Gauge(
    "ultrasound_cache_warmup_duration_seconds", ..., registry=r)

# 缓存延迟指标
self.cache_write_latency_p99_seconds = Gauge(
    "ultrasound_cache_write_latency_p99_seconds", ..., registry=r)
self.cache_read_latency_p99_seconds = Gauge(
    "ultrasound_cache_read_latency_p99_seconds", ..., registry=r)
```

**步骤2**: 添加对应的setter方法

**步骤3**: 修改 `CacheMonitor._update_prometheus()` 同步所有指标

```python
def _update_prometheus(self) -> None:
    try:
        c = get_metrics_collector()
        c.set_cache_hit_rate(self._overall.overall_hit_rate)
        c.cache_bloom_filter_interception_rate.set(self._penetration.bloom_filter_interception_rate)
        c.cache_null_value_hit_rate.set(self._penetration.null_value_hit_rate)
        c.cache_db_penetration_rate.set(self._penetration.db_penetration_rate)
        c.cache_mutex_lock_success_rate.set(self._breakdown.mutex_lock_success_rate)
        c.cache_mutex_lock_timeout_count.set(self._breakdown.mutex_lock_timeout_count)
        c.cache_hotkey_permanent_coverage.set(self._breakdown.hotkey_permanent_coverage)
        c.cache_ttl_randomization_offset_percent.set(self._avalanche.ttl_randomization_offset_percent)
        c.cache_simultaneous_expiry_5min.set(self._avalanche.simultaneous_expiry_5min)
        c.cache_warmup_completion_rate.set(self._avalanche.warmup_completion_rate)
        c.cache_warmup_duration_seconds.set(self._avalanche.warmup_duration_seconds)
        c.cache_write_latency_p99_seconds.set(self._overall.write_latency_p99_seconds)
        c.cache_read_latency_p99_seconds.set(self._overall.read_latency_p99_seconds)
    except Exception:
        _logger.error("cache_prometheus_sync_failed", exc_info=True)
```

**步骤4**: 更新测试

**验证**: `python -m pytest tests/unit/obs/test_metrics_collector.py tests/unit/obs/test_cache_monitor.py -v`

---

## P1-03: 互斥锁超时改为滑动窗口统计

### 问题
`mutex_lock_timeout_count` 为累计值，设计要求"次/小时"。

### 修复步骤

**步骤1**: 在 `CacheMonitor` 中添加互斥锁超时时间戳队列

文件: `app/observability/cache_monitor.py`

```python
# __init__ 中添加:
self._lock_timeout_timestamps: deque = deque(maxlen=10000)

# record_mutex_lock_attempt 中修改:
if success:
    self._breakdown.lock_successes += 1
else:
    self._breakdown.lock_timeouts += 1
    self._lock_timeout_timestamps.append(time.time())

# 计算每小时超时数:
def _get_hourly_lock_timeouts(self) -> int:
    now = time.time()
    cutoff = now - 3600
    while self._lock_timeout_timestamps and self._lock_timeout_timestamps[0] < cutoff:
        self._lock_timeout_timestamps.popleft()
    return len(self._lock_timeout_timestamps)
```

**步骤2**: 修改 `check_alerts()` 中互斥锁超时告警使用 `_get_hourly_lock_timeouts()`

**步骤3**: 更新测试

**验证**: `python -m pytest tests/unit/obs/test_cache_monitor.py -v`

---

## P1-04+05: 修复VM内存监控持续5min条件 + 降频恢复bug

### 问题1
设计要求"持续5分钟超过150MB触发Warning"，当前立即触发。

### 问题2
`_handle_high_memory()` 中NORMAL分支是死代码，降频后无法恢复。

### 修复步骤

**步骤1**: 在 `MemoryMonitorMetrics` 中添加持续时间追踪字段

文件: `app/observability/metrics.py`

```python
@dataclass
class MemoryMonitorMetrics:
    current_rss_mb: float = 0.0
    peak_rss_mb: float = 0.0
    alert_level: AlertLevel = AlertLevel.NORMAL
    is_reduced_frequency: bool = False
    scrape_interval: float = 15.0
    total_checks: int = 0
    warning_count: int = 0
    critical_count: int = 0
    warning_since: Optional[float] = None
    critical_since: Optional[float] = None
```

**步骤2**: 修改 `_check_memory()` 实现持续5分钟条件

```python
async def _check_memory(self) -> None:
    # ...获取rss_mb...
    alert_level = self._calculate_alert_level(rss_mb)
    now = time.time()

    async with self._lock:
        self._metrics.current_rss_mb = rss_mb
        # ...peak/total_checks更新...

        # 持续时间追踪
        if alert_level == AlertLevel.WARNING:
            if self._metrics.warning_since is None:
                self._metrics.warning_since = now
            if now - self._metrics.warning_since >= 300:  # 5分钟
                effective_level = AlertLevel.WARNING
            else:
                effective_level = AlertLevel.NORMAL  # 未持续5分钟，暂不告警
        elif alert_level == AlertLevel.CRITICAL:
            if self._metrics.critical_since is None:
                self._metrics.critical_since = now
            effective_level = AlertLevel.CRITICAL  # Critical立即触发
        else:
            self._metrics.warning_since = None
            self._metrics.critical_since = None
            effective_level = AlertLevel.NORMAL

        # 处理告警和降频
        if effective_level in (AlertLevel.WARNING, AlertLevel.CRITICAL):
            self._handle_high_memory(effective_level, rss_mb)
        elif effective_level == AlertLevel.NORMAL and self._metrics.is_reduced_frequency:
            self._restore_normal_frequency(rss_mb)

    # ...触发回调...
```

**步骤3**: 将NORMAL分支从 `_handle_high_memory()` 移到独立方法 `_restore_normal_frequency()`

```python
def _restore_normal_frequency(self, rss_mb: float) -> None:
    _internal_logger.info("Memory normalized - restoring scrape interval",
                         current_mb=rss_mb,
                         restored_interval=self._config.default_scrape_interval)
    self._metrics.scrape_interval = self._config.default_scrape_interval
    self._metrics.is_reduced_frequency = False
```

**步骤4**: 从 `_handle_high_memory()` 中删除NORMAL分支死代码

**步骤5**: 更新测试

- 修改 `test_handle_memory_normal_restores_frequency`: 改为调用 `_restore_normal_frequency()`
- 新增 `test_warning_not_triggered_before_5min_duration`
- 新增 `test_warning_triggered_after_5min_duration`
- 新增 `test_critical_triggered_immediately`
- 新增 `test_frequency_restored_when_memory_normalizes`

**验证**: `python -m pytest tests/unit/obs/test_metrics.py -v`

---

## P1-06: 实现按病种CUSUM + 双周统计

### 问题
CUSUMMonitor是全局单一实例，无病种维度，无双周统计。

### 修复步骤

**步骤1**: 此功能已在P0-03的 `HallucinationCUSUMBridge` 中部分覆盖（`register_disease_monitor` 方法）

**步骤2**: 在 `CUSUMMonitor` 中添加双周统计支持

文件: `app/observability/cusum.py`

```python
def record_biweekly_stats(self, period: int, total: int, severe: int) -> None:
    rate = severe / max(total, 1)
    self._biweekly_stats.append({
        "period": period,
        "total": total,
        "severe": severe,
        "rate": rate,
    })
    if rate > self._config.target_rate:
        _logger.critical("biweekly_severe_hallucination_rate_exceeded", ...)

def check_compact_rules(self) -> List[str]:
    alerts = []
    # 原有周统计规则...
    # 新增双周规则
    if len(self._biweekly_stats) >= 2:
        recent_2 = self._biweekly_stats[-2:]
        consecutive_with_severe = sum(1 for w in recent_2 if w["severe"] >= 1)
        if consecutive_with_severe >= 2:
            alerts.append("consecutive_2_biweekly_periods_with_severe")
    return alerts
```

**步骤3**: 更新测试

**验证**: `python -m pytest tests/unit/obs/test_cusum.py -v`

---

## P1-07: 轻量Web仪表盘

### 问题
设计要求集成于主服务进程的轻量Web仪表盘，完全缺失。

### 修复步骤

**步骤1**: 创建 `app/observability/dashboard.py`

实现一个基于FastAPI的轻量仪表盘路由，从Prometheus HTTP API获取指标数据，渲染为简单HTML页面：

```python
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

@router.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request):
    # 从MetricsCollector获取关键指标
    # 渲染为简单HTML表格
    ...

@router.get("/hallucination", response_class=HTMLResponse)
async def hallucination_dashboard(request: Request):
    # 幻觉率监控面板
    ...

@router.get("/cache", response_class=HTMLResponse)
async def cache_dashboard(request: Request):
    # 缓存防护监控面板
    ...
```

**步骤2**: 在FastAPI应用中注册路由

**步骤3**: 新增测试 `tests/unit/obs/test_dashboard.py`

**验证**: `python -m pytest tests/unit/obs/test_dashboard.py -v`

---

## P1-08: 配置vmbackup自动备份

### 问题
设计要求vmbackup每小时自动备份，保留7天。

### 修复步骤

**步骤1**: 在 `docker-compose.dev.yml` 中添加vmbackup服务

```yaml
vmbackup:
  image: victoriametrics/vmbackup:v1.93.0
  depends_on:
    - victoriametrics
  volumes:
    - vmdata:/storage
    - vmbackup-data:/backup
  command:
    - "-storageDataPath=/storage"
    - "-snapshot.createURL=http://victoriametrics:8428/snapshot/create"
    - "-dst=file:///backup"
    - "-cron=0 * * * *"
    - "-retainLastDays=7"
  restart: unless-stopped
```

**步骤2**: 添加vmbackup-data卷定义

**验证**: `docker-compose -f docker-compose.dev.yml config`

---

## P1-09: 修正FULL profile日志存储后端选择

### 问题
设计要求full profile使用Elasticsearch，实现使用FileLogStorage。

### 修复步骤

**步骤1**: 修改 `logging.py` 中 `LogStorage.__init__` 的profile选择逻辑

文件: `app/observability/logging.py`

将 full profile 的默认后端从 `FileLogStorage` 改为 `ElasticsearchLogStorage`（当Elasticsearch不可用时降级为FileLogStorage）。

**步骤2**: 更新测试

**验证**: `python -m pytest tests/unit/obs/test_logging.py -v`

---

## P1-10: 补全缺失的Prometheus指标注册

### 问题
9项关键监控指标未注册Prometheus指标。

### 修复步骤

**步骤1**: 在 `MetricsCollector._init_metrics()` 中添加缺失指标

```python
# Redis监控
self.redis_process_alive = Gauge("ultrasound_redis_process_alive", ..., registry=r)
self.redis_container_restarts = Counter("ultrasound_redis_container_restarts_total", ..., registry=r)

# 审计日志降级
self.audit_log_degradation_total = Counter("ultrasound_audit_log_degradation_total", ..., registry=r)

# PostgreSQL连接池
self.pg_connection_pool_usage = Gauge("ultrasound_pg_connection_pool_usage", ..., ["pool"], registry=r)

# GPU持续超温
self.gpu_sustained_high_temp_seconds = Gauge("ultrasound_gpu_sustained_high_temp_seconds", ..., ["device"], registry=r)

# 边缘端指标（添加deployment_profile label）
# 通过在现有nav_latency和gpu_temperature中添加profile label实现
```

**步骤2**: 添加对应的setter/recorder方法

**步骤3**: 更新测试

**验证**: `python -m pytest tests/unit/obs/test_metrics_collector.py -v`

---

## P2级修复（批量处理）

### P2-01+02: CUSUMMonitor和CacheMonitor线程安全

在 `CUSUMMonitor` 和 `CacheMonitor` 中添加 `threading.Lock`：

```python
# CUSUMMonitor
self._lock = threading.Lock()

def update(self, is_severe_hallucination: bool) -> Dict[str, Any]:
    with self._lock:
        # 原有逻辑

# CacheMonitor
self._lock = threading.Lock()

# 所有record_*方法中:
with self._lock:
    # 原有逻辑
```

### P2-03: 修正P99计算

```python
@staticmethod
def _calculate_p99(data: List[float]) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    import math
    index = min(math.ceil(0.99 * len(sorted_data)) - 1, len(sorted_data) - 1)
    return sorted_data[max(index, 0)]
```

### P2-04: VictoriaMetricsPusher添加重试/退避

```python
async def _push_loop(self) -> None:
    retry_delay = self._interval
    max_retry_delay = 300.0  # 5分钟上限
    while not self._shutdown_event.is_set():
        try:
            await self._push_metrics()
            retry_delay = self._interval  # 成功后重置
        except Exception as e:
            _logger.error("VictoriaMetrics push failed", error=str(e))
            self._fail_count += 1
            retry_delay = min(retry_delay * 2, max_retry_delay)
        await asyncio.sleep(retry_delay)
```

### P2-05: 统一单例模式

将 `CacheMonitor.get_instance()` 改为双重检查锁模式：

```python
class CacheMonitor:
    _instance: Optional["CacheMonitor"] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "CacheMonitor":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
```

### P2-06: 统一日志器命名

将 `metrics.py` 中的 `_internal_logger` 改为 `_logger`，与其他模块一致。

### P2-07: 统一配置引用方式

所有模块统一通过构造函数参数传入配置，避免直接引用settings。

### P2-08: 完善Pusher优雅关闭

```python
async def stop(self) -> None:
    self._shutdown_event.set()
    if self._task and not self._task.done():
        try:
            await asyncio.wait_for(self._task, timeout=10.0)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
```

---

## 执行顺序总结

```
P0-01 (CUSUM公式) → P0-02 (校准器粒度) → P0-03 (数据管道) → P0-04 (熔断机制) → P0-05 (告警升级)
                                                                              ↓
P1-01 (缓存告警补全) → P1-02 (Prometheus接入) → P1-03 (滑动窗口) → P1-04+05 (VM内存监控)
                                                                              ↓
P1-06 (病种CUSUM) → P1-07 (仪表盘) → P1-08 (vmbackup) → P1-09 (日志后端) → P1-10 (指标补全)
                                                                              ↓
P2-01~08 (批量代码质量修复)
```

每个P0/P1修复完成后立即运行对应测试套件验证。全部修复完成后运行完整OBS测试套件：

```bash
python -m pytest tests/unit/obs/ tests/unit/llm/test_hallucination_detector.py -v
```
