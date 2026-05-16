# 超声诊疗决策支持AI系统 - Phase0A SG安全网关审查与修复成果归档

> **归档版本**: v1.0
> **归档日期**: 2026-05-09
> **阶段**: Phase0A — Gate0A（核心技术框架验证）
> **轨道**: SG安全网关
> **审查状态**: 完成全面系统性审查，修复2项P0、4项P1、4项P2问题，新增13个测试用例，62个相关测试全部通过

---

## 一、项目概述

### 1.1 项目背景

本项目为**超声诊疗决策支持AI系统**的SG（Safety Gateway）安全网关模块，作为系统安全防线的核心组件，负责在诊断结论输出前执行四项安全校验（同意状态→脱敏→禁忌→阈值→一致性），确保患者隐私保护和临床安全。SG以独立进程运行，与主API服务进程隔离，具备熔断器保护、降级输出、OOM保护、P99延迟监控等生产级可靠性机制。

### 1.2 阶段目标

Phase0A阶段SG安全网关轨道包含以下核心任务：

- ✅ 独立进程部署（Docker容器隔离，端口9001，独立Redis DB1）
- ✅ 四项安全校验流程（同意状态→脱敏→禁忌→阈值→一致性）
- ✅ 5种降级原因 + 2种客户端降级 = 7种降级场景完整覆盖
- ✅ 熔断器模式保护数据库查询（CLOSED→OPEN→HALF_OPEN三态）
- ✅ P99延迟基准 < 5ms（含P50<1ms、P95<3ms）
- ✅ OOM保护（oom_score_adj=-1000）
- ✅ 健康检查5组件（Redis/PostgreSQL/规则引擎/熔断器/阈值引擎）
- ✅ 脱敏规则热更新（Redis Pub/Sub + 回滚机制）
- ✅ 阈值热更新（Redis Pub/Sub + DB持久化）
- ✅ 全面系统性审查与修复

### 1.3 验收标准

| 验收项 | 标准 | 状态 | 实际达成 |
|--------|------|------|---------|
| 进程隔离 | SG独立容器+端口+OOM保护 | ✅ | Docker独立容器，主服务依赖SG healthy |
| 延迟基准 | P99<5ms, P95<3ms, P50<1ms | ✅ | 基准测试通过（mock场景），运行时P99监控已修复 |
| 降级路径 | 7种降级场景完整覆盖 | ✅ | 5种DegradedReason + 2种客户端降级，含DB_TIMEOUT修复 |
| OOM保护 | oom_score_adj=-1000 | ✅ | Docker Compose + 启动脚本双重配置 |
| 健康检查 | 5组件+P99告警+看门狗 | ✅ | 三态语义(healthy/degraded/unhealthy)已修复 |
| 测试覆盖 | 单元测试+性能基准+边界条件 | ✅ | 30个测试类，130+用例，62个相关测试全部通过 |

---

## 二、实现功能说明

### 2.1 核心校验流程

**执行顺序**: 同意状态 → 脱敏 → 禁忌 → 阈值 → 一致性

```
请求进入
  │
  ├─ 1.同意状态校验
  │    ├─ DB错误/熔断器打开 → SG_UNAVAILABLE (降级)
  │    ├─ 查询超时 → DEGRADED + DB_TIMEOUT (降级)
  │    ├─ WITHDRAWN → FAILED (拒绝)
  │    └─ DENIED/None → FAILED (拒绝)
  │
  ├─ 2.脱敏校验
  │    └─ 检测到敏感数据 → DEGRADED + SENSITIVE_DATA_DETECTED
  │
  ├─ 3.禁忌校验
  │    └─ 禁忌命中 → DEGRADED + CONTRAINDICATION_HIT
  │
  ├─ 4.阈值校验
  │    └─ 置信度低于阈值 → DEGRADED + CONFIDENCE_BELOW_THRESHOLD
  │
  └─ 5.一致性校验
       └─ 不一致 → FAILED (直接拒绝)
```

**核心代码**: [checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py) — `perform_full_check()` 方法

### 2.2 进程隔离架构

| 组件 | 配置 |
|------|------|
| Dockerfile | `Dockerfile.safety-gateway.dev`（独立镜像） |
| 端口 | 9001（主API使用8000） |
| Redis | DB1（主服务使用DB0） |
| OOM保护 | `oom_score_adj: -1000` |
| 资源限制 | CPU 2核 / 内存 1G |
| 健康检查 | `curl -f http://localhost:9001/health`，间隔5s |
| 启动依赖 | 主API `depends_on: safety-gateway: condition: service_healthy` |

**配置文件**: [docker-compose.dev.yml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/docker-compose.dev.yml) L91-128

### 2.3 降级输出机制

当校验结果为DEGRADED时，`apply_degraded_output()` 执行以下操作：

1. **白名单字段过滤**: 仅保留 `observations`/`status`/`request_id`/`session_id`
2. **白名单字段内容脱敏**: 字符串类型做脱敏处理
3. **非白名单字段移除**: 记录在 `degraded_fields` 列表中
4. **附加降级标记**: `conclusion_suppressed=True` + 根据降级原因添加特定标记

### 2.4 熔断器模式

三态模型保护数据库查询：

| 状态 | 触发条件 | 行为 |
|------|----------|------|
| CLOSED | 正常 | 放行请求 |
| OPEN | 失败次数≥5 | 拒绝请求，执行fallback |
| HALF_OPEN | 冷却期后(30s) | 放行探测请求 |

**核心代码**: [circuit_breaker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/circuit_breaker.py)

### 2.5 健康检查系统

5组件健康检查 + P99延迟监控：

| 组件 | 检查方式 | 状态语义 |
|------|----------|----------|
| Redis | `ping()` | healthy/unhealthy |
| PostgreSQL | `SELECT 1` | healthy/unhealthy |
| 规则引擎 | 启用规则数 | healthy/degraded(0条启用) |
| 熔断器 | 是否有OPEN状态 | healthy/unhealthy |
| 阈值引擎 | 是否已加载 | healthy/degraded(未加载) |

**P99延迟告警**:
- P99 ≥ 5ms → critical
- P99 ≥ 4ms → warning
- P99 < 4ms → ok

**整体状态**（三态语义）:
- `healthy`: 所有组件healthy
- `degraded`: 存在degraded组件但无unhealthy组件
- `unhealthy`: 存在unhealthy组件

**核心代码**: [health.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health.py)

### 2.6 热更新机制

| 组件 | 机制 | 频道 | 回滚 |
|------|------|------|------|
| 脱敏规则 | Redis Pub/Sub | `desensitization_rules_update` | ✅ 验证失败自动回滚旧规则 |
| 阈值配置 | Redis Pub/Sub | `sg:threshold:update` | ✅ DB加载失败回退默认阈值 |

### 2.7 错误码体系

| 错误码 | 异常类 | 含义 |
|--------|--------|------|
| SG_ERROR | SafetyGatewayError | 基类 |
| SG_DB_CONNECTION | DatabaseConnectionError | 数据库连接失败 |
| SG_DB_TIMEOUT | DatabaseQueryTimeoutError | 数据库查询超时 |
| SG_DB_QUERY | DatabaseQueryError | 数据库查询执行失败 |
| SG_CONSENT_CHECK | ConsentCheckError | 同意状态检查失败 |
| SG_SENSITIVE_DATA | SensitiveDataError | 检测到敏感数据 |
| SG_CONTRAINDICATION | ContraindicationError | 检测到禁忌条件 |
| SG_THRESHOLD | ThresholdError | 置信度低于阈值 |
| SG_CIRCUIT_BREAKER | CircuitBreakerError | 熔断器已打开 |
| E4005 | ServiceUnavailableError | 安全网关服务不可用 |

**核心代码**: [errors.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/errors.py)

---

## 三、关键技术点

### 3.1 HealthChecker全局单例化（P0修复）

**问题**: `/health`端点每次请求创建新的`HealthChecker()`实例，延迟统计（`_latency_samples`）存储在实例属性中，导致P99延迟监控完全失效。

**修复**: `/health`端点改用`_get_health_checker()`全局单例，与`checker.py`中`record_latency()`写入的实例一致。

```python
# 修复前（main.py）
@app.get("/health")
async def health_check():
    health_checker = HealthChecker()  # 每次新建实例！
    return await health_checker.check_all()

# 修复后
@app.get("/health")
async def health_check():
    from safety_gateway.checker import _get_health_checker as _get_sg_health_checker
    health_checker = _get_sg_health_checker()  # 使用全局单例
    return await health_checker.check_all()
```

### 3.2 P99百分位计算统一（P1修复）

**问题**: 性能基准测试使用离散索引 `latencies[int(n*0.99)]`，而`health.py`使用线性插值，两种算法结果不同。

**修复**: 基准测试统一使用与`health.py`相同的线性插值算法：

```python
def compute_percentile(sorted_data: list[float], p: int) -> float:
    n = len(sorted_data)
    if n == 0:
        return 0.0
    index = (p / 100) * (n - 1)
    if index.is_integer():
        return sorted_data[int(index)]
    lower = sorted_data[int(index)]
    upper = sorted_data[min(int(index) + 1, n - 1)]
    return lower + (upper - lower) * (index - int(index))
```

### 3.3 DB_TIMEOUT降级原因激活（P1修复）

**问题**: `DegradedReason.DB_TIMEOUT`在枚举中定义但从未被使用。consent查询超时时返回`DENIED`（FAILED）而非降级。

**修复**: consent查询超时时返回`DEGRADED` + `DB_TIMEOUT`，进入降级输出模式：

```python
# 修复前：超时仅记录警告，继续执行（consent_status=DENIED → FAILED）
if is_timeout:
    logger.warning("sg_consent_check_timeout_continuing_with_conservative_policy", ...)

# 修复后：超时直接进入降级模式
if is_timeout:
    logger.warning("sg_consent_check_timeout_entering_degraded_mode", ...)
    return CheckResult(
        status=CheckResultStatus.DEGRADED,
        degraded_reason=DegradedReason.DB_TIMEOUT,
        consent_check_timeout=True,
        ...
    )
```

### 3.4 健康检查三态语义（P2修复）

**问题**: `check_all()`仅区分`healthy`和`unhealthy`，规则引擎/阈值引擎返回`degraded`时被等同于`unhealthy`。

**修复**: 实现三态语义判断：

```python
if components_healthy:
    overall = "healthy"
elif components_degraded and all(s["status"] in ("healthy", "degraded") for s in statuses):
    overall = "degraded"
else:
    overall = "unhealthy"
```

### 3.5 递归深度保护

`checker.py`中`MAX_RECURSION_DEPTH = 10`，防止恶意构造的深层嵌套数据结构导致栈溢出：

```python
async def _check_dict_recursive(self, data, depth=0):
    if depth >= MAX_RECURSION_DEPTH:
        logger.warning("sg_dict_recursion_depth_exceeded", depth=depth)
        return []
    ...
```

### 3.6 脱敏规则热更新回滚

`reload_rules()`在加载新规则前先备份旧规则，验证失败自动回滚：

```python
def reload_rules(self):
    old_rules = self._rules.copy()
    old_patterns = {k: v for k, v in self._compiled_patterns.items()}
    try:
        # 加载+验证+编译新规则
        ...
    except Exception:
        self._rules = old_rules       # 回滚
        self._compiled_patterns = old_patterns
        return False
```

---

## 四、遇到的问题及解决方案

### 4.1 P0级问题

| 编号 | 问题 | 影响 | 解决方案 |
|------|------|------|----------|
| PI-05 | `/health`端点每次创建新HealthChecker实例 | P99延迟监控完全失效，延迟统计永远为空 | 改用`_get_health_checker()`全局单例 |
| PN-03 | P99监控因PI-05完全失效 | P99告警永远不会触发 | 同PI-05修复 |

### 4.2 P1级问题

| 编号 | 问题 | 影响 | 解决方案 |
|------|------|------|----------|
| LB-03 | P99百分位计算方法不一致 | 测试通过不代表运行时P99满足要求 | 统一使用线性插值算法 |
| DP-08 | DB_TIMEOUT降级原因定义但从未使用 | 死代码，7种降级场景实际仅6种生效 | consent超时返回DEGRADED+DB_TIMEOUT |
| OM-03 | OOM测试验证的是无关配置 | 测试名称与内容不匹配，测试无效 | 改为验证docker-compose.dev.yml中oom_score_adj |
| TL-02 | 熔断器测试不隔离 | 测试间可能相互影响 | 添加teardown_method清理breaker |

### 4.3 P2级问题

| 编号 | 问题 | 解决方案 |
|------|------|----------|
| LB-05 | 延迟统计窗口过小（100样本） | 增加到500 |
| HC-07/08 | degraded状态语义不一致 | check_all()区分healthy/degraded/unhealthy三态 |
| HC-11 | 看门狗仅检查checker非None | 增加P99状态检查 |
| TL-04 | 边界条件测试不充分 | 新增4个边界条件测试 |

### 4.4 已知遗留问题

| 编号 | 问题 | 优先级 | 说明 |
|------|------|--------|------|
| DP-09 | 白名单字段内容被脱敏影响临床可用性 | P2 | 需产品决策是否保留原始内容 |
| DP-10 | 多降级原因叠加场景 | P2 | 当前短路返回仅记录第一个原因 |
| OM-05 | 内存压力测试不够真实 | P2 | 仅分配100KB，不足以触发OOM压力 |
| OM-07 | 缺少内存泄漏检测 | P2 | SG作为长驻服务需长期运行内存增长测试 |
| PN-05 | P99告警仅日志无主动通知 | P2 | 需集成webhook/邮件告警 |
| TL-06 | 一致性校验仅比较grade和finding | P2 | 未比较severity/confidence等字段 |

---

## 五、文件清单

### 5.1 核心源码（safety_gateway/）

| 文件 | 行数 | 职责 |
|------|------|------|
| [main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py) | 340 | FastAPI服务入口，独立进程，7个API端点 |
| [checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py) | 485 | 主校验器，四项校验流程+降级输出+HealthChecker单例 |
| [desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/desensitization.py) | 392 | 脱敏引擎，9条默认规则+4条补丁规则+热更新回滚 |
| [health.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health.py) | 234 | 健康检查，5组件+P99延迟监控+三态语义 |
| [threshold.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/threshold.py) | 552 | 阈值引擎，DB加载+缓存TTL+Redis热更新 |
| [consent_checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/consent_checker.py) | 162 | 同意状态校验，重试+熔断保护 |
| [circuit_breaker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/circuit_breaker.py) | 218 | 熔断器三态模型+管理器 |
| [contraindication.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/contraindication.py) | 216 | 禁忌校验引擎，8条默认规则 |
| [errors.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/errors.py) | 163 | 16个错误码+异常层次结构 |
| [validation_result.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/validation_result.py) | 91 | 4种校验状态+5种降级原因+7个结果模型 |

### 5.2 测试文件

| 文件 | 行数 | 测试类数 | 用例数 |
|------|------|----------|--------|
| [test_safety_gateway.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_safety_gateway.py) | 2036 | 30 | 130+ |
| [test_safety_gateway_performance.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/benchmark/test_safety_gateway_performance.py) | 204 | 2 | 5 |

### 5.3 配置文件

| 文件 | 说明 |
|------|------|
| [docker-compose.dev.yml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/docker-compose.dev.yml) L91-128 | SG独立容器配置 |
| [Dockerfile.safety-gateway.dev](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/Dockerfile.safety-gateway.dev) | SG独立Dockerfile |
| [desensitization_rules_safety.yaml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/desensitization_rules_safety.yaml) | 9条SG脱敏规则 |
| [desensitization_rules_safety_patch.yaml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/desensitization_rules_safety_patch.yaml) | 4条补丁规则 |
| [start_safety_gateway.sh](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/scripts/start_safety_gateway.sh) | SG启动脚本（含oom_score_adj设置） |

### 5.4 客户端与错误码

| 文件 | 说明 |
|------|------|
| [safety_gateway_client.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/safety_gateway_client.py) | 主服务调用SG的HTTP客户端 |
| [errors.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/errors.py) | E4005/E4005A/E4005B/E4005C错误码 |

### 5.5 审查文档（.trae/documents/）

| 文件 | 说明 |
|------|------|
| [sg-validation-testing-comprehensive-audit-plan.md](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/.trae/documents/sg-validation-testing-comprehensive-audit-plan.md) | 本次全面审查计划 |
| sg-deep-verification-testing-audit-plan.md | 深度验证测试审计计划 |
| sg-verification-testing-audit-plan.md | 验证测试审计计划 |
| sg-verification-testing-audit-report.md | 验证测试审计报告 |
| sg-safety-gateway-development-plan.md | SG开发计划 |
| sg-safety-gateway-fix-plan-v3.md | SG修复计划v3 |
| sg-safety-gateway-independent-audit-plan.md | SG独立审计计划 |
| sg-safety-gateway-systematic-audit-report.md | SG系统性审计报告 |
| sg-safety-gateway-systematic-review-plan.md | SG系统性审查计划 |
| sg-systematic-fix-plan-phase0a.md | SG Phase0A系统性修复计划 |

---

## 六、配置参考

### 6.1 SG相关配置项（settings.py）

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `safety_gateway_host` | `0.0.0.0` | SG绑定地址 |
| `safety_gateway_port` | `9001` | SG服务端口 |
| `safety_gateway_whitelist_fields` | `['observations','status','request_id','session_id']` | 降级输出白名单字段 |
| `safety_gateway_enable_degraded_mode` | `True` | 是否启用降级模式 |
| `sg_db_retry_max_attempts` | `3` | DB重试最大次数 |
| `sg_db_retry_delay_base` | `0.5` | DB重试基础延迟(秒) |
| `sg_db_retry_max_total_time_seconds` | `10.0` | DB重试总时间上限(秒) |
| `sg_threshold_cache_ttl_seconds` | `300` | 阈值缓存TTL(秒) |
| `sg_circuit_breaker_failure_threshold` | `5` | 熔断器失败阈值 |
| `sg_circuit_breaker_success_threshold` | `3` | 熔断器恢复成功阈值 |
| `sg_circuit_breaker_cooldown_period` | `30.0` | 熔断器冷却时间(秒) |
| `sg_p99_latency_warning_threshold_ms` | `4.0` | P99延迟告警阈值(ms) |
| `sg_p99_latency_critical_threshold_ms` | `5.0` | P99延迟严重阈值(ms) |

### 6.2 默认阈值

| 器官 | 阈值 |
|------|------|
| thyroid（甲状腺） | 0.85 |
| liver（肝脏） | 0.88 |
| cardiac（心脏） | 0.92 |

### 6.3 默认禁忌规则

| 器官 | 条件 | 严重度 |
|------|------|--------|
| thyroid | pregnancy + FNA | high |
| thyroid | anticoagulant_therapy | medium |
| liver | coagulopathy | high |
| liver | pregnancy + CT | medium |
| cardiac | pregnancy + stress_echo | high |
| cardiac | aortic_stenosis_severe | high |
| global | pregnancy + radiation | critical |
| global | contrast_allergy | high |

---

## 七、使用说明

### 7.1 启动SG服务

**Docker Compose方式**（推荐）:
```bash
docker-compose -f docker-compose.dev.yml up safety-gateway
```

**直接启动**:
```bash
bash scripts/start_safety_gateway.sh
```

### 7.2 API端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 服务基本信息 |
| GET | `/health` | 健康检查（含P99延迟统计） |
| GET | `/health/watchdog` | 看门狗检查（含P99状态） |
| GET | `/api/v1/consent-status/{patient_id}` | 查询患者同意状态 |
| POST | `/api/v1/safety/check` | 安全校验主端点 |
| POST | `/api/v1/safety/consistency` | 一致性校验 |
| POST | `/api/v1/safety/contraindication` | 禁忌校验 |

### 7.3 安全校验请求示例

```json
POST /api/v1/safety/check
{
  "patient_id": "P001",
  "output_data": {
    "organ": "thyroid",
    "diagnosis_data": {},
    "conclusions": [{"organ": "thyroid", "confidence": 0.95}],
    "fusion_output": {"diagnostic_conclusion": {"grade": "T1", "finding": "良性"}},
    "specialist_conclusions": [{"organ": "thyroid", "grade": "T1", "finding": "良性"}]
  }
}
```

### 7.4 运行测试

```bash
# 单元测试
python -m pytest tests/unit/test_safety_gateway.py -v

# 性能基准测试
python -m pytest tests/benchmark/test_safety_gateway_performance.py -v

# 仅运行本次新增测试
python -m pytest tests/unit/test_safety_gateway.py -v -k "TestHealthCheckerGlobalSingleton or TestDBTimeoutDegradedReason or TestHealthCheckDegradedStatus or TestBoundaryConditionsExtended"
```

### 7.5 脱敏规则热更新

通过Redis Pub/Sub触发规则热更新：
```bash
redis-cli PUBLISH desensitization_rules_update "reload"
```

### 7.6 阈值热更新

通过Redis Pub/Sub触发阈值更新：
```bash
redis-cli PUBLISH sg:threshold:update '{"action": "update", "organ": "thyroid", "threshold": 0.90}'
redis-cli PUBLISH sg:threshold:update '{"action": "refresh"}'
redis-cli PUBLISH sg:threshold:update '{"action": "delete", "organ": "thyroid"}'
```

---

## 八、测试结果

### 8.1 本次审查修复验证

**执行命令**: `python -m pytest tests/unit/test_safety_gateway.py -v -k "TestCircuitBreaker or TestHealthCheck or TestP99 or TestDegraded or TestOOM or TestServiceIsolation or TestMainService or TestFullCheck or TestBoundary or TestHealthCheckerGlobal or TestDBTimeout or TestHealthCheckDegraded or TestBoundaryConditionsExtended"`

**结果**: **62 passed, 1 skipped**

跳过原因: `test_oom_score_adj_read_from_proc` — Windows环境无法读取`/proc/self/oom_score_adj`

### 8.2 新增测试用例清单

| 测试类 | 测试方法 | 验证内容 |
|--------|----------|----------|
| TestHealthCheckerGlobalSingleton | test_get_health_checker_returns_singleton | 全局单例一致性 |
| TestHealthCheckerGlobalSingleton | test_record_latency_persists_across_calls | 延迟数据持久化 |
| TestHealthCheckerGlobalSingleton | test_latency_samples_not_lost_on_new_health_check_call | 多次调用不丢失数据 |
| TestDBTimeoutDegradedReason | test_consent_timeout_returns_degraded_with_db_timeout | consent超时→DEGRADED+DB_TIMEOUT |
| TestDBTimeoutDegradedReason | test_db_timeout_degraded_reason_in_apply_degraded_output | DB_TIMEOUT降级输出标记 |
| TestHealthCheckDegradedStatus | test_overall_degraded_when_rules_engine_degraded | 规则引擎degraded→overall degraded |
| TestHealthCheckDegradedStatus | test_overall_unhealthy_when_db_unhealthy | DB unhealthy→overall unhealthy |
| TestHealthCheckDegradedStatus | test_overall_healthy_when_all_healthy | 全部healthy→overall healthy |
| TestBoundaryConditionsExtended | test_confidence_zero | confidence=0→阈值检查失败 |
| TestBoundaryConditionsExtended | test_confidence_one | confidence=1.0→阈值检查通过 |
| TestBoundaryConditionsExtended | test_empty_conclusions_list | 空conclusions列表处理 |
| TestBoundaryConditionsExtended | test_unknown_organ_threshold | 未知器官阈值检查 |

---

## 九、修改记录

### 本次审查修复（2026-05-09）

| 文件 | 修改内容 | 问题编号 |
|------|----------|----------|
| safety_gateway/main.py | /health端点使用全局HealthChecker单例 | PI-05/PN-03 |
| safety_gateway/main.py | 看门狗端点增加P99状态检查 | HC-11 |
| safety_gateway/checker.py | consent超时返回DEGRADED+DB_TIMEOUT | DP-08 |
| safety_gateway/health.py | MAX_LATENCY_SAMPLES 100→500 | LB-05 |
| safety_gateway/health.py | check_all()三态语义(healthy/degraded/unhealthy) | HC-07/08 |
| tests/benchmark/test_safety_gateway_performance.py | 统一P99百分位计算方法 | LB-03 |
| tests/unit/test_safety_gateway.py | OOM测试验证docker-compose配置 | OM-03 |
| tests/unit/test_safety_gateway.py | 熔断器测试teardown清理 | TL-02 |
| tests/unit/test_safety_gateway.py | 新增4个测试类13个用例 | TL-04/PI-05/DP-08/HC-07 |
