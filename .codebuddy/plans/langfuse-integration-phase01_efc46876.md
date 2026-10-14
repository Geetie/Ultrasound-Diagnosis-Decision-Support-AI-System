---
name: langfuse-integration-phase01
overview: Langfuse 集成阶段 0+1：打通 OTLP trace 上报（真机判读在 Langfuse UI 可见）+ PHI 脱敏钩子与 AI 语义属性（Generation 升级），按官方生产实践落地，含走查/回写。
todos:
  - id: probe-version
    content: SSH 核对 Langfuse 容器版本 ≥3.63（版本不足则记录并评估升级后再继续）
    status: completed
  - id: probe-tracing
    content: 勘察 tracing.py 的 span 属性记录现状（哪些文本进属性、DiagnosisSpan 用法）与 .env 注入机制
    status: completed
  - id: ui-setup
    content: Use [Skill:autoglm-browser-agent] 浏览器自动化：Langfuse UI（经隧道 30100）注册/登录 owner → 建项目 uai-prod → 取 pk/sk 存服务器 .env 同口径临时文件
    status: completed
  - id: connect-otlp
    content: 服务器 .env 加 OBS_TRACING_ENABLED=true + OTLP Basic headers（pk:sk）→ 重启主服务 → 真机判读 → Langfuse Public API 查 trace 非空（19 号验收①）
    status: completed
  - id: phi-mask
    content: PHI 脱敏 SpanProcessor（导出前正则处理 span 文本属性，复用 SG L3 正则，失败返回原值）+ 单测
    status: completed
  - id: genai-attrs
    content: diagnosis.output span 加 gen_ai.* 语义属性（model/completion）→ Langfuse Generation 渲染
    status: completed
  - id: wrap-ship
    content: 走查 21/21 + 门禁 + quality/vitest 全绿 → A.47 附录 + 19 号 P1 标记 + feature_list + commit/push/sync
    status: completed
---

## 需求：Langfuse 集成阶段 0+1（trace 打通 + PHI 脱敏 + AI 语义属性）

用户已授权（浏览器自动化建项目取 Key），要求按调研定稿的生产实践落地 Langfuse 接入。本轮交付：

1. **阶段 0 打通**：核对 Langfuse 容器版本（≥3.63）→ UI 建项目取 pk/sk → 服务器配置 OBS_TRACING → 真机判读 trace 上报 → Langfuse UI/API 可见完整 trace（19 号验收①）
2. **阶段 1 语义增强**：

- **PHI 脱敏钩子**（生产实践定稿）：纯 OTel 链路在 OTLP 导出前脱敏——自定义 SpanProcessor 正则处理 span 文本属性（复用 SG L3 脱敏正则：姓名/患者 ID/电话），钩子纪律=try/except 包裹失败返回原值（官方：抛异常丢整批）
- **Generation 升级**：`diagnosis.output` span 加 `gen_ai.*` 语义属性（model/prompt/completion），Langfuse 渲染为 LLM Generation

3. **治理项落地**：Data Retention 配置说明 + 备份覆盖（PG/CH/S3）写入文档
4. **验收**：走查 21/21 保持、quality/vitest 全绿、A.47 回写、19 号 P1 采集完成标记

边界：P2 数据集/评分、P3 门控不在本轮（下一批）。

## 技术方案

### 现状依据（已勘察）

- `app/observability/tracing.py`：手写 OTel，8 个 diagnosis span，HTTP OTLP exporter + Basic headers 支持**代码就绪**，仅未启用
- `settings.py:491-495`：`obs_tracing_enabled=False`、endpoint 已指 30100、headers 空
- 服务器：Langfuse v3 三容器健康（30100 health 200）；`.env` 无 OBS_TRACING 配置

### 实施要点

1. **版本核对**：容器 `package.json` version ≥3.63（SDK 兼容底线；不足则列入升级项再继续）
2. **pk/sk 获取**：Playwright 经隧道访问 `127.0.0.1:30100` → 注册/登录 owner → 建项目「uai-prod」→ Settings → API Keys → 记录 pk/sk（凭据只存服务器 .env，不入 repo）
3. **接通**：服务器 `.env` 加 `OBS_TRACING_ENABLED=true` + `OBS_TRACING_OTLP_HEADERS=Authorization=Basic <base64(pk:sk)>`（endpoint 用默认 30100）→ 重启主服务 → 真机判读 → Langfuse Public API（pk/sk 鉴权）查 trace 存在性验证
4. **PHI 脱敏 SpanProcessor**：`tracing.py` 加自定义 `SpanProcessor`（on_end 前遍历 span attributes 文本值做正则替换——失败返回原值，官方纪律「抛异常丢整批」）；单测：含姓名/ID 的属性导出后为脱敏值
5. **Generation 语义**：`SPAN_DIAGNOSIS_OUTPUT` 的 set_attribute 加 `gen_ai.system`/`gen_ai.request.model`/`gen_ai.completion`（Langfuse 按 gen_ai 约定渲染 Generation）
6. **回写**：A.47 附录 + 19 号 P1 标记完成 + feature_list（新增 langfuse-trace-ingestion done）+ progress/handoff

### 验证

- 本机：pytest quality（新增脱敏单测）、vitest 不受影响、走查 21/21
- 真机：判读 → Langfuse Public API `GET /api/public/traces?name=diagnosis.request` 非空 + span 树完整
- 门禁：PASS=24 保持