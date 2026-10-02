# 接线与集成系统性审计报告

> 审计日期：2026-10-02 ｜ 方法：4 路并行代码探索（生产者-消费者配对、app.state/DI 装配、前后端契约、跨进程契约）+ 对全部 P0 发现逐一人工复核。
> 结论分级：**P0 = 用户可见/安全链路断裂**；P1 = 静默失效；P2 = 单侧存在/卫生问题。

## 一、总体结论

诊断主链路、导航 WebSocket 数据面、报告四段流、safety_gateway HTTP 契约、`?request_id=` 契约均**闭合且与 SAD 一致**（见第五节）。但发现 **5 处 P0 脱钩、8 处 P1 脱钩**，其中前端会话三连与 WS 鉴权两处会让"登录后的核心闭环"（建会话→实时通道）直接不可用。

---

## 二、P0 — 生产者/消费者脱钩（已逐一复核确认）

### P0-1 前端会话接口三连 404（路径+方法双错）
- 前端调用：`frontend/src/services/sessionService.ts:9,14,19` → `POST /api/v1/session`、`GET /api/v1/session/{id}`、`POST /api/v1/session/{id}/end`
- 后端实际：`app/api/v1/session.py:48,83,96` 注册为 **`/api/v1/sessions`**（复数，`app/main.py:631`），且无 `/end`——正确契约是 `PATCH /sessions/{id}` + `{status}`（`app/models/schemas.py:628-630`）
- 调用链真实存在：`hooks/useSession.ts:3,51,86,104` → `pages/SessionCreatePage.tsx:26`，**用户可见故障**；且会话建不起来 → `sessionId` 缺失 → WS 双通道也不会建立。
- `hooks/useRealtimeConnection.ts:24` 注释仍引用不存在的 `/session/new`，属同一脱钩的陈旧痕迹。

### P0-2 WS 双通道鉴权脱钩：前端发 JWT，后端只认服务端签发的 nav token
- 前端：`frontend/src/websocket/DualWebSocketManager.ts:183-191,229-237` 发 `{type:'auth', session_id, token}`，token 来自 `hooks/useRealtimeConnection.ts:31` 的 `localStorage.access_token`（**JWT**）。
- 后端 `app/api/websocket/nav.py:688-690`：只认 ① `device_id+api_key`（查库校验，:677）② 服务端内部签发 token（:683-684 → `AuthTokenManager._tokens` 内存表，:330-363）。该 token **只在 WS 认证成功后**由 :709 经 AuthOK 下发，无 HTTP 端点可预先获取。
- JWT 与 `secrets.token_urlsafe` 随机串永远不等 → 每次连接 5 秒 auth 阶段被 `4001` 关闭。**路径通、鉴权链路断**。（edge 端走 api_key 路径，不受影响。）

### P0-3 同意撤回事件：双投递 + 缺 `withdrawn_at` 必进死信
- 消费端契约：`app/security/consent_withdrawal_consumer.py:345` 要求 `withdrawn_at`（`datetime.fromisoformat`）。
- 生产者 A【合规】：`app/repositories/consent_repository.py:151-164` xadd 含全字段 → 正常消费。
- 生产者 B【脱钩】：`app/api/v1/consent.py:91-96` 在 `create_consent` 之后**再次** xadd，但**缺 `withdrawn_at`** → 解析必抛 → 进死信流（consumer.py:428）。即每次撤回 = 1 条正常 + 1 条必败，死信被系统性污染。

### P0-4 edge 设备管理 4 条请求全部脱钩 → 设备系统性假隔离
- `edge/device_manager.py:139` register：字段只发 `{"device_id"}`，后端 `DeviceRegisterRequest` 要求 device_name/device_type/location/jetpack_version（`app/api/v1/device.py:34-45`）→ 422；且后端要求 `RoleChecker(["SYSTEM_ADMIN"])` JWT，edge 只带 `X-Device-Id/X-Device-Token`（`edge/device_auth.py:30-31`），后端无该头处理逻辑 → 401/403。
- `edge/device_manager.py:153,184` → `POST /api/v1/admin/device/heartbeat`、`/health`：**后端不存在**（device.py 仅 4 端点；心跳仅存在于 WS 层 `nav.py:572`）。
- `edge/ota_client.py:157` → `GET /api/v1/admin/device/ota/{package}/{version}`：**后端不存在**，compose/deploy 亦无独立 OTA 服务器。
- 失败被 edge 吞掉记 `record_failure`（:142-161），**连续 3 次失败进入隔离态** → 上线即系统性假隔离。

### P0-5 告警升级循环从未启动：critical webhook 永不发出
- `app/observability/alert_escalation/manager.py:213-237`：`AlertEscalationManager.start()` 驱动 webhook flush + 超时升级循环。
- 生产侧已接线（`app/main.py:95-120` CUSUM/熔断回调、`app/observability/vm_backup.py:39-41`），但 `start()` 在 app 内**零调用**（shutdown 亦无 stop）→ `_pending_webhooks` 只进不出，**医疗安全类 critical 告警 webhook 永不投递**、升级/提醒逻辑永不执行。

---

## 三、P1 — 静默失效的接线

| # | 问题 | 证据 |
|---|------|------|
| P1-1 | **脱敏规则同步频道名不匹配**：后端发布 `{prefix}:desens:rules:update`（`ultrasound:desens:rules:update`），gateway 订阅**裸名** `desensitization_rules_update` → 规则变更永远同步不到 gateway | `app/config/redis_keys.py:75`、`app/security/desensitization.py:364-370` vs `safety_gateway/main.py:35` |
| P1-2 | **审计链周期校验未接线**：`start_periodic_verification`（含 `verify_all_chains`）app 内零调用，lifespan 未启动。ADR-6"断链即可定位篡改点"运行期不生效 | `app/services/audit_service.py:200-231`；仅测试引用 |
| P1-3 | **obs 看板多处指标无生产者**：`assessment_latency`、`severe/overall/high_risk_hallucination_rate`、`retrieval_empty_rate` 读取侧存在、setter 无生产调用 → 面板恒空；CacheMonitor 整条链（record_* 零调用 + prometheus sync 未启动）失效 | `app/observability/obs_dashboard.py:133-146,161,168-179` vs `collector.py:87,115-125`、`cache_monitor/monitor.py` |
| P1-4 | **DI 层整体死代码**：`app/dependencies.py` 中 `get_dispatcher/get_fusion_engine/get_rag_service/get_monitor_agent/get_knowledge_agent/get_inference_engine/get_gpu_schedule/get_safety_gateway_client/get_device_repo` 全库无 Depends 调用；实际消费走 `diagnosis_pipeline.py:418,467,938` 的 getattr 兜底。T-GA-INT-01 宣称的注入层名存实亡 | definitions `app/dependencies.py:39-128` |
| P1-5 | **死 app.state 属性**：`gpu_schedule`（main.py:80）、`consent_consumer`（main.py:88，且失败分支不赋 None，shutdown 用局部变量）、`metrics_collector`（wiring.py:40）、`diagnosis_workers` 全库无生产读取方 | — |
| P1-6 | **retry 跳过 Self-RAG 语义未生效**：`ultrasound:diagnosis:retry_skip_self_rag:{id}` 只写不读，全库无读取方 | 写 `app/api/v1/diagnosis.py:246-255` |
| P1-7 | **容器主机名脱钩**：compose.dev 的 api 容器未设 `SAFETY_GATEWAY_HOST`/`VLLM_HOST`/`LLAMA_CPP_HOST`，settings 默认 `localhost`（`app/config/settings.py:17,141,144`）→ 容器内请求自指。另 api 用 Redis db0、gateway 用 db1、settings 默认 db1，共享键（脱敏 HMAC/令牌映射）有隔离隐患 | `docker-compose.dev.yml:77,115-117` |
| P1-8 | **阈值更新流无生产者**：gateway 消费 `sg:threshold:update`，backend 定义了键但全库无 xadd，gateway 自身 publish 无入口；PG 直写阈值路径反而闭合 | `safety_gateway/threshold_subscriber.py:12` vs `app/config/redis_keys.py:140,221` |

## 四、P2 — 单侧存在 / 与文档偏差

1. **DLQ 只写不读**：`DIAGNOSIS_DLQ` 写入 `diagnosis_worker.py:247-250`，无任何消费者/运维出口（叠加 P0-3 的死信污染）。
2. **两个通知频道无订阅者**：`CONSENT_WITHDRAWAL_NOTIFICATION`（consumer.py:87-90 publish）、`edge_stop_ai_annotation`（:106-108 publish）——edge/ 不订阅后者，"撤回后停止边缘标注"语义实际由前端/轮询兜底还是悬空，需产品确认。
3. **`SchedulingAgentHandler`**（consumer.py:161-167）显式 `raise NotImplementedError`，启用即炸，属半成品接入。
4. **ADR-4 偏差**：白名单实际 **4 份**（`edge/safety.py:28-54` + safety_gateway 三处），逐项 diff **完全一致**；一致性测试 `tests/unit/edg/test_whitelist_consistency.py` 真实 import 四处定义（无复制常量），有效。但 SAD 说第三份在"app/security 脱敏层"——实际该层无导航白名单，文档描述失真；另有**第 5 份游离副本** `app/config/settings.py:19-22` 仅 8 项且零引用，建议删除。
5. **孤儿 settings / validator 缺口**：`rag_top_k`（specialist 用硬编码 `DEFAULT_RAG_TOP_K=5`，`app/agents/specialist/base.py:85`）、`int_pipeline_total_timeout_ms`、`obs_metrics_scrape_interval` 等无读取；validator 未覆盖 `int_worker_pool_size`、`consent_withdrawal_consumer_enabled`、SG 地址等接线开关。
6. **前端 refresh_token 从不使用**（`services/api.ts:26-31` 存了不刷，401 直接踢回登录）；`/diagnosis/multi-organ` 前端无入口；`/speech/health`、`/auth/login` 等纯闲置。
7. SAD"16 个路由模块"核对**一致**（main.py imports 恰 16 个 v1 router + 2 个 WS）。

---

## 五、已闭合链路（正面确认，与 SAD 一致）

- **诊断主链路**：submit/retry（`diagnosis.py:228,363`）+ risk_flag=high 自动升级（`navigation_upgrade.py:381-383`）→ `DIAGNOSIS_PENDING_QUEUE`（统一走 `RedisKey` 常量，无字符串漂移）→ worker BLPop（`diagnosis_worker.py:127`）→ `run_diagnosis_pipeline`（签名匹配）→ 结果落库 + Redis 缓存 + WS 通知 → 前端 `GET /diagnosis/result/{id}` 回查。闭合。
- **导航数据面**：edge `nav_result` 消息（`edge/websocket_server.py:174-206`）与 `EdgeNavResultMessage`（`app/models/schemas.py:495-512`）逐字段一致；`ws:nav:*` pub/sub fanout 订阅/发布同源自洽。
- **`?request_id=` 契约**：两条提交路径带参（`ExaminationPage.tsx:61`、`AppLayout.tsx:90`）→ 结果页消费 + 轮询（`DiagnosisPage.tsx:39,84`）→ 守卫测试 `DiagnosisFlowWiring.test.ts` 真实钉住。闭合。
- **报告四段流**：草稿→签名→推送→校验五个端点与 `ReportPage.tsx:82-152` 一一配对。闭合。
- **safety_gateway HTTP 契约**：client 四个调用（check/consent-status/consistency/contraindication）路径+字段与 gateway 端逐一匹配；**E4005B**（PG 不可用拒输出）gateway→client 透传完整；warmup/健康探测指向真实 `/health`。闭合。
- **指标导出链**：`/metrics` + VictoriaMetrics 推送已接线（`main.py:167-169`，历史断链已修）。

## 六、修复优先级建议

1. 立即：P0-1（改 sessionService 三行 + endSession 改 PATCH）、P0-2（前端改用 device api_key 或后端补 JWT→nav token 交换端点）、P0-5（lifespan 启停 AlertEscalationManager）。
2. 紧随：P0-3（consent.py 补 `withdrawn_at` 并去重投递）、P0-4（后端补 heartbeat/health/ota 路由或 edge 侧摘除 + register 契约对齐）、P1-1（频道名统一）。
3. 排期：P1-2/3/6（三条"安全/可观测静默失效"）、P2-1/2（DLQ 与无订阅频道，先与产品确认语义再决定补消费或删除）。
4. 卫生：P1-4/5（DI 死代码：要么让 endpoint 真正走 Depends，要么删工厂改用显式 getattr 兜底注释）、SAD ADR-4 描述修正、游离白名单副本删除、validator 补接线开关校验。
