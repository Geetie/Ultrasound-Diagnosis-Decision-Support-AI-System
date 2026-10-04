# session-handoff.md — 会话交接

> 多会话/长任务专用。**Last Updated / Current Objective / Recommended Next Step / Blockers / Files / Next Session** 六项必填。
> 下一位 Agent 先读本节，再读 `progress.md` 与 `feature_list.json`。

## Last Updated

2026-10-04（HEAD `c71dad8`；交付冲刺：全链路真机打通，门禁 23/23，详见 16 号附录 A.23）

## Current Objective

**10-17 交付**：系统已达到"能用"基线（真机全链路 PASS），剩余为前端真机交互人工验证、
P1-13、交付文档与 NMPA 材料。技术侧无已知阻塞。

## Snapshot（交接摘要）

- **交付三大阻塞已修**（A.23）：①8002 缺 `/v1/chat/completions` → LLM 全链路 404，已换回
  官方 OpenAI server（Qwen2.5-7B-Instruct，gpu-mem 0.46）；②临床前端**从未部署**——已构建
  （VITE_BASE=/ui/）并挂载 `/ui` + SPA 回落，契约对账 18 路径全命中；③8006 判官被误改为
  vLLM，已恢复 llama_cpp_server + GGUF（**启动必须串行**，并发互踩 memory probe）。
- **P2-10 订正并修复**：多器官并未"绕开 SG"（逐器官跑完整管线），真实缺口是
  `create_request` 漏传 clinical_text/patient_context/image_ids → SG 无事实运行；
  已透传 + 从同会话继承，5 条单测钉住。
- **新守卫**：`test_deploy_script_refs.py`（部署脚本悬空引用——llama_cpp_server.py 与
  vllm_server.py 先后被当探针删除但脚本仍引用，同款事故两次）；`check_frontend_api_contract.py`
  （前后端契约对账——**不能用 ast.parse**，TSX 必炸且静默 continue 会产出假通过）。
- **验收门禁**：`scripts/deploy_acceptance_gate.sh` 8 组 23 项，当前 PASS=23 FAIL=0；
  助手脚本已正式入库（drill_token/drill_pick_report/drill_tamper_check），/tmp 探针已清零。
- 分割旁路默认**关闭**（`nodule_contour_in_pipeline_enabled=False`），演练时用
  `NODULE_CONTOUR_IN_PIPELINE_ENABLED=1` 环境变量开启；**必须在重启主服务前 export**。
- 多器官分割矩阵：thyroid 0.7961 / cardiac 0.8395（CAMUS，按患者划分）/ liver 无公开数据。
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
