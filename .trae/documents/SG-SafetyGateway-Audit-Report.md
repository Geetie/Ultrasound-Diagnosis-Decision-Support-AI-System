# SafetyGateway轨道系统性审查报告

**审查日期**：2026-05-12
**审查对象**：SafetyGateway（SG）安全网关轨道全部验证环节和测试逻辑
**审查原则**：独立、客观、深入、批判性
**审查范围**：safety_gateway/目录36个源文件 + 6个测试文件 + Docker/部署配置 + 5份参考文档

---

## 审查总结

| 严重程度 | 数量 | 说明 |
|----------|------|------|
| P0-Critical | 5 | 必须修复，影响系统安全底线 |
| P1-High | 8 | 应当修复，影响系统可靠性或与文档一致性 |
| P2-Medium | 9 | 建议修复，影响可维护性或代码质量 |
| P3-Low | 4 | 可选优化，改善代码风格或文档 |

---

## SafetyGateway轨道应当进行的修复项（显式声明）

### [SG-FIX-001] Dockerfile USER sguser 导致 OOM 保护启动脚本路径不可达

**严重程度**：P0-Critical
**审查维度**：功能正确性
**涉及文件**：[Dockerfile.safety-gateway.dev](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/Dockerfile.safety-gateway.dev)、[scripts/start_safety_gateway.sh](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/scripts/start_safety_gateway.sh)
**文档偏差**：与01-系统架构设计.md§3.4 OOM保护要求不一致

**问题描述**：
Dockerfile设置`USER sguser`（非root），但[start_safety_gateway.sh](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/scripts/start_safety_gateway.sh#L4)中OOM保护逻辑`echo -1000 > /proc/self/oom_score_adj`需要root权限。以sguser身份运行时，`id -u`≠0，OOM保护代码路径完全不可达。

当前仅依赖docker-compose.dev.yml中的`oom_score_adj: -1000`容器级配置。若通过`docker run`直接启动（非docker-compose），OOM保护将完全缺失，违反01-系统架构设计.md中"SafetyGateway的oom_score_adj = -1000（禁止被OOM Killer杀死）"的强制要求。

**影响分析**：在非docker-compose部署场景下，SG进程可能被OOM Killer杀死，导致安全校验不可用，违反安全底线原则。

**修复建议**：
1. Dockerfile中移除`USER sguser`，改为在start_safety_gateway.sh中通过gosu降权（已有此逻辑但被USER指令绕过）
2. 或在Dockerfile中将CMD改为以root启动，由start_safety_gateway.sh内部处理降权

```dockerfile
# 修复方案：移除 USER sguser，让 start_safety_gateway.sh 以 root 启动后 gosu 降权
# 删除: USER sguser
CMD ["bash", "/app/scripts/start_safety_gateway.sh"]
```

---

### [SG-FIX-002] 同意状态校验缺少Redis降级回退路径

**严重程度**：P0-Critical
**审查维度**：功能正确性
**涉及文件**：[consent_checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/consent_checker.py)
**文档偏差**：与04-安全合规.md§6"PostgreSQL不可用时，SafetyGateway从Redis只读缓存读取同意状态（TTL 60s）"不一致

**问题描述**：
04-安全合规.md明确要求"PostgreSQL不可用时，SafetyGateway从Redis只读缓存读取同意状态（TTL 60s），缓存未命中返回E4005B"。但[consent_checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/consent_checker.py#L82)中`check_consent_status`方法在PG不可用时直接返回`ConsentCheckResult(None, False, True)`（error=True），**完全没有Redis只读缓存回退逻辑**。

代码注释声明"SG直查PG策略：PG不可用时拒绝所有诊断输出(E4005B)，不降级为Redis缓存"，这与01-系统架构设计.md的"权威防线"原则一致（消除缓存窗口安全漏洞），但与04-安全合规.md的降级策略矛盾。

**影响分析**：这是一个**设计决策冲突**。代码选择了更严格的安全策略（宁可拒绝也不使用可能过期的缓存），但违反了04-安全合规.md的降级路径设计。在PG短暂不可用时，系统将拒绝所有诊断输出而非降级为Redis缓存，可能导致服务可用性低于设计预期。

**修复建议**（二选一）：
1. **推荐**：修改04-安全合规.md文档，将SG同意状态校验的Redis回退路径标记为"不实现"，与代码的更严格策略对齐。理由：01-系统架构设计.md的安全论证更充分（500ms缓存窗口存在确定性安全漏洞）
2. 在consent_checker.py中实现Redis只读缓存回退，但需接受≤60s旧值窗口风险

---

### [SG-FIX-003] docker-compose.dev.yml 主服务(api)缺少对SG的depends_on依赖

**严重程度**：P0-Critical
**审查维度**：功能正确性
**涉及文件**：[docker-compose.dev.yml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/docker-compose.dev.yml)
**文档偏差**：与06-开发环境与进度规划.md"depends_on确保SafetyGateway先于主服务就绪"不一致

**问题描述**：
06-开发环境与进度规划.md要求"主服务depends_on safety-gateway: condition: service_healthy，确保SafetyGateway先于主服务就绪"。但docker-compose.dev.yml中api服务**未定义对safety-gateway的depends_on依赖**。

当前api服务的depends_on仅包含postgres和redis，缺少safety-gateway。这意味着docker-compose up时，api可能在SG健康检查通过前就开始接收请求，导致安全校验不可用。

**影响分析**：系统启动期间可能存在安全校验空窗期，违反"先校验后输出"的A级安全标准。

**修复建议**：
```yaml
api:
  depends_on:
    postgres:
      condition: service_healthy
    redis:
      condition: service_healthy
    safety-gateway:
      condition: service_healthy
```

---

### [SG-FIX-004] E4005错误码子码拆分与文档定义不一致

**严重程度**：P0-Critical
**审查维度**：功能正确性
**涉及文件**：[errors.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/errors.py)、[main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py)
**文档偏差**：与01-系统架构设计.md§3.4 E4005错误码体系不一致

**问题描述**：
01-系统架构设计.md定义E4005子码为：
- E4005A：SafetyGateway不可用（健康检查连续失败）
- E4005B：PostgreSQL不可用
- E4005C：SafetyGateway + 融合层同时不可用

但代码实现为：
- E4005（无子码）：ServiceUnavailableError — 通用不可用
- E4005A1：DesensitizationSafetyViolationError — 脱敏防线降级+敏感数据
- E4005A2：ConsentSafetyViolationError — SG不可用+同意已撤回
- E4005B：DatabaseUnavailableError — 数据库不可用
- E4005C：CombinedUnavailableError — SG+融合层同时不可用

**关键偏差**：
1. 文档的E4005A定义为"健康检查连续失败"，代码的E4005A1/A2分别对应脱敏和同意场景，语义不匹配
2. ServiceUnavailableError使用E4005（无子码），与文档中E4005A的定义产生歧义
3. [main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py)中`sg_endpoint_handler`装饰器将所有非SafetyGatewayError异常包装为ServiceUnavailableError(E4005)，可能掩盖真实错误类型

**影响分析**：前端无法根据文档定义的E4005A正确判断"SG健康检查连续失败"场景，可能导致错误处理逻辑异常。

**修复建议**：
1. 新增`HealthCheckFailureError`错误类，code="E4005A"，与健康检查连续失败场景对齐
2. 将E4005A1/A2的文档定义更新为代码实现的细化拆分
3. sg_endpoint_handler中区分可恢复错误和不可恢复错误，避免统一包装为E4005

---

### [SG-FIX-005] 集成测试完全缺失

**严重程度**：P0-Critical
**审查维度**：功能正确性
**涉及文件**：tests/integration/目录
**文档偏差**：与06-开发环境与进度规划.md"一期集成测试须覆盖7种故障场景"不一致

**问题描述**：
06-开发环境与进度规划.md要求"一期集成测试须覆盖7种故障场景"，但tests/integration/目录下**无任何SG相关集成测试文件**。当前316个测试函数全部为单元测试（使用mock模拟外部依赖），无法验证：
1. SG与PostgreSQL/Redis的真实交互
2. SG与融合层的真实HTTP通信
3. Docker环境下的进程隔离能力
4. 7种降级场景的端到端流程
5. P99延迟在真实网络环境下的表现

**影响分析**：单元测试无法发现集成层面的问题（如连接池泄漏、网络超时、序列化错误等），Gate 0验证项（主服务崩溃时独立返回E4005）无法通过自动化测试验证。

**修复建议**：
1. 创建`tests/integration/sg/`目录
2. 实现至少以下集成测试：
   - `test_sg_postgres_consent.py`：真实PG连接的同意状态校验
   - `test_sg_degradation_e2e.py`：7种降级场景端到端测试
   - `test_sg_process_isolation.py`：进程隔离能力验证
   - `test_sg_health_check_e2e.py`：健康检查端到端验证

---

### [SG-FIX-006] sg_db_retry_max_total_time_seconds 默认值与文档不一致

**严重程度**：P1-High
**审查维度**：功能正确性
**涉及文件**：[sg_config.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_config.py#L44)
**文档偏差**：与00b-统一配置参考手册不一致

**问题描述**：
[sg_config.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_config.py#L44)中`sg_db_retry_max_total_time_seconds`默认值为15.0，但00b-统一配置参考手册中为10.0。偏差50%。

**影响分析**：重试总时间15s超过了SG不可用判定阈值15s（3×(4s+1s)），可能导致在SG已被判定不可用后仍在重试，浪费资源。

**修复建议**：将默认值改为10.0，与文档一致，且确保重试总时间小于不可用判定阈值。

---

### [SG-FIX-007] consent_status为None时进入降级模式而非拒绝

**严重程度**：P1-High
**审查维度**：功能正确性
**涉及文件**：[checker/consent_phase.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker/consent_phase.py#L72)
**文档偏差**：与01-系统架构设计.md"权威防线"定位的严格性可能不一致

**问题描述**：
[consent_phase.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker/consent_phase.py#L72)中，当`consent_status is None`（查询返回None，非超时非DB错误）时，进入降级输出模式（DegradedReason.DB_TIMEOUT）。但作为"同意状态校验权威防线"，未知同意状态应视为不安全，更合理的处理可能是拒绝输出。

**影响分析**：在同意状态完全未知的情况下仍输出降级诊断结果，可能违反"宁可拒绝输出诊断性结论，不可输出未经安全校验的诊断结论"的安全底线原则。

**修复建议**：评估是否应将consent_status=None改为拒绝输出（FAILED），而非降级输出（DEGRADED）。至少应使用独立的DegradedReason（如CONSENT_STATUS_UNKNOWN）而非复用DB_TIMEOUT。

---

### [SG-FIX-008] /health端点对degraded状态返回HTTP 200

**严重程度**：P1-High
**审查维度**：功能正确性
**涉及文件**：[main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py#L119)
**文档偏差**：与01-系统架构设计.md§3.4健康检查机制可能不一致

**问题描述**：
[main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py#L119)中`/health`端点对degraded状态返回HTTP 200 + `X-SG-Status: degraded`头。但主服务健康检查通常只检查HTTP状态码，不解析自定义头。这可能导致主服务认为SG健康，而实际上SG处于降级状态。

Docker Compose健康检查`curl -f http://localhost:9001/health`也仅检查HTTP状态码，degraded状态下curl返回0（成功），Docker认为容器健康。

**影响分析**：主服务可能在SG降级状态下仍发送完整诊断请求，而非触发降级处理流程。

**修复建议**：评估是否应将degraded状态的HTTP状态码改为非200（如207 Multi-Status或503），或在主服务侧增加对X-SG-Status头的解析逻辑。

---

### [SG-FIX-009] 人名检测子串匹配可能产生误报

**严重程度**：P1-High
**审查维度**：代码质量
**涉及文件**：[desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/desensitization.py#L248)

**问题描述**：
[desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/desensitization.py#L248)中`check_person_name`方法使用`name_combo in matched_text`进行子串匹配。例如"张医生"会匹配到"小张医生"中的子串，产生误报。同样，"王主任"会匹配到"老王主任"。

**影响分析**：误报会导致正常诊断文本被标记为敏感数据，触发不必要的降级输出，降低系统可用性。

**修复建议**：改用精确匹配或添加边界检查，如`matched_text == name_combo`或使用正则的`\b`/字符边界。

---

### [SG-FIX-010] 补丁规则加载失败时静默忽略

**严重程度**：P1-High
**审查维度**：代码质量
**涉及文件**：[desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/desensitization.py#L101)

**问题描述**：
[_load_patch_rules](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/desensitization.py#L101)在加载失败时仅记录日志，不抛出异常也不返回错误状态。04-安全合规.md要求"补充规则维护要求：每季度审查更新、双人安全评审、纳入CI自动化测试"，但加载失败时系统静默继续运行，可能导致安全防线缺失而不知情。

**影响分析**：补丁规则（护照号、医保号、车牌号、银行卡号）缺失时，这些敏感信息可能通过SG校验而未被拦截。

**修复建议**：补丁规则加载失败时，至少应将健康检查状态降级为degraded，或在rules_engine健康检查中区分主规则和补丁规则的加载状态。

---

### [SG-FIX-011] 一致性校验矛盾对列表不完整

**严重程度**：P1-High
**审查维度**：功能正确性
**涉及文件**：[checker/consistency_phase.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker/consistency_phase.py#L95)
**文档偏差**：与02-核心流程与知识库.md一致性校验要求可能不充分

**问题描述**：
[consistency_phase.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker/consistency_phase.py#L95)中CONTRADICTION_PAIRS仅3对（良性/恶性、正常/异常、阴性/阳性），超声诊断领域存在更多关键矛盾对未覆盖，如：
- 囊性/实性
- 弥漫性/局灶性
- 高回声/低回声/无回声
- 良性/TI-RADS 4+（分级与定性矛盾）

此外，`_is_negated`方法使用`text.startswith(prefix + word)`，但否定词可能在句子中间而非开头，如"结节表现为非恶性特征"中"非恶性"不在句首。

**影响分析**：矛盾对不完整可能导致一致性校验漏过真实矛盾，否定词检测不精确可能导致假阳性。

**修复建议**：
1. 扩展CONTRADICTION_PAIRS，增加超声诊断领域常见矛盾对
2. 将否定词检测改为在匹配词周围局部窗口内搜索否定前缀，而非仅检查句首
3. 将confidence_divergence阈值0.3配置化（移至sg_config.py）

---

### [SG-FIX-012] _is_negated 否定词检测仅检查句首

**严重程度**：P1-High
**审查维度**：功能正确性
**涉及文件**：[checker/consistency_phase.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker/consistency_phase.py#L103)

**问题描述**：
如SG-FIX-011中所述，`_is_negated`使用`text.startswith(prefix + word)`仅检查文本开头。但医疗文本中否定词常出现在句子中间，如"该结节表现为非恶性特征"中"非恶性"不在句首，导致误判为矛盾。

**修复建议**：改为在匹配词前的局部窗口（如前5个字符）内搜索否定前缀。

---

### [SG-FIX-013] checker.py与checker/__init__.py内容完全重复

**严重程度**：P2-Medium
**审查维度**：代码质量
**涉及文件**：[checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py)、[checker/__init__.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker/__init__.py)

**问题描述**：
[checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py)和[checker/__init__.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker/__init__.py)内容完全相同，都是re-export orchestrator的符号。Python包解析时，`safety_gateway.checker`可能指向checker.py模块或checker/包，取决于导入顺序，存在歧义风险。

**影响分析**：维护时需同步修改两个文件，若遗漏可能导致导入不一致。

**修复建议**：删除顶层checker.py，仅保留checker/__init__.py作为包入口。

---

### [SG-FIX-014] sg_types.py废弃兼容层仍存在

**严重程度**：P2-Medium
**审查维度**：代码质量
**涉及文件**：[sg_types.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_types.py)

**问题描述**：
[sg_types.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_types.py)标记为"此模块已废弃"，但仍被测试文件[test_safety_gateway.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/sg/test_safety_gateway.py#L63)导入使用（`from safety_gateway.sg_types import ConsentStatus`）。

**影响分析**：废弃模块仍在使用，增加维护负担，且新开发者可能误用废弃接口。

**修复建议**：将测试文件中的`from safety_gateway.sg_types import ConsentStatus`改为`from safety_gateway.validation_result import ConsentStatus`，然后删除sg_types.py。

---

### [SG-FIX-015] desensitization.py模块级实例化可能触发副作用

**严重程度**：P2-Medium
**审查维度**：代码质量
**涉及文件**：[desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/desensitization.py#L516)

**问题描述**：
[desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/desensitization.py#L516)末尾`sg_desensitization_engine = get_safety_gateway_desensitization_engine()`在模块级执行，import时即触发引擎实例化和规则加载。若YAML文件不存在或格式错误，import时可能产生异常或回退到默认规则，且此行为对调用方不可见。

**影响分析**：import副作用使测试隔离更困难，且可能在应用启动前就消耗资源。

**修复建议**：改为惰性初始化，首次使用时才创建实例。

---

### [SG-FIX-016] LatencyTracker采样窗口可能过短

**严重程度**：P2-Medium
**审查维度**：代码质量
**涉及文件**：[health/latency_tracker.py](file:///e:/Ultrasound%20Diagnosis%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health/latency_tracker.py#L18)

**问题描述**：
[latency_tracker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health/latency_tracker.py#L18)中MAX_SAMPLES=500，在高并发场景下（如1000 req/s），采样窗口仅覆盖0.5秒，P99统计可能无法反映真实的长尾延迟分布。

**影响分析**：P99统计可能低估真实延迟，导致延迟告警不及时。

**修复建议**：将MAX_SAMPLES配置化（移至sg_config.py），或改为基于时间窗口的采样策略（如保留最近60秒的样本）。

---

### [SG-FIX-017] 禁忌条件匹配仅支持精确字符串匹配

**严重程度**：P2-Medium
**审查维度**：功能正确性
**涉及文件**：[contraindication.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/contraindication.py#L201)

**问题描述**：
[contraindication.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/contraindication.py#L201)中`_match_condition`仅支持精确字符串匹配（`condition in patient_conditions`），代码中已有TODO注释"当前仅支持精确字符串匹配，未来可能需要支持层级匹配"。例如"pregnancy"不会匹配"pregnancy_radiation"。

**影响分析**：若诊断数据中的条件名与规则定义不完全一致，禁忌校验将漏过。

**修复建议**：实现层级匹配或别名映射，或至少在文档中明确声明条件名必须精确匹配的约束。

---

### [SG-FIX-018] 熔断器_update_state()线程安全依赖asyncio单线程约束

**严重程度**：P2-Medium
**审查维度**：代码质量
**涉及文件**：[circuit_breaker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/circuit_breaker.py#L70)

**问题描述**：
[circuit_breaker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/circuit_breaker.py#L70)中`_update_state()`方法修改`self._state`等可变状态，依赖asyncio单线程约束保证线程安全。代码注释已明确警告"若未来引入多线程调用（如run_in_executor），必须添加同步保护"。当前uvicorn --workers 1确保单线程，但若改为多worker部署将产生竞态条件。

**影响分析**：当前无实际风险（单worker部署），但未来扩展时可能引入bug。

**修复建议**：在_update_state()中添加asyncio.Lock保护，或在文档中明确声明"SG必须以单worker模式运行"的约束。

---

### [SG-FIX-019] apply_degraded_output中conclusion_suppressed对所有降级原因硬编码为True

**严重程度**：P2-Medium
**审查维度**：功能正确性
**涉及文件**：[degraded_output.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/degraded_output.py#L161)

**问题描述**：
[degraded_output.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/degraded_output.py#L161)中`conclusion_suppressed=True`对所有降级原因硬编码。但某些降级场景（如DB_TIMEOUT、EDGE_DISCONNECTED）可能不应抑制诊断结论——这些场景下结论本身是安全的，只是元数据不完整。

**影响分析**：过度抑制诊断结论可能降低临床决策支持的有效性。

**修复建议**：根据DegradedReason区分是否应抑制结论，仅对真正涉及安全风险的降级原因（如SENSITIVE_DATA_DETECTED、CONTRAINDICATION_HIT）设置conclusion_suppressed=True。

---

### [SG-FIX-020] Dockerfile中COPY . .在pip install之后

**严重程度**：P3-Low
**审查维度**：代码质量
**涉及文件**：[Dockerfile.safety-gateway.dev](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/Dockerfile.safety-gateway.dev)

**问题描述**：
Dockerfile中`COPY . .`在`RUN pip install`之后，导致代码变更时pip install的Docker缓存层失效，每次代码变更都需重新安装依赖。

**修复建议**：先COPY requirements.txt并pip install，再COPY其余代码，利用Docker缓存层优化构建速度。

---

### [SG-FIX-021] test_safety_gateway.py 239个测试函数集中在单文件

**严重程度**：P3-Low
**审查维度**：代码质量
**涉及文件**：[test_safety_gateway.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/sg/test_safety_gateway.py)

**问题描述**：
239个测试函数集中在单个文件中，可维护性差。建议按功能域拆分为多个文件（如test_desensitization.py、test_consent.py、test_degradation.py等）。

**修复建议**：按功能域拆分测试文件，每个文件不超过50个测试函数。

---

### [SG-FIX-022] 性能基准测试HTTP端到端延迟标记为集成测试

**严重程度**：P3-Low
**审查维度**：功能正确性
**涉及文件**：[test_safety_gateway_performance.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/benchmark/sg/test_safety_gateway_performance.py#L237)

**问题描述**：
TestHTTPEndToEndLatency类标记为`@pytest.mark.integration`，可能不在CI中运行。这是唯一验证HTTP端到端P99<5ms的测试，若不在CI中运行则无法持续验证延迟基准。

**修复建议**：在CI配置中确保集成测试在SG服务可用时运行，或提供独立的性能验证流水线。

---

### [SG-FIX-023] NEGATION_PREFIXES中"无恶性"与"无"重复

**严重程度**：P3-Low
**审查维度**：代码质量
**涉及文件**：[checker/consistency_phase.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker/consistency_phase.py#L31)

**问题描述**：
NEGATION_PREFIXES中同时包含"无"和"无恶性"。由于`_is_negated`使用`text.startswith(prefix + word)`，"无"已覆盖"无恶性"的前缀匹配场景，"无恶性"是冗余项。

**修复建议**：移除"无恶性"，保留更通用的"无"。

---

### [SG-FIX-024] contraindication.py的ContraindicationEngine未注册到registry

**严重程度**：P2-Medium
**审查维度**：代码质量
**涉及文件**：[contraindication.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/contraindication.py#L233)

**问题描述**：
[contraindication.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/contraindication.py#L233)中`get_contraindication_engine()`使用双重检查锁+sg_registry注册，但ContraindicationEngine的`__init__`不接收参数且不调用`load_rules()`。首次通过registry获取的实例`_loaded=False`，直到`check()`被调用时才延迟加载默认规则。这与其他引擎（如ThresholdEngine）的初始化模式不一致。

**影响分析**：首次健康检查`check_rules_engine()`可能返回degraded（因为ContraindicationEngine未加载规则），但实际上规则会在首次check时加载。

**修复建议**：在ContraindicationEngine.__init__中调用_load_default_rules()，或在registry注册时使用带初始化的工厂函数。

---

### [SG-FIX-025] FusionProbe.get_availability存在TOCTOU竞态

**严重程度**：P2-Medium
**审查维度**：代码质量
**涉及文件**：[health/fusion_probe.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health/fusion_probe.py#L60)

**问题描述**：
[FusionProbe.get_availability](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health/fusion_probe.py#L60)中，缓存TTL检查在`_check_lock`内，但`check()`方法（实际执行HTTP请求）不在锁内。多个并发调用可能同时触发HTTP请求，虽然不会导致数据不一致，但可能产生不必要的请求风暴。

**影响分析**：在低并发下无影响，高并发下可能对融合层产生额外负载。

**修复建议**：将check()调用也纳入_check_lock保护范围，或使用singleflight模式确保同时只有一个HTTP请求在执行。

---

## 审查通过项（确认正确实现）

以下关键设计要求经审查确认已正确实现：

| 审查项 | 状态 | 说明 |
|--------|------|------|
| 独立uvicorn实例 | ✅ 通过 | supervisord配置正确，--workers 1 |
| HTTP localhost通信 | ✅ 通过 | FastAPI端点定义完整 |
| 端口9001 | ✅ 通过 | 配置、Docker、supervisord一致 |
| 5阶段校验编排 | ✅ 通过 | Consent→Edge→Desensitization→ContraindicationThreshold→Consistency |
| 入口快速拒绝 | ✅ 通过 | consent_status_at_input参数实现 |
| 直查PostgreSQL | ✅ 通过 | 不使用缓存（比文档更严格） |
| 熔断器保护 | ✅ 通过 | 三态转换逻辑正确 |
| 重试机制 | ✅ 通过 | 指数退避+最大总时间限制 |
| 异构冗余脱敏规则 | ✅ 通过 | 11主规则+4补丁规则，与主规则源模式不同 |
| 规则热更新回滚 | ✅ 通过 | reload_rules中old_rules/old_patterns备份恢复 |
| 降级输出白名单 | ✅ 通过 | 9个白名单字段与文档一致 |
| 降级输出脱敏 | ✅ 通过 | 降级输出仍经脱敏引擎处理 |
| 禁忌分级术语过滤 | ✅ 通过 | FORBIDDEN_GRADING_TERMS + 否定前缀检测 |
| Docker oom_score_adj | ✅ 通过 | docker-compose中-1000配置正确 |
| 内存预算512MB | ✅ 通过 | Docker限制+配置+监控一致 |
| 健康检查三级状态 | ✅ 通过 | healthy/degraded/unhealthy判定逻辑正确 |
| 看门狗端点 | ✅ 通过 | /health/watchdog返回alive+p99_status |
| P99延迟追踪 | ✅ 通过 | LatencyTracker实现完整，百分位算法正确 |
| 安全底线原则 | ✅ 通过 | SG不可用时拒绝所有诊断输出 |
| 先校验后输出 | ✅ 通过 | 编排器按序执行，短路返回 |
| Redis连接池隔离 | ✅ 通过 | db=1，独立SafetyGatewayRedisPool |
| 不导入融合层模块 | ✅ 通过 | 代码中无融合层模块导入 |
| CORS配置 | ✅ 通过 | 仅允许必要来源 |
| 阈值热更新 | ✅ 通过 | Redis Pub/Sub + DB持久化 |
| 递归深度限制 | ✅ 通过 | MAX_RECURSION_DEPTH=20 |
| 线程安全快照模式 | ✅ 通过 | desensitization引擎_rules_lock保护 |

---

## 修复优先级排序

| 优先级 | 修复项 | 严重程度 | 说明 |
|--------|--------|----------|------|
| 1 | SG-FIX-001 | P0-Critical | OOM保护启动脚本不可达 |
| 2 | SG-FIX-003 | P0-Critical | 主服务缺少对SG的depends_on |
| 3 | SG-FIX-004 | P0-Critical | E4005错误码与文档不一致 |
| 4 | SG-FIX-005 | P0-Critical | 集成测试完全缺失 |
| 5 | SG-FIX-002 | P0-Critical | 同意状态Redis回退路径缺失（需决策） |
| 6 | SG-FIX-006 | P1-High | 重试总时间配置偏差 |
| 7 | SG-FIX-007 | P1-High | consent_status=None降级vs拒绝 |
| 8 | SG-FIX-008 | P1-High | /health degraded返回200 |
| 9 | SG-FIX-009 | P1-High | 人名检测子串匹配误报 |
| 10 | SG-FIX-010 | P1-High | 补丁规则加载静默失败 |
| 11 | SG-FIX-011 | P1-High | 矛盾对列表不完整 |
| 12 | SG-FIX-012 | P1-High | 否定词检测仅检查句首 |
| 13 | SG-FIX-013 | P2-Medium | checker.py重复 |
| 14 | SG-FIX-014 | P2-Medium | sg_types.py废弃仍存在 |
| 15 | SG-FIX-015 | P2-Medium | 模块级实例化副作用 |
| 16 | SG-FIX-016 | P2-Medium | LatencyTracker采样窗口 |
| 17 | SG-FIX-017 | P2-Medium | 禁忌条件精确匹配限制 |
| 18 | SG-FIX-018 | P2-Medium | 熔断器线程安全约束 |
| 19 | SG-FIX-019 | P2-Medium | conclusion_suppressed硬编码 |
| 20 | SG-FIX-024 | P2-Medium | ContraindicationEngine未预加载 |
| 21 | SG-FIX-025 | P2-Medium | FusionProbe TOCTOU竞态 |
| 22 | SG-FIX-020 | P3-Low | Dockerfile缓存优化 |
| 23 | SG-FIX-021 | P3-Low | 测试文件拆分 |
| 24 | SG-FIX-022 | P3-Low | 性能测试CI集成 |
| 25 | SG-FIX-023 | P3-Low | 否定前缀冗余项 |
