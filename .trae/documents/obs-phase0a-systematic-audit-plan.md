# OBS 可观测性轨道 Phase 0A 系统性审查计划

> **审查类型**: 独立、客观、深入、批判性系统性审查  
> **审查范围**: OBS 可观测性轨道在 Phase 0A 的实施情况  
> **核心参考文档**: 05-可观测性与运维.md (P0)、01-系统架构设计.md (P1)、06-开发环境与进度规划.md (P2)、00-项目总览与核心指标.md (辅助)

---

## 一、审查执行步骤

### 步骤 1：设计规范一致性审查

**目标**: 逐项比对设计文档与实际实现，识别偏离与未实现要素

**1.1 幻觉率监控一致性审查**

对照 05-可观测性与运维.md §2 和 00-项目总览与核心指标.md §2.2：

| 审查项 | 设计要求 | 实现文件 | 审查动作 |
|--------|---------|---------|---------|
| 严重幻觉率 ≤0.5% 监控 | full 周统计 / compact 周统计+CUSUM | metrics_collector.py, cusum.py | 验证 Gauge 注册与 CUSUM 双机制是否完整联动 |
| 高风险病种幻觉率 ≤1% 监控 | full 周统计 / compact 双周统计+CUSUM | metrics_collector.py, cusum.py | 验证是否支持按病种维度统计与独立 CUSUM 实例 |
| 整体幻觉率 ≤5% 监控 | 月统计 | metrics_collector.py | 验证月度统计窗口实现 |
| CUSUM 参数校准 SOP | §161-172 蒙特卡洛模拟 ARL₀≥100, ARL₁≤50 | cusum.py CUSUMCalibrator | 验证校准流程是否完整实现且参数与文档一致 |
| compact 熔断规则 | 连续3周≥1例 或 单周≥3例 → 暂停AI辅助 | cusum.py check_compact_rules() | 验证规则逻辑正确性与告警触发机制 |
| 安全熔断自动执行 | 周统计>0.5% 或 CUSUM超阈值 → 自动暂停 | cusum.py | 验证是否实现了自动暂停机制（当前仅告警回调） |

**1.2 缓存防护监控一致性审查**

对照 05-可观测性与运维.md §86-123 和 01-系统架构设计.md §2.3：

| 审查项 | 设计要求 | 实现文件 | 审查动作 |
|--------|---------|---------|---------|
| 布隆过滤器拦截率 ≥95% | <90% Warning, <80% Critical | cache_monitor.py | 验证 check_alerts() 是否包含此告警规则 |
| 空值缓存命中率 ≥80% | <60% Warning | cache_monitor.py | 验证是否实现此告警规则 |
| 穿透至数据库查询率 ≤1% | >5% Warning, >10% Critical | cache_monitor.py | 验证告警阈值与文档一致 |
| 互斥锁获取成功率 ≥98% | <95% Warning, <90% Critical | cache_monitor.py | 验证是否实现此告警规则 |
| 互斥锁等待超时 ≤5次/小时 | >20次/小时 Warning, >50次/小时 Critical | cache_monitor.py | 验证超时计数是否按时窗口统计 |
| 热点key永不过期覆盖率 100% | <100% Warning | cache_monitor.py | 验证是否实现此告警规则 |
| TTL随机化偏移率 ±10% | 偏移为0% Critical | cache_monitor.py | 验证是否实现此告警规则 |
| 缓存同时失效5分钟窗口 ≤10个 | >50个 Warning, >100个 Critical | cache_monitor.py | 验证5分钟窗口滑动实现 |
| 缓存预热完成率 100% | <100% Warning, <80% Critical | cache_monitor.py | 验证是否实现此告警规则 |
| 缓存预热耗时 <30s | >60s Warning, >120s Critical | cache_monitor.py | 验证是否实现此告警规则 |
| 缓存命中率 ≥70% | <60% Warning, <40% Critical | cache_monitor.py | 验证告警阈值 |
| 缓存写入延迟P99 <10ms | >50ms Warning | cache_monitor.py | 验证是否实现此告警规则 |
| 缓存读取延迟P99 <5ms | >20ms Warning | cache_monitor.py | 验证是否实现此告警规则 |
| 缓存防护告警升级流程 | §104-111 四种告警场景升级策略 | cache_monitor.py | 验证是否实现升级流程 |
| 缓存防护日志收集 | §113-122 六类日志事件 | cache_monitor.py | 验证日志字段完整性 |

**1.3 告警升级路径一致性审查**

对照 05-可观测性与运维.md §195-203：

| 审查项 | 设计要求 | 实现状态 | 审查动作 |
|--------|---------|---------|---------|
| Warning 4小时内确认 | 24小时未确认升级至上级医院运维 | ❌ 未实现 | 确认缺失范围 |
| Critical 15分钟内确认 | 15分钟未确认通知上级医院运维 | ❌ 未实现 | 确认缺失范围 |
| 1小时未确认升级 | 通知医院行政值班+设备供应商 | ❌ 未实现 | 确认缺失范围 |
| 安全熔断无需人工确认 | 系统自动执行 | ⚠️ 部分实现 | 验证CUSUM告警是否触发自动暂停 |
| ALERT_CONTACTS 环境变量 | JSON格式含姓名/角色/联系方式/上级医院/行政值班/供应商 | ❌ 未实现 | 确认缺失范围 |
| 告警联系人热更新 API | PUT /api/v1/admin/alert-contacts | ❌ 未实现 | 确认缺失范围 |
| AlertManager Webhook | Critical告警推送至飞书/钉钉/企业微信 | ❌ 未实现 | 确认缺失范围 |

**1.4 compact模式监控方案一致性审查**

对照 05-可观测性与运维.md §186-203：

| 审查项 | 设计要求 | 实现状态 | 审查动作 |
|--------|---------|---------|---------|
| 轻量Web仪表盘 | 集成于主服务进程，基于Prometheus HTTP API | ❌ 未实现 | 确认缺失范围 |
| VictoriaMetrics单机版 | Docker容器，mem_limit:200MB, oom_score_adj:-500 | ✅ docker-compose.dev.yml | 验证配置完整性 |
| VictoriaMetrics内存超限检测 | >150MB Warning, >180MB Critical+降频60s | ✅ metrics.py | 验证持续5分钟条件是否实现 |
| VictoriaMetrics数据备份 | vmbackup每小时自动备份，保留7天 | ❌ 未实现 | 确认缺失范围 |
| VictoriaMetrics OOM保护 | oom_score_adj=-500 | ✅ docker-compose.dev.yml | 验证 |

**1.5 其他关键监控指标一致性审查**

对照 05-可观测性与运维.md §2 完整指标表：

| 审查项 | 设计要求 | 实现状态 | 审查动作 |
|--------|---------|---------|---------|
| SafetyGateway健康检查 | 每4s HTTP /health，连续3次超时(15s) Critical | ⚠️ Gauge已注册 | 验证是否有主动探测逻辑 |
| SafetyGateway进程状态 | 进程崩溃Critical，自动重试≤5s | ❌ 未实现 | 确认缺失 |
| 严重幻觉检测率 | >5% Warning, >10% Critical | ❌ 未实现 | 确认缺失 |
| 审计日志降级写入频率 | >0次/天 Warning | ❌ 未实现 | 确认缺失 |
| PostgreSQL熔断器监控 | 连接池>80% Warning, 熔断Critical | ❌ 未实现 | 确认缺失 |
| BM25终极兜底持续时间 | >2h Warning, >4h Critical | ⚠️ Gauge已注册 | 验证是否有告警逻辑 |
| 双写补偿队列积压 | >100条 Warning, >1000条 Critical | ⚠️ Gauge已注册 | 验证是否有告警逻辑 |
| 边缘端监控指标 | 导航推理延迟/GPU温度/显存/健康探测 | ❌ 未实现 | 确认缺失 |

---

### 步骤 2：代码质量评估

**目标**: 深入分析代码健壮性、可维护性、可扩展性和性能

**2.1 CUSUM 模块 (cusum.py)**

| 审查维度 | 审查要点 |
|---------|---------|
| 算法正确性 | CUSUM更新公式 `s_plus = max(0, s_plus + (x - p0 - delta))` 是否符合标准CUSUM定义 |
| 线程安全 | CUSUMState 无锁保护，多线程并发update是否存在竞态 |
| 告警回调异常处理 | _notify_alerts 捕获异常后仅日志，是否应记录失败回调 |
| CUSUMCalibrator | 蒙特卡洛模拟的随机种子固定为42，是否影响校准结果的统计意义 |
| ARL计算 | 未触发时 run_length = simulation_days * 2，此截断方式是否引入偏差 |
| 周统计与CUSUM联动 | record_weekly_stats 和 update 是独立调用，无自动联动机制 |
| delta参数 | 默认delta=0.005与target_rate相同，是否合理（标准CUSUM中delta通常为允许偏移量的一半） |

**2.2 缓存监控模块 (cache_monitor.py)**

| 审查维度 | 审查要点 |
|---------|---------|
| 线程安全 | CacheMonitor 无锁保护，_penetration/_breakdown/_avalanche/_overall 的并发读写安全 |
| P99计算 | `_calculate_p99` 使用 `int(0.99 * len)` 索引，与标准P99定义（nearest-rank法）的偏差 |
| 告警规则完整性 | check_alerts() 仅覆盖4种告警，设计文档要求13+种告警规则 |
| 互斥锁超时统计 | mutex_lock_timeout_count 为累计值而非小时窗口值，与设计要求"≤5次/小时"不一致 |
| Prometheus更新 | _update_prometheus 仅更新 cache_hit_rate，其他指标未同步到Prometheus |
| deque容量 | write_latencies/read_latencies maxlen=1000，高并发下是否足够计算P99 |
| expiry_timestamps | maxlen=10000，5分钟窗口内超过10000个key失效时数据丢失 |

**2.3 指标采集模块 (metrics_collector.py)**

| 审查维度 | 审查要点 |
|---------|---------|
| 单例模式 | 双重检查锁定正确性（Python GIL下是否必要） |
| CollectorRegistry | 使用独立registry而非默认REGISTRY，VictoriaMetrics推送是否兼容 |
| VictoriaMetricsPusher | 无重试机制、无指数退避、推送失败仅计数 |
| 推送间隔 | 默认15s，与设计文档scrape_interval对齐 |
| httpx超时 | 5s超时是否合理，VictoriaMetrics不可达时是否阻塞推送循环 |
| 优雅关闭 | stop()取消task但未等待当前推送完成 |

**2.4 内存监控模块 (metrics.py)**

| 审查维度 | 审查要点 |
|---------|---------|
| 持续5分钟条件 | 设计文档要求"持续5分钟超过150MB触发Warning"，当前实现立即触发 |
| 降频恢复逻辑 | _handle_high_memory 中 NORMAL 分支永远不会被调用（因为只在 WARNING/CRITICAL 时进入） |
| psutil依赖 | 跨平台兼容性，Windows/Linux内存读取差异 |
| 异步锁 | asyncio.Lock 在非异步上下文中的使用风险 |

**2.5 日志模块 (logging.py)**

| 审查维度 | 审查要点 |
|---------|---------|
| FULL profile存储后端 | 设计文档要求Elasticsearch，实现默认使用FileLogStorage |
| compact profile | 设计文档要求统一PostgreSQL，实现正确 |
| 批量写入降级 | awrite_batch失败后逐条重试，可能加剧数据库压力 |
| LogRecord序列化 | extra字段加前缀"extra_"，kwargs加前缀"param_"，与审计日志表结构对齐问题 |
| asyncio事件循环 | write()方法中get_event_loop()在Python 3.12+已弃用 |

**2.6 链路追踪模块 (tracing.py)**

| 审查维度 | 审查要点 |
|---------|---------|
| OpenTelemetry可选依赖 | ImportError时静默降级，是否应有更明确的告警 |
| DiagnosisSpan | 仅支持async with，不支持sync with |
| 采样率 | compact默认0.1，full提升至0.5，与设计文档对齐 |
| 全局状态 | _tracer_provider/_tracer/_initialized 使用模块级全局变量，测试隔离风险 |

**2.7 审计链模块 (audit_chain.py)**

| 审查维度 | 审查要点 |
|---------|---------|
| 数据库依赖 | 直接依赖SQLAlchemy AsyncSession，与模块解耦原则冲突 |
| 哈希链完整性 | CriticalOperationAudit 哈希链构建逻辑正确性 |
| 跨链校验 | AuditCrossChain 周期性校验任务异常处理 |
| 全局锁 | _cross_chain_lock/_critical_chain_lock 使用asyncio.Lock，模块级全局状态 |

---

### 步骤 3：代码风格合规性审查

| 审查维度 | 审查要点 |
|---------|---------|
| 模块文档字符串 | 部分模块有英文docstring(enums.py, metrics.py)，部分无(cusum.py, cache_monitor.py) |
| 日志器命名 | logging.py用`_internal_logger`，其他模块用`_logger`，不一致 |
| 类型注解 | 部分方法返回值缺少类型注解（如get_all_metrics返回Dict[str, Any]过于宽泛） |
| 命名规范 | Prometheus指标名使用snake_case与ultrasound_前缀，一致；但CacheEventType与日志事件名不完全对齐 |
| 错误处理 | 部分模块异常捕获后仅日志记录（cache_monitor._update_prometheus, tracing），部分静默忽略 |
| 配置引用 | cusum.py直接引用settings，cache_monitor.py通过metrics_collector间接引用，不一致 |
| 单例模式 | MetricsCollector用双重检查锁，CacheMonitor用简单if判断，LogStorage用双重检查锁，不一致 |

---

### 步骤 4：功能正确性验证

**4.1 CUSUM功能验证**

| 测试场景 | 验证要点 |
|---------|---------|
| 正常运行（幻觉率≤0.5%） | CUSUM s_plus应趋近0，不触发告警 |
| 异常偏移（幻觉率>0.5%） | s_plus应持续增长并超过h=3 |
| 周统计规则 | 连续3周≥1例 → 触发；单周≥3例 → 触发 |
| CUSUM校准 | ARL₀≥100, ARL₁≤50 的参数组合是否可找到 |
| 告警回调 | 触发后回调是否正确执行，异常回调是否不影响其他回调 |
| reset后状态 | 重置后所有状态归零 |

**4.2 缓存监控功能验证**

| 测试场景 | 验证要点 |
|---------|---------|
| 布隆过滤器拦截率计算 | intercepted/total比例正确 |
| 互斥锁超时小时窗口 | 当前实现为累计值，需验证是否满足"次/小时"要求 |
| 5分钟滑动窗口 | key_expiry时间戳deque的正确清理 |
| P99计算精度 | 与标准nearest-rank法的偏差 |
| 告警规则覆盖 | 13+种设计告警规则中已实现4种的正确性 |
| Prometheus同步 | _update_prometheus是否同步所有指标 |

**4.3 VictoriaMetrics集成验证**

| 测试场景 | 验证要点 |
|---------|---------|
| 指标推送 | VictoriaMetricsPusher正确推送Prometheus格式指标 |
| 推送失败处理 | 网络不可达时是否优雅降级 |
| 内存监控阈值 | 150MB/180MB阈值触发与持续5分钟条件 |
| Docker Compose配置 | VictoriaMetrics v1.93.0, mem_limit:200MB, oom_score_adj:-500 |

**4.4 日志框架验证**

| 测试场景 | 验证要点 |
|---------|---------|
| compact模式PostgreSQL存储 | 日志正确写入audit_logs表 |
| full模式存储后端 | 设计要求Elasticsearch，实现为FileLogStorage |
| 批量写入性能 | P99 < 1ms入队延迟 |
| NTP同步状态标记 | ntp_sync_status字段正确附加 |
| 日志分类 | diagnostic/operational/audit三类正确分类 |

---

## 二、审查发现分级标准

| 级别 | 定义 | 示例 |
|------|------|------|
| **P0-阻断** | 设计要求的核心功能完全缺失，直接影响系统安全或合规 | 安全熔断自动执行未实现、告警升级路径未实现 |
| **P1-严重** | 设计要求的重要功能缺失或实现偏差，影响可观测性完整性 | 缓存防护13+告警规则仅实现4种、VictoriaMetrics持续5分钟条件未实现 |
| **P2-中等** | 代码质量问题或设计偏差，不影响核心功能但影响可维护性 | 线程安全、P99计算精度、命名不一致 |
| **P3-建议** | 改进建议，不影响当前功能正确性 | 文档字符串统一、错误处理增强 |

---

## 三、审查输出

审查完成后将生成结构化审查报告，包含：

1. **审查发现汇总表** — 按P0/P1/P2/P3分级，每条包含：审查维度、设计要求、实际实现、偏差描述、影响评估、修复建议
2. **设计规范一致性矩阵** — 逐项对照设计文档与实现的状态（✅一致/⚠️部分一致/❌缺失）
3. **代码质量评估报告** — 按模块的健壮性/可维护性/可扩展性/性能评估
4. **功能正确性验证结果** — 测试场景与验证结论
5. **修复优先级建议** — 按P0→P3排序的修复路线图

---

## 四、执行顺序

1. 运行现有测试套件，确认基线通过率
2. 执行步骤1（设计规范一致性审查）— 逐项比对文档与代码
3. 执行步骤2（代码质量评估）— 静态分析+代码审查
4. 执行步骤3（代码风格合规性审查）— 规范检查
5. 执行步骤4（功能正确性验证）— 补充测试+边界验证
6. 汇总审查发现，生成分级审查报告
7. 自我反思：评估审查过程的完整性和客观性
