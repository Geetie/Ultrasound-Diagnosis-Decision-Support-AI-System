# Phase 0A FE前端轨道开发计划

## 一、任务概览

根据 `各阶段并发任务.md` 和 `开发计划.md`，Phase 0A FE前端轨道包含4个任务：

| 任务ID | 任务名称 | 优先级 | 依赖 | 可交付物 | 验收标准 |
|--------|---------|--------|------|---------|---------|
| T-GA-FE-01 | 前端项目脚手架(React 18+Ant Design 5+Zustand) | P0 | 无 | 前端项目+路由+主题 | 开发服务器启动 |
| T-GA-FE-02 | DICOM影像渲染(cornerstone.js+身份信息遮罩) | P0 | T-GA-FE-01 | DICOM查看器组件 | 影像渲染+窗位调节 |
| T-GA-FE-03 | DualWebSocketManager(导航通道+DICOM帧通道+重连) | P0 | T-GA-FE-01 | WebSocket管理器 | 双连接+指数退避重连 |
| T-GA-FE-04 | 导航标注叠加渲染(已校验/待校验/校验超时视觉区分) | P0 | T-GA-FE-02, T-GA-FE-03 | 导航标注组件 | 三种状态视觉区分 |

**并行路径**：`T-GA-FE-01 → T-GA-FE-02/03(并行) → T-GA-FE-04`

## 二、设计方向：未来医疗科技风

### 2.1 视觉论文 (Visual Thesis)

**深空医疗**——以深邃暗色为基底，冷蓝青光为脉络，琥珀警示为安全锚点，构建超声影像在数字空间中的精确映射界面。

### 2.2 设计系统令牌 (Design Tokens)

#### 色彩体系

```
--color-bg-primary:       #0a0e1a    (深空蓝黑，主背景)
--color-bg-secondary:     #111827    (次级面板背景)
--color-bg-elevated:      #1a2035    (浮层/卡片背景)
--color-bg-hover:         #1e2745    (悬停态)

--color-accent-primary:   #00d4ff    (冷蓝青，主强调色-导航/交互)
--color-accent-secondary: #7c3aed    (紫蓝，次强调色-智能体/AI)
--color-accent-success:   #10b981    (翠绿，已校验/成功)
--color-accent-warning:   #f59e0b    (琥珀，待校验/警告)
--color-accent-danger:    #ef4444    (红色，错误/严重)
--color-accent-info:      #3b82f6    (蓝色，信息提示)

--color-text-primary:     #e2e8f0    (主文本)
--color-text-secondary:   #94a3b8    (次级文本)
--color-text-muted:       #64748b    (弱化文本)
--color-text-inverse:     #0f172a    (反色文本)

--color-border-default:   #1e293b    (默认边框)
--color-border-accent:    #00d4ff33  (强调边框，半透明青)
--color-border-warning:   #f59e0b80  (警告边框，半透明琥珀)
```

#### 字体体系

```
--font-display:  'Orbitron', sans-serif     (标题/数据展示，科技感)
--font-body:     'Inter', sans-serif        (正文/UI文本，可读性)
--font-mono:     'JetBrains Mono', monospace (代码/数据/帧索引)
```

#### 间距与圆角

```
--spacing-xs: 4px    --radius-sm: 6px
--spacing-sm: 8px    --radius-md: 10px
--spacing-md: 16px   --radius-lg: 16px
--spacing-lg: 24px   --radius-xl: 24px
--spacing-xl: 32px
```

#### 动效

```
--duration-fast:    150ms   (微交互)
--duration-normal:  300ms   (常规过渡)
--duration-slow:    500ms   (页面级过渡)
--ease-default:     cubic-bezier(0.4, 0, 0.2, 1)
--ease-spring:      cubic-bezier(0.34, 1.56, 0.64, 1)
```

### 2.3 内容规划

1. **Hero/主工作区**：DICOM影像查看器占据主视口，导航标注实时叠加
2. **辅助面板**：诊断结果、AI状态指示器、会话信息
3. **状态栏**：连接状态、会话倒计时、知识库版本
4. **交互入口**：语音指令、诊断请求、反馈提交

### 2.4 动效规划

1. **入场序列**：页面加载时面板从边缘滑入，DICOM查看器淡入
2. **导航标注动效**：标注框从虚线→实线的校验状态过渡动画
3. **连接状态脉动**：WebSocket连接状态指示灯呼吸灯效果

## 三、技术架构

### 3.1 技术栈确认

| 维度 | 选型 | 版本 |
|------|------|------|
| 框架 | React 18 | ^18.3 |
| 构建工具 | Vite | ^5.x |
| UI组件库 | Ant Design 5 | ^5.x |
| DICOM渲染 | cornerstone.js + cornerstoneWADOImageLoader | 最新稳定版 |
| 状态管理 | Zustand | ^4.x |
| 图表 | ECharts | ^5.x |
| 路由 | React Router | ^6.x |
| HTTP客户端 | Axios | ^1.x |
| WebSocket | 原生API + 自封装DualWebSocketManager | — |
| CSS方案 | Tailwind CSS + CSS Modules (主题变量) | ^3.x |
| TypeScript | TypeScript | ^5.x |

### 3.2 项目目录结构

```
frontend/
├── public/
│   └── favicon.svg
├── src/
│   ├── app/
│   │   ├── App.tsx                    # 根组件+路由
│   │   ├── routes.tsx                 # 路由配置
│   │   └── providers.tsx              # 全局Provider包裹
│   ├── components/
│   │   ├── layout/
│   │   │   ├── AppLayout.tsx          # 主布局(侧边栏+顶栏+内容区)
│   │   │   ├── Sidebar.tsx            # 侧边导航
│   │   │   ├── TopBar.tsx             # 顶部状态栏
│   │   │   └── StatusBar.tsx          # 底部连接/会话状态栏
│   │   ├── dicom/
│   │   │   ├── DicomViewer.tsx        # DICOM查看器主组件
│   │   │   ├── DicomToolbar.tsx       # 窗位/缩放/工具栏
│   │   │   ├── DicomOverlay.tsx       # 身份信息遮罩层
│   │   │   └── cornerstone.ts         # cornerstone初始化与工具配置
│   │   ├── navigation/
│   │   │   ├── NavigationOverlay.tsx  # 导航标注叠加渲染
│   │   │   ├── AnnotationBox.tsx      # 单个标注框组件
│   │   │   └── AnnotationLegend.tsx   # 标注状态图例
│   │   ├── diagnosis/
│   │   │   ├── DiagnosisPanel.tsx     # 诊断结果面板(Phase 0B)
│   │   │   └── DiagnosisRequest.tsx   # 诊断请求触发(Phase 0B)
│   │   ├── session/
│   │   │   ├── SessionCreator.tsx     # 检查会话创建
│   │   │   └── SessionInfo.tsx        # 会话信息展示
│   │   ├── consent/
│   │   │   └── ConsentGate.tsx        # 知情同意状态门控(Phase 0B)
│   │   └── common/
│   │       ├── StatusIndicator.tsx    # 连接状态指示灯
│   │       ├── LoadingOverlay.tsx     # 加载遮罩
│   │       └── ErrorBoundary.tsx      # 错误边界
│   ├── hooks/
│   │   ├── useDicomViewer.ts          # DICOM查看器Hook
│   │   ├── useNavigationAnnotations.ts # 导航标注Hook
│   │   └── useSession.ts             # 会话管理Hook
│   ├── stores/
│   │   ├── websocketStore.ts          # WebSocket连接状态
│   │   ├── sessionStore.ts            # 会话状态
│   │   ├── dicomStore.ts              # DICOM影像状态
│   │   ├── navigationStore.ts         # 导航标注状态
│   │   └── uiStore.ts                # UI状态(暗室模式等)
│   ├── services/
│   │   ├── api.ts                     # Axios实例+拦截器
│   │   ├── diagnosisService.ts        # 诊断API
│   │   ├── sessionService.ts          # 会话API
│   │   ├── consentService.ts          # 同意管理API
│   │   └── imagingService.ts          # DICOM上传API
│   ├── websocket/
│   │   ├── DualWebSocketManager.ts    # 双连接WebSocket管理器
│   │   ├── navChannel.ts             # 导航通道处理
│   │   ├── dicomChannel.ts           # DICOM帧通道处理
│   │   └── types.ts                  # WebSocket消息类型定义
│   ├── styles/
│   │   ├── theme.ts                  # 主题令牌定义
│   │   ├── global.css                # 全局样式+CSS变量
│   │   └── animations.css            # 动画关键帧
│   ├── types/
│   │   ├── api.ts                    # API响应类型
│   │   ├── dicom.ts                  # DICOM相关类型
│   │   ├── navigation.ts             # 导航标注类型
│   │   ├── session.ts                # 会话类型
│   │   └── websocket.ts              # WebSocket消息类型
│   ├── utils/
│   │   ├── idGenerator.ts            # 幂等键生成(request_id等)
│   │   ├── dicomIndexMapper.ts       # DICOM索引转换
│   │   └── consentGuard.ts           # 同意状态前端检查
│   ├── main.tsx                       # 入口
│   └── vite-env.d.ts
├── index.html
├── package.json
├── tsconfig.json
├── tsconfig.node.json
├── vite.config.ts
├── tailwind.config.ts
├── postcss.config.js
└── .eslintrc.cjs
```

### 3.3 关键接口契约

#### DualWebSocketManager 接口

```typescript
interface DualWebSocketManager {
  connect(sessionId: string, token: string): void;
  disconnect(): void;

  onNavigationMessage(handler: (msg: NavigationMessage) => void): () => void;
  onDicomFrame(handler: (frame: DicomFrameData) => void): () => void;
  onDiagnosisResult(handler: (notification: DiagnosisNotification) => void): () => void;
  onSessionTerminated(handler: (event: SessionTerminatedEvent) => void): () => void;
  onConnectionStatusChange(handler: (status: ConnectionStatus) => void): () => void;

  getNavChannelStatus(): ChannelStatus;
  getDicomChannelStatus(): ChannelStatus;
}
```

#### 导航标注类型

```typescript
interface NavigationAnnotation {
  label: string;
  confidence: number;
  threshold: number;
  bbox: [number, number, number, number]; // [x, y, w, h]
}

type AnnotationValidationStatus =
  | 'verified'       // 已校验：实线边框+高亮填充+实色标签
  | 'pending'        // 待校验：虚线边框+半透明填充+闪烁提示
  | 'timeout'        // 校验超时/后端不可达
  | 'revoked';       // 后端校验移除

interface NavigationMessage {
  type: 'navigation';
  frame_id: string;
  annotations: NavigationAnnotation[];
  desensitization_status: 'full' | 'degraded';
  timestamp: number;
}
```

## 四、实施步骤

### Step 1: T-GA-FE-01 前端项目脚手架

1. **初始化Vite + React + TypeScript项目**
   - `npm create vite@latest frontend -- --template react-ts`
   - 配置vite.config.ts (代理、路径别名)

2. **安装核心依赖**
   - React 18 + React Router 6
   - Ant Design 5 + @ant-design/icons
   - Zustand
   - Axios
   - Tailwind CSS 3 + PostCSS + Autoprefixer

3. **配置主题系统**
   - 创建 `styles/theme.ts`：Ant Design 5 ConfigProvider 主题令牌
   - 创建 `styles/global.css`：CSS变量 + 全局样式
   - 创建 `styles/animations.css`：动画关键帧
   - 配置 Tailwind CSS 自定义主题扩展

4. **搭建布局框架**
   - AppLayout：左侧边栏 + 顶栏 + 主内容区 + 底部状态栏
   - Sidebar：导航菜单(检查主界面、诊断结果、质控报告等)
   - TopBar：系统标题 + 用户信息 + 暗室模式切换
   - StatusBar：WebSocket连接状态 + 会话TTL + 知识库版本

5. **配置路由**
   - `/` → 检查主界面(DICOM + 导航)
   - `/diagnosis` → 诊断结果页(Phase 0B占位)
   - `/session/new` → 创建检查会话
   - `/settings` → 设置页(暗室模式等)

6. **创建全局Provider**
   - Ant Design ConfigProvider(主题)
   - React Router BrowserRouter
   - 错误边界

7. **验证**：`npm run dev` 开发服务器启动，页面可访问

### Step 2: T-GA-FE-02 DICOM影像渲染

1. **安装cornerstone.js依赖**
   - cornerstone-core
   - cornerstone-wado-image-loader
   - cornerstone-tools (窗位/缩放/平移)
   - dicom-parser

2. **初始化cornerstone**
   - `components/dicom/cornerstone.ts`：初始化cornerstone引擎
   - 配置WADO image loader
   - 注册cornerstone tools (WindowLevel, Zoom, Pan)

3. **实现DicomViewer组件**
   - cornerstone-enabled元素
   - 影像加载与渲染
   - 窗宽窗位调节 (WW/WL)
   - 缩放/平移交互
   - 帧切换(多帧DICOM)

4. **实现DicomToolbar组件**
   - 窗位调节滑块
   - 缩放控制
   - 重置视图
   - 工具切换(窗位/缩放/平移)

5. **实现DicomOverlay组件**
   - 身份信息遮罩 (`SHOW_DICOM_OVERLAY` 配置项)
   - 患者ID/姓名遮罩
   - 检查信息保留显示(检查日期、设备型号等)

6. **实现useDicomViewer Hook**
   - 封装cornerstone初始化/销毁生命周期
   - 影像加载状态管理
   - 工具切换逻辑

7. **创建dicomStore (Zustand)**
   - 当前影像状态
   - 窗位参数
   - 帧索引映射表
   - 帧缓存

8. **验证**：加载DICOM文件，影像正常渲染，窗位可调节，身份信息遮罩生效

### Step 3: T-GA-FE-03 DualWebSocketManager

1. **定义WebSocket消息类型** (`websocket/types.ts`)
   - NavigationMessage
   - DicomFrameData
   - DiagnosisNotification
   - SessionTerminatedEvent
   - ConnectionStatus
   - ChannelStatus
   - 心跳消息

2. **实现DualWebSocketManager核心类**
   - 双连接管理(导航通道 + DICOM帧通道)
   - 连接建立与认证流程
   - 指数退避重连 (1s→2s→4s→8s→最大30s)
   - 心跳机制 (5s间隔，3次无响应判定断开)
   - 事件分发系统(发布-订阅模式)
   - 连接状态追踪

3. **实现navChannel处理器**
   - 导航标注消息解析
   - 诊断结果通知处理
   - 安全校验移除通知 (navigation_revocation)
   - 会话终止事件 (session_terminated)
   - 器官切换事件处理

4. **实现dicomChannel处理器**
   - 二进制帧解析 (frame_index + flags + dicom_data + CRC32)
   - CRC32校验
   - 帧缓存管理
   - 帧索引映射

5. **创建websocketStore (Zustand)**
   - 导航通道状态 (connected/disconnected/reconnecting)
   - DICOM帧通道状态
   - 最后心跳时间
   - 重连次数

6. **实现StatusIndicator组件**
   - 连接状态指示灯 (绿色=已连接/黄色=重连中/红色=断开)
   - 呼吸灯动效(已连接)
   - 闪烁动效(重连中)

7. **验证**：双连接建立成功，心跳正常，断线重连指数退避，消息正确分发

### Step 4: T-GA-FE-04 导航标注叠加渲染

1. **定义导航标注类型** (`types/navigation.ts`)
   - NavigationAnnotation
   - AnnotationValidationStatus
   - 标注视觉样式映射

2. **实现navigationStore (Zustand)**
   - 当前帧标注列表
   - 标注校验状态映射
   - 低置信度标注处理
   - 无标注帧静默处理

3. **实现NavigationOverlay组件**
   - SVG/Canvas叠加层(覆盖在DICOM影像上)
   - 标注框渲染
   - 帧ID关联(仅渲染当前帧标注)
   - 标注状态驱动的视觉样式

4. **实现AnnotationBox组件**
   - 已校验：实线边框(#10b981) + 高亮填充 + 实色标签
   - 待校验：虚线边框(#f59e0b) + 半透明填充(≤60%) + 闪烁"待校验"提示
   - 校验超时/后端不可达：红色连接状态指示灯 + "导航服务中断"提示
   - 后端校验移除：标注消失 + 短暂红色闪烁 + "导航标注经安全校验已移除"提示

5. **实现AnnotationLegend组件**
   - 标注状态图例(已校验/待校验/超时/移除)
   - 置信度阈值说明

6. **实现useNavigationAnnotations Hook**
   - 订阅WebSocket导航消息
   - 标注状态更新
   - 校验状态转换逻辑
   - 低置信度帧过滤

7. **集成到DicomViewer**
   - NavigationOverlay作为DicomViewer的叠加层
   - 帧切换时标注同步更新
   - 标注与影像坐标对齐

8. **验证**：三种标注状态视觉区分明确，虚线间距≥4px，半透明度≤60%，1秒内可识别状态差异

## 五、验收标准映射

| 任务 | 验收标准 | 验证方法 |
|------|---------|---------|
| T-GA-FE-01 | 开发服务器启动 | `npm run dev` 成功，页面可访问 |
| T-GA-FE-02 | 影像渲染+窗位调节 | 加载DICOM文件，影像正常显示，窗位滑块可调节 |
| T-GA-FE-03 | 双连接+指数退避重连 | 模拟断线，观察重连间隔符合1→2→4→8→30s |
| T-GA-FE-04 | 三种状态视觉区分 | 已校验(实线绿)/待校验(虚线琥珀)/超时(红色中断)视觉差异明显 |

## 六、风险与注意事项

1. **CSP兼容性**：Ant Design 5.x在严格CSP下可能异常，需验证`unsafe-inline`禁用后功能完整性
2. **cornerstone.js版本兼容**：cornerstone-core与cornerstone-tools版本需匹配
3. **DICOM帧二进制解析**：小端序字节解析需严格按规范实现
4. **WebSocket重连防抖**：重连后延迟2秒查询会话TTL，2秒内再次重连取消前次查询
5. **标注视觉区分度**：虚线间距≥4px、半透明度≤60%、色相差异≥30°
