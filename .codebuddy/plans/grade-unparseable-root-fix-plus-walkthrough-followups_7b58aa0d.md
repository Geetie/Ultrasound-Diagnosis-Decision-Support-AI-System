---
name: grade-unparseable-root-fix-plus-walkthrough-followups
overview: 根治专科 Agent 的 grade_unparseable/lvef_unparseable 降级（把已有的分级约束解码 schema 接入 specialist 链路、统一 4a/4b↔TR4 口径、降级文案改为可读中文并保底重试），并把本轮 p26 梯队端到端走查发现的其余问题（器官错配、报告入口点击不切换、零静默登记）一并纳入修复与门禁。
todos:
  - id: inference-structured-output
    content: "打通采样层结构化输出：base.py:509-520 经 extra 透传 response_format(json_schema)，vllm.py:146-176 按硬规则 #5 增加 response_format 并以 guided_json 回落，llama.cpp 走 GBNF 能力探测；补单测"
    status: completed
  - id: specialist-canonical-grade
    content: 专科 Agent 分级链路统一：thyroid/liver/cardiac 三处 _parse_response 改用 grading_definitions 的 canonical 抽取并按 _GRADE_HIERARCHY 主级映射（4a/4b/4c 收敛为 TR4），保留器官正则二级兜底；同步硬化三个 prompt.md（建议→硬约束 + "无法判断"可判定态）
    status: completed
    dependencies:
      - inference-structured-output
  - id: degrade-readable-retry
    content: 降级可读化与有界重试：技术串移出医生可见正文（改可读中文 + reasoning_trace 技术细节 + needs_review=True），抽取失败时用约束解码重试至多 1 次，并识别 finish_reason=length 截断；新增零技术串进正文的守卫测试
    status: completed
    dependencies:
      - specialist-canonical-grade
  - id: obs-and-degrade-gate
    content: 可观测与门禁：日志补 organ/extracted_grade/finish_reason/llm_raw_len/retry_count（不落 PHI），新增 scripts/audit/p27_grade_extraction.py 统计降级率并给退出码；用 [skill:remote-gpu-server-devtest] 在服务器实测 response_format 与 guided_json 有效性、跑 p27 并把真实数字入档
    status: completed
    dependencies:
      - degrade-readable-retry
  - id: organ-mismatch-fix
    content: 修器官错配（临床安全）：frontend/src/types/session.ts:15-25 ORGAN_PRESETS 补 'cardiac' 并同步 SessionCreatePage.tsx:320-332 部位按钮；影像与声明部位不符时给可读提示而非静默出结论；真机浏览器验收（沿用 p26 医生视角并补截图）
    status: completed
  - id: report-entry-nav-fix
    content: 定位并修"点工作区报告入口后 URL 已切但视图不切换"：基于 d0-report-nav-stuck.png 现场与直接导航反证，定位 SPA 切换环节并修复；用 node scripts/audit/p26_squad_e2e_walkthrough.mjs 回归至 T1-7c PASS
    status: completed
  - id: harness-writeback
    content: 状态回写与登记：用 [skill:uai-harness-maintenance] 更新 feature_list.json（新增 grade-extraction-robustness / organ-image-consistency / report-entry-nav，登记语音入口与 admin/device 权限口径）、progress.md、session-handoff.md，追加 16 号附录 A（A.56），并跑 harness 审计 ≥70
    status: completed
    dependencies:
      - inference-structured-output
      - specialist-canonical-grade
      - degrade-readable-retry
      - obs-and-degrade-gate
      - organ-mismatch-fix
      - report-entry-nav-fix
---

