# SG安全网关轨道系统性审查报告（第九轮）

**审查日期**：2026-05-11
**审查范围**：SafetyGateway轨道全部源码、测试、配置、部署及文档一致性
**审查原则**：独立、客观、深入、批判性
**审查方法**：逐文件静态代码分析 + 设计文档交叉验证 + 降级路径端到端追踪

---

## 一、审查概况

| 维度 | 数值 |
|------|------|
| 核心代码文件 | 18个.py文件，4,219行 |
| 设计文档预估 | 2,500-3,000行 |
| 超出预估 | +41%~+69% |
| 测试用例 | 245个（238单元+7性能） |
| 设计文档预估 | 80-120个 |
| 超出预估 | +104%~+206% |
| 降级场景枚举 | 10种DegradedReason |
| 设计文档要求 | 7种 |
| E4005子类型 | E4005/E4005A1/E4005A2/E4005B/E4005C |

---

## 二、R8遗留修复项验证

| R8遗留项 | 描述 | 验证结果 | 详细分析 |
|----------|------|----------|----------|
| SG-FIX-R8-01 | three_layer_desensitization.py键名不匹配 | ⚠️部分修复 | 第127行做了兼容处理`degraded_mode = check_result.get("degraded_mode", check_result.get("degraded", False))`，但第197-198行仍使用`check_result.get("passed", check_result.get("allowed", True))`和`check_result.get("degraded_mode", check_result.get("degraded", False))`的兼容模式。SG服务端SafetyCheckResponse使用`passed`/`degraded_mode`，客户端_e4005_response()使用`allowed`/`degraded`。两套键名并存，虽然兼容处理降低了运行时风险，但增加了维护复杂度。**根本问题未解决**：应统一为一套键名 |
| SG-FIX-R8-02 | desensitization_degraded语义错误 | ⚠️部分修复 | process_full_pipeline()第185行将`layer1_degraded`（第1层是否降级）传给SG的`desensitization_degraded`参数。但设计文档中"脱敏降级"指"融合层脱敏服务超时/降级"（第二道防线），而非"标准化层脱敏时数据被修改"（第一道防线的正常行为）。第1层`modified=True`（脱敏引擎正常工作）≠"脱敏降级"。**语义仍不正确** |
| SG-FIX-R7-06 | 客户端E4005响应不区分子类型 | ✅已修复 | safety_gateway_client.py第307-321行，`_e4005_response()`现在接受`sub_code`参数，支持E4005A1/A2/B/C子类型。第139-156行从503响应体中提取`code`字段并传递给`_e4005_response()` |
| SG-FIX-R7-04 | 独立姓名脱敏规则覆盖不足 | ⚠️部分修复 | desensitization.py第153-164行有`sg_patient_name`和`sg_patient_name_standalone`两条规则，但`sg_patient_name_standalone`要求"患者/病人/被检者"前缀，无法匹配无前缀的独立姓名。YAML中`sg_patient_name_simple`（行首2-4字中文姓名）已定义但`enabled: false`。医疗人名词典匹配提供了兜底覆盖，但正则规则层仍有缺口 |
| SG-FIX-R7-01 | 服务端响应键名遗漏 | ⚠️部分修复 | three_layer_desensitization.py第131-138行，layer3_result包含`layer`/`desensitized`/`final`/`check_result`/`modified`，但缺少`degraded`键（layer1/layer2都有）。第197-198行通过兼容取值处理了缺失，但layer3结果结构不一致 |

---

## 三、新发现的问题

### P0级（安全关键）

#### SG-FIX-R9-01：consent_checker Redis降级路径与设计文档"SG禁用缓存"要求存在根本偏差

- **严重程度**：P0
- **文件**：[consent_checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/consent_checker.py#L62-L93)
- **设计文档参考**：01-系统架构设计 §3.4 "SafetyGateway拦截点始终直查PostgreSQL，不使用任何缓存" + 04-安全合规 §6 "SafetyGateway输出前直查PostgreSQL获取同意状态快照"
- **问题描述**：consent_checker.py实现了完整的Redis缓存降级路径（第62-93行`_check_redis_cache`，第191-221行多处fallback），当PostgreSQL不可用时降级为Redis只读缓存。虽然代码注释（第5-16行）声称"正常路径直查PG，异常路径降级为Redis只读"与设计文档"不矛盾"，但设计文档的明确要求是"SafetyGateway拦截点**始终直查PostgreSQL，不使用任何缓存**"，且给出了安全论证："500ms缓存窗口内，若患者撤回同意，缓存仍为consented，SafetyGateway复用缓存则漏过撤回"。
- **安全风险**：Redis缓存中的同意状态可能过期（TTL=60s，sg_config.py第47行），患者撤回同意后SG可能从Redis读到旧状态"granted"而放行诊断输出。设计文档明确拒绝了概率论证（"即使500ms远短于诊断处理时间，仍存在确定性安全漏洞"），但代码实现了60s TTL的缓存降级。
- **修复建议**：移除consent_checker中的Redis缓存降级路径。PostgreSQL不可用时直接返回错误（E4005B），而非降级为Redis缓存。这与设计文档"SafetyGateway直查PG"的明确要求一致。如果需要保留Redis降级作为工程折中，必须：(1)将TTL从60s降至500ms；(2)在安全合规文档中明确声明此偏差及风险；(3)确保Redis缓存仅由SG自身写入（而非依赖主服务写入）。
- **修复类型**：代码修复

#### SG-FIX-R9-02：process_full_pipeline()中desensitization_degraded语义错误导致SG误判脱敏状态

- **严重程度**：P0
- **文件**：[three_layer_desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/three_layer_desensitization.py#L162-L186)
- **设计文档参考**：01-系统架构设计 降级策略总表 "融合层脱敏不可用"场景
- **问题描述**：第162行`layer1_degraded = layer1_result.get("degraded", False)`获取第1层（标准化层）的degraded标记。但第78-79行，标准化层结果中`degraded`始终为`False`（硬编码）。第185行将`layer1_degraded`传给SG的`desensitization_degraded`参数。问题：(1)标准化层的`degraded`始终为False，所以SG的`desensitization_degraded`参数永远为False；(2)即使标准化层`modified=True`（脱敏引擎正常工作），这也不等于"脱敏降级"——"脱敏降级"指融合层脱敏服务超时/不可用；(3)第2层（融合层）的`degraded`状态未被传递给SG。
- **安全风险**：SG的`desensitization_degraded`参数永远为False，意味着即使融合层脱敏服务确实降级，SG也无法感知，不会触发E4005A1（脱敏降级+敏感数据→拒绝输出）的保护路径。
- **修复建议**：(1)第2层（融合层）应输出`degraded`标记，表示融合层脱敏服务是否降级；(2)将第2层的`degraded`状态（而非第1层）传递给SG的`desensitization_degraded`参数；(3)如果第2层脱敏服务超时，应设置`degraded=True`。
- **修复类型**：代码修复

#### SG-FIX-R9-03：docker-compose.dev.yml缺少restart策略，SG崩溃后不会自动重启

- **严重程度**：P0
- **文件**：[docker-compose.dev.yml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/docker-compose.dev.yml#L93-L130)
- **设计文档参考**：01-系统架构设计 §3.4 "SafetyGateway独立进程配置supervisor看门狗，崩溃后自动重启（≤5s）" + 06-开发环境 "Docker Compose配置中须定义depends_on + 健康检查"
- **问题描述**：safety-gateway服务未配置`restart`策略。Docker Compose默认restart为"no"，意味着容器崩溃后不会自动重启。设计文档要求supervisor看门狗≤5s重启，但docker-compose.dev.yml中既无restart策略，也未配置supervisor。
- **安全风险**：SG进程崩溃后无法自动恢复，系统将永久处于E4005拒绝状态，直到人工干预。
- **修复建议**：在safety-gateway服务中添加`restart: always`或`restart: unless-stopped`。同时确认supervisor配置（deploy/supervisor/safety-gateway.conf）是否在Docker容器内生效——如果容器内运行supervisor，则需确保supervisor进程为PID 1。
- **修复类型**：配置修复

### P1级（功能偏差）

#### SG-FIX-R9-04：three_layer_desensitization.py键名双轨制增加维护风险

- **严重程度**：P1
- **文件**：[three_layer_desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/three_layer_desensitization.py#L127) + [safety_gateway_client.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/safety_gateway_client.py#L307-L321)
- **设计文档参考**：01-系统架构设计 §3.4 SafetyGateway通信协议
- **问题描述**：SG服务端SafetyCheckResponse使用`passed`/`degraded_mode`，客户端_e4005_response()使用`allowed`/`degraded`。three_layer_desensitization.py第127行和第197-198行使用兼容取值模式`.get("degraded_mode", .get("degraded", False))`。两套键名并存。
- **修复建议**：统一为一套键名。建议以SG服务端SafetyCheckResponse的定义为准（`passed`/`degraded_mode`），客户端_e4005_response()和three_layer_desensitization.py统一使用服务端键名。
- **修复类型**：代码修复

#### SG-FIX-R9-05：COMBINED_FAILURE降级输出中safety_gateway_unavailable=True语义错误

- **严重程度**：P1
- **文件**：[degraded_output.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/degraded_output.py#L113-L116)
- **设计文档参考**：01-系统架构设计 降级策略总表
- **问题描述**：第115行，当降级原因为COMBINED_FAILURE（禁忌+阈值+融合不可用组合）时，设置`safety_gateway_unavailable = True`。但COMBINED_FAILURE场景中SG本身是可用的（正在执行降级输出），不可用的是融合层。`safety_gateway_unavailable=True`暗示SG不可用，与实际不符。
- **安全风险**：前端可能根据`safety_gateway_unavailable=True`显示"安全校验服务不可用"的错误提示，但实际SG正在正常工作（只是融合层不可用+禁忌/阈值故障）。用户可能误以为系统安全防线已完全失效。
- **修复建议**：COMBINED_FAILURE场景应设置`fusion_bypassed=True`（而非`safety_gateway_unavailable=True`），因为SG本身可用，不可用的是融合层。同时可添加`combined_failure=True`标记。
- **修复类型**：代码修复

#### SG-FIX-R9-06：SENSITIVE_DATA_DETECTED降级输出中fusion_degraded=True语义不准确

- **严重程度**：P1
- **文件**：[degraded_output.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/degraded_output.py#L94-L96)
- **设计文档参考**：01-系统架构设计 降级策略总表
- **问题描述**：第95行，当降级原因为SENSITIVE_DATA_DETECTED（融合层可用+检测到敏感数据）时，设置`fusion_degraded = True`。但此场景中融合层是可用的，降级的是SG的输出级别（过滤diagnostic_conclusion），而非融合层本身。"fusion_degraded"按设计文档语义应表示"融合层脱敏服务降级"。
- **修复建议**：SENSITIVE_DATA_DETECTED场景应使用`sg_degraded=True`（SG降级输出）而非`fusion_degraded=True`。或者引入更精确的标记如`output_degraded=True`。
- **修复类型**：代码修复

#### SG-FIX-R9-07：sg_patient_name_simple规则disabled导致无前缀姓名正则检测缺口

- **严重程度**：P1
- **文件**：[desensitization_rules_safety.yaml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/desensitization_rules_safety.yaml#L47-L52) + [desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/desensitization.py#L65-L69)
- **设计文档参考**：01-系统架构设计 §3.4 "结构化身份模式拦截+医疗人名词典匹配，召回率≥85%"
- **问题描述**：YAML中`sg_patient_name_simple`（`^[\u4e00-\u9fa5]{2,4}$`）已定义但`enabled: false`，原因是"dict字段值上下文中误报率高"。当前姓名检测依赖：(1)`sg_patient_name`（需前后缀如"患者：""张医生"）；(2)`sg_patient_name_standalone`（需"患者/病人/被检者"前缀）；(3)医疗人名词典匹配（需姓氏+职称后缀）。无前缀、无后缀的独立中文姓名（如输出中直接出现"张三"）无法被正则规则检测，仅靠词典匹配覆盖。
- **安全风险**：词典匹配仅覆盖"姓氏+职称后缀"组合（如"张医生"），无法检测"张三"这类无职称后缀的姓名。设计文档要求召回率≥85%，但无前缀姓名场景可能低于此阈值。
- **修复建议**：(1)评估医疗人名词典是否可扩展覆盖常见全名（如"张三""李四"等常见姓名）；(2)考虑启用`sg_patient_name_simple`但限制其仅对特定字段（如`patient_name`字段）生效，而非对所有dict值生效；(3)在Gate 0测试集中验证无前缀姓名的召回率。
- **修复类型**：代码修复

#### SG-FIX-R9-08：consent_checker Redis缓存TTL=60s远超设计文档500ms安全窗口

- **严重程度**：P1
- **文件**：[sg_config.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_config.py#L47)
- **设计文档参考**：01-系统架构设计 §3.4 "SafetyGateway禁用缓存的安全论证：500ms缓存窗口内，若患者撤回同意，缓存仍为consented"
- **问题描述**：sg_config.py第47行`sg_consent_cache_ttl_seconds: int = 60`，Redis缓存TTL为60秒。设计文档论证了500ms缓存窗口的风险，但代码实现了60s TTL。即使Redis降级路径被视为工程折中，60s TTL也远超设计文档的安全论证范围。
- **修复建议**：如果保留Redis降级路径，TTL应降至≤2s（考虑PG查询延迟+网络延迟的安全余量）。或者完全移除Redis降级路径（见SG-FIX-R9-01）。
- **修复类型**：配置修复

#### SG-FIX-R9-09：health.py中OOM保护检测在Windows环境下始终返回unknown

- **严重程度**：P1
- **文件**：[health.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health.py#L296-L314)
- **设计文档参考**：01-系统架构设计 §3.4 "oom_score_adj = -1000（禁止OOM Killer杀死）"
- **问题描述**：第298行`/proc/{pid}/oom_score_adj`仅Linux存在。第306-314行`_read_oom_from_docker_compose()`作为fallback读取docker-compose配置。但在Windows开发环境下，两个路径都不可靠：(1)`/proc`不存在；(2)docker-compose.dev.yml路径可能不正确（取决于工作目录）。OOM保护状态在Windows开发环境下始终为`unknown`或`configured`。
- **修复建议**：(1)在health.py中添加Windows环境检测，明确标注OOM保护在Windows下不可验证；(2)在Docker容器内运行时确保`/proc`路径可用；(3)在CI/CD中添加Linux环境下的OOM保护验证测试。
- **修复类型**：代码修复

### P2级（代码质量/风格）

#### SG-FIX-R9-10：代码量4,219行超出设计文档预估2,500-3,000行约41%-69%

- **严重程度**：P2
- **文件**：safety_gateway/ 全部18个文件
- **设计文档参考**：06-开发环境与进度规划 "独立Python package代码量约2,500-3,000行"
- **问题描述**：实际代码4,219行，超出预估上限约41%。主要贡献文件：checker.py(789行)、threshold.py(585行)、desensitization.py(487行)、main.py(408行)、health.py(406行)。threshold.py和health.py的代码量较大，可能包含可精简的逻辑。
- **修复建议**：评估是否需要精简代码。如果功能完整性需要此代码量，应更新设计文档中的预估。
- **修复类型**：文档更新

#### SG-FIX-R9-11：测试用例245个超出设计文档预估80-120个约104%-206%

- **严重程度**：P2
- **文件**：tests/unit/test_safety_gateway.py(238个) + tests/benchmark/test_safety_gateway_performance.py(7个)
- **设计文档参考**：06-开发环境与进度规划 "测试用例约80-120个"
- **问题描述**：测试用例数量远超预估，但这是正向偏差——更充分的测试覆盖。CI增量时间需验证是否在2-3分钟内。
- **修复建议**：更新设计文档中的测试用例预估。验证CI增量时间。
- **修复类型**：文档更新

#### SG-FIX-R9-12：circuit_breaker.py _update_state()非线程安全

- **严重程度**：P2
- **文件**：[circuit_breaker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/circuit_breaker.py#L70-L91)
- **问题描述**：第70-91行`_update_state()`方法修改`self._state`等可变状态，但未持有`self._lock`。代码注释（第73-78行）声明"依赖asyncio事件循环的单线程特性保证线程安全"，但如果未来引入`run_in_executor`或多线程调用，将产生竞态条件。当前在纯asyncio环境下安全，但缺乏防御性保护。
- **修复建议**：在_update_state()中添加注释或断言，明确声明asyncio单线程前提。或添加_lock保护。
- **修复类型**：代码修复

#### SG-FIX-R9-13：redis_pool.py使用ClassVar存储客户端实例，多事件循环场景可能泄漏

- **严重程度**：P2
- **文件**：[redis_pool.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/redis_pool.py#L34-L36)
- **问题描述**：第34-36行使用ClassVar存储`_client`/`_init_lock`/`_lock_loop_id`。第39-49行`_get_lock()`检测事件循环变化并重建lock，但旧lock可能仍被其他协程持有。在uvicorn reload场景下，旧事件循环的客户端可能未正确关闭。
- **修复建议**：在close()方法中添加事件循环ID检查，确保关闭的是当前事件循环的客户端。
- **修复类型**：代码修复

---

## 四、设计文档一致性审查

### 4.1 P0：01-系统架构设计.md

| 审查项 | 设计要求 | 实现现状 | 偏差 | 修复项 |
|--------|----------|----------|------|--------|
| 进程架构 | 独立uvicorn实例，HTTP localhost通信 | ✅ main.py uvicorn.run()，Dockerfile独立进程 | 无 | - |
| HTTP通信P99<5ms | HTTP通信开销P99<5ms | ✅ benchmark测试存在，keep-alive+连接池 | 需实测验证 | - |
| 端到端P99<50ms | 端到端处理延迟P99<50ms | ✅ health.py P99统计，配置warning=4ms/critical=5ms | ⚠️配置阈值针对HTTP通信开销，端到端50ms阈值未在配置中体现 | SG-FIX-R9-14 |
| 核心校验 | 脱敏+禁忌+同意状态+一致性 | ✅ checker.py四阶段校验 | 无 | - |
| E4005体系 | E4005/A1/A2/B/C | ✅ errors.py完整定义，main.py异常处理器映射503 | 无 | - |
| OOM保护 | oom_score_adj=-1000 | ✅ docker-compose.dev.yml + start_safety_gateway.sh + health.py检测 | ⚠️Windows环境检测不可靠 | SG-FIX-R9-09 |
| 安全等级A | 先校验后输出，零容忍 | ✅ checker.py流程：四阶段校验→PASSED才输出 | 无 | - |
| 降级路径 | 融合层不可用→仅观察描述+fusion_bypassed | ✅ degraded_output.py白名单过滤 | ⚠️fusion_bypassed标记仅在FUSION_UNAVAILABLE场景设置，COMBINED_FAILURE场景设置safety_gateway_unavailable=True（语义错误） | SG-FIX-R9-05 |
| 融合层判定 | 基于进程可达性(HTTP /health) | ✅ health.py get_fusion_availability() | 无 | - |
| NER方案 | 结构化身份模式+医疗人名词典，不依赖ML | ✅ desensitization.py纯正则+词典匹配 | ⚠️无前缀姓名检测缺口 | SG-FIX-R9-07 |
| 内存预算 | ≤512MB | ✅ docker-compose.dev.yml 512M限制 + health.py内存检测 | 无 | - |
| SG禁用缓存 | SafetyGateway直查PG，不使用缓存 | ❌ consent_checker.py实现Redis降级路径 | 根本偏差 | SG-FIX-R9-01 |
| conclusion_suppressed | 融合层主动抑制时SG跳过diagnostic_conclusion过滤 | ✅ checker.py第512-516行 | 无 | - |
| 幻觉严重 | hallucination_severity=severe时附加安全标记 | ✅ checker.py第369-372行 | 无 | - |
| 规则热更新 | Redis Pub/Sub同步+回滚 | ✅ main.py _rules_sync_subscriber() + desensitization.py reload_rules() | 无 | - |

#### SG-FIX-R9-14：端到端P99<50ms阈值未在配置中体现

- **严重程度**：P1
- **文件**：[sg_config.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_config.py#L43-L44)
- **设计文档参考**：01-系统架构设计 §3.4 "SafetyGateway端到端处理延迟P99须<50ms"
- **问题描述**：sg_config.py第43-44行仅配置了HTTP通信开销的P99阈值（warning=4ms, critical=5ms），但设计文档要求的端到端P99<50ms阈值未在配置中体现。health.py的P99统计记录的是完整校验流程延迟，但缺少50ms的阈值配置和告警。
- **修复建议**：添加`sg_e2e_p99_latency_warning_threshold_ms`和`sg_e2e_p99_latency_critical_threshold_ms`配置项，默认值分别为40ms和50ms，并在health.py中添加端到端P99状态判断。
- **修复类型**：代码修复

### 4.2 P0：04-安全合规.md

| 审查项 | 设计要求 | 实现现状 | 偏差 | 修复项 |
|--------|----------|----------|------|--------|
| 三重防线 | SG为第三道防线：结构化身份模式拦截 | ✅ desensitization.py纯正则+词典 | 无 | - |
| 异构冗余 | desensitization_rules_safety.yaml独立编写 | ✅ YAML规则与主规则源模式不同 | 无 | - |
| 补充规则 | desensitization_rules_safety_patch.yaml独立于主规则源 | ✅ patch.yaml包含护照号/医保号/车牌号/银行卡号 | 无 | - |
| 同意校验 | 两阶段：入口快速拒绝+输出前直查PG | ✅ checker.py入口快速拒绝 + consent_checker.py直查PG | ⚠️Redis降级路径违反"禁用缓存" | SG-FIX-R9-01 |
| 类型化脱敏 | [REDACTED:{类型}]格式 | ✅ desensitization.py replacement字段 | 无 | - |
| 双重降级 | fusion_degraded+safety_gateway_unavailable标记 | ⚠️degraded_output.py标记语义有误 | COMBINED_FAILURE场景标记错误 | SG-FIX-R9-05 |

### 4.3 P1：06-开发环境与进度规划.md

| 审查项 | 设计要求 | 实现现状 | 偏差 | 修复项 |
|--------|----------|----------|------|--------|
| 代码组织 | 独立Python package safety_gateway/ | ✅ | 无 | - |
| 代码量 | 约2,500-3,000行 | ❌ 4,219行 | +41%~+69% | SG-FIX-R9-10 |
| 测试用例 | 约80-120个 | ❌ 245个 | +104%~+206% | SG-FIX-R9-11 |
| 端口 | SAFETY_GATEWAY_PORT=9001 | ✅ sg_config.py默认9001 | 无 | - |
| 健康检查 | http://localhost:9001/health | ✅ main.py /health端点 | 无 | - |
| Gate 0 | 主服务崩溃时独立返回E4005 | ✅ 测试存在 | 需实测验证 | - |
| Docker Compose | depends_on+healthcheck | ✅ docker-compose.dev.yml | ⚠️缺少restart策略 | SG-FIX-R9-03 |
| OOM保护 | oom_score_adj=-1000 | ✅ docker-compose.dev.yml | 无 | - |

---

## 五、降级场景端到端验证

### 5.1 DegradedReason覆盖矩阵

| # | DegradedReason | 触发路径 | checker.py代码位置 | 预期行为 | 验证结果 |
|---|----------------|----------|-------------------|----------|----------|
| 1 | SENSITIVE_DATA_DETECTED | 融合层可用+脱敏检测到敏感数据 | _check_desensitization_phase第557-574行 | DEGRADED+降级输出 | ✅路径正确 |
| 2 | DB_TIMEOUT | 同意状态查询超时 | _check_consent_phase第430-444行 | DEGRADED+降级输出 | ✅路径正确 |
| 3 | CONTRAINDICATION_HIT | 检测到禁忌条件 | _check_contraindication_threshold_phase第642-662行 | DEGRADED+降级输出 | ✅路径正确 |
| 4 | CONFIDENCE_BELOW_THRESHOLD | 置信度低于阈值 | _check_contraindication_threshold_phase第664-682行 | DEGRADED+降级输出 | ✅路径正确 |
| 5 | FUSION_UNAVAILABLE | 融合层不可用（无其他故障） | _check_contraindication_threshold_phase第684-698行 | DEGRADED+仅观察描述 | ✅路径正确 |
| 6 | COMBINED_FAILURE | 禁忌+阈值+融合不可用组合 | _check_contraindication_threshold_phase第619-640行 | DEGRADED+组合降级 | ✅路径正确，但标记语义有误（SG-FIX-R9-05） |
| 7 | EDGE_DISCONNECTED | 边缘后端断连 | _check_edge_connectivity第485-499行 | DEGRADED+本地降级 | ✅路径正确 |
| 8 | FUSION_UNAVAILABLE_SENSITIVE_DETECTED | 融合层不可用+敏感数据 | _check_desensitization_phase第521-535行 | FAILED+E4005A1 | ✅路径正确 |
| 9 | DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED | 融合层脱敏降级+敏感数据 | _check_desensitization_phase第537-555行 | FAILED+E4005A1 | ✅路径正确，但desensitization_degraded参数永远为False（SG-FIX-R9-02） |
| 10 | SG_UNAVAILABLE_CONSENT_WITHDRAWN | SG不可用+患者撤回同意 | _check_consent_phase第391-404行 | FAILED+E4005A2 | ✅路径正确 |

### 5.2 设计文档7种降级场景覆盖分析

设计文档要求的7种降级场景与代码10种DegradedReason的映射：

| 设计文档场景 | 对应DegradedReason | 覆盖 |
|-------------|-------------------|------|
| 融合层全部不可用 | FUSION_UNAVAILABLE | ✅ |
| SafetyGateway可用+融合层脱敏不可用 | DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED | ✅ |
| 脱敏校验服务超时 | SENSITIVE_DATA_DETECTED | ✅ |
| 禁忌校验命中 | CONTRAINDICATION_HIT | ✅ |
| 置信度低于阈值 | CONFIDENCE_BELOW_THRESHOLD | ✅ |
| 数据库不可用 | DB_TIMEOUT | ✅ |
| 组合故障 | COMBINED_FAILURE | ✅ |

额外3种DegradedReason（EDGE_DISCONNECTED、FUSION_UNAVAILABLE_SENSITIVE_DETECTED、SG_UNAVAILABLE_CONSENT_WITHDRAWN）为设计文档场景的细化，合理且必要。

---

## 六、测试逻辑审查

| 审查项 | 结果 | 说明 |
|--------|------|------|
| TL-01 测试覆盖率 | ✅ 245个测试用例，远超80-120个预估 | 238单元+7性能 |
| TL-02 降级路径专项测试 | ✅ TestDegradedPathSevenScenarios类覆盖7种场景 | 需验证10种DegradedReason是否全部覆盖 |
| TL-03 Mock正确性 | ⚠️ 需逐个验证 | 关键mock：consent_checker、redis_pool、db_session |
| TL-04 测试隔离性 | ✅ _reset_safety_gateway_checker()重置单例 | 需验证所有测试都调用reset |
| TL-05 边界条件 | ✅ TestBoundaryConditions类存在 | 需验证空值/极值覆盖 |
| TL-06 性能测试 | ✅ test_safety_gateway_performance.py存在 | 7个性能测试，含P99延迟测试 |
| TL-07 集成测试 | ✅ TestHTTPEndToEndLatency类存在 | 需验证Docker环境测试 |

---

## 七、部署配置审查

| 审查项 | 结果 | 说明 |
|--------|------|------|
| DC-01 docker-compose.dev.yml完整性 | ⚠️ | 缺少restart策略（SG-FIX-R9-03） |
| DC-02 Dockerfile正确性 | ✅ | 独立构建，依赖安装正确 |
| DC-03 supervisor配置 | ✅ | safety-gateway.conf存在，autorestart=true |
| DC-04 start_safety_gateway.sh | ✅ | OOM保护设置正确（echo -1000 > /proc/self/oom_score_adj） |
| DC-05 restart策略缺失 | ❌ | docker-compose.dev.yml未配置restart: always（SG-FIX-R9-03） |
| DC-06 主服务depends_on | ✅ | api服务定义了depends_on safety-gateway condition: service_healthy |

---

## 八、规则异构冗余验证

| 审查项 | 结果 | 说明 |
|--------|------|------|
| HR-01 desensitization_rules_safety.yaml与主规则源对比 | ✅ | 每条规则的description中标注了异构差异（如"精确日期格式校验""整体匹配+号段校验"等），正则模式确实不同 |
| HR-02 patch.yaml补充规则独立性 | ✅ | 包含护照号/医保号/车牌号/银行卡号4条补充规则，主规则源未覆盖 |
| HR-03 contraindication_rules_safety.yaml | ✅ | 按器官分类+全局规则，条件枚举清晰 |
| HR-04 medical_person_names.yaml | ✅ | 姓氏+职称后缀组合，默认覆盖20个常见姓氏+8个职称 |

---

## 九、修复项汇总

### SafetyGateway轨道应当进行的修复项

| 编号 | 问题描述 | 严重程度 | 修复类型 |
|------|----------|----------|----------|
| SG-FIX-R9-01 | consent_checker Redis降级路径与设计文档"SG禁用缓存"要求存在根本偏差 | P0 | 代码修复 |
| SG-FIX-R9-02 | process_full_pipeline()中desensitization_degraded语义错误导致SG误判脱敏状态 | P0 | 代码修复 |
| SG-FIX-R9-03 | docker-compose.dev.yml缺少restart策略，SG崩溃后不会自动重启 | P0 | 配置修复 |
| SG-FIX-R9-04 | three_layer_desensitization.py键名双轨制增加维护风险 | P1 | 代码修复 |
| SG-FIX-R9-05 | COMBINED_FAILURE降级输出中safety_gateway_unavailable=True语义错误 | P1 | 代码修复 |
| SG-FIX-R9-06 | SENSITIVE_DATA_DETECTED降级输出中fusion_degraded=True语义不准确 | P1 | 代码修复 |
| SG-FIX-R9-07 | sg_patient_name_simple规则disabled导致无前缀姓名正则检测缺口 | P1 | 代码修复 |
| SG-FIX-R9-08 | consent_checker Redis缓存TTL=60s远超设计文档500ms安全窗口 | P1 | 配置修复 |
| SG-FIX-R9-09 | health.py中OOM保护检测在Windows环境下始终返回unknown | P1 | 代码修复 |
| SG-FIX-R9-10 | 代码量4,219行超出设计文档预估2,500-3,000行约41%-69% | P2 | 文档更新 |
| SG-FIX-R9-11 | 测试用例245个超出设计文档预估80-120个约104%-206% | P2 | 文档更新 |
| SG-FIX-R9-12 | circuit_breaker.py _update_state()非线程安全 | P2 | 代码修复 |
| SG-FIX-R9-13 | redis_pool.py使用ClassVar存储客户端实例，多事件循环场景可能泄漏 | P2 | 代码修复 |
| SG-FIX-R9-14 | 端到端P99<50ms阈值未在配置中体现 | P1 | 代码修复 |

### 修复优先级排序

**P0（必须立即修复）**：
1. SG-FIX-R9-01：移除Redis缓存降级或降至≤2s TTL + 安全合规文档声明偏差
2. SG-FIX-R9-02：修正desensitization_degraded语义，传递第2层（融合层）脱敏降级状态
3. SG-FIX-R9-03：添加restart: always到docker-compose.dev.yml

**P1（应尽快修复）**：
4. SG-FIX-R9-05：修正COMBINED_FAILURE标记语义
5. SG-FIX-R9-06：修正SENSITIVE_DATA_DETECTED标记语义
6. SG-FIX-R9-14：添加端到端P99<50ms阈值配置
7. SG-FIX-R9-04：统一键名为服务端定义
8. SG-FIX-R9-08：降低Redis缓存TTL或移除
9. SG-FIX-R9-07：评估无前缀姓名检测方案
10. SG-FIX-R9-09：添加Windows环境OOM检测说明

**P2（可延后修复）**：
11. SG-FIX-R9-10：更新设计文档代码量预估
12. SG-FIX-R9-11：更新设计文档测试用例预估
13. SG-FIX-R9-12：circuit_breaker线程安全注释
14. SG-FIX-R9-13：redis_pool多事件循环处理

---

## 十、审查结论

### 10.1 整体评估

SafetyGateway轨道的核心架构设计（进程隔离、四阶段校验、10种降级场景、E4005错误码体系）实现完整，与设计文档的核心要求基本一致。代码质量整体良好，模块职责清晰，线程安全措施到位。

### 10.2 关键风险

1. **SG同意状态Redis缓存降级**（SG-FIX-R9-01/R9-08）是与设计文档最根本的偏差。设计文档明确论证了"SG禁用缓存"的安全必要性，但代码实现了60s TTL的Redis降级路径。此偏差可能导致患者撤回同意后SG仍放行诊断输出。

2. **desensitization_degraded语义错误**（SG-FIX-R9-02）导致SG的E4005A1保护路径（脱敏降级+敏感数据→拒绝输出）实际上无法被触发，因为`desensitization_degraded`参数永远为False。

3. **docker-compose缺少restart策略**（SG-FIX-R9-03）意味着SG崩溃后无法自动恢复，与设计文档"supervisor看门狗≤5s重启"的要求不符。

### 10.3 正面发现

1. 进程隔离架构实现完整：独立uvicorn实例、HTTP通信、Docker独立容器
2. 四阶段校验流程（同意→边缘→脱敏→禁忌/阈值/一致性）逻辑清晰
3. 降级场景覆盖全面：10种DegradedReason覆盖了设计文档要求的7种场景并做了合理细化
4. 规则异构冗余实现正确：YAML规则与主规则源模式不同，patch.yaml包含独立补充规则
5. 健康检查完整：6个组件检查+P99延迟统计+OOM保护检测+内存预算监控
6. 测试覆盖充分：245个测试用例远超预估
