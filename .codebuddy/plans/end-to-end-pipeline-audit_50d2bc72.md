---
name: end-to-end-pipeline-audit
overview: 先收掉已知阻塞（4f 测量读数），再按 10 条管线分组对系统做全量端到端摸排：医生主流程（影像交互/判读/分割/报告）走真机走查复验，走查未覆盖的管线（认证生命周期/同意撤回/设备/告警/报告分发/FHIR 等）用落盘探针脚本逐一实测，P0 阻断缺陷当场修、其余登记，最后统一回写文档与提交。
todos:
  - id: fix-4f-measure
    content: 部署调试 build → probe_measure.mjs 抓 ANNOTATION_COMPLETED cachedStats 实际结构 → 修 handler 提取 → 移除调试日志与 debugToolState/window.__uaiCs → 走查 4f/4g 复验 PASS
    status: completed
  - id: p1p2-main-flow
    content: P1 医生主流程走查 21/21 复跑（真实 CAMUS 双帧）+ P2 影像交互探针（窗宽窗位/缩放/平移拖拽实测——A.38 假绿直接复验）
    status: completed
    dependencies:
      - fix-4f-measure
  - id: p4-auth
    content: P4 认证生命周期探针：login/token/verify/refresh/revoke-refresh/nav-token/token-stats 全序列落盘脚本实测
    status: completed
  - id: p5-consent
    content: P5 同意与撤回消费探针：GRANT→建会话→发判读→WITHDRAW→断言在途取消与 cancelled 终态（pipeline L438-466）
    status: completed
  - id: p6-device
    content: P6 设备生命周期探针：register/checkin/heartbeat/health/OTA 下载/deregister/recovery 全序列（probe 专用设备，不碰真实数据）
    status: completed
  - id: p7-alert-report
    content: P7 告警升级探针（webhook→active-alerts→CUSUM 校准→SG 阈值→contacts）+ P8 报告分发探针（push/verify/PATCH）
    status: completed
  - id: p9p10-aux
    content: P9 辅助管线探针（speech/voice/feedback/session PATCH/attributes/segmentation health）+ P10 FHIR 四资源 CRUD 探针
    status: completed
  - id: fix-and-rerun
    content: 缺陷分级处置：P0 当场修+复验，P1/P2 登记 feature_list；受影响管线探针重跑确认
    status: completed
    dependencies:
      - p1p2-main-flow
      - p4-auth
      - p5-consent
      - p6-device
      - p7-alert-report
      - p9p10-aux
  - id: wrap-a49
    content: 收尾：摸排结果汇总表（10 管线×结果）→ A.49 附录 + progress/handoff/feature_list 回写 + 门禁复跑 + commit/push/sync + /tmp 探针清理
    status: completed
    dependencies:
      - fix-and-rerun
---

## 需求：端到端管线全量摸排（列出管线 → 逐条排查）

用户原话：「走端到端摸排，列出要摸排的管线，一一排查过去！」

### 背景与动机

- A.41（多帧+标注持久化）已提交部署，但真机走查 4f（测量画线出 mm 读数）失败——排障已定位 `.ws-image__overlay` 挡事件（已修）与 ANNOTATION_COMPLETED 数据提取不匹配（调试日志已加未部署）。
- 用户推断意图：单点修 bug 不够——A.38 暴露过「按钮存在但拖拽全被 overlay 挡死」的假绿模式，要求**系统性把所有端到端管线过一遍**，逐一实测而非凭「代码存在」判定可用。
- 长期制度：摸排一律真实数据（服务器 ~/uai/data/camus/ CAMUS 序列经 scripts/camus_frame_to_dicom.py 现转 DICOM）；走查每步带耗时戳；无证据不得称已完成。

### 摸排对象：10 条管线（勘察定稿）

| # | 管线 | 覆盖方式 | 已知风险点 |
| --- | --- | --- | --- |
| P1 | **医生主流程**（登录→建会话→装载→判读→报告→签发→打印） | 走查脚本 21 判据复跑 | 4f 当前 FAIL |
| P2 | **影像交互**（窗宽窗位/缩放/平移拖拽、多帧滚动、帧指示器、翻帧） | 探针实测（A.41 已修 overlay 挡事件，需复验） | overlay 修复后未回归 |
| P3 | **测量与标注持久化**（画线读数 mm、PUT/GET annotations、帧级归属） | 探针 + 走查 4f/4g | ANNOTATION_COMPLETED 提取待修 |
| P4 | **认证生命周期**（login/token/refresh/revoke-all/nav-token/verify） | 落盘 API 探针 | 走查从未覆盖 |
| P5 | **同意与撤回消费**（consent GRANT/WITHDRAW → 在途诊断取消 → cancelled 终态） | API 探针 + DB 终态核查 | 走查只测正向态 |
| P6 | **设备生命周期**（register/checkin/heartbeat/health/OTA/deregister/recovery） | API 探针 | 全部未覆盖 |
| P7 | **告警升级**（webhook → active-alerts → CUSUM 校准 → SG 阈值 → contacts） | API 探针 | 全部未覆盖 |
| P8 | **报告分发与验证**（push 推送/verify 验签/PATCH） | API 探针 | 走查止步于已签发 |
| P9 | **辅助管线**（speech 识别/voice 确认/feedback/session PATCH/attributes hints/segmentation health） | API 探针 | 部分未覆盖 |
| P10 | **FHIR 互操作**（Patient/ImagingStudy/DiagnosticReport/Observation CRUD） | API 探针 | 全部未覆盖 |


### 排查纪律

- 每条管线产出：实测结果（PASS/FAIL + 证据）、P0 阻断缺陷当场修、P1/P2 登记不扩散
- 真实数据制度：影像用 CAMUS 现转 DICOM；探针脚本落盘 scp 执行（不内联 ssh，远端 fish）
- 全程记录：摸排结果汇总表 → 16 号附录 A.49

## 技术方案

### 现状依据

- 走查脚本 scripts/doctor_flow_walkthrough.mjs 实际 21 判据（P1/P2/P3 的载体），4f 当前 FAIL
- cornerstorne.ts onMeasureCompleted 调试日志已加、build 产物未部署——先部署抓 cachedStats 实际结构修 handler，再移除调试代码与 debugToolState/window.__uaiCs 调试钩子
- 后端 19 个 router（app/main.py L763-788）+ 2 WebSocket；前缀叠加坑（segmentation/attributes 自带 prefix）
- 探针模板：C:\Windows\Temp\probe_measure.mjs（Playwright 登录→建会话→装载→交互断言）与落盘 API 探针模式（本地 WriteAllText → scp → 远端 bash，凭据只走环境变量）

### 实施顺序

1. **收口 P3 阻塞**（测量读数）：部署当前调试 build → probe_measure.mjs 抓 cachedStats 实际结构 → 修 handler 提取 → 移除调试日志/debugToolState → 走查 4f/4g 复验
2. **P1+P2 复验**：走查 21/21 复跑（含 4h 流式字数）+ 影像交互探针（窗宽窗位/缩放/平移拖拽——A.38 假绿的直接复验）
3. **P4-P10 逐条探针**：每条管线一个落盘 bash/python 探针（curl API 序列 + 断言），按「认证→同意→设备→告警→报告分发→辅助→FHIR」顺序执行；P5 撤回消费额外核查 DB cancelled 终态（pipeline.py L438-466 语义）
4. **缺陷分级处置**：P0 阻断（医生流程走不通）当场修+复验；P1 功能缺陷登记 feature_list planned；P2 观察项记录
5. **统一收尾**：摸排结果汇总表（10 管线 × 结果）→ A.49 附录 + progress/handoff/feature_list 回写 → 门禁复跑 → commit/push/sync

### 探针工程规范

- API 探针统一模板：登录取 token → 逐端点 curl（-w 状态码+耗时）→ 断言预期 → 输出 PASS/FAIL 行；全部落盘 scripts/audit/ 入库（可复跑）
- 摸排只读优先：破坏性端点（deregister/revoke-all/consent WITHDRAW）用专用测试实体（probe 设备/探针会话），不碰真实数据
- 撤回消费探针链：GRANT→建会话→发判读→WITHDRAW→断言 pipeline cancelled 终态（pipeline.py L438-466）

### SubAgent

- **code-explorer**
- Purpose: 已完成勘察（路由清单/管线阶段/走查判据/未覆盖面），执行中若某管线探针失败需快速定位代码根因时再次使用（medium 强度）
- Expected outcome: 失败管线的根因定位（文件:行号），支撑当场修复