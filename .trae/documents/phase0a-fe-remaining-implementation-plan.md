# Phase 0A FE前端轨道开发计划（续）— 剩余实施步骤

## 一、当前进度评估

### 已完成的工作

| 类别    | 已完成项                                                                                          | 状态 |
| ----- | --------------------------------------------------------------------------------------------- | -- |
| 项目脚手架 | Vite + React + TypeScript 初始化                                                                 | ✅  |
| 核心依赖  | antd\@6, zustand\@5, axios, react-router-dom\@7, echarts\@6, tailwindcss\@4                   | ✅  |
| 配置文件  | vite.config.ts, tailwind.config.ts, postcss.config.js                                         | ✅  |
| 主题系统  | styles/theme.ts, styles/global.css, styles/animations.css                                     | ✅  |
| 类型定义  | types/api.ts, dicom.ts, navigation.ts, session.ts, websocket.ts                               | ✅  |
| 状态管理  | stores/uiStore.ts, sessionStore.ts, dicomStore.ts, navigationStore.ts, websocketStore.ts      | ✅  |
| 服务层   | services/api.ts, diagnosisService.ts, sessionService.ts, consentService.ts, imagingService.ts | ✅  |
| 工具函数  | utils/idGenerator.ts, dicomIndexMapper.ts, consentGuard.ts                                    | ✅  |

### 待完成的工作（按任务分组）

| 任务ID       | 待完成项                                                                             | 优先级 |
| ---------- | -------------------------------------------------------------------------------- | --- |
| T-GA-FE-01 | 布局组件(AppLayout/Sidebar/TopBar/StatusBar)、路由配置、全局Provider、入口文件重写                  | P0  |
| T-GA-FE-02 | cornerstone.js安装与初始化、DicomViewer/Toolbar/Overlay组件、useDicomViewer Hook           | P0  |
| T-GA-FE-03 | DualWebSocketManager核心类、navChannel/dicomChannel处理器、StatusIndicator组件             | P0  |
| T-GA-FE-04 | NavigationOverlay/AnnotationBox/AnnotationLegend组件、useNavigationAnnotations Hook | P0  |

### 需要修复的问题

1. **Tailwind CSS v4 兼容性**：当前安装了 tailwindcss\@4.3.0，但 tailwind.config.ts 和 postcss.config.js 是 v3 格式。Tailwind v4 使用 CSS-first 配置，不再使用 JS 配置文件。需要降级到 v3 或迁移到 v4 CSS 配置方式。
2. **tsconfig.app.json 缺少路径别名**：需要添加 `@` 路径别名映射
3. **App.tsx 仍为 Vite 默认模板**：需要替换为项目实际代码
4. **main.tsx 需要引入全局样式**：当前引入 `index.css` 而非 `global.css`
5. **antd v6 兼容性**：项目文档指定 antd\@5，但安装了 antd\@6。antd\@6 的 ThemeConfig 类型可能有变化，需要验证。

## 二、实施计划

### Step 1: 修复基础设施问题

**1.1 降级 Tailwind CSS 到 v3 或迁移到 v4 CSS 配置**

方案选择：降级到 Tailwind CSS v3，因为：

- tailwind.config.ts 已经是 v3 格式
- 项目团队对 v3 更熟悉
- v4 的 CSS-first 配置方式与现有 postcss.config.js 不兼容

操作：

- 卸载 tailwindcss\@4，安装 tailwindcss\@3
- 保留现有 tailwind.config.ts 和 postcss.config.js

**1.2 更新 tsconfig.app.json 添加路径别名**

```json
{
  "compilerOptions": {
    "baseUrl": ".",
    "paths": {
      "@/*": ["src/*"]
    }
  }
}
```

**1.3 清理 Vite 默认文件**

- 删除 `src/App.css`、`src/index.css`、`src/assets/react.svg`、`src/assets/vite.svg`、`src/assets/hero.png`
- 更新 `index.html` 标题

### Step 2: T-GA-FE-01 完成脚手架 — 布局与路由

**2.1 创建** **`src/app/providers.tsx`**

全局 Provider 包裹层：

- Ant Design `ConfigProvider`（暗色主题 + 自定义令牌）
- React Router `BrowserRouter`
- 错误边界 `ErrorBoundary`

**2.2 创建** **`src/app/routes.tsx`**

路由配置：

- `/` → 检查主界面（DICOM + 导航标注）
- `/session/new` → 创建检查会话
- `/diagnosis` → 诊断结果页（Phase 0B 占位）
- `/settings` → 设置页（暗室模式等）

**2.3 创建** **`src/app/App.tsx`**

根组件：引入 providers + routes

**2.4 创建布局组件**

- `src/components/layout/AppLayout.tsx`：主布局框架（侧边栏 + 顶栏 + 内容区 + 底部状态栏）
  - 使用 Ant Design Layout 组件
  - 深空医疗风格：暗色背景 + 冷蓝青强调
  - 侧边栏可折叠
  - 顶栏固定
- `src/components/layout/Sidebar.tsx`：侧边导航
  - 菜单项：检查主界面、诊断结果、质控报告、知识库管理、设置
  - 图标使用 @ant-design/icons
  - 选中项高亮色 #00d4ff
  - Orbitron 字体标题
- `src/components/layout/TopBar.tsx`：顶部状态栏
  - 系统标题"超声诊疗决策支持系统"（Orbitron 字体）
  - 暗室模式切换开关
  - 用户信息显示
  - 器官选择器（甲状腺/肝脏/心脏）
- `src/components/layout/StatusBar.tsx`：底部状态栏
  - WebSocket 连接状态指示灯（双通道独立显示）
  - 会话 TTL 倒计时
  - 知识库版本号
  - 部署模式标识（compact/full）

**2.5 创建通用组件**

- `src/components/common/StatusIndicator.tsx`：连接状态指示灯
  - 绿色呼吸灯 = 已连接
  - 黄色闪烁 = 重连中
  - 红色 = 断开
- `src/components/common/LoadingOverlay.tsx`：加载遮罩
  - 深色半透明背景
  - 冷蓝青旋转加载图标
- `src/components/common/ErrorBoundary.tsx`：错误边界
  - 捕获渲染异常
  - 显示友好错误页面

**2.6 创建页面组件（占位）**

- `src/pages/ExaminationPage.tsx`：检查主界面（后续集成 DICOM + 导航）
- `src/pages/SessionCreatePage.tsx`：创建检查会话
- `src/pages/DiagnosisPage.tsx`：诊断结果页（Phase 0B 占位）
- `src/pages/SettingsPage.tsx`：设置页

**2.7 更新入口文件**

- 重写 `src/main.tsx`：引入 global.css + animations.css，挂载 App
- 删除旧的 `src/App.tsx`、`src/App.css`、`src/index.css`

**2.8 验证**：`npm run dev` 成功启动，页面显示未来医疗科技风布局

### Step 3: T-GA-FE-02 DICOM影像渲染

**3.1 安装 cornerstone.js 依赖**

```
npm install cornerstone-core cornerstone-wado-image-loader cornerstone-tools dicom-parser
npm install -D @types/cornerstone-core
```

注意：cornerstone.js 生态存在 v2（legacy）和 v3（@cornerstonejs/）两代。根据项目文档指定 `cornerstone.js`，使用经典 v2 版本。

**3.2 创建** **`src/components/dicom/cornerstone.ts`**

cornerstone 初始化模块：

- 初始化 cornerstone 核心
- 配置 cornerstoneWADOImageLoader（Web Worker + codec）
- 注册 cornerstone-tools（WindowLevel, Zoom, Pan, Length）
- 导出初始化函数和清理函数

**3.3 创建** **`src/components/dicom/DicomViewer.tsx`**

DICOM 查看器主组件：

- cornerstone-enabled div 容器
- 影像加载与渲染（支持 WADO-URI 和 WADO-RS）
- 窗宽窗位调节
- 缩放/平移交互
- 多帧 DICOM 帧切换
- 与 NavigationOverlay 集成（叠加层定位）
- 响应式尺寸适配

**3.4 创建** **`src/components/dicom/DicomToolbar.tsx`**

工具栏组件：

- 工具切换按钮组（窗位/缩放/平移）
- 窗位数值显示与调节滑块
- 缩放比例显示
- 重置视图按钮
- DICOM 信息覆盖层开关
- 帧导航（上一帧/下一帧/帧号显示）
- 未来医疗科技风格：暗色工具栏 + 冷蓝青图标

**3.5 创建** **`src/components/dicom/DicomOverlay.tsx`**

身份信息遮罩层：

- `SHOW_DICOM_OVERLAY` 配置项控制（默认开启）
- 遮罩患者 ID、患者姓名
- 保留显示：检查日期、设备型号、机构名称
- 半透明深色背景遮罩

**3.6 创建** **`src/hooks/useDicomViewer.ts`**

DICOM 查看器 Hook：

- cornerstone 初始化/销毁生命周期管理
- 影像加载状态（loading/loaded/error）
- 工具切换逻辑
- 窗位参数读写
- viewport 状态同步到 dicomStore

**3.7 验证**：加载 DICOM 文件，影像正常渲染，窗位可调节，身份信息遮罩生效

### Step 4: T-GA-FE-03 DualWebSocketManager

**4.1 创建** **`src/websocket/DualWebSocketManager.ts`**

核心管理器类：

- 双连接管理（导航通道 `/api/v1/realtime/nav` + DICOM帧通道 `/api/v1/realtime/dicom`）
- 连接建立与认证流程：
  1. 先建立导航通道，发送 auth 消息
  2. 收到 auth\_ok 后获取 token
  3. 用 token 建立 DICOM 帧通道
- 指数退避重连：1s → 2s → 4s → 8s → 最大 30s
  - 两个连接独立重连
  - 重连后自动恢复认证
- 心跳机制：5s 间隔发送 ping，3 次无 pong 判定断开
- 事件分发系统（发布-订阅模式）：
  - onNavigationMessage
  - onDicomFrame
  - onDiagnosisResult
  - onSessionTerminated
  - onConnectionStatusChange
- 连接状态追踪（同步到 websocketStore）
- 重连后会话 TTL 检查防抖：延迟 2s 查询，2s 内再次重连取消前次查询，连续防抖超 5 次强制查询

**4.2 创建** **`src/websocket/navChannel.ts`**

导航通道处理器：

- 导航标注消息解析 → 更新 navigationStore
- 诊断结果通知 → 触发轮询获取完整结果
- 安全校验移除通知（navigation\_revocation）→ 更新标注状态为 revoked
- 会话终止事件（session\_terminated）→ 清理会话状态
- 器官切换事件 → 更新 organSwitching 状态
- 低置信度标注处理：confidence < threshold 时 annotations 为空，前端提示"建议启动标准评估"

**4.3 创建** **`src/websocket/dicomChannel.ts`**

DICOM 帧通道处理器：

- 二进制帧解析：4B frame\_index（小端序）+ 1B flags + 4B dicom\_data\_length（小端序）+ N bytes DICOM data + 4B CRC32（小端序）
- CRC32 校验：校验失败丢弃帧
- 帧缓存管理：维护最近帧的缓存
- 帧索引映射：通过 DicomIndexMapper 转换

**4.4 创建** **`src/hooks/useSession.ts`**

会话管理 Hook：

- 创建会话
- 查询会话状态
- 结束会话
- 会话 TTL 倒计时
- 同步到 sessionStore

**4.5 验证**：双连接建立成功，心跳正常，断线重连指数退避，消息正确分发

### Step 5: T-GA-FE-04 导航标注叠加渲染

**5.1 创建** **`src/components/navigation/NavigationOverlay.tsx`**

导航标注叠加层：

- SVG 叠加层（覆盖在 DicomViewer 上）
- 绝对定位，与 DICOM 影像区域对齐
- 根据当前帧 ID 过滤标注
- 器官切换中状态处理：
  - 显示"器官模型切换中，导航暂停"提示
  - 收到 organ\_switching: false 后恢复正常渲染
- 后端不可达时：导航标注区域替换为"导航服务中断"提示 + 红色连接状态指示灯

**5.2 创建** **`src/components/navigation/AnnotationBox.tsx`**

单个标注框组件：

- 已校验（verified）：实线边框 #10b981 + 高亮填充（opacity 0.15）+ 实色标签
- 待校验（pending）：虚线边框 #f59e0b + 半透明填充（opacity ≤ 0.6）+ 闪烁"待校验"提示
  - 虚线间距 ≥ 4px（dashArray: "8 4"）
  - 闪烁动画 1.5s 周期
- 校验超时（timeout）：显示"导航服务中断"提示
- 后端校验移除（revoked）：标注消失 + 短暂红色闪烁 + "导航标注经安全校验已移除：\[原因]"提示
- 置信度显示（标签内）
- 低置信度标注帧处理：隐藏标注，显示虚线框黄色提示"AI置信度不足，建议人工评估"
- 无标注帧：静默处理，不显示任何 AI 标注

**5.3 创建** **`src/components/navigation/AnnotationLegend.tsx`**

标注状态图例：

- 已校验（绿色实线框）
- 待校验（琥珀色虚线框）
- 校验超时（红色）
- 校验移除（红色闪烁）
- 置信度阈值说明
- 器官级阈值信息

**5.4 创建** **`src/hooks/useNavigationAnnotations.ts`**

导航标注 Hook：

- 订阅 WebSocket 导航消息
- 标注状态更新（pending → verified / revoked）
- 校验状态转换逻辑
- 低置信度帧过滤
- 无标注帧静默处理
- 同步到 navigationStore

**5.5 集成到 ExaminationPage**

- DicomViewer + NavigationOverlay 叠加
- DicomToolbar 工具栏
- AnnotationLegend 图例
- SessionInfo 会话信息面板
- 帧切换时标注同步更新
- 标注与影像坐标对齐

**5.6 验证**：三种标注状态视觉区分明确，虚线间距 ≥ 4px，半透明度 ≤ 60%，1 秒内可识别状态差异

## 三、文件创建清单

### 需要新建的文件（共 20 个）

```
src/app/providers.tsx
src/app/routes.tsx
src/app/App.tsx
src/components/layout/AppLayout.tsx
src/components/layout/Sidebar.tsx
src/components/layout/TopBar.tsx
src/components/layout/StatusBar.tsx
src/components/common/StatusIndicator.tsx
src/components/common/LoadingOverlay.tsx
src/components/common/ErrorBoundary.tsx
src/components/dicom/cornerstone.ts
src/components/dicom/DicomViewer.tsx
src/components/dicom/DicomToolbar.tsx
src/components/dicom/DicomOverlay.tsx
src/components/navigation/NavigationOverlay.tsx
src/components/navigation/AnnotationBox.tsx
src/components/navigation/AnnotationLegend.tsx
src/websocket/DualWebSocketManager.ts
src/websocket/navChannel.ts
src/websocket/dicomChannel.ts
src/hooks/useDicomViewer.ts
src/hooks/useNavigationAnnotations.ts
src/hooks/useSession.ts
src/pages/ExaminationPage.tsx
src/pages/SessionCreatePage.tsx
src/pages/DiagnosisPage.tsx
src/pages/SettingsPage.tsx
```

### 需要修改的文件

```
src/main.tsx                    # 重写入口
tsconfig.app.json               # 添加路径别名
index.html                      # 更新标题
```

### 需要删除的文件

```
src/App.tsx                     # Vite 默认模板
src/App.css                     # Vite 默认样式
src/index.css                   # Vite 默认样式
```

## 四、关键技术决策

### 4.1 Tailwind CSS 版本

**决策**：降级到 Tailwind CSS v3

**理由**：

- tailwind.config.ts 已按 v3 格式编写
- postcss.config.js 是 v3 的 PostCSS 插件模式
- Tailwind v4 使用 CSS-first 配置，与现有配置文件不兼容
- v3 生态更成熟，文档更完善

### 4.2 antd 版本

**决策**：保留 antd\@6（实际安装版本），验证主题兼容性

**理由**：

- antd\@6 向后兼容 antd\@5 的 ThemeConfig
- 如果 ThemeConfig 类型不兼容，再降级到 antd\@5

### 4.3 cornerstone.js 版本

**决策**：使用经典 cornerstone.js v2 生态（cornerstone-core + cornerstone-tools + cornerstone-wado-image-loader）

**理由**：

- 项目文档明确指定 cornerstone.js
- v2 生态文档丰富，社区示例多
- v3（@cornerstonejs/）是重大重构，API 完全不同

### 4.4 React 版本

**决策**：保留 React 19（Vite 模板默认安装）

**理由**：

- React 19 向后兼容 React 18 API
- antd\@6 已支持 React 19

## 五、验收标准

| 任务         | 验收标准                     | 验证方法                            |
| ---------- | ------------------------ | ------------------------------- |
| T-GA-FE-01 | 开发服务器启动，未来医疗科技风布局渲染      | `npm run dev` 成功，页面显示深空医疗风格 UI  |
| T-GA-FE-02 | DICOM 影像渲染 + 窗位调节 + 身份遮罩 | 加载 DICOM 文件，影像正常显示，窗位滑块可调节      |
| T-GA-FE-03 | 双连接 + 指数退避重连 + 心跳        | 模拟断线，观察重连间隔 1→2→4→8→30s         |
| T-GA-FE-04 | 三种标注状态视觉区分               | 已校验(实线绿)/待校验(虚线琥珀)/超时(红色)视觉差异明显 |

