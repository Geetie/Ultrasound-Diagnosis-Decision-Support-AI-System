# session-handoff.md — 会话交接

> 多会话/长任务专用。**Last Updated / Current Objective / Recommended Next Step / Blockers / Files / Next Session** 六项必填。
> 下一位 Agent 先读本节，再读 `progress.md` 与 `feature_list.json`。

## Last Updated

2026-10-03（HEAD `957901a`；并发与性能审查已收口）

## Current Objective

Harness 工程收口：把项目现状、踩坑、禁则、可复用流程沉淀为 Agent 可快速掌握的文档与 SKILL 体系
（`AGENTS.md` + 状态三件套 + `init.sh` + 两个 SKILL），使后续会话可直接 clean restart。

## Snapshot（交接摘要）

- 项目处于**生产化收口期**：技术轨道已达标（检索/推理/融合/SG/降级/可观测），剩余为口径决策、外部数据与 NMPA 交付物。
- 本轮 harness 变更：新增/重写 `AGENTS.md`、`feature_list.json`、`progress.md`、`session-handoff.md`、`init.sh`；
  新增 SKILL `uai-harness-maintenance`；更新 SKILL `remote-gpu-server-devtest`。
- 未跟踪文件待处置：`scripts/_verify_services.sh`、`scripts/server_restart_api.sh`、`scripts/_smoke_graph.py`。

## 并发与性能审查（2026-10-03，本轮）

- **发现 26 项（P0×4 / P1×12 / P2×10），已修 17 项**，全部零语义风险项（加锁/信号量/上界/
  超时/回收/offload）；9 项未修并写明理由（其中 2 项需产品决策、1 项是 HEAD 既有 INT 失败）。
- 关键 P0：①多器官联合诊断**原先完全无限流**；②**refresh token rotate 的 check-then-act
  竞态**（同一 token 可派生两个有效后继 → reuse 检测被绕过）；③WS 下行**持锁 await
  send_json**；④登录 bcrypt + HSM 签名、onnxruntime、pydicom 解析在事件循环里同步执行。
- 关键 P1：诊断管线**无端到端超时**（`pipeline_dispatch/fusion_budget_s=6.0` 是从未被读取的
  孤儿配置，最坏 ~240s 占死全部 worker 槽位）→ 新增 `pipeline_total_budget_s=180`。
- 报告：`reports/concurrency_perf_audit.md` / `.json`；压测：`reports/concurrency_perf_report.json`
  （脚本 `scripts/bench_concurrency_staircase.py`，1/5/10 走 HTTP 多医生、20/50 走 direct_enqueue）。
- 回归：新增 46 例（4 个 `test_concurrency_audit_fixes.py`）；核心四包覆盖率 **91%**（上轮 90.04%）。
- **真机遗留**：接手时 8002 未监听（被并行工作线开 GPU 窗口时 `pkill VLLM::EngineCore` 连杀），
  本轮以 `--gpu-memory-utilization 0.40` 拉起 Qwen2.5-7B-Instruct（与判官 8006 共卡）。

## Blockers

| 阻塞 | 谁解 | 备注 |
|---|---|---|
| faithfulness 95% 门控口径 | 项目组 | 判官侧已穷尽（双口径 + 思考对照三方验证） |
| 甲状腺注册数据 | 管理决策 + 授权 | 无商用公开集，须自有/合作医院 |
| 核心覆盖率 90% | 真机依赖 | SoftHSM / 真实 Redis 语义 |
| 服务器代码与仓库同步 | 本机可做 | `git archive` 铺 HEAD + 重启主服务 |

## Files（交接涉及的路径）

- Harness：`AGENTS.md`、`progress.md`、`feature_list.json`、`session-handoff.md`、`init.sh`
- Skills：`.codebuddy/skills/remote-gpu-server-devtest/{SKILL.md,reference.md}`、`.codebuddy/skills/uai-harness-maintenance/{SKILL.md,reference.md}`
- 事实源：`docs-for-guiding generating docs/超声诊疗决策支持系统/{00-项目总览与核心指标.md,16-开发进展汇报-20260928.md}`
- 验证：`Makefile`、`pyproject.toml`、`.github/workflows/{ci.yml,security-scan.yml}`

## Next Session

1. 读 `progress.md` → 执行 `Recommended Next Step`（口径决策 / UDT 送审 / 清收未跟踪文件）。
2. 若触服务器：加载 SKILL `remote-gpu-server-devtest`，先读「铁律（先读）」5 条。
3. 收尾按 `AGENTS.md` 的 `End of Session` 走，并跑 harness 审计（分数 ≥70）。

## Restart Markers（clean restart 检查）

- [ ] `./init.sh` 通过（或说明为何跳过）。
- [ ] `progress.md` 的 Last Updated / Current Objective / Recommended Next Step 已刷新。
- [ ] `feature_list.json` 状态迁移已记录（含 evidence）。
- [ ] 新结论已追加 `16` 号附录 A（如涉及实测数字/决策）。
- [ ] 远程 `/tmp/_*` 已清、GPU 进程清单核对（嵌入×3 + vLLM 8002）。
