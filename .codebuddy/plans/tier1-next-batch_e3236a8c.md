---
name: tier1-next-batch
overview: 执行第一梯队四项：①qdrant 检索质量口径闭合（服务路径 vs eval 直连口径对比，含 kb_version 一致性核查）②多帧分期③帧级归属精确化（分割帧元数据透出/报告帧号/前端展示）③设备列表 + FHIR 患者搜索端点（含探针）④fusion-validate 复测与孤儿预算配置接线。全部按「开发本机 / 测试服务器」制度，产出真机证据并回写 harness。
todos:
  - id: a-retrieval-parity
    content: 新建 scripts/audit/p15_retrieval_parity.py：核查 kb_version 口径（.env/qdrant payload/PG 三处）+ 136 golden 双口径 Top10 对比，真机跑出 ≤2pp 判定并用 [skill:remote-gpu-server-devtest] 留证
    status: completed
  - id: d-validate-baseline
    content: 新增 validate 基准探针：真实判读 N≥6 轮采集 fusion.validate_ms，输出 P50/P95/P99 现状数字（先量后改，入 16 号附录）
    status: completed
  - id: d-validate-degrade
    content: 按测量结果接线孤儿预算 pipeline_fusion_budget_s（validate 超时→needs_review 降级 + timings）+ 非 FULL 层跳过 Self-RAG LLM；复测 P99
    status: completed
    dependencies:
      - d-validate-baseline
---

## 需求概述

继续执行「第一梯队」四项远期目标的开发（用户上一轮选定，本轮落地）：

## 代办项

1. **qdrant 检索质量口径闭合**（A.50.8 遗留）：qdrant 已上线并服务判读，但 Gate 2 的 98.53% 来自 eval 脚本直连 PG 语料的进程内余弦口径，**与生产服务路径（Retriever→qdrant）是否等价未验**；同时存在口径隐患：`settings.rag_kb_version='kb_v1.0.0'` 与 qdrant 灌入的 kb_v1.3.0 可能不一致（`.env` 是否覆盖待查）。
2. **多帧分期③帧级归属精确化**：分割旁路现只取该 session 第一张影像（`rows[0]`），单文件多帧时固定取中间帧（`n_frames//2`）；findings 里虽有 `frame_index/total_frames`，但无「择优帧」语义、报告层无帧号呈现、前端 DICOM 层不展示分割。
3. **设备/患者列表端点**：`device.py` 无 list 端点、`device_repository` 无 list 方法；系统**无 patients 路由**（患者存在性只走 FHIR `GET /fhir/Patient/{id}` + consent），走查流程因此用「校验 + 手工录入」补位，工作列表页靠 `GET /api/v1/sessions`。
4. **fusion-validate 降载**：dispatcher 已达标（P50 1.34s），但 `fusion.validate_ms`（含 Self-RAG LLM 校验，Layer2/3 已并行）**从未复测**；另发现 `pipeline_dispatch_budget_s` / `pipeline_fusion_budget_s` 为**孤儿配置**（全仓无读取点），validate 级无超时/降级开关。

## 验收判据（逐项）

- 检索：服务路径 Top10 命中率与 eval 口径差 **≤2pp**，且 kb_version 口径对齐结论有据可查。
- 多帧③：多帧 DICOM 判读后分割结果**标明择优帧号**（含评估帧数），前端只在对应帧呈现分割、其余帧不显示；报告/结论可见帧号。
- 列表端点：`GET /api/v1/admin/device`（分页+过滤）与患者列表/搜索端点真实可用，前端工作列表/建会话数据驱动，探针全绿。
- validate：复测 `fusion.validate_ms` P50/P95，**P99 < 1.5s**；若超线则以「validate 级预算 + 优雅降级」收口并实证降级路径（needs_review=True 而非失败）。

## 约束与制度

- 开发一律本机、测试一律学校服务器（student03@10.20.28.21）；先加载 SKILL `remote-gpu-server-devtest` 铁律。
- 真机证据回写 16 号附录 A；探针入 `scripts/audit/`；git archive 同步=HEAD 镜像（先 commit 再同步）。
- E5 已挂 blocked，本轮不做；不动他人资源、服务只绑 127.0.0.1。

## 一、技术栈与现状（已核实）

- 后端 FastAPI + SQLAlchemy(async) + Redis + PG(pgvector)/Qdrant；前端 React+TS+Vite（Cornerstone3D 5.11.3）。
- 生产检索：`app/rag/retriever.py:142 Retriever.retrieve` → `_dense_search:416` → `_vector_client.search(...):430-437`；向量客户端由 `app/embedding/index_factory.py` 工厂装配（`create_index_client_from_settings():184`），注入点 `app/main.py:322,376-384`。
- eval 口径脚本（全部绕开服务）：`scripts/gate2_bge_top10.py:29-53,89`、`scripts/t1_eval_retrieval.py:52,119,140`（进程内 dense/BM25/RRF）。
- 分割旁路：`app/services/diagnosis_pipeline.py:313` → `_safe_nodule_contour:1240`（`rows[0]:1288`、`extract_frame_from_bytes:(1305)` 无 frame_index）→ `app/imaging/dicom_pixels.py:79,91`（None→中间帧）；findings.segmentation 已含 `image_id/frame_index/total_frames`（`:1316-1334`，落库 `:945-947`）。
- 前端：`WorkspaceDicomLayer.tsx:139-142`(imageIndex→frameIndex)、`:182`(serialized.frame_index)、`:224-229`(referencedImageId 优先/frame_index 回退)；本层不展示分割。
- 融合层：`app/fusion/service.py:102 fuse` → `:142 validate`（`t_validate:140`，`validate_ms:153`），Self-RAG 在 LayeredValidator 内（Layer2/3 `asyncio.gather` 并行）；timings 键 `fusion.validate_ms:374`、`fusion.crag_ms:375`；孤儿配置 `settings.py:244-245`，生效仅 `pipeline_total_budget_s:256`（`diagnosis_worker.py:224`）。

## 二、实现方案（逐项）

### A. 检索口径闭合（新建对比探针 + 口径核查，无生产代码改动优先）

1. **口径核查**：`scripts/audit/p15_retrieval_parity.py` 先断言三件事——服务器 `~/uai/.env` 是否覆盖 `RAG_KB_VERSION`；qdrant collection（`ultrasound_knowledge_chunks`，A.50.8 灌入 3269 点）中 payload 的 `kb_version` 分布；PG `vector_index_knowledge_chunks` 的 kb 分布。三者不一致即**口径缺陷**，如实记录并给出对齐动作（改 .env 或重灌）。
2. **同集对比**：读服务器 `~/uai/data/golden/kb_qa_golden_v1.json`（136 queries），**两条腿**在同一进程内跑：

- 服务路径腿：直接构造 `Retriever`（复用 `create_index_client_from_settings()` + 8004 嵌入），调 `retrieve()`，统计 Top10 命中（与 t1 口径的 query→chunk 命中定义一致）。
- eval 腿：复用 `t1_eval_retrieval.cosine_topk` 直算，复现 Gate 2 数字。
输出 `reports/retrieval_parity_<date>.json`：两侧 Top10/R@10/MRR + 差值 + 判定（≤2pp PASS）。

3. 不改生产代码（除非核查发现 kb_version 口径错配——那属于真缺陷，改 `.env` 并记录）。

### B. 多帧③帧级归属（后端择优帧 + 前端按帧呈现）

1. **择优帧（后端，最小侵入）**：`_safe_nodule_contour` 由「取 rows[0] + 中间帧」升级为：

- 多张 image_ids → 逐张取代表帧（沿用中间帧策略）跑分割，**在 ≤3 张内选 mean_prob 最高者**（避免 O(N) 全栈推理）；
- 单张多帧 DICOM（`frame_count>1`）→ 采样 `{mid, mid±k}` 至多 3 帧择优（`extract_frame_from_bytes(content, frame_index=...)` 已支持传帧号）；
- 返回结构增补 `frames_evaluated`、`selection_policy="best_mean_prob"`、逐帧简表（帧号+概率，不含掩膜，控制体积），保留现有 `image_id/frame_index/total_frames`。

2. **报告/结论帧号**：`build_sg_output_data`/结论文本渲染处补「第 X/Y 帧（择优）」字样（仅当 `total_frames>1`），保证医生可见帧归属。
3. **前端按帧呈现**：`WorkspaceDicomLayer.tsx` 新增轻量分割覆盖层——当 `segmentation.available && segmentation.frame_index === frameIndex` 时渲染掩膜 PNG（base64，半透明叠加，`pointer-events:none`），翻到其他帧自动隐藏（复用现有 frameIndex 状态与重渲染路径，避开 A.41 的 overlay 挡事件坑）。
4. **测试**：后端单测（择优逻辑、帧采样、结构字段）；前端 vitest（按帧显示/隐藏分支）。

### C. 设备/患者列表端点（补真实数据源，前端接线）

1. **设备列表**：`device_repository` 加 `list_devices(status/device_type/offset/limit)->(rows,total)`；`device.py` 加 `GET /api/v1/admin/device`（admin Bearer，返回 `{items,total,offset,limit}`，字段脱敏：**不回 api_key 原文**）。
2. **患者列表/搜索**：新增 `app/api/v1/patients.py` → `GET /api/v1/patients?q=&limit=&offset=`：数据源以 `fhir Patient` 表为主（fhir.py 已有 Patient CRUD），LEFT JOIN 最近 consent 状态，返回 `{patient_id,name,gender,consent_status,last_session_at}`；只读、分页、`get_current_active_user`。
3. **前端接线**：`SessionCreatePage` 的患者入口由「手工录入+校验」改为**搜索下拉**（调新端点）；`WorklistPage` 保留 sessions 数据源但补「按患者筛选」可选；不推翻现有走查路径（保留手工录入兜底，避免破坏 21 判据）。
4. **探针**：`scripts/audit/p16_list_endpoints.py`（设备 list 权限/分页/无 api_key 泄漏 + 患者搜索命中/未授权 401）。

### D. fusion-validate 复测与降载

1. **先测量**：扩 `scripts/audit/p14_dispatcher_bench.py`（或新增 `p14b_validate_bench.py`）——N≥6 轮真实判读，采集 `fusion.validate_ms`，输出 P50/P95/P99，**先落现状数字**（与 E14 同款「先量后改」纪律）。
2. **降载（按测量结果择一/组合，保持语义不降级为错误）**：

- **接线孤儿预算**：把 `pipeline_fusion_budget_s` 真正用于 validate——`fusion/service.py` 的 validate 调用外包 `asyncio.wait_for`，超时则 `needs_review=True` + `review_reason="fusion_validate_budget_exceeded"`（与既有 `:143-152` 抛错降级同语义），并打 timings；
- **Self-RAG 分层门控**：`response_tier != FULL`（FAST/COMPACT）时跳过 Self-RAG LLM（保留启发式），记 `timings["fusion.self_rag_skipped"]=True`；
- 若仍超线才考虑 prompt 瘦身/批内合并（避免无证据的重构）。

3. **验收**：复测 P99<1.5s；并**显式实测降级路径**（注入短预算 → 断言 needs_review 且结果仍返回，不 5xx）。

## 三、执行与验证制度

- 顺序：A（探针，只读）→ D（先量后改）→ B/C（代码+前端）。
- 每项真机跑探针取证；批量提交后 `git archive` 同步 + 门禁（PASS=24 基线不得退化）+ 新增测试本地全绿（unit/api、vitest）。
- 文档回写：16 号附录 A 新增一节（含实测数字与口径结论）、21 号清单/路线图刷新、progress/session-handoff、feature_list（新增 device-list/patient-list/fusion-validate 条目与 multi-frame-ph3 进度）。

## 四、风险与规避

- **kb_version 口径错配**（A 的最大风险）：先核查再对比，错配则停下修口径，不拿错配数字下结论。
- **多帧采样增加判读耗时**：限 ≤3 帧且复用同一 session 内分割单例；`segmentation.bypass_ms` 纳入 timings 监控（当前 ~百 ms 级）。
- **前端分割覆盖层重蹈 A.41 overlay 挡事件**：新层强制 `pointer-events:none` 并加 vitest 断言。
- **validate 预算设太紧误杀正常请求**：默认沿用配置值（6.0s）并只做「超时降级」，不在测量前调小。

## Agent Extensions

### Skill

- **remote-gpu-server-devtest**
- Purpose: 服务器侧部署/重启/真机探针/门禁执行时遵循铁律（fish 落盘脚本、CUDA_VISIBLE_DEVICES=0、不碰他人资源、真机数字回写）
- Expected outcome: 四个探针在服务器上可复跑并产出可入档的实测数字（检索对比/validate 时延/列表端点/多帧分割）
- **uai-harness-maintenance**
- Purpose: 本轮收尾状态回写（16 号附录 A、21 号清单、progress/session-handoff/feature_list）与 harness 审计
- Expected outcome: 单一事实源更新无重复、审计分数不低于 70（目标满分）

### SubAgent

- **code-explorer**
- Purpose: 探针失败时快速定位根因（如检索对比差异来源、validate 超时点），以及前端分割覆盖层接入点的二次确认
- Expected outcome: 每处差异/失败有 文件:行号 级根因，支撑当场修复而非猜测