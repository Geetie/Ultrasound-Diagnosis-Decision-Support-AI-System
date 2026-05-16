# 前端系统性开发优化与架构重构实施计划

## 项目概况

- **项目**：超声诊疗决策支持AI系统 — 前端
- **技术栈**：React 19 + TypeScript 6 + Vite 8 + Zustand 5 + Ant Design 6 + CornerstoneJS + Tailwind CSS
- **当前阶段**：Phase 0A（部分功能占位），需推进至 Phase 0B 完整功能
- **计划目标**：系统性开发优化 + 全面架构重构，遵循现代前端最佳实践

---

## 第一阶段：关键 Bug 修复与安全加固（P0）

> 医疗系统核心功能缺陷与安全隐患，必须首先解决

### 1.1 修复 DicomToolbar WW/WL Slider 不生效（功能性 Bug）

**问题**：Slider onChange 仅更新 Zustand store 的 viewport 值，未调用 cornerstone 的 `setViewport()` 实际改变影像显示。store 与 cornerstone 渲染状态脱节。

**修复方案**：
- 在 `DicomToolbar` 的 Slider onChange 中，通过 `cornerstone.setViewport(element, viewport)` 同步更新影像
- 建立 store → cornerstone 的单向数据流：store 变更 → 同步到 cornerstone → cornerstone 变更事件 → 回写 store
- 提取 `useCornerstoneSync` hook 统一管理 store 与 cornerstone 的双向同步

**涉及文件**：
- `src/components/dicom/DicomToolbar.tsx`
- `src/components/dicom/DicomViewer.tsx`
- 新建 `src/hooks/useCornerstoneSync.ts`

### 1.2 修复 SessionCreatePage 表单无法提交（功能性 Bug）

**问题**：Form 有 submit 按钮但无 `onFinish` 回调，未导入 `useSession` hook，数据无法提交到后端。

**修复方案**：
- 导入 `useSession` hook，连接 `onFinish` 到 `createSession`
- 添加表单验证（patient_id 必填、device_id 必填、target_organs 至少选一个）
- 提交成功后导航到检查主界面
- 添加提交中 loading 状态和错误处理

**涉及文件**：
- `src/pages/SessionCreatePage.tsx`
- `src/hooks/useSession.ts`

### 1.3 修复 ExaminationPage "请求诊断"按钮无功能（功能性 Bug）

**问题**：按钮无 `onClick` 处理器，点击无反应。

**修复方案**：
- 连接 `diagnosisService.requestDiagnosis` 到按钮 onClick
- 添加幂等性 key 生成（使用 `idGenerator.generateRequestId`）
- 请求后启动 WebSocket 结果监听 + HTTP 轮询降级方案
- 添加请求中/成功/失败状态反馈

**涉及文件**：
- `src/pages/ExaminationPage.tsx`
- `src/services/diagnosisService.ts`

### 1.4 修复 401 重定向死循环（安全 Bug）

**问题**：`api.ts` 在 401 时重定向到 `/login`，但路由表中无 `/login` 路由，通配符重定向回 `/`，形成循环。

**修复方案**：
- 添加 `/login` 路由和 `LoginPage` 组件
- 401 时清除 token 并导航到 `/login`，而非 `window.location.href`
- 使用 React Router 的 `navigate` 而非硬跳转，避免破坏 SPA 状态
- 添加路由守卫：未认证时重定向到登录页

**涉及文件**：
- `src/services/api.ts`
- `src/app/routes.tsx`
- 新建 `src/pages/LoginPage.tsx`
- 新建 `src/components/common/AuthGuard.tsx`

### 1.5 修复 navigationStore.removeAnnotations 忽略 annotationIds（数据安全 Bug）

**问题**：`_annotationIds` 参数被完全忽略，实现为按 frameId 删除所有标注，可能导致医疗标注误删。

**修复方案**：
- 实现按 annotationIds 过滤删除
- 为 `NavigationAnnotation` 类型添加 `id` 字段（与后端 `annotation_ids` 对应）
- 修复 `navChannel.ts` 中 `handleNavigationRevocation` 的空字符串 bug

**涉及文件**：
- `src/stores/navigationStore.ts`
- `src/types/navigation.ts`
- `src/types/websocket.ts`
- `src/websocket/navChannel.ts`

### 1.6 修复 WebSocket 状态覆盖 Bug

**问题**：`onclose` 中的 `updateStoreStatus('disconnected')` 覆盖了 `scheduleNavReconnect` 设置的 `'reconnecting'` 状态；`onerror` + `onclose` 双触发重连。

**修复方案**：
- 合并 `setNavChannelStatus` 和 `updateConnectionStatus` 为单次 set 调用
- 在 `onclose` 中判断是否已调度重连，若是则保持 `reconnecting` 状态
- 添加 `isReconnecting` 标志位防止 `onerror` + `onclose` 双重调度

**涉及文件**：
- `src/websocket/DualWebSocketManager.ts`
- `src/stores/websocketStore.ts`

### 1.7 修复 WebSocket URL 不安全构造

**问题**：硬编码 `ws://` 协议，生产环境 HTTPS 页面下浏览器阻止混合内容。

**修复方案**：
- 根据页面协议自动选择 `wss://` 或 `ws://`
- 从配置中读取 WebSocket 基础 URL

**涉及文件**：
- `src/websocket/DualWebSocketManager.ts`

### 1.8 修复 Vite 开发环境 WebSocket 代理缺失

**问题**：HTTP `/api` 已代理，但 WebSocket 连接未配置 `ws: true`，开发环境 WS 连接失败。

**修复方案**：
- 在 `vite.config.ts` 的 proxy 配置中添加 `ws: true`

**涉及文件**：
- `vite.config.ts`

---

## 第二阶段：架构重构与代码质量提升（P1）

> 消除技术债务，建立可维护、可扩展的代码架构

### 2.1 消除 DicomViewer 与 useDicomViewer 重复

**问题**：两者实现几乎相同的逻辑，DicomViewer 未使用 useDicomViewer hook，useDicomViewer 是死代码。

**方案**：
- 重构 `useDicomViewer` 为唯一的逻辑层，包含 cornerstone 初始化、图像加载、viewport 同步、metadata 提取
- `DicomViewer` 组件仅负责渲染 DOM 和调用 hook
- 删除 DicomViewer 中内联的重复逻辑

**涉及文件**：
- `src/components/dicom/DicomViewer.tsx`
- `src/hooks/useDicomViewer.ts`

### 2.2 统一暗室模式状态源

**问题**：`isDarkRoomMode` 同时存在于 `sessionStore` 和 `uiStore`，状态不一致风险。

**方案**：
- 删除 `sessionStore` 中的 `isDarkRoomMode`、`setDarkRoomMode`、`isPureReportMode`
- 统一使用 `uiStore` 管理暗室模式
- `sessionStore` 通过 `uiStore.getState()` 读取暗室模式状态
- 更新所有引用处

**涉及文件**：
- `src/stores/sessionStore.ts`
- `src/stores/uiStore.ts`
- `src/components/layout/TopBar.tsx`
- `src/pages/SettingsPage.tsx`

### 2.3 建立 snake_case ↔ camelCase 转换层

**问题**：API 返回 snake_case，Store 使用 camelCase，当前无转换层导致 Store 数据实际为空/错误。

**方案**：
- 安装 `camelize-ts` 或自建轻量转换工具
- 在 `api.ts` 响应拦截器中统一将 snake_case 转为 camelCase
- 在 `api.ts` 请求拦截器中统一将 camelCase 转为 snake_case
- 更新所有 service 函数移除手动字段映射

**涉及文件**：
- `src/services/api.ts`
- 新建 `src/utils/transform.ts`
- 所有 `src/services/*.ts`

### 2.4 优化 Zustand Store 选择器粒度

**问题**：组件以 `useXxxStore()` 解构整个 store，任何状态变化都触发重渲染。

**方案**：
- 所有组件改用 selector 模式：`useDicomStore((s) => s.activeTool)`
- 对多字段选择使用 `shallow` 比较器：`useDicomStore(selectViewport, shallow)`
- 提取常用 selector 为独立函数便于复用和测试

**涉及文件**：
- 所有使用 store 的组件和 hooks

### 2.5 修复 websocketStore 双重渲染

**问题**：`setNavChannelStatus` 先 set 通道状态，再调用 `updateConnectionStatus` 第二次 set，触发两次渲染。

**方案**：
- 合并为单次 set 调用，在 set 中同时计算通道状态和综合连接状态
- 提取 `deriveConnectionStatus` 纯函数

**涉及文件**：
- `src/stores/websocketStore.ts`

### 2.6 修复 pollDiagnosisResult 无法取消

**问题**：轮询无法取消，组件卸载后仍继续，造成内存泄漏。

**方案**：
- 接收 `AbortSignal` 参数
- 返回取消函数
- 优先使用 WebSocket 推送，轮询仅作降级方案
- 添加指数退避

**涉及文件**：
- `src/services/diagnosisService.ts`

### 2.7 修复 organStatuses 类型断言

**问题**：`{} as Record<OrganType, OrganStatus>` 掩盖空值，运行时访问 undefined。

**方案**：
- 改为 `Partial<Record<OrganType, OrganStatus>>`
- 更新所有访问处添加空值检查

**涉及文件**：
- `src/stores/sessionStore.ts`
- `src/types/session.ts`

### 2.8 添加路由守卫与懒加载

**问题**：无路由守卫，无代码分割，首屏加载慢。

**方案**：
- 使用 `React.lazy()` + `Suspense` 懒加载所有页面组件
- 创建 `AuthGuard` 组件保护需要认证的路由
- 创建 `SessionGuard` 组件保护需要活跃会话的路由
- 添加 404 页面替代静默重定向

**涉及文件**：
- `src/app/routes.tsx`
- 新建 `src/components/common/AuthGuard.tsx`
- 新建 `src/components/common/SessionGuard.tsx`
- 新建 `src/pages/NotFoundPage.tsx`

### 2.9 建立统一错误处理策略

**问题**：错误处理分散，无全局通知机制，部分错误被静默吞掉。

**方案**：
- 在 `api.ts` 响应拦截器中统一处理业务错误码（E1001-E4999）
- 创建 `useErrorHandler` hook 统一错误展示逻辑
- 使用 Ant Design `message` 或 `notification` 作为全局错误通知
- 根据 `retryable` 和 `retry_strategy` 字段决定是否显示重试按钮
- 添加 `window.onerror` 和 `unhandledrejection` 全局捕获

**涉及文件**：
- `src/services/api.ts`
- 新建 `src/hooks/useErrorHandler.ts`
- `src/app/providers.tsx`

### 2.10 清理死代码与未使用导入

**问题**：`containerRef` 未使用、`DatabaseOutlined` 死导入、`handleFrameChange` 无意义包装、`useDicomViewer` 死代码等。

**方案**：
- 删除 DicomViewer 中未使用的 `containerRef`
- 删除 Sidebar 中未使用的 `DatabaseOutlined` 导入
- 删除 DicomViewer 中无意义的 `handleFrameChange` 包装
- 删除或重构 `useDicomViewer` 死代码

**涉及文件**：
- `src/components/dicom/DicomViewer.tsx`
- `src/components/layout/Sidebar.tsx`

---

## 第三阶段：样式系统重构与主题统一（P1-P2）

> 建立单一设计 token 源，消除样式散落和三源维护问题

### 3.1 建立设计 Token 单一源

**问题**：颜色值在 `theme.ts`、`global.css`、`tailwind.config.ts` 三处独立维护，极易不一致。

**方案**：
- 创建 `src/styles/tokens.ts` 作为唯一设计 token 定义源
- 导出颜色、间距、字体、动画等 token 常量
- `theme.ts` 从 tokens 导入配置 Ant Design
- `tailwind.config.ts` 从 tokens 导入配置 Tailwind
- `global.css` 的 CSS 变量由 tokens 生成

**涉及文件**：
- 新建 `src/styles/tokens.ts`
- `src/styles/theme.ts`
- `tailwind.config.ts`
- `src/styles/global.css`

### 3.2 消除内联样式与魔法字符串

**问题**：21 个文件中硬编码颜色值 `#0a0e1a`、`#00d4ff` 等，字体 `'Orbitron'` 等多处重复。

**方案**：
- 所有内联 `style={{}}` 迁移到 Tailwind 类名或 CSS Modules
- 颜色值统一使用 Tailwind 的 `bg-med-primary` 等语义化类名
- 字体使用 Tailwind 的 `font-display`、`font-mono` 等语义化类名
- 逐步替换，按组件分批进行

**涉及文件**：
- 所有组件文件

### 3.3 统一动画定义

**问题**：`animations.css` 和 `tailwind.config.ts` 的 keyframes 重复定义。

**方案**：
- 保留 Tailwind 配置中的 keyframes 定义（推荐方式）
- 删除 `animations.css` 中重复的 keyframes
- 保留 `animations.css` 中仅 CSS 可实现的复杂动画

**涉及文件**：
- `src/styles/animations.css`
- `tailwind.config.ts`

### 3.4 修复 Ant Design 暗色主题 algorithm

**问题**：`theme.ts` 中 `algorithm: undefined`，导致部分组件仍显示亮色背景。

**方案**：
- 设置 `algorithm: theme.darkAlgorithm`
- 验证所有 Ant Design 组件在暗色主题下的渲染效果

**涉及文件**：
- `src/styles/theme.ts`

### 3.5 字体本地化与 CSP 兼容

**问题**：Google Fonts 外部依赖在内网环境不可用；严格 CSP 策略下可能被阻止。

**方案**：
- 下载 Orbitron、Inter、JetBrains Mono 字体文件到 `public/fonts/`
- 使用 `@font-face` 本地声明替代 Google Fonts 链接
- 确保字体加载策略符合 CSP `font-src 'self'` 要求

**涉及文件**：
- `src/styles/global.css`
- 新建 `public/fonts/` 目录

### 3.5 添加 prefers-reduced-motion 支持

**问题**：闪烁动画对光敏性癫痫患者有风险，缺少无障碍媒体查询。

**方案**：
- 在 `animations.css` 和 `tailwind.config.ts` 中添加 `prefers-reduced-motion` 媒体查询
- 闪烁动画（blink-warning、blink-danger、annotation-pending-blink）在 reduced-motion 下改为静态高亮

**涉及文件**：
- `src/styles/animations.css`
- `tailwind.config.ts`

---

## 第四阶段：缺失功能实现（Phase 0B 核心功能）

> 对照文档规范，实现所有已定义但未完成的前端功能

### 4.1 诊断结果页完整实现

**当前状态**：DiagnosisPage 仅有占位文字。

**实现方案**：
- 渐进式加载展示：先显示诊断结论，再加载依据溯源
- 诊断结论区：分级显示（A/B/C 级，对应不同安全校验状态）
- 依据溯源区：展示推理路径、知识库引用、置信度
- 质控状态区：显示 `hallucination_severity`、`fusion_status`、`desensitization_status`
- 缓存依据标记：黄色背景高亮（暗室模式：琥珀色边框）
- 严重幻觉警告：不可关闭红色警告横幅 + 诊断结论红色边框 + 强制人工复核
- 标准/全量路径差异展示：蓝色边框"初步结论" + 绿色边框"深度分析" + 黄色高亮差异

**涉及文件**：
- `src/pages/DiagnosisPage.tsx`
- 新建 `src/components/diagnosis/DiagnosisConclusion.tsx`
- 新建 `src/components/diagnosis/EvidenceTrace.tsx`
- 新建 `src/components/diagnosis/HallucinationWarning.tsx`
- 新建 `src/components/diagnosis/QualityControlBadge.tsx`

### 4.2 患者知情同意页实现

**当前状态**：仅有 consentService API 封装，无 UI。

**实现方案**：
- 同意书展示与签署界面
- 三种状态交互：同意 → 解锁 AI 诊断功能；不同意 → 纯报告模式（AI 入口灰色禁用）；撤回 → 立即隐藏诊断结论区域
- 纯报告模式：保留基础报告编辑，隐藏所有 AI 诊断入口
- 撤回后前端收到 WebSocket `session_terminated` 事件立即隐藏诊断结论
- 根据 `consent_status_at_output` 字段决定是否展示诊断结论

**涉及文件**：
- 新建 `src/pages/ConsentPage.tsx`
- 新建 `src/components/consent/ConsentForm.tsx`
- 新建 `src/components/consent/PureReportMode.tsx`
- `src/utils/consentGuard.ts`（增强）
- `src/app/routes.tsx`（添加路由）

### 4.3 反馈提交页实现

**当前状态**：完全未实现。

**实现方案**：
- L2/L3 分级反馈表单
- 修正/纠错/补充三种反馈类型
- 最少操作步骤设计
- 支持语音输入（与语音交互模块集成）
- 提交成功/失败反馈

**涉及文件**：
- 新建 `src/pages/FeedbackPage.tsx`
- 新建 `src/components/feedback/FeedbackForm.tsx`
- `src/app/routes.tsx`（添加路由）

### 4.4 语音交互界面实现

**当前状态**：后端有 `/api/v1/voice/confirm` 接口，前端无 UI。

**实现方案**：
- 转写文本实时显示区域（< 500ms 延迟）
- 确认面板弹出：30 秒超时自动关闭
- 仅诊断触发和分级变更类指令需确认，导航控制类直接执行
- 确认方式：点击按钮 / 语音"确认执行" / 脚踏开关
- 失败降级为手动输入

**涉及文件**：
- 新建 `src/components/voice/VoicePanel.tsx`
- 新建 `src/components/voice/VoiceConfirmDialog.tsx`
- 新建 `src/hooks/useVoiceInteraction.ts`
- 新建 `src/services/voiceService.ts`

### 4.5 暗室模式完整实现

**当前状态**：有开关但视觉规范未完全达标。

**实现方案**：
- 深色主题，高对比度
- 琥珀色/橙色边框替代黄色背景
- 最小亮度
- 闪烁边框确保安全事件不被忽视
- AI 诊断失败：琥珀色闪烁边框 + 3 次蜂鸣（暗室模式）；红色横幅（正常模式）
- 通过设计 token 系统统一管理暗室模式颜色

**涉及文件**：
- `src/styles/tokens.ts`
- `src/stores/uiStore.ts`
- 所有组件（颜色适配）

### 4.6 导航标注安全状态视觉区分完善

**当前状态**：AnnotationBox 有基本实现，但缺少部分状态视觉。

**完善方案**：
- 已校验：实线边框 + 高亮填充 + 实色标签 ✓（已有）
- 待校验：虚线边框 + 半透明填充 + 闪烁提示"待校验"（需完善）
- 校验超时/后端不可达："导航服务中断"提示 + 红色连接状态指示灯（需实现）
- 后端校验移除：标注消失 + 红色闪烁提示（需实现）
- 低置信度帧：隐藏标注 + 虚线框提示（需实现）

**涉及文件**：
- `src/components/navigation/AnnotationBox.tsx`
- `src/components/navigation/NavigationOverlay.tsx`
- `src/stores/navigationStore.ts`

### 4.7 质控报告页实现

**当前状态**：完全未实现。

**实现方案**：
- 科室统计概览
- 趋势图（ECharts）
- AI 修正率告警
- 主任角色权限控制

**涉及文件**：
- 新建 `src/pages/QcReportPage.tsx`
- 新建 `src/components/qc/QcDashboard.tsx`
- 新建 `src/components/qc/TrendChart.tsx`
- `src/app/routes.tsx`（添加路由）

### 4.8 知识库管理页实现

**当前状态**：完全未实现。

**实现方案**：
- 版本列表展示
- 变更记录查看
- 复审通知
- 管理员角色权限控制

**涉及文件**：
- 新建 `src/pages/KbManagementPage.tsx`
- 新建 `src/components/kb/VersionList.tsx`
- `src/app/routes.tsx`（添加路由）

---

## 第五阶段：性能优化（P2）

> 提升应用响应速度和资源利用效率

### 5.1 Vite 生产构建优化

**方案**：
- 配置 `build.rollupOptions.output.manualChunks` 拆分大型依赖
  - `vendor-react`: react, react-dom, react-router-dom
  - `vendor-antd`: antd, @ant-design/icons
  - `vendor-cornerstone`: cornerstone-core, cornerstone-tools, cornerstone-wado-image-loader, dicom-parser
  - `vendor-echarts`: echarts, echarts-for-react
- 启用 `build.cssCodeSplit: true`
- 配置 `build.target: 'es2020'` 减少转译开销

**涉及文件**：
- `vite.config.ts`

### 5.2 DICOM 帧缓存优化

**问题**：`dicomChannel.ts` 的 `frameCache` 是普通 Map，读取不刷新顺序，非真正 LRU。

**方案**：
- 实现 LRU 缓存（或使用 `lru-cache` 库）
- 添加最大缓存帧数限制（如 100 帧）
- 帧重复/乱序处理：检测重复 frameIndex 并跳过

**涉及文件**：
- `src/websocket/dicomChannel.ts`

### 5.3 dicomIndexMapper 查询优化

**问题**：`getBySessionIndex` 和 `getByFrameId` 线性扫描 O(n)。

**方案**：
- 添加反向索引 Map：`sessionIndex → mapping` 和 `frameId → mapping`
- 查询复杂度降为 O(1)

**涉及文件**：
- `src/utils/dicomIndexMapper.ts`

### 5.4 ID 生成器安全加固

**问题**：`Math.random()` 不保证唯一性，医疗系统不可接受。

**方案**：
- 使用 `crypto.randomUUID()` 替代 `Math.random() + Date.now()`
- 提取 `generatePrefixedId(prefix)` 基础函数消除 4 个函数的重复模式

**涉及文件**：
- `src/utils/idGenerator.ts`

### 5.5 TTL 倒计时精度修复

**问题**：`setInterval` 在后台标签页被节流，TTL 显示严重偏差。

**方案**：
- 记录 `expiredAt = Date.now() + ttlMs`
- 每次 tick 计算 `Math.max(0, expiredAt - Date.now())`
- 使用 `document.visibilitychange` 事件在页面可见时立即校正

**涉及文件**：
- `src/hooks/useSession.ts`

### 5.6 ObjectURL 内存泄漏修复

**问题**：ExaminationPage 上传文件后未调用 `URL.revokeObjectURL`。

**方案**：
- 在 `setImageId` 前释放旧的 ObjectURL
- 在组件卸载时清理当前 ObjectURL

**涉及文件**：
- `src/pages/ExaminationPage.tsx`

### 5.7 WebSocket 最大重连次数限制

**问题**：指数退避会无限重试，网络长期不可用时造成无意义重连。

**方案**：
- 添加 `maxReconnectAttempts = 20` 配置
- 超过最大次数后停止重连，显示"连接不可用"提示
- 提供手动重连按钮

**涉及文件**：
- `src/websocket/DualWebSocketManager.ts`

---

## 第六阶段：TypeScript 类型安全加固（P2）

> 医疗系统对类型安全要求极高，需全面加固

### 6.1 启用 TypeScript strict 模式

**方案**：
- 在 `tsconfig.app.json` 中启用 `strict: true`
- 修复所有由此产生的类型错误
- 特别关注 `strictNullChecks` 和 `noImplicitAny`

**涉及文件**：
- `tsconfig.app.json`
- 所有 `.ts`/`.tsx` 文件

### 6.2 修复类型定义不严格

**问题清单**：
- `DiagnosisResult.status: string` → 联合类型
- `DiagnosisResult.diagnosis: Record<string, unknown>` → 结构化类型
- `DiagnosisRequestResponse.response_tier: string` → 联合类型
- `SessionInfo.organ_statuses: Record<string, string>` → `Partial<Record<OrganType, OrganStatus>>`
- `WsDiagnosisNotification.status: string` → 联合类型
- `NavigationAnnotation` 缺少 `id` 字段
- `NavigationAnnotation.bbox` 元组无语义标注 → 命名属性对象
- `WsNavigationMessage.annotations` 缺少 `id` 字段

**涉及文件**：
- `src/types/api.ts`
- `src/types/navigation.ts`
- `src/types/websocket.ts`
- `src/types/session.ts`

### 6.3 统一 DicomFrameData 与 ParsedDicomFrame

**问题**：两个接口描述同一概念但字段不同。

**方案**：
- 合并为统一的 `DicomFrame` 类型
- 更新 `dicomChannel.ts` 和 `dicom.ts` 的引用

**涉及文件**：
- `src/types/dicom.ts`
- `src/websocket/dicomChannel.ts`

### 6.4 consentService 代码重复消除

**问题**：`grantConsent` 和 `withdrawConsent` 结构完全相同。

**方案**：
- 提取 `consentAction` 通用函数
- 提取共享参数类型 `ConsentActionData`

**涉及文件**：
- `src/services/consentService.ts`

---

## 第七阶段：响应式设计与无障碍（P2-P3）

> 确保系统在不同设备和无障碍场景下可用

### 7.1 响应式布局实现

**方案**：
- AppLayout：侧边栏在小屏自动折叠，支持手势滑动展开
- ExaminationPage：右侧面板在小屏改为底部抽屉
- SessionCreatePage/DiagnosisPage：卡片宽度自适应
- DicomToolbar：窄屏下工具栏改为可滚动或折叠菜单
- 使用 Ant Design Grid 系统和 Tailwind 响应式断点

**涉及文件**：
- `src/components/layout/AppLayout.tsx`
- `src/pages/ExaminationPage.tsx`
- `src/pages/SessionCreatePage.tsx`
- `src/components/dicom/DicomToolbar.tsx`

### 7.2 无障碍性改进

**方案**：
- 所有交互元素添加 `aria-label`
- StatusBar 状态文本添加 `role="status"` 和 `aria-live`
- DicomViewer 添加 `role="img"` 和 `aria-label`
- AnnotationBox SVG text 改为 `aria-label` + 隐藏文本
- StatusIndicator 使用 `aria-label` 替代 `title`
- ErrorBoundary 错误信息添加 `role="alert"`
- 所有按钮添加 `type="button"` 防止意外提交
- 键盘导航支持（Tab 顺序、焦点管理）

**涉及文件**：
- 所有组件文件

### 7.3 触摸屏与手套操作适配

**方案**：
- 所有可点击元素最小尺寸 44px
- 增大点击区域（padding）
- 减少精细操作需求
- 手势支持（缩放、平移）

**涉及文件**：
- 所有交互组件

---

## 第八阶段：CornerstoneJS 迁移与 DICOM 功能增强（P3）

> 从旧版 CornerstoneJS 迁移到新版，增强 DICOM 功能

### 8.1 CornerstoneJS 新版迁移评估

**问题**：`cornerstone-core` v2 + `cornerstone-tools` v6 已废弃。

**方案**：
- 评估迁移到 `@cornerstonejs/core` + `@cornerstonejs/tools` 的工作量
- 新版优势：TypeScript 原生支持、WebGL 渲染、更好的性能
- 迁移策略：先保持旧版运行，在新分支中逐步迁移
- 如果迁移工作量过大（>2周），则先为旧版添加 `@types` 声明文件

**涉及文件**：
- `package.json`
- `src/components/dicom/cornerstone.ts`
- `src/components/dicom/DicomViewer.tsx`
- `src/hooks/useDicomViewer.ts`

### 8.2 DICOM 工具扩展

**当前**：仅初始化了 WwwcTool/ZoomTool/PanTool。

**方案**：
- 添加 LengthTool（距离测量）
- 添加 AngleTool（角度测量）
- 添加 EllipticalROITool（椭圆 ROI）
- 添加 ArrowAnnotateTool（箭头标注）
- 工具切换与 DicomTool 类型定义对齐

**涉及文件**：
- `src/components/dicom/corstone.ts`
- `src/types/dicom.ts`
- `src/components/dicom/DicomToolbar.tsx`

### 8.3 DICOM 上传增强

**方案**：
- 添加文件类型验证（.dcm / DICM magic number）
- 添加文件大小限制提示
- 添加上传进度条
- 添加重试逻辑

**涉及文件**：
- `src/services/imagingService.ts`
- `src/pages/ExaminationPage.tsx`

---

## 第九阶段：测试体系建设（P3）

> 建立前端测试金字塔，保障代码质量

### 9.1 单元测试框架搭建

**方案**：
- 安装 Vitest + @testing-library/react + @testing-library/jest-dom
- 配置 Vitest（与 Vite 共享配置）
- 编写 Store 单元测试（sessionStore、navigationStore 等）
- 编写 utils 单元测试（dicomIndexMapper、idGenerator、consentGuard）
- 编写 service 单元测试（mock axios）

### 9.2 组件测试

**方案**：
- 使用 @testing-library/react 测试关键组件
- DicomViewer 渲染测试
- NavigationOverlay 标注状态测试
- ConsentPage 状态切换测试
- ErrorBoundary 错误捕获测试

### 9.3 E2E 测试

**方案**：
- 安装 Playwright
- 核心流程 E2E：创建会话 → 上传 DICOM → 请求诊断 → 查看结果
- 知情同意流程 E2E
- WebSocket 连接与重连测试

---

## 实施顺序与依赖关系

```
第一阶段（P0 Bug修复）
  ├── 1.1 DicomToolbar Slider 修复
  ├── 1.2 SessionCreatePage 表单修复
  ├── 1.3 请求诊断按钮修复
  ├── 1.4 401 重定向修复
  ├── 1.5 removeAnnotations 修复
  ├── 1.6 WebSocket 状态覆盖修复
  ├── 1.7 WebSocket URL 安全修复
  └── 1.8 Vite WS 代理配置
       │
第二阶段（P1 架构重构）── 依赖第一阶段完成
  ├── 2.1 DicomViewer/useDicomViewer 去重
  ├── 2.2 暗室模式统一
  ├── 2.3 snake_case/camelCase 转换层
  ├── 2.4 Store 选择器优化
  ├── 2.5 websocketStore 双重渲染修复
  ├── 2.6 pollDiagnosisResult 可取消化
  ├── 2.7 organStatuses 类型修复
  ├── 2.8 路由守卫与懒加载
  ├── 2.9 统一错误处理
  └── 2.10 死代码清理
       │
第三阶段（P1-P2 样式系统）── 可与第二阶段并行
  ├── 3.1 设计 Token 单一源
  ├── 3.2 消除内联样式
  ├── 3.3 统一动画定义
  ├── 3.4 Ant Design 暗色主题修复
  ├── 3.5 字体本地化
  └── 3.6 prefers-reduced-motion
       │
第四阶段（Phase 0B 功能）── 依赖第二、三阶段完成
  ├── 4.1 诊断结果页
  ├── 4.2 知情同意页
  ├── 4.3 反馈提交页
  ├── 4.4 语音交互界面
  ├── 4.5 暗室模式完善
  ├── 4.6 导航标注视觉区分
  ├── 4.7 质控报告页
  └── 4.8 知识库管理页
       │
第五阶段（P2 性能优化）── 可与第四阶段并行
  ├── 5.1 Vite 构建优化
  ├── 5.2 DICOM 帧缓存优化
  ├── 5.3 dicomIndexMapper 查询优化
  ├── 5.4 ID 生成器安全加固
  ├── 5.5 TTL 倒计时精度
  ├── 5.6 ObjectURL 泄漏修复
  └── 5.7 WebSocket 重连限制
       │
第六阶段（P2 类型安全）── 可与第五阶段并行
  ├── 6.1 strict 模式
  ├── 6.2 类型定义修复
  ├── 6.3 DicomFrame 类型统一
  └── 6.4 consentService 去重
       │
第七阶段（P2-P3 响应式与无障碍）── 依赖第四阶段完成
  ├── 7.1 响应式布局
  ├── 7.2 无障碍改进
  └── 7.3 触摸屏适配
       │
第八阶段（P3 CornerstoneJS 迁移）── 独立轨道
  ├── 8.1 新版迁移评估
  ├── 8.2 DICOM 工具扩展
  └── 8.3 DICOM 上传增强
       │
第九阶段（P3 测试体系）── 贯穿全过程
  ├── 9.1 单元测试
  ├── 9.2 组件测试
  └── 9.3 E2E 测试
```

---

## 关键风险与应对

| 风险 | 概率 | 影响 | 应对策略 |
|------|------|------|----------|
| CornerstoneJS 新版迁移工作量超预期 | 高 | 高 | 先为旧版添加类型声明，迁移作为独立轨道 |
| Ant Design 6.x 严格 CSP 下功能异常 | 低 | 中 | 立项阶段验证 CSP 兼容性；备选 Radix UI + Tailwind CSS |
| snake_case/camelCase 转换引入新 Bug | 中 | 高 | 逐模块迁移，每个模块迁移后运行完整测试 |
| 暗室模式颜色适配遗漏 | 中 | 低 | 建立设计 token 系统后统一管理，减少遗漏 |
| WebSocket 重连逻辑修改引入新问题 | 中 | 高 | 添加 WebSocket 单元测试和模拟测试 |

---

## 验收标准

### 第一阶段验收
- [ ] DicomToolbar Slider 可实际调节窗宽窗位
- [ ] SessionCreatePage 表单可提交并创建会话
- [ ] 请求诊断按钮可触发诊断请求
- [ ] 401 时正确跳转登录页，无死循环
- [ ] removeAnnotations 按指定 ID 删除标注
- [ ] WebSocket 重连状态正确显示 reconnecting
- [ ] 开发环境 WebSocket 连接正常

### 第二阶段验收
- [ ] DicomViewer 使用 useDicomViewer hook，无重复逻辑
- [ ] 暗室模式仅 uiStore 一个状态源
- [ ] API 响应自动转换为 camelCase
- [ ] 组件使用 selector 模式订阅 store
- [ ] 路由守卫生效，未认证不可访问受保护页面
- [ ] 全局错误通知可用

### 第三阶段验收
- [ ] 设计 token 单一源建立，三处配置统一引用
- [ ] 无硬编码颜色值
- [ ] Ant Design 组件全部暗色渲染
- [ ] 字体本地化，无外部依赖
- [ ] prefers-reduced-motion 下闪烁动画降级

### 第四阶段验收
- [ ] 诊断结果页完整展示（结论+依据+质控+警告）
- [ ] 知情同意页三种状态交互正确
- [ ] 反馈提交页 L2/L3 分级可用
- [ ] 语音确认面板弹出与超时关闭
- [ ] 暗室模式视觉规范达标
- [ ] 导航标注四种安全状态视觉区分
- [ ] 质控报告页与知识库管理页可用

### 第五阶段验收
- [ ] 生产构建 chunk 合理拆分
- [ ] DICOM 帧缓存 LRU 淘汰生效
- [ ] dicomIndexMapper 查询 O(1)
- [ ] ID 使用 crypto.randomUUID()
- [ ] TTL 倒计时后台切回后准确
- [ ] WebSocket 重连有最大次数限制

### 第六阶段验收
- [ ] TypeScript strict 模式无错误
- [ ] 所有 status 字段为联合类型
- [ ] NavigationAnnotation 有 id 字段
- [ ] DicomFrame 类型统一

### 第七阶段验收
- [ ] 小屏设备布局不溢出
- [ ] 所有交互元素有 aria-label
- [ ] 键盘可完整操作核心流程
- [ ] 触摸屏最小点击区域 44px

### 第八阶段验收
- [ ] CornerstoneJS 迁移评估报告完成
- [ ] DICOM 测量工具可用
- [ ] 上传有进度反馈和文件验证

### 第九阶段验收
- [ ] Store 和 utils 单元测试覆盖率 > 80%
- [ ] 关键组件测试覆盖
- [ ] 核心流程 E2E 测试通过
