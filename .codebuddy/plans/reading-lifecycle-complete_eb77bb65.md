---
name: reading-lifecycle-complete
overview: 按阅片全生命周期补齐缺口：多帧滚动+帧指示器（分期①收尾）、窗宽窗位/缩放/平移工具激活 UI、判读性能优化（分割权重启动预热+前端进度反馈）与生命周期盘点登记（测量/报告导出/历史对比分层）。
todos:
  - id: explore-touchpoints
    content: Use [SubAgent:code-explorer] 勘察三处改造点：cornerstone.ts displayImage/scroll API、WorkspaceImageRegion SVG 层、fusion service.py 计时插入位
    status: completed
  - id: warmup-segmentation
    content: 分割权重启动预热：start_main.sh 或 app lifespan 中后台空跑一次各 organ segmenter，验收=重启后首次判读 segmentation.bypass_ms < 500ms
    status: completed
    dependencies:
      - explore-touchpoints
  - id: fusion-timing-split
    content: fusion 计时拆分：fuse 内部拆 self_rag_llm_ms / crag_llm_ms 子计时写入 timings_ms，让 14s 可归因（不改 LLM 本身）
    status: completed
    dependencies:
      - explore-touchpoints
  - id: frontend-progress
    content: 前端感知：轮询间隔首秒后 1000ms + 「分析中 · 已用时 Ns」实时秒数显示
    status: completed
  - id: multiframe-scroll
    content: 多帧滚动：displayImage 多张 setStack + scroll 封装 + WorkspaceImageRegion 帧指示器「n/N ‹ ›」+ 键盘←→ + SVG 标注帧归属，vitest 覆盖单帧默认沉默/多帧指示器
    status: completed
    dependencies:
      - explore-touchpoints
  - id: tool-ui
    content: 浏览工具激活 UI：cornerstone.activateTool 封装 + 影像区工具胶囊组（窗宽窗位/缩放/平移互斥单选），vitest 覆盖激活态切换
    status: completed
    dependencies:
      - explore-touchpoints
  - id: print-view
    content: 报告打印视图：打印按钮 + @media print 样式（隐藏导航/按钮仅正文）
    status: completed
  - id: walkthrough-guards
    content: 走查脚本 +3 断言（帧指示器/工具胶囊/身份）+ 渲染守卫 +2 + feature_list 销号与远期登记
    status: completed
    dependencies:
      - multiframe-scroll
      - tool-ui
  - id: verify-ship-a38
    content: 全量验证（vitest/tsc/lint/build/契约对账/quality）→ C 盘构建部署 → 门禁 → 真机走查（含重启后首次判读 segmentation<500ms 验证）→ 重录视频 → A.38 回写 + commit/push/同步
    status: completed
    dependencies:
      - warmup-segmentation
      - fusion-timing-split
      - frontend-progress
      - multiframe-scroll
      - tool-ui
      - print-view
      - walkthrough-guards
---

## 需求：阅片全生命周期补齐（用户三个点）

用户看走查视频后提出三点：①视频里没看到多帧查看的拖动动作（分期①只交付了上传侧，帧滚动 UI 未做）；②等待 AI 分析 33 秒太慢；③要求从**阅片全生命周期**系统盘点 API 层与医生交互层还漏了什么，并补齐。

### 已勘察确认的事实

**性能（服务器 timings_ms 实测）**：

- 分割权重冷启动 5713ms → 热身后 50-62ms（服务启动未预热，最大单一收益点）
- fusion.fuse 0.7~14.1s 波动 = Self-RAG/CRAG 的 **LLM 校验调用**（app/fusion/service.py:136 validate + :164 crag_handler；代码注释自认"融合层不含 LLM < 500ms"预算，但计时把 LLM 包进去了）
- dispatcher（专科 LLM）1~8.6s
- pipeline.total 热身后 4.4s——用户感知的 33s = 冷启动轮次 20.4s + 前端 2s 轮询粒度 + 上传/首帧时间

**阅片浏览（缺口集中区）**：窗宽窗位/缩放/平移工具已在 cornerstone.ts ensureInit 注册，**但 UI 无任何激活入口**；帧滚动/播放缺；测量未做（P2）

**生命周期盘点**：开单✅/装载✅/浏览✗/**判读⚠️**/报告✅/**分发✗（无打印/PDF 导出，医生只能截图）**/历史对比✗（远期）/质控✗（Langfuse 已规划）

### 交付范围（用户拍板项：多帧滚动+性能+浏览工具本期做；报告导出做轻量打印视图；历史对比/标注持久化登记远期）

## 技术方案

### 1. 多帧滚动 + 帧指示器（分期①收尾）

- `useDicomUpload` 已聚合 `imageIds` 数组 → `WorkspaceDicomLayer`/`cornerstone.ts displayImage` 改为 `setStack(imageIds)` 多张（StackViewport 原生支持）+ 暴露 `scroll(distance)`/`currentIndex`
- `WorkspaceImageRegion` 右下角帧指示器「n/N ‹ ›」胶囊（多张时才渲染，单张默认沉默）；键盘 ←→ 翻帧；SVG 分割标注仅对有分割结果的帧渲染（帧归属：后端 segmentation.findings 带 frame_index）
- 走查断言：上传单帧样本 → 指示器显示「1/1」或隐藏（默认沉默口径：单帧不渲染指示器）；多帧样本走查登记为后续（服务器无多帧样本，需造）

### 2. 性能优化（先测量后动刀）

- **分割权重启动预热**：start_main.sh 服务就绪后（或 app lifespan startup）后台调用一次各 organ 的 segmenter（空跑 1 帧或 model warmup），消掉首诊 5.7s；验证 = 重启后首次判读 segmentation.bypass_ms < 500ms
- **前端感知优化**（不改后端也有收益）：①轮询 pollInterval 2000→1000ms（首秒后）②「分析中」界面加**已用时秒数**实时显示（降低焦虑，不动后端）③轮询粒度导致的"最后 2s"消除
- **fusion LLM 计时拆分**：fusion.fuse_ms 拆出 self_rag_llm_ms 子计时（先让 14s 可见可归因；LLM 本身提速属远期性能提升阶段，本轮只做测量与展示，不在本次优化模型调用）
- 验收：热身轮次端到端（上传→结论）目标 ≤ 15s；timings 三段拆分后可归因

### 3. 浏览工具激活 UI（阅片缺口收口）

- cornerstone.ts 增加 `activateTool(element, toolName)` 封装（setToolMode ACTIVE/ENABLED）
- `WorkspaceImageRegion` 左上角工具胶囊组：窗宽窗位/缩放/平移三键（已注册工具，零新依赖），激活态高亮 attention；单选互斥（激活一个其余 ENABLED）
- 帧指示器与工具组同处影像区角落，均遵循"默认沉默"（无影像不渲染）

### 4. 报告导出（轻量：打印视图）

- 报告页加「打印/导出 PDF」按钮 → `window.print()` + `@media print` 样式（隐藏导航/按钮，仅报告正文）——零后端改动，覆盖"给患者/病历系统"的最小需求；完整 PACS/HIS 推送登记远期

### 5. 走查脚本与守卫同步

- doctor_flow_walkthrough.mjs：+帧指示器存在断言（单帧时隐藏或 1/1）、+工具胶囊组存在断言、+身份断言（身份不写死）
- 渲染守卫：+「工具激活入口存在」（WorkspaceImageRegion 须含工具切换 UI）
- feature_list：帧滚动 done 销号；登记「标注持久化」「报告 PACS/HIS 推送」「历史对比」为远期

### SubAgent

- **code-explorer**
- Purpose：实施前快速核对三处现状——①cornerstone.ts 的 displayImage/setStack 与 IStackViewport scroll API 可用性；②WorkspaceImageRegion 的 SVG 标注层结构（帧归属改造点）；③fusion/service.py 的 validate/crag 调用处插入子计时的准确位置
- Expected outcome：一份带行号的改造点清单，避免盲改