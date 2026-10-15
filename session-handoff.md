# session-handoff.md — 会话交接

> 多会话/长任务专用。**Last Updated / Current Objective / Recommended Next Step / Blockers / Files / Next Session** 六项必填。
> 下一位 Agent 先读本节，再读 `progress.md` 与 `feature_list.json`。

## Last Updated

2026-10-15（分支 `main`；**A.66 已交付** —— ①确诊评测仪器饱和（p40）②自造两类难集（p41）③难集复评：**扩边真有召回增益，但当前排序转不成收益 → `NO_GO`**（p42）④长文档家底量化：**257 篇指南/规范文档（434 块）已在库、边覆盖完整 → 阻塞点不是语料**。上一轮 A.65 离线仿真判定亦已交付）。

- **A.66①仪器确诊（p40）**：136 条 golden 分层 **top-1 = 129 / top-3 = 4 / top-10 = 1 / miss = 2**（累计 hit@10 **0.9853**）→ `SATURATED`；最弱科室 **肿瘤科 hit@3 0.9333（n=45）**；根因＝golden 的 query **全取自对话语料**（`t1b_build_qa_golden.py` 只吃 `dialogue_docs.jsonl`）→ 从未考过"找指南知识/跨文档邻居"。顺手修掉我自己的判定 bug：把"第 4~10 名"那层占比当累计 hit@10（已抽成 `instrument_verdict()` + 守卫）。
- **A.66②自造难集（p41，产物带 `synthetic: true` + `usage: expansion_ablation_only`）**：**crossdoc 200 条**（同 `(organ, section_path)` 组取 A 的问句、gold = B 另一篇文档；规则校验丢 11 条重叠过高）+ **guideline 40 条**（走 8006 Qwen3-8B 生成，丢 2 条重叠 + 1 条源文过短）。构造期两个真事实：**KB 内容已脱敏**（1257 块里含 `患者提问` 的 = 0，该词变成 `[REDACTED]：` → 问句必须回未脱敏源 `~/uai/data/kb_sources/dialogue_docs.jsonl`）；**8002 是 Qwen2.5-7B 且 `/v1/chat/completions` 404**（改用判官 8006）。
- **A.66③难集复评（p42；前置成立性 + 与 p39 同源三臂/P0P1P2）**：
  | 集 | 难集成立 | hit@5 baseline / 任取 / 排序 | 池子召回 | 裁决 |
  |---|---|---|---|---|
  | crossdoc（200） | ✅ gold 不在 top-10 占 **0.955** | **0.015 / 0.0192 / 0.015** | **0.045 → 0.195** | **`NO_GO`**（P0✅ P1✅ **P2❌**） |
  | guideline（40） | ❌ 只 0.10 | 0.85 / 0.18 / 0.85 | 0.90 → 1.00 | `NO_GO_INSTRUMENT_NOT_READY`（不出结论） |
  **这改写了 A.65**：扩边**确实有召回价值**（+15pp），但"用 query 相似度排序"这个假设是错的 → **真问题是换选择器信号**（结构/角色，或交给真 reranker）。另：指南检索**不弱**（baseline 0.85），推翻我的猜测。
- **A.66④长文档家底（只读分组统计）**：1057 文档＝**指南/规范类 257 篇（434 块）** + 对话类 800 篇（823 块）；**多块文档 121 篇、其中 120 篇是指南/规范类**（2~10 块，均 400~520 tok）；**多块但零边 = 0** → **长文档不缺、边也不缺**；A.64/A.65 里"前置＝语料先有长文档"的说法**就地收窄**为"缺的是邻居选择器"。附带登记：`source_doc_id` 粒度两语料不一致（指南类出现按块形如 `..._0001`）。

### A.66 复跑入口与踩坑

- **①难度体检**（服务器，需 8004+DB）：`~/uai/venv/bin/python scripts/audit/p40_golden_difficulty_profile.py`
- **②造难集**：`... scripts/audit/p41_build_hard_query_set.py --mode crossdoc`（确定性，0.2s）
  ／ `--mode guideline --gen-url http://127.0.0.1:8006 --gen-model Qwen3-8B`（**别用 8002**：它是 Qwen2.5-7B 且 chat 端点 404）
- **③复评**：`... scripts/audit/p42_reeval_expansion_on_hard_set.py --set crossdoc`（退出码 1 = 仪器不成立）
- **踩坑**：
  1. **仪器饱和会给出完全错误的结论**：同一件事（扩边）在饱和集上"零增益"、在真难集上"+15pp" → **先确认仪器能不能测**；
  2. **正则口径会随脱敏漂移**：`患者提问` → `[REDACTED]`（KB 0 命中）→ 问句类一律回**未脱敏源**；
  3. **端口/模型别靠记忆**：探测一次（`/v1/models` + 一次真实调用）比改三次代码便宜；
  4. **`source_doc_id` 粒度不一致**（对话按篇、指南出现按块）→ 跨语料做"文档级"统计必须写明。

- **A.65 WP1（本轮唯一开发项）图谱扩边 + 邻居排序：先仿真、后否决**。探针 `scripts/audit/p39_graph_neighbor_ranking_sim.py`（**离线不写库**；复用 `t1_eval_retrieval.{embed_texts,cosine_topk}` 与 `gate2_bge_top10.doc_level_hit_at_k`，一把尺子）。136 条 golden、同一候选池（跨文档同章节，与 `p38 --coverage` 同口径）三臂对照：
  | 臂 | hit@5 |
  |---|---|
  | baseline（现状 top-5，不扩边） | **0.9853**（与 p36/p15 逐位一致 → 口径自证） |
  | 任取 5（同池随机，20 次采样均值） | **0.0963** |
  | 按 query 余弦重排取 5（模拟生产 rerank） | **0.9853** |
  池子召回：现状 top-10 **0.9853** → 扩边后 **0.9853**（**零增益**）→ 判定 **`NO_GO_NO_RECALL_GAIN`**（P0 零增益 False；P1 不退化 True；P2 排序有效 True）→ **不建 rc、不写边、`graph_query_enabled` 维持 False**。
- **两条硬结论（都可复跑）**：①**裸扩边（不排序）会把 hit@5 从 0.9853 稀释到 0.0963** —— 独立验证 A.64 的 `SUPERNODE_RISK`；②跨文档同章节边**零召回收益**（现状 top-10 已含 gold 98.53%，天花板只剩 1.47pp）→ **真正的前置条件不是排序，而是语料先有可分的长文档**（与 A.64.2 的结论互相印证）。
- **判据本身修过一次（值得记）**：首跑只写了 P1/P2 → 报告给出 **虚假 `GO`**，而同一份报告里池子召回明明是零增益 → 当场补 **P0（扩边必须真把 gold 拉进池子）**，并把首跑真实数字写成守卫回归锚点。**"不退化"不等于"有价值"。**
- **踩坑（会害死探针）**：`Retriever(session_factory=None)` 时每个命中都是 `_placeholder_chunk`（`app/rag/retriever.py:624`：`content="[placeholder]"`、**`source_doc_id=chunk_id`**）→ 按来源/内容的统计**静默全 0**（首跑 hit@5 = 0.0/0.0/0.0）。修法：探针一律传真实 `AsyncSessionLocal`、`source_doc_id` 从 DB 解析、并新增 `count_placeholder_chunks()` + **占位块占多数即响亮失败**（退出码 2，不产出报告）。

- **A.64 WP1 TN3K 口径判定**：旧报告 `tests/reports/t2a_tn3k_generalization.json`（AUC 0.6789 / n=1228）是**孤儿产物** —— 全仓**无生成脚本**（schema `malignancy_auc`/`acc_at_0.5` 唯一）、`n=1228` 与 TN3K 官方 test **614** 张不符；资产只有一条链（`models/t2a_v2/t2a_finetune.pt` sha256 `7a5849d5…` + `t2a_probe_lr.joblib` `40bfa6b4…`；回退路径不存在，8003 的 `T2A_*` env 与启动日志三处互证）。真机同尺子复跑：正向 **0.4929 [0.4438,0.5402]**、**标签反向腿 0.5071**、**Youden 最佳阈值 0.799 → sens 0.2331 / spec 0.8254（和 1.06）** → 判定 **`ORPHAN_UNREPRODUCIBLE`**。处置：旧 json 加 `_status: VOIDED` + `_voided_reason` + `_replacement`（原字段保留）；16 号 A.63.3 就地改为「已判定：见 A.64.1」。附带登记：t2a 有**四条链四套 AUC 实现** → 新探针一律只用 p29 的秩和（守卫 `test_t2a_metric_single_source.py`）。
- **A.64 WP2 知识库结构与图谱扩边（两项都否掉，有数）**：`p38` 结构体检＝**1257 块 / 1057 文档、单块文档 936（88.5%）**、token 中位 **146**（<256 占 68.8%）、带边块 321（25.5%）、`section_path` 非空 100% → 诊断 `MAJORITY_SINGLE_CHUNK_DOCS`：**overlap 类切分改造对 88.5% 的文档零影响 → 不做**。`p38 --coverage` 量化跨文档同章节边：适用面可达 **136/136（100%）**、种子 408/408，但**候选池 51262 条 = 每种子 125.6** 而检索器只取 5 → **`SUPERNODE_RISK`**（灌噪声）→ 按预注册判据（指标不得回退无法保证）**不建 rc、不切生产**。前置条件：**先做邻居排序（相似度选 top-k）**；语料侧根治仍是长文档结构化入库（外部）。
- **A.64 WP3 一页纸**：两项指标口径（0.0906 / 0.3788 vs 门限 0.95/0.90 → 建议仍不切 gate，三条可选语义方案）、t2a 三口径引用规则（对外只引公开集下界）、评测集 700 例 / 医生标注 50 例成本（1~2 人小时）、甲状腺数据（NC 集可用于研发；注册仍需自有数据；TNSCUI 实际不可得）。见 16 号 A.64.3。

### A.64 复跑入口与踩坑

- **WP1 口径对照**（服务器，需 8003）：`~/uai/venv/bin/python scripts/audit/p37_t2a_tn3k_parity.py --report ~/uai/reports/t2a_tn3k_parity.json`
  → 退出码 0（判定已给，含 `ORPHAN_UNREPRODUCIBLE`）/ 1（若判成标签反向，属真缺陷）/ 2（数据或服务缺失）。
- **WP2 结构体检**（服务器，只需 DB）：`~/uai/venv/bin/python scripts/audit/p38_kb_structure_health.py --kb-version kb_v1.3.0`
- **WP2 扩边量化**（服务器，需检索器+DB）：`... scripts/audit/p38_kb_structure_health.py --coverage`
  （报告 `reports/kb_graph_coverage.json`；`candidates_per_seed` ≥20 → `SUPERNODE_RISK`，别急着落库）
- **踩坑（A.64 新增）**：
  ① **`$HOME` 在 PowerShell 内联 ssh 里会被撕成 `\C:\Users\...`**（本轮又踩两次）→ **任何远端命令都落脚本再 `scp`**；
  ② **数据先量化再动手**：这轮两个改动（切分 overlap、跨文档同章节边）都是"直觉合理、数据否掉"，直接开工就是白干 + 灌噪声；
  ③ **数字必须先能复跑才进台账**：旧 0.6789 之所以害人就是没有生成脚本 → 本轮量化写成 `p38 --coverage`（可复跑）而非临时脚本。

- **A.63① 图谱邻居扩展：修好接线 + 数字裁决**。真缺陷＝`app/rag/service.py` 的 `min_score_threshold` 把 `score=0.0` 的图谱邻居整批吞掉（**开了等于没开**）→ 只对 `retrieval_source=GRAPH` 豁免 + 留痕 `min_score_filter_applied`；`app/rag/retriever.py` 把「种子没边」与「邻居已被 top-k 覆盖」**分开留痕**（第一版混为一谈，真机第一跑就被引向错误方向）。**裁决**（`scripts/audit/p36_graph_expansion_ab.py`，136 条 golden 同进程开/关）：两腿 hit@1/5/10 = 0.9485/0.9853/0.9853、MRR 0.9635 **完全一致**、新增块 **0**；**适用面＝带边种子 0/136**（命中「一篇一块」对话语料）；机制对照：多块文档种子 +1 邻居、单块种子 0 → **结论：保持 `graph_query_enabled=False`**，前提是 KB 要有长文档多块切分。边投影其实已完整（321 块/400 条，`kb_backfill_relations.py` → `to_update=0`）。
- **A.63② answer_relevancy 落地**：官方 prompt 照抄（`scripts/prompts/ragas_answer_relevancy.py`，与 faithfulness **同 tree sha 298b6827…**，并**来源自证**：同 sha 抓的 faithfulness util 与本地参考副本 sha256 一致）；官方公式 `mean(cos(生成问题, 原始问题)) × int(not 全部 noncommittal)`、strictness=3。**真机第一跑只有 2/40 测到**——真因是判官先吐 `<think>` 把 256 token 吃光（漏 `/no_think`）→ 修后 **37/40**。最终同切片：**faithfulness 0.0906**（与 A.62.14 逐位一致）/ **answer_relevance 0.3788**（生成问题数全为 3，5 条按官方规则归零，区间 0.0000~0.6622）。门控两项并列 + `--skip-answer-relevancy`。
- **A.63③ 跨来源评测**：计划的 **TNSCUI 取不到**（Zenodo `10.5281/zenodo.3715942` 只有论文 PDF；API 搜索无数据集记录；`data/us_datasets/*.zip` 全是 4 KB 占位）→ 脚本 `--dataset tnscui` **显式失败**，改用 TN3K（614 张；冻结清单 `tests/reports/t2a_tn3k_cross_source_manifest.json`；许可按 14 号 NC、只评测不训练）。生产 8003 端点 **AUC 0.4929 [0.4438, 0.5402]**、sens@0.5 0.9068 / spec@0.5 0.0767、614/614 无失败 → **与旧探针 0.6789（n=1228）不一致，登记待查**。三口径分列＝开发集 0.8751 / AUL 公开集 0.8950 / TN3K 跨来源 **0.4929**。
- **本机门禁**：`pytest tests/unit` 全量 **6693 passed / 0 failed / 4 skipped**；`tests/unit/rag + quality` 全绿（棘轮：新文件曾把 ruff `I001`/`E402` 顶上去、静默判据判过我新写的两处 `except` → 已就地修，**新代码同样要过零静默与 ruff**）。

- **批 2-A 证据链（CRAG 路径）**：成因 `app/fusion/crag.py:450-458`（CRAG 新块并入落库证据）＋ `crag.py:546-554` 写的 `crag_evidence_chunks` **全仓无消费者**、`_build_specialist_context`（`563-570`）也没把 `clinical_context` 传给 reinvoke → 新块**从未进 prompt 却进了 knowledge_ids**。已补 CRAG 留痕、p30 新判定 `CRAG_SUPPLEMENT`（反证腿 9 条）、修 `--all` 假 PASS。真机 n=5：甲状腺 2/2 `PREFIX_TRUNCATED`、心脏 3/3 `CRAG_SUPPLEMENT`（模型未见 4~5 块）。**修法 A/B 待 00 号拍板**；拍板前 `knowledge_ids` 不是「结论依据」的完整凭据。
- **批 2-B 静默收敛三段**：observability 32→0 / rag+fusion 16→0 / security+services+safety_gateway+edge+integrations 37→0；全仓 **348→263 处**（基线两段更新）。
- **批 2-E 静默全仓归零**：剩余 263 处**一次清完**（判据即施工图，不另立清单）→ **0 处 / 0 文件**；134 文件 / +611 行（265 行 `logger.*` 留痕 + `import logging`/模块 logger），`silent_catch_baseline.json` 的 `counts` 清空。**只加留痕、零语义改动**：`pytest tests/unit -n 8 --dist=loadfile`（独占）**0 failed / 6673 passed / 4 skipped**；`pytest tests/unit/quality` **209 passed**；ruff SIM105 基线 37→18。详见 16 号 A.62.15。**注意**：基线清空后**未登记=0**，新代码想静默只能在 `except` 同行写 `# silent-ok: 理由`。
- **批 2-E 真机收尾（A.62.16）**：①`git archive` **整树同步**（抽检 `app/knowledge/version.py` sha256 一致）+ 主服务按 PID 重启（health/ui 200）；②**`tar -xf` 是覆盖不是同步** —— 对账出服务器上有 **8 个仓库已删的 `.py`**，服务器侧原有的 **7 处静默全来自它们**（差点误判成"同步后还有静默"）→ 非破坏隔离到 `~/uai/_stale_20261014/` + 冷启验证；③**真机抓到本批自身一个真缺陷**：正常兜底路径被升成 warning 会**刷屏**（一次 p27 回归 23 条全来自 `_parse_simple_version`，而它失败是设计内探测）→ 降 `logger.debug` 写明理由，复跑增量 **0**；④p27 甲状腺/心脏两腿各 **PASS=4 FAIL=0**（修复前后两轮）+ 渲染层 p28 **PASS=7 FAIL=0**（截图 `C:/Windows/TEMP/uai_p28/`：`p28-full.png` / `p28-image.png` / `p28-conclusion.png`）；⑤服务器复扫 **851 文件 / silent_total=0**；`/tmp/_*` 32 → 0。
- ⚠️ **共享服务器上有并行工作线**：本轮按硬规则 #11「全量清 `/tmp/_*`」时，删掉了 3 个**不是本轮创建**的脚本（`_bayi_sheet.sh` 21:50、`_srv_probe_bayi{,2}.sh` 22:50，不可恢复）。**今后只删本次会话自己建的**：先 `ls -l --time-style=+%F_%T /tmp/_*` 看名字与 mtime，近 10 分钟内出现且不属于本轮的**不动**（判据 = 脚本名 + mtime 双条件，不是"所有 `_` 前缀"）。详见 16 号 A.62.16⑥。
- **批 2-C p20 G5.4**：到达后等渲染稳定（计数连续两次一致）+ 逐帧扫 + 只重试一次；真机 **3 连跑全 31/0**（见 16 号 A.62.8）。
- **批 2-D**：全量偶发**已复现并修掉** —— `sec` 密钥版本用例在全量顺序下 `DID NOT RAISE RuntimeError`，根因是「旧密钥记录存在且可解 → 兼容解密返回」这条正常契约分支被别处 leak 的 Redis mock 触发，而用例假设「版本+999 必然查不到旧密钥」→ 代码改为**显式抛约定异常** + 用例拆两条顺序无关；全量 `pytest tests/unit`（独占）**0 failed / 6602 passed**。

- **批 1 尾段（口径 C）**：批 1 第 3 项交付时口径 C **只是个名字**（`--answer-mode` 只认 `raw`/`reasoning`，代码里没有该模式；A.62.2 里那句"数字已给出"属无证据断言，已就地更正）。本轮实现 `scripts/langfuse/answer_modes.py` + `--answer-mode derived_stripped`，并把结论正文的机器字段标记抽成 app 侧 SSOT（`app/inference/grading_definitions.FIELD_MARKERS_*`，分级抽取与评测口径共用）。**三口径同切片（n=40）实测：A 0.0906 / B 0.1258 / C 0.0836**，口径 C 重复跑一次**逐样本零差异**（判官无噪声）。**结论：口径之间只值 ~0.03 → 不要切 `--judge-mode gate`**（切换判据四条见其 help）。详见 16 号 A.62.14。

### A.63 复跑入口与踩坑（下一位直接用）

- **图谱扩展开/关两态**（服务器，无需重启服务；脚本自己切换开关）：
  `cd ~/uai/src && set -a && source ~/uai/.env && set +a && ~/uai/venv/bin/python scripts/audit/p36_graph_expansion_ab.py`
  → 报告 `~/uai/reports/graph_expansion_ab.json`；退出码 0（GAIN/NO_GAIN）/1（REGRESSION）/2（前置缺失：
  golden 缺失、**库里没边**）。看 `compare.rescue_count` 与 `leg_graph_on.neighbors_total`。
  **想让图谱真有收益的前提**：知识库里得有长文档多块切分（当前对话语料一篇一块 → 适用面 0/136）。
- **两项指标并列**（服务器，需判官 8006 + 嵌入 8004）：
  `cd ~/uai/src && set -a && source ~/uai/.env && set +a && ~/uai/venv/bin/python scripts/langfuse/judge_eval_set.py --eval-set ~/uai/data/eval/double_engine_eval_set.jsonl --report ~/uai/reports/langfuse_eval_judge_a63.json --judge-url http://127.0.0.1:8006 --model Qwen3-8B --limit 40 --answer-mode raw`
  → 报告顶层 `faithfulness` / `answer_relevance` 是**数字或 null**（门控按 float 读）；逐条明细在
  `per_sample[*].answer_relevance`。再看 `eval_gate.py --judge-report <报告>`（两项并列；
  `--skip-answer-relevancy` 是「本次不判」，与「报告为 null（未测到）」是两件事）。
- **跨来源评测**（服务器，需 8003）：
  `~/uai/venv/bin/python scripts/audit/p35_thyroid_cross_source_eval.py --dataset tn3k --data-dir ~/uai/data/tn3k_dataset --clip-url http://127.0.0.1:8003 --report ~/uai/reports/t2a_tn3k_cross_source.json`
  清单在**仓库里** `tests/reports/t2a_tn3k_cross_source_manifest.json`（复跑会逐条校验 sha256，换集立刻失败）；
  `--dataset tnscui` 会**显式失败**并说明该集在 Zenodo 实际不可得（别当脚本坏）。
- **踩坑（A.63 新增，都会再犯）**：
  ① **判官必须 `/no_think`**：漏了它，Qwen3-8B 先吐一整段 `<think>`，小 `max_tokens` 被吃光 →
  表现为"解析不出 JSON"的**假未测到**（实测 40 条里 35 条）。
  ② **"没测到"与"低分"必须分开**：第一版 answer_relevance 只有 2/40 测到、给 0.51，
  若按 0 计会被读成"模型很差"；现在一律 `None + error`，门控记 `not_measured`。
  ③ **脚本自举**：新脚本一律 `sys.path.insert(0, 仓库根)`，别指望外部 `PYTHONPATH`
  （本轮 `kb_backfill_relations.py` 与 `judge_eval_set.py` 各踩一次 `No module named 'app'`）。
  ④ **分次 `git add` 会造出「测试已提交、实现未提交」的中间态**：`6f13fd3` 里测试带着
  「成因区分」用例进了仓库而实现没进 → 该提交上这条用例会红；已用 `aefb104` 补提交。
  **跨文件改动一次 `git add` 到位，提交前跑一次触及包的测试。**

### A.62 批 1 尾段（L2 三口径）复跑入口

- **三口径跑齐**（服务器，判官 8006；⚠️ fish 不支持 `VAR=x cmd`，必须 `env`）：
  `cd ~/uai/src && env PYTHONPATH=/mnt/ee16/home/student03/uai/src ~/uai/venv/bin/python scripts/langfuse/judge_eval_set.py --eval-set ~/uai/data/eval/double_engine_eval_set.jsonl --report ~/uai/reports/langfuse_eval_judge_a62_derived_stripped.json --judge-url http://127.0.0.1:8006 --model Qwen3-8B --limit 40 --answer-mode derived_stripped`
  把 `--answer-mode` 换成 `raw` / `reasoning` 即另两个口径。**三份报告必须同一 `eval_set_sha256` + 同一 `limit` 才可比**（报告里已写进这两个字段）。
- **看什么**：`mode_coverage.applied` = 该口径**真的改写过**多少条 —— 口径 B 在 12/169 条上是退化成"判全文"的（旧英文降级串 5 + markdown 4 + `**分级:**` 加粗 3），那不是 bug 是锚点缺失，但**必须有数**，否则"口径 B 的分数"其实是口径 A 的。
- **守卫**：`pytest tests/unit/quality/test_answer_modes.py`（SSOT 同步腿 / 真机四形态腿 / 反证腿）、
  `pytest tests/unit/quality/test_eval_gate_layers.py`（切换判据必须明说四条）。
- **⚠️ 一个度量陷阱**：口径 B 与 C 的名次**会随聚合方式翻转**（逐样本平均 B>A>C，池化支持率 A>B>C）
  → 比较时必须指定同一聚合口径，不能混着看。

### A.62 批 3 第一段（评测资产）复跑入口

- **去重口径**（服务器）：`python scripts/langfuse/collect_eval_set.py` —— 输出末两行会打印
  `不同患者 N 人` 与 `去重口径 {...}`；**`image_only > 0` 就是去重已降级为按影像**（口径告警）。
  守卫：`pytest tests/unit/quality/test_collect_eval_set_keys.py`（7 例，含"源码必须保留会话级回退"）。
  真机 2026-10-14：168 条 / 12 位患者 / `patient_image` 307/307。
- **患者维度上屏**：`curl -s http://127.0.0.1:18000/api/v1/eval-set/stats`（带 token）应见
  `patient_count` / `patient_id_missing` / `dedup_reason`；反馈页「评测集进度」区块底部显示
  「不同患者 N 人 · 去重口径 …」。
- **独立集冻结/校验（拿到独立数据时第一步就做）**：`python scripts/audit/p34_freeze_independent_manifest.py freeze --input <labels.jsonl> --out data/eval/t2a_independent_manifest.json --dev-patients <开发集患者名单>` → 之后每次跑批前 `verify --manifest <该清单>`。**三条硬判据**：缺 `patient_id` 报错（缺 ID 只能当「公开集下界」）、与开发集**患者级**交集必须为空、sha256 冻结（换了影像立刻判红）。清单 JSONL 每行 `{patient_id,path,label[,organ]}`。
- **医生操作动作验证（改反馈/会话相关代码后必跑）**：`node scripts/audit/p33_doctor_actions.mjs --dicom <多帧样本> --organ 甲状腺`（本机经隧道）—— 走完整医生动作序列并断言「反馈挂在**本次判读**上」；真机 13/0。**它抓到过的真 bug**：前端自造 `request_id` + 会话丢失 → 提交必 404（评测集医生标注永远 0）。闭环复核：`collect_eval_set.py` 看 `医生标注坏例` 是否 +1，再用 `p32` 看界面数字。
- **医生标注配合清单**：21 号「批 3」节末尾附件（一页纸）；进度可在反馈页对照。
- **浏览器联调（必跑，别只信 curl）**：`ssh -N -L 8000:127.0.0.1:18000 student03@…` 起隧道 →
  `UAI_BASE_URL=http://127.0.0.1:8000 UAI_DOCTOR_USER=smoke-doctor UAI_DOCTOR_PASSWORD=$(cat 口令) \
  node scripts/audit/p32_feedback_eval_progress.mjs --out <目录>` —— **页面数字逐项 = 当场 API 返回**
  （不硬编码），真机 `PASS=15/FAIL=0`；退出码 2 = 登录或前端不可达。改前端后**必跑**：
  单测用假数据、curl 看 JSON，**都证明不了浏览器里的数字对不对**。
- ⚠️ **别被"样本数"骗**：168 条里 152 条来自同一演练患者（`DRILL-PATIENT-001`），
  独立病例只有 12 个 —— 「700 例」的瓶颈是**临床使用**，跑探针涨不了独立病例。

### A.62 批 2 复跑入口（下一位直接用）

- 证据链：服务带 `UAI_EVIDENCE_CHAIN_DEBUG=1` 起 → 跑判读 → `python scripts/audit/p30_evidence_chain.py --all`（读 `~/uai/logs/api.log` 的 `evidence_chain` 与 `crag_evidence_merge` 两族留痕 + DB → 输出分布，任何非 `PREFIX_TRUNCATED` 即判红）。
- 静默棘轮：`python tests/unit/quality/test_no_silent_catch.py` 会打印实测计数（**当前应为 `counts: {}` = 0 处 / 0 文件**）；若将来又出现，先问「该不该静默」——正当理由写 `# silent-ok: 理由`（**必须写在 `except` 同行**，与前端规则不同），否则把 `counts` 写回 `tests/unit/quality/silent_catch_baseline.json`（**只许下调**；基线清空后未登记文件默认 0）。
- p20 真机：本机 `$env:UAI_DOCTOR_USER='smoke-doctor'` + `UAI_DOCTOR_PASSWORD`（服务器 `~/uai/.smoke_pw`）+ `UAI_BASE_URL=http://127.0.0.1:8000`（隧道）→ `node scripts/audit/p20_frontend_boundary.mjs --dicom <多帧.dcm>`；多帧样本可现生成：`python scripts/camus_frame_to_dicom.py --out /tmp/w.dcm --count 7 --multiframe`。

2026-10-13（分支 `main`；**A.62 三梯队现状总览 + 分批计划 + 批 1 交付**——用户拍板：三梯队全量现状 + 完整计划、**价值/风险优先**、一次给全分 4 批、外部依赖写「前置条件 + 建议方案 + 现在就能做的准备」、**写人话**。

**现状总览与计划**：21 号新增「三梯队现状总览」（梯队一性能代码项已闭合 / 梯队二全交付 / 梯队三一半交付：Langfuse 技术打通但 148/700、t2a 公开集下界已出而注册级缺数据）+「分批开发计划（批 1~4）」；`feature_list.json` 建 4 个批次条目 `a62-batch1~4`。

**批 1（已收口）**：
- **证据链**：判读走在 **Redis 队列 → worker 进程**（端点只入队），所以**进程内拦截探针必然捕空**（实测：类级包装 `RAGServiceImpl.retrieve` 一条都没捕到）→ 改在**生产侧**留痕（`UAI_EVIDENCE_CHAIN_DEBUG=1`，`base._log_evidence_chain` 打 prompt 块 + 落库块，同 request_id 对齐）→ 探针 `scripts/audit/p30_evidence_chain.py` 读留痕 + DB 判定。真机 **n=7**：甲状腺 **5/5 `PREFIX_TRUNCATED`**（prompt 5、落库 3 = **前 3 条**）；心脏 **2/2 `MISMATCH`**（落库块不在 prompt 留痕里 → 新登记 `evidence-chain-crag-path` 待查）。
- **L2 口径**：记录版 vs 模型所见版 faithfulness = **0.0 vs 0.0（2/2）** → L2 低分是**口径**问题（派生决策不计入知识支持），待 00 号拍板；`--judge-mode gate` 一行可切。
- **心脏腿文案**：`readable.py::cardiac_lvef_unparseable_text()`（补填 `LVEF 55%` 后重发 / 无测量则人工判读），保留 needs_review 与 trace；**不做**「抽取读临床文本」（实测零受益）。

### A.62 批 1 复跑入口与踩坑（下一位直接用）

- 证据链：①带上开关重启主服务（`export UAI_EVIDENCE_CHAIN_DEBUG=1` 后再起 uvicorn）；②跑一次判读（`p27_grade_extraction.py --rounds 1`）；③`python scripts/audit/p30_evidence_chain.py --all`（读 `~/uai/logs/api.log` 的 `evidence_chain` 两行 + DB，输出判定分布与「落库有不等于 prompt」的块）。
- L2 判官：`judge_eval_set.py --judge-url http://127.0.0.1:8006 --model Qwen3-8B --answer-mode raw|reasoning`。
- **踩坑（本轮新增，都会再犯）**：
  ① **判读不在 API 进程里跑**（Redis 队列 → worker）→ 任何「进程内拦截」都捕空；查证据链只能靠生产侧留痕。
  ② **给被测系统打桩前先直调自检**：签名写错（`retrieve(self, request)` 被我按 3 参调用）→ TypeError 被优雅降级吞掉 → 探针报「什么都没发生」的假象。
  ③ **一次请求可能有多条留痕**（CRAG 再检索/专科重调）：按 request_id 聚合时**不能后写覆盖**（第一版把 2 次压成 1 条 → 心脏腿假 MISMATCH），要保留 `*_calls` 与保序并集。
  ④ **解析留痕要截到最后一个 `]`**：行尾有兄弟字段（`prompt_chunks=5`）→ `literal_eval` 报错，而我把异常吞成空 list → 又报「日志里没有留痕」。
  ⑤ **评估「要不要改」之前先量**：心脏腿「抽取也读临床文本」的念头被「14 条降级里 0 条文本含 LVEF」否掉。

2026-10-13（分支 `main`；**A.61 Langfuse 摄取根因终解 + 评测集采集/门控/进度上屏**——
A.60 把 Langfuse 摄取判为「阻塞」，本轮啃到底，**根因 #3 其实是两处**：
①**`REDIS_CONNECTION_STRING` 漏设** —— Langfuse v3 镜像只读这个名（容器 `env` 实证 + 镜像产物
grep 33:0，**不读 `REDIS_URL`**）→ compose 补上；②**`langfuse-worker` 服务与 web 复用同一镜像**
→ "worker"容器实跑 `node ./web/server.js`（`ps` 实证）→ **没有任何队列消费者**，现象为
"OTLP 导出 200 + `bull:otel-ingestion-queue:wait` 堆积 + ClickHouse `traces` 恒 0 + **零错误日志**"
→ 换官方 `ghcr.io/langfuse/langfuse-worker:3`。**修后**：worker 日志
`otel-ingestion-queue executor started: true`、队列 wait 归零、**traces 0→12**（`diagnosis.dispatch/
fusion/output`）、公开 API `GET /api/public/traces → totalItems=12`（**与界面同源**，19 号门控①满足）。
③**采集器重写为业务库来源**（19 号要的"正确口径"只在医生修正反馈里，trace 没有）：
`diagnosis_results`（输入 `clinical_text` / 输出 `findings.fusion_result.content`）+
`feedback_records`（`feedback_type='correction'` → 正确口径，口径同 `correction_rate_monitor`）+
`dicom_images`（去重依据 `sop_instance_uid`；**全仓不存在字节级 image_sha256**，已核实）；
无影像样本**不入集但显式计数** `skipped_no_image`。真机 **142 条**（286 判读按"同患者同影像"折叠）
→ 进度 **142/700 = 20.3%**；`doctor_labeled 0/50`（尚无医生修正反馈，属临床使用前置）。
④**门控两层化**（棘轮口径）：`enforced`（`created_at >= --enforce-since`，默认可读化上线时刻
`2026-10-13T08:00:00`，**决定退出码**）/ `informational`（全量，仅告知）—— 首跑 118/142 存量泄漏
会让门禁**永远红**（存量含 A.59 之前的旧判读）；L2 忠实度**已接入**（RAGAS 官方 prompt 复用 +
独立判官 8006；口径 A 0.0798 / 口径 B 0.1891，n=12）——⚠️ 首版「本仓库无 RAGAS 判官脚本」的判断
**是错的**（漏检 `scripts/**`），已在 16 号 A.61.7 更正；L2 暂按 `advisory`（口径未拍板，
报告仍记 `ok=false`），`--judge-mode gate` 一行纳入退出码。近端实测 **3/4 PASS**（G1 100% ·
**G2 泄漏 = 0** · G4 100% · **G3 41.7% 判红**）+ L2 advisory 展示。
⑤**进度上屏**：`GET /api/v1/eval-set/stats`（只读报告；缺报告/损坏均 **200 + 可读原因**；
匿名 401）+ 反馈页只读「评测集进度（双引擎）」区块 + `make eval-collect/eval-gate`。
真机：**p26 35/0 · p28 7/0 · p20 31/0 · p27 3 轮 4/0**。详见 16 号附录 A.61）

### A.61 复跑入口与踩坑（下一位直接用）

- Langfuse 摄取自检（**服务器**，30 秒）：`docker logs --tail 20 langfuse-langfuse-worker-1 | grep -i executor`
  应见 `otel-ingestion-queue executor started`；`docker exec langfuse-redis-1 redis-cli -a "$PW" LLEN
  bull:otel-ingestion-queue:wait` 应为 0；`docker exec langfuse-clickhouse-1 clickhouse-client --query
  "SELECT count() FROM traces"` 应随判读增长。**三个判据缺一就是没通**（导出 200 不代表落库）。
- 评测集（**服务器**）：`make eval-collect`（= `collect_eval_set.py`，默认写
  `~/uai/data/eval/double_engine_eval_set.jsonl` + `~/uai/reports/langfuse_eval_set.json`）
  → `make eval-gate`（近端判红/存量告知；`--enforce-since` **生产应传本次部署时间戳**）。
- L2 判官（**服务器**，需 8006 判官就绪）：`python scripts/langfuse/judge_eval_set.py
  --judge-url http://127.0.0.1:8006 --model Qwen3-8B --since 2026-10-13T08:00:00 --limit 12`
  （`--answer-mode raw|reasoning` 两口径对照；产出 `~/uai/reports/langfuse_eval_judge.json`）
  → `eval_gate.py --judge-report <该报告>`（默认 `advisory` 展示不判红；口径拍板后加
  `--judge-mode gate`）。**判官调用必须给 max_tokens**（Qwen3 先吐 `<think>`，否则截断成假"no JSON"）。
- 进度面上屏（本机→服务器）：`curl http://127.0.0.1:8000/api/v1/eval-set/stats`（匿名应 401；
  带 token 应见 `available/progress/targets`）。
- **踩坑（A.61 新增，都会再犯）**：
  ① **"worker 容器"不一定在跑 worker**：Langfuse v3 的 web 与 worker 是**两个镜像**
  （`langfuse` / `langfuse-worker`），复用同一镜像会让 worker 静默变成第二个 web —— 判据是
  `ps -ef` 看入口 + 队列 `:wait` 深度（**堆积 ≠ 消费失败**，`:failed` 为 0 时尤其要看这个）。
  ② **自托管栈的变量名要 grep 镜像产物**（`REDIS_URL` vs `REDIS_CONNECTION_STRING` 文档全靠不住）：
  `docker exec <c> sh -c "grep -rhoE 'REDIS_[A-Z_]+' /app | sort | uniq -c"` 半分钟出答案。
  ③ **累积型资产的"质量门控"必须分存量/近端**：否则上线日之前的样本会让门禁**永远红**（实测 118/142）。
  ④ **docker pull 超时 ≠ 拉不到**：1.1GB 镜像多次"超时"后仍可续传完成（本会话先判错一次）。
  ⑤ **fish 登录壳**：`unset LD_LIBRARY_PATH` 必炸（`Unknown command: unset`）→ 一律写脚本
  `scp` 过去 `bash /tmp/x.sh`；`PYTHONPATH=$HOME/uai/src` 在 PowerShell 内联里会被吞成
  `\C:\Users\...` → 同样落脚本。
  ⑥ 宿主会把某些一行式命令判成"服务"并**吞掉 stdout**（`Start-Sleep` + 读日志尤其容易中招）→
  写日志文件 + 用**单独一条只读命令**取结果。
  ⑦ **复用管线要连解析器一起复用**：只复用 prompt、自写解析会读出**假 0 分**（判官把逐条判定放在
  `statements` 键下，`t1_eval_faithfulness.py:240` 就是这么读的）。**"有断言却解析出 0 条 verdict"**
  要报错，不许当 0 分（"测量失败" ≠ "测得 0"）。
  ⑧ **判官调用必须显式 `max_tokens`**：Qwen3 先吐 `<think>`，复用不带该参数的 `_chat` → 输出截断 →
  `no JSON` 假失败（实measure 13/24）。
  ⑨ **写"不存在"之前换一种检索复核**：那句"本仓库无 RAGAS 判官脚本"来自一次漏掉 `scripts/**` 的
  grep，而资产一直在 —— **"没找到" ≠ "不存在"**。

### 上一轮（A.60）Last Updated 原文

2026-10-13（分支 `main`；**A.60 零静默存量收敛 + Langfuse 栈根因 + t2a 公开集全集复验**——
①**前端静默 catch 44→0**（10 文件全 0、基线清空）：新增 `frontend/src/utils/errorReport.ts`
（统一上报 + sink 注入；只记录不展示），真处置 13 处（**`cornerstone.displayImage` 整栈预缓存链重新抛出**
= A.35 病灶；标注存档失败新增 `data-region="annotation-archive-error"` 可见角标；DicomViewer
失败走 IMAGE LOAD ERROR overlay…），其余 31 处 `silent-ok` 写明理由；规则放宽为接受
`/* silent-ok: 理由 */` 与 catch 块内标记。
②**后端 Tier A 80 处**（14 文件 + 2 处 `suppress` 后门）→ **运行时 208→128 / 总 428→348**（基线下调；
守卫 docstring 数字校正）。重点：`app/inference/base.py` 首次有模块级 logger（**拒绝标志写失败=临床
红线→warning**）、审计写库/心跳发送失败→warning、`nodule_segmentation` 区分「未训练器官」与「未知
异常」、`refresh_token` 撤族「该撤没撤」留痕且不 raise、`emergency_signing` fail-closed 留痕、
日志目录降级/轮转失败→warning。
③**Langfuse**：实测**一条 trace 都没有**；根因 1 = compose `REDIS_URL` **漏 Redis 密码**
（web `Failed to instantiate otel ingestion queue`）→ **已修**（仓库+服务器）；根因 2 = 部署侧
`.env` 的 OTLP Basic 头**手抄错一位**且含空格未加引号（`source .env` 断词）→ **已修**；
修完**仍**报同一条队列错误、真实 OTLP 载荷 **500**（空批次 200）→ **摄取阻塞**（Langfuse 栈问题）。
已交付：`scripts/langfuse/collect_eval_set.py`（去重+进度+指纹）、`eval_gate.py`（四条指标门控）、
`tracing.py` 的 OTLP 头截断告警、`TestLangfuseStackConfig` 守卫；`/api/v1/eval-set/stats` 与
反馈页进度条**未做**（无 trace 无数字）。
④**t2a 公开集全集复验**（`scripts/audit/p29_t2a_aul_holdout.py`）：AUL **735/735 无失败**、
**AUC 0.8950 / 95%CI [0.8712, 0.9168]**、敏感性@0.5 0.9011、特异性@0.5 0.6933、三分类 0.7429、
门控（0.75）PASS；**口径 `public_dataset_holdout_lower_bound`（非注册级独立验证）**；途中修 8003 的
`T2A_FT_WEIGHTS` 默认路径过期（`/v1/classify` 全批 503 而 `/health` 200 的「能力静默不可用」）。
真机：**p26 PASS=35 FAIL=0 · p28 PASS=7 FAIL=0 · p20 PASS=31 FAIL=0**（首跑 30/1 属探针时序偶发）·
**p27 PASS=4 FAIL=0**。详见 16 号附录 A.60）

### A.60 复跑入口与踩坑（下一位直接用）

- 前端静默门禁：`cd frontend && npm run lint:silent-catch`（当前 **0 文件 / 0 处**；扫描 156 文件）
  与 `pytest tests/unit/quality/test_frontend_silent_catch.py`（含反证腿）
- 后端静默棘轮：`pytest tests/unit/quality/test_no_silent_catch.py`（基线 **178 文件/348 处**；
  **豁免标记必须写在 `except` 同行**——与前端规则不同，别写进块里）
- t2a 公开集复验（**服务器**，需 8003 与分类资产）：
  `PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p29_t2a_aul_holdout.py --aul-dir ~/uai/data/aul
  --clip-url http://127.0.0.1:8003`（清单在 `tests/reports/t2a_aul_manifest.json`，逐条校验 sha256；
  **8003 必须带 `T2A_FT_WEIGHTS`** 或让候选路径命中 `models/t2a_v2/`）
- Langfuse 评测集/门控（**服务器**）：`python scripts/langfuse/collect_eval_set.py --out <jsonl>` →
  `python scripts/langfuse/eval_gate.py --eval-set <jsonl>`（**A.61 起可用**；脚本口径已换为
  **业务库来源** + 两层门控，见上方 A.61 复跑入口）
- 真机回归：p26（35）/ p28（7）/ p20（31）/ p27（各 4）
- **踩坑（A.60 新增）**：
  ① **服务健康 ≠ 能力可用**：8003 `/health` 200 而 `/v1/classify` 全批 503；
  Langfuse 六容器 Up 而 OTLP 摄取队列起不来 —— 能力级探针要打**能力端点**；
  ② **豁免标记位置前后端不同**（见上）；
  ③ **提交消息含引号必须走 `git commit -F <file>`**（PowerShell 会撕碎 `-m` 字串）；
  ④ **`data/` 被 gitignore** → 冻结清单/证据别放 `data/`（改放 `tests/reports/`）；
  ⑤ `Start-Process cmd.exe /c x.bat` 静默不跑（第 4 次）→ 直连 exe + 绝对路径重定向；
  ⑥ `Get-Content -Raw` 会触发宿主编码守卫 → 用 `[IO.File]::ReadAllText()`；PowerShell **无 heredoc**。

**下一批候选**：①修 Langfuse OTLP 摄取（解锁 ≥700 采集 + 进度面）；②后端静默继续收敛剩余运行时
128 处；③p20 G5.4 时序竞态硬化。
从**核对 A.58 登记项的归因**出发：①**更正一条错误归因**（A.58.10① 把专科正文里的
`[cardiac] LVEF=unknown, severity=normal` 误记成安全门问题；安全门那句本来就是中文）；
②**结论正文可读化**：新增 `app/agents/specialist/readable.py`（`graded_content` / `cardiac_content` /
`severity_cn`（ASE 标签 + canonical 短名两套词表）/ 五个降级文案）与 `schemas.ORGAN_CN_LABELS`
+ `organ_cn()`（器官中文名单一事实源），三专科 + `base.py` 三处降级 + `dispatcher` + `pipeline`
兜底全部改中文；机器值只留 `reasoning_trace`；`consistency._extract_lvef` 容许中文右括号
（正文形态「（LVEF）62%」可解析）；③**降级必转人工**（三处降级原先不置 `needs_review` = 静默降级）；
④**`aggregate()` 不再丢 `needs_review`**（`invoke()` 唯一出口）；⑤**P0**：`needs_review` 未算
结果终态 → **医生打开待复核检查永远停在「分析中」**（p28 P28-2 真阳性抓到）→ 抽出
`TERMINAL_RESULT_STATUSES`（completed/needs_review/cancelled/failed/timeout）。
真机：**p26 PASS=35 FAIL=0 · p28 PASS=7 FAIL=0**（新增 P28-4）· **p27 甲状腺/心脏两腿各 PASS=4 FAIL=0**
（新增 P27-5，心脏腿用 `--organ cardiac`）。本地 `tests/unit` **6565 passed / 1 例全量顺序偶发**
（`tests/unit/sec/...::test_decrypt_with_wrong_key_version_raises`，单独跑恒过，已登记待查）。详见 16 号附录 A.59）

### A.59 复跑入口与踩坑（下一位直接用）

- 可读文案自检（**服务器**，秒级）：`PYTHONPATH=~/uai/src ~/uai/venv/bin/python -c "from
  app.agents.specialist.readable import cardiac_content, evidence_insufficient_text; print(...)"`
- 正文可读性真机门禁（**服务器**）：`p27_grade_extraction.py --rounds 8 --organ thyroid`（P27-5 部位名）
  与 `--organ cardiac`（英文键值最早就出在这里）
- 渲染层（**本机**）：`node scripts/audit/p28_degradation_visibility.mjs`（新增 P28-4；反证腿需屏蔽
  `/api/v1/imaging/**`；**挑会话已改为「终态」判定**）
- 端到端（**本机**）：`node scripts/audit/p26_squad_e2e_walkthrough.mjs --dicom1 … --dicom2 …`
- 静态守卫：`pytest tests/unit/agt/test_grade_extraction_contract.py`（`TestNoTechnicalStringInDoctorFacingContent`
  已扩围到器官代码 / `LVEF=` / `severity=` / 三处降级串 + 正常路径 + LLM 异常分支）
- **踩坑（A.59 新增）**：
  ① **同一段 UI 文本可能是几层拼接的**：判"哪个模块吐的串"必须看**该层代码**，别按相邻关系猜
  （A.58.10① 就是这么错的）；② **修好一个缺陷会让下一个缺陷可见**（降级不再静默后，前端"终态集合漏项"
  立刻现形）→ 终态/枚举这类集合**一次穷举完**；③ **`Start-Process cmd.exe /c x.bat` 会静默不跑**
  （第 3 次）→ 一律直连 exe + 绝对路径重定向，且**先确认日志文件存在**；④ **PowerShell 内联多行 python
  必炸** → 写 `.py` 再跑；⑤ `git add` 里 `app/models`（含 `__pycache__`）的 ignore 警告**不代表
  `schemas.py` 被忽略**（用 `git ls-files` / `git cat-file -e HEAD:<path>` 核实）。

**下一批（A.59 批次剩余，按序）**：①存量静默收敛（前端影像层 38 处 + 后端主路径 ~45 处，基线下调；
真机 p26/p20）②Langfuse ≥700 例采集 + 指标门控（容器已在跑）③t2a 公开集 held-out 复验（标「公开集下界」）。

### 上一轮（A.58）Last Updated 原文

2026-10-13（分支 `main`；**A.58「零静默失败」常驻机制 + 结论正文可读化 + 两项口径落地**——
把 20 号研究产出的「零静默」从**出事才清查**升级为**常驻门禁**，三层各带反证：
①**约定层（后端）** `test_no_silent_catch.py` 由「7 文件白名单 + 正则」改为 **`ast` 全仓扫描**
（193 个 git 跟踪 `*.py`）+ 存量基线（**428 处，只许下调**）+ 7 主路径文件**零容忍** +
`contextlib.suppress` 后门一并判；②**约定层（前端）** 本地规则 `local/no-silent-catch`
（catch 必须 `console.error` + 用户可见降级）+ **独立 config/基线棘轮**（44 处/10 文件，不动
`--max-warnings 1`）+ `npm run lint:silent-catch`；③**渲染层** 新探针
`p28_degradation_visibility.mjs`（15s 窗口 + clip 截图 PIL 像素方差 + **`page.route` abort 反证腿**）
—— 反证腿当场暴露产品级缺陷：**影像取回失败原先静默退回设计预览态（假标注框 + 零提示）**，已修
（`data-region="image-load-error"` 可读告警 + `showMock` 不再渲染示意框）；④**语义层** 模型
「反向索要」守卫（`app/agents/specialist/solicitation.py`：强动作 + 影像对象判据，豁免 A.56 转人工
文案）→ `needs_review` + trace，**只在索要真进正文时**才换正文；⑤**结论正文可读化**
（`utils/text.ts#stripMarkdownEmphasis`，仅显示层）；⑥**口径落地**：语音进主流程（工作区页面级
`VoiceControl` + 建会话页口述；**踩坑**：写全局 `sessionStore` 会让 `AppLayout` 的 nav WebSocket
消费者误连，p26 `T0-err` 当场抓到 → 改**显式 `sessionId` 入参**）、设置页「设备与权限」（403 可读）。
真机：**p26 PASS=35 FAIL=0**（上轮 33/0）· **p28 PASS=6 FAIL=0** · **p20 PASS=31 FAIL=0** ·
**p27 PASS=3 FAIL=0（降级 0/8）**。详见 16 号附录 A.58）

### A.58 复跑入口与踩坑（下一位直接用）

- 渲染层降级可见性（**本机**）：`node scripts/audit/p28_degradation_visibility.mjs`
  （先建会话→装真实多帧 DICOM；产出 `C:/Windows/TEMP/uai_p28/` 截图 + `p28_variance*.json`；
  **退出码 0/1/2**；反证腿会用 `page.route` 屏蔽 `/api/v1/imaging/**`（影像字节面））
- 零静默棘轮：`pytest tests/unit/quality/test_no_silent_catch.py test_frontend_silent_catch.py`
  （后端 428 处基线 / 前端 44 处基线；**只许下调**，重算见两文件头注释）；
  `cd frontend && npm run lint:silent-catch`（独立 config，**不要**并进 `npm run lint`）
- 语义层守卫：`pytest tests/unit/agt/test_no_solicitation_guard.py`（21 例，含「请结合图像与病史
  自行判断」不误伤的反证）
- 结论正文归一化：`frontend/src/utils/text.ts`（只做显示层；`__` **只在成对时**去，避免改动
  `grade_unparseable` 这类技术标识符）
- **踩坑（A.58 新增，都会再犯）**：
  ① **往全局 store 写字段 = 触发别人的副作用**：为让语音可用而把会话写进 `sessionStore`，
  直接让 `AppLayout` 的 `useRealtimeConnection` 「以为会话就绪」去连 nav WebSocket（隧道下必失败，
  p26 `T0-err` 抓到）。**页面级组件要什么就显式传什么**。
  ② **登录刚跳完就一次性 `count()` = 假 FAIL**：工作区是懒加载路由，刚落点时 DOM 里还没有工作区
  节点 → 必须**轮询等渲染**（p26 已改 30×500ms，并在 detail 带 `workspace-root=1`）。
  ③ **严格预算的 ESLint 上别加新规则**（`--max-warnings 1`）→ 必须独立 config + 基线棘轮。
  ④ **`Start-Process cmd.exe /c x.bat` 可能静默不跑**（无日志无进程）→ 改
  `Start-Process node.exe -ArgumentList … -RedirectStandardOutput <绝对路径>`，并**先确认日志文件存在**。
  ⑤ **改了包就要跑它的棘轮**：本轮新脚本一处多余 `int()` 让 ruff `RUF046 13 > 12` 立刻红。
  ⑥ 静态产物仍需 `VITE_BASE=/ui/` 清空式重建 + 部署（见 A.55/A.52 踩坑）。

### 上一轮（A.57）Last Updated 原文

2026-10-13（分支 `main`；**A.57 第一/第二梯队收口 —— p26 零 FAIL**——收口 A.56 走查登记的
易做项，并在复跑中又抓到 2 个真缺陷：①**报告模板补「影像分割（模型辅助）」段**（liver/cardiac
原缺 `{{nodule_summary}}` → 心脏/肝脏报告正文看不到分割面积与帧级溯源）→ 真机 **T1-5b 由 FAIL
转 PASS**；②**影像角标去硬编码**（参数条写死「甲状腺 · 线阵 12 MHz · 增益 62 · 深度 10 cm」、
时间写死「2026-10-02」，心脏会话照显甲状腺→改「声明部位 + 影像参数以设备为准/参数待装载」+
会话起始时间）；③**历史对比默认配对修正**（原取 `comparable_organs[0]`（按器官名排序），患者
最新两次是心脏时却默认并排更早的甲状腺对 → 改优先「最近一次检查所在器官」；文案「结节面积」→
「分割面积」）；④**p20 重跑 PASS=31 FAIL=0**（A.55 尾巴 G1.2 复核通过）+ 21 号路线图刷新。
真机 **p26 PASS=33 FAIL=0**（同时修掉 p26 自身的竞态：它原先"先等分割入口 90s 再等结论"，
判读冷启动时会制造假 FAIL）。详见 16 号附录 A.57）

### A.57 复跑入口与踩坑（下一位直接用）

- 第一/第二梯队端到端走查（**本机** Playwright + 隧道；样本在服务器现转，见下）：
  `node scripts/audit/p26_squad_e2e_walkthrough.mjs --dicom1 <w1.dcm> --dicom2 <w2.dcm>`
  （样本：`scripts/camus_frame_to_dicom.py --out /tmp/uai_w1.dcm --count 7 --multiframe
  --keyword 4CH_half`；第二次换 `2CH_half`（同器官 cardiac）；scp 回本机 `C:\Windows\TEMP\uai_e2e\`）
- 前端深度边界（**本机**）：`node scripts/audit/p20_frontend_boundary.mjs --dicom <7 帧样本>`
- 报告模板改动后**必须重启主服务**（`report_service._TEMPLATE_CACHE` 是进程内缓存）：
  `cd ~/uai/src && set -a && source ~/uai/.env && set +a && nohup ~/uai/venv/bin/python -m uvicorn
  app.main:app --host 127.0.0.1 --port 18000 >> ~/uai/logs/api.log 2>&1 &`
- 守卫：`pytest tests/unit/quality`（105 例，含角标数据驱动守卫）、
  `pytest tests/unit/api/test_report_templates.py`（模板渲染）、
  `vitest run src/pages/__tests__/WorkspacePage.test.tsx src/pages/__tests__/PatientHistoryPage.test.tsx`
- **踩坑（都会再犯）**：
  ① **启动期资产有进程内缓存**（报告模板）→ 改完**必须重启主服务**；只同步文件不重启 = "改了没生效"。
  ② **重启后第一次判读明显变慢**（冷启动，实测 ~90s vs 常态 ~10s）→ **探针要先等结果落地再验结果
     里的入口**；反过来（先等入口）会在慢判读时稳定产生**假 FAIL**。
  ③ **页面用例传 `dicomImageId` 会拉进 cornerstone 依赖图 → vitest 长时间不返回**：真实影像分支
     用静态守卫 + 真机 p26 覆盖，页面用例只渲染预览态。
  ④ **`Start-Process -FilePath x.bat` 报 "not a valid application"** → 要
     `Start-Process cmd.exe -ArgumentList '/c','x.bat'`；子进程 `%TEMP%` 可能与当前 shell 不同，
     日志写**绝对路径**。
  ⑤ **cmd 里中文参数会因代码页变形**（`vitest -t "中文"` 恒 0 匹配）→ 过滤词用 ASCII。
  ⑥ **服务器 shell 是 fish**：带 `|`/`$`/引号的 ssh 一行式命令会被双层解释 → **写脚本 + scp 过去跑**。

### 上一轮（A.56）复跑入口与踩坑

- 第一/第二梯队端到端走查（**本机** Playwright + 隧道，被测系统 = 服务器）：
  `node scripts/audit/p26_squad_e2e_walkthrough.mjs --dicom1 <w1.dcm> --dicom2 <w2.dcm>`
  （需 `UAI_DOCTOR_USER/PASSWORD`；样本在服务器现转：`scripts/camus_frame_to_dicom.py
  --count 7 --multiframe --keyword 4CH_half` / 第二次换 `2CH_half`（同器官 cardiac）；
  产出 `C:/Windows/TEMP/uai_p26/` 截图 + `p26_report.json`；退出码 0=全过）
- 分级提取降级率门禁（**服务器**）：`~/uai/venv/bin/python scripts/audit/p27_grade_extraction.py
  --rounds 8`（退出码 0=降级率达标且零技术串）
- 结构化输出能力实测（**服务器**）：`scripts/audit/p27a_structured_output_probe.py`
- 守卫：`pytest tests/unit/quality/test_frontend_render_guard.py`（34 例，含 `useTransitions`
  与器官声明三例）、`tests/unit/agt/test_grade_extraction_contract.py`（27 例）
- **踩坑（都会再犯）**：
  ① **react-router v7 的 `<BrowserRouter>` 默认 `useTransitions`**：location 更新走 transition lane；
  含重依赖（cornerstone）的页面里该 lane 可能**永不提交** → 导航静默失效（URL 变、视图不变、
  无报错）。排查要点：**点击后有没有路由 chunk 请求**（没有 = React 没开始渲染新路由）+
  **同页面普通 lane 的 setState 是否仍生效**（生效 = 不是 React 卡死）。
  ② **本机 vitest 执行 `WorkspaceImageRegion.test.tsx` 会挂**（collect 正常、真执行长时间不返回）
  → 覆盖改由静态守卫 + 真机 p26 承担。
  ③ **cmd 里中文参数会因代码页变形**：`vitest -t "器官错配"` 恒 0 匹配（表现全是 skipped）→ 用 ASCII 过滤词。
  ④ **`Start-Process` 子进程的 `%TEMP%` 与当前 shell 可能不同**：日志一律写**绝对路径**；
  且 **bat 不能直接 `Start-Process -FilePath x.bat`**（要 `cmd.exe /c`）。
  ⑤ 前端产物仍必须走 `scripts/deploy_frontend.sh` 口径（`VITE_BASE=/ui/`）——见上一轮 A.55 踩坑。

### 上一轮（A.55）复跑入口与踩坑

- 对比度真机取证（**本机** Playwright + 隧道；被测系统是服务器）：
  `node scripts/audit/p25_overlay_contrast.mjs`
  （需 `UAI_DOCTOR_USER/PASSWORD`；产出两主题截图 + `overlay_elements.json` DOM 旁证 +
  `contrast_result.json`；退出码 0=通过 / 1=不达标 / 2=输入不合格，可进 CI）
- 纯数学复算（不需要浏览器）：
  `python scripts/contrast.py --sidecar <json> --shot <png> --json-out <out>`
- 守卫：`pytest tests/unit/quality/test_contrast_tokens.py tests/unit/quality/test_frontend_render_guard.py`
- **踩坑（都会再犯）**：
  ① **前端产物必须走 `scripts/deploy_frontend.sh`**（它设了 `VITE_BASE=/ui/`）；直接
  `npm run build` 会用默认 base `/`，产物引用 `/assets/…` → **全站白屏**（A.55 事故）。
  构建后先看 `dist/index.html` 的引用前缀再部署。
  ② **本机 `npm run build` / `vite build` 会被工具当成 watch 命令、10s 后中断**
  （实测 dist 被清空却没写出产物）→ 用批处理 / `Start-Process` 分离进程，输出重定向到文件再读。
  ③ **工作区 URL 是 `/ui/`，不是 `/ui/workspace`**（后者被 `path="*"` 静默回落且**丢 query**）。
  ④ **`chromium.launch` 不接受 `--user-data-dir`**，要指定 profile 用 `launchPersistentContext`。
  ⑤ 改了工具/包要跑它的棘轮：A.53/A.55 的 ruff 增量已在本轮就地收敛（UP031 基线 33→24）。
  ⑥ p20 的 G1.2 同一 URL 陷阱已就地修正但**未重跑**（需 `--dicom` 真样本）——下轮跑 p20 时确认 31/31。

### 上一轮（A.54）Last Updated 原文

2026-10-12（分支 `main`；**A.54 第二梯队③ 历史对比阅片已交付**——后端
`GET /api/v1/patients/{id}/sessions` + 前端 `/ui/history`（时间线配对 / 并排影像 /
面积变化 Δ% / 跨器官不给数字）；真机 p23 **18/18**、p24 **11/11**、同批次回归
p22 **9/9**。途中修掉 2 个真缺陷：**并排影像因容器无高度 → DICOM 层塌成 0×0
（canvas 不渲染）**、**跨器官仍给「面积变化 -23.2%」伪病情变化**；另把报告页入口
从"已生成报告才出现"改为无条件渲染（患者标识两级解析）。详见 16 号附录 A.54）

### A.54 复跑入口与踩坑（下一位直接用）

- 后端真机：`cd ~/uai/src/scripts/audit && ~/uai/venv/bin/python p23_patient_history.py`
  （H1 结构 / H2 patient_exists / H3 倒序 / H5 organ 过滤 / H7 limit 422 / **H9 患者隔离** /
  **H10c 结论采用报告诊断意见** —— H10 会**现造报告 + 补录标记结论**再回查，不需要预置数据）
- 前端真机（**本机**，先起隧道 `ssh -N -L 8000:127.0.0.1:18000 student03@10.20.28.21`）：
  `node scripts/audit/p24_history_compare_ui.mjs`（报告与截图在 `C:/Windows/TEMP/uai_p24/`；
  J7 会现造「甲状腺+心脏」混合患者，需 `UAI_ADMIN_PASSWORD`）
- **踩坑（会再犯）**：
  ① **`WorkspaceDicomLayer` 必须是"有尺寸 + 定位"的容器的子元素**（它自身
  `position:absolute; inset:0`）——放进无高度 div 会**静默不渲染**（页面只显示一句含糊
  的"影像加载中/不可用"）。新页面复用它时务必给 `position:relative + height`。
  ② **跨器官不得给测值变化**：面积/测值 Δ 必须先确认 `organ` 一致，否则是伪造临床趋势
  （曾有 -23.2% 的假变化）。同理 `comparable_organs` 只有 ≥2 次才非空。
  ③ **探针选样不要复用待测字段**（p24 原先用接口的 `comparable_organs` 挑患者 = 与接口
  同错同对）→ 从 `items` 现算「同一器官 ≥2 次」。
  ④ **异步渲染断言要等待**：DICOM 加载是异步链，立即断言 canvas 会假阴性（p24 J5 曾误报）。
  ⑤ 本机 `vitest` 对含 cornerstone 依赖图的重文件（`WorkspaceImageRegion.test.tsx`）多次
  >10min 未完成；该行为由真机 p22 C6 / p24 J5 覆盖，**不要据此宣称该文件通过**。

### 上一轮（A.52）Last Updated 原文

2026-10-12（**A.52 A.51 批次端到端摸排与验证**——后端 6 断面真机探针
`scripts/audit/p19_a51_e2e_audit.py` **49/49 PASS**、浏览器层走查
`scripts/a51_ui_walkthrough.mjs` **11/11 PASS**（本机 Playwright + 隧道，真实 CAMUS
单文件 7 帧）；摸出并修复 **3 个真缺陷**：①知识库版本口径 kb_v1.0.0（会话/报告/结果，
与报告证据行的 kb_v1.3.0 自相矛盾）②前端帧级分割门控拿 Cornerstone 句柄比后端 id
→ **掩膜任何帧都不显示** ③影像加载失败静默黑屏（结论照出，更易误读）；另修多帧样本
奇数字节与产物堆积。详见 16 号附录 A.52）

### A.52 复跑入口与踩坑（下一位直接用）

- 后端摸排：`cd ~/uai/src/scripts/audit && ~/uai/venv/bin/python p19_a51_e2e_audit.py`
  （6 断面：运行期配置 / qdrant+dense 腿 / 设备列表 / 患者列表 / 端到端判读 / 部署产物）
- 浏览器走查（**本机**跑，被测系统是服务器）：先起隧道
  `ssh -N -L 8000:127.0.0.1:18000 student03@10.20.28.21`，再
  `node scripts/a51_ui_walkthrough.mjs --dicom <CAMUS 单文件多帧样本>`；
  样本在服务器现转：`scripts/camus_frame_to_dicom.py --out x.dcm --count 7 --multiframe`
  （**必须带 `--multiframe`** 才是单文件多帧路径）→ scp 回本机。
- **容量压测（第二梯队②）**：`bash scripts/run_bench.sh --levels 1,5,10,20,50 --users 10
  --cooldown 20 --poll-deadline 240 --output reports/prod_capacity_a53.json`（服务器；
  **必须以服务同源环境跑**，启动器已入库 `scripts/run_bench.sh`）。汇总脚本读
  `reports/prod_capacity_a53.json`。
- **cine 走查（第二梯队①）**：`node scripts/audit/p22_cine_playback.mjs --dicom <单文件多帧>`
  （需 `UAI_ADMIN_USER/PASSWORD` 兜底造授权患者；报告 `C:/Windows/TEMP/uai_p22/`）。
- **前端边界摸排（31 项，退出码可进 CI）**：
  `node scripts/audit/p20_frontend_boundary.mjs --dicom <同上>` —— 覆盖认证守卫 / 建会话
  输入边界（阈值·通配·注入·超长·不存在·空值·必填）/ 装载坏文件三类 / 报告页异常入参 /
  帧越界与**归属唯一性** / XSS 与视口 / 前端→后端请求面 0 个 5xx。报告落在
  `C:/Windows/TEMP/uai_p20/p20_report.json`（含请求清单）。
- **踩坑（会再犯）**：
  ①**显示栈里的 imageId 是 Cornerstone 句柄**（`dicomfile:N?frame=k`），不是后端
  `img_…`；凡是要与后端 image_id 比的地方，必须走 `frameOwners` 对齐数组（A.52.3-2）。
  ②**多帧 DICOM 每帧字节数必须为偶**（Rows×Cols 为奇 → `总长/帧数` 不整除 →
  Cornerstone 报 `Invalid frame length`；pydicom 自己却能读，所以"后端能跑通"不代表
  前端能渲染）。
  ③**前端产物必须清空式部署**（覆盖式拷贝会把历次 hash 文件留在仓库里；
  用 `scripts/deploy_frontend.sh`）。
  ④多层 props 透传改签名后 `tsc -b` 才是唯一裁判（测试夹具也要同步补字段）。
  ⑤**新建会话页「设备编号」是必填**（空则「开始检查」禁用，title 提示要填设备编号）——
  别按「后端不校验设备存在性」推断成「可以不填」。
  ⑥**FHIR 读患者必须「先存在性（404）→ 再同意（403）」**：先查同意会让不存在的患者
  变成 403「未签署同意」，把医生引去质控，且前端「404→就地建档」永不可达（A.52.3-5）。
  ⑦**医生可见的错误文案必须是中文**（后端英文原文会原样显示）；有棘轮守卫
  `tests/unit/quality/test_error_messages_are_chinese.py`，机器对机器面（device/alerts/utils）
  在允许清单里显式豁免。
  ⑨**不要随手删 `frontend/node_modules/.vite`**：清了之后本机 vitest 冷缓存重转极慢
    （>10 min 仍不产出摘要，多轮重试同现象），前端全量/单文件验证会直接卡住。
    另：`--reporter=basic` 与 `--reporter=json` **不能同时给**（两个 --reporter 会让 vitest 挂起）。
  ⑧**改了某个包的实现，必须跑那个包的测试**：A.51 改 `qdrant_client` 用 `query_points`
  时没跑 `tests/unit/emb`，5 例红被瞒了整整一轮（AsyncMock 下 `hasattr` 恒真，旧 mock 静默失效）。
- 本轮修复后的门禁：`deploy_acceptance_gate.sh` **PASS=24 FAIL=0**（服务器已部署到
  `429049a` + 后续文档提交）。

## 上一轮（A.51）Last Updated 原文

2026-10-12（**A.51 远期第一梯队四项收口**——①检索口径闭合（服务腿
hit@10 0.9853 = eval 腿，diff +0.00pp；顺带修 qdrant-client `search()` 移除 + kb 向量
未灌 两个真缺陷）；②多帧分期③择优帧 + 帧级归属（p17 15/15、p18 12/12，报告含「第
X/Y 帧」）；③设备/患者列表端点（p16 14/14，零 api_key）；④fusion-validate 降载
（P99 2296→885ms PASS，降级路径实测 `validate_degraded=1` 且结果正常返回）。
详见 16 号附录 A.51。上一轮 A.50.10 E11/E13 真 HAPI 闭环仍成立）

### A.51 复跑入口（下一位直接用）

- 探针（服务器）：`cd ~/uai/src/scripts/audit && ~/uai/venv/bin/python p15_retrieval_parity.py`
  （`p16_list_endpoints.py` / `p17_multiframe_segmentation.py` / `p18_multiframe_singlefile.py`
  / `p14b_validate_bench.py` 同目录，全部可复跑）
- 单测：`tests/unit/services/test_multiframe_frame_selection.py`、
  `tests/unit/api/test_list_endpoint_contracts.py`、`tests/unit/fus/test_validate_budget_degrade.py`
- 真多帧样本：`python scripts/camus_frame_to_dicom.py --out /tmp/x.dcm --count 7 --multiframe`
  （A.51 新增 `--multiframe`：N 帧写进**一个** DICOM，供择优帧复验）
- **踩坑（会再犯）**：用 `replace_in_file` 在类定义处插入新类时，**插入边界会吃掉相邻类的
  尾部字段**（本轮把 `DeviceRemoteRecoveryResponse.expires_at` 吞进了新类 → 运行期
  pydantic ValidationError，静态检查与 `import app.main` 都发现不了）。改 schema 后
  **必须**跑 `tests/unit/api/test_list_endpoint_contracts.py` 这类字段集守卫。
- **踩坑**：`tsc -b` 的增量缓存会掩盖 HEAD 既有类型错误；清缓存（改前端文件即失效）
  才会暴露。前端改动后务必真跑 `tsc -b + vite build`，不要只看 vitest。
- 前端产物部署：`cd frontend && VITE_BASE=/ui/ npm run build` → 拷贝 `dist/*` 到
  **`app/static/ui/`**（该目录入库，随之提交）。

### ✅ 服务器同步状态（2026-10-12 已补齐）

- **服务器 `~/uai/src` 已是 HEAD 镜像**：`git archive HEAD` 铺开 + **12 个关键文件
  sha256 对照 MATCH=12 / DIFF=0**（app 代码 5 个 + 仓储 1 个 + feature_list/progress/
  session-handoff + p15 探针 + 16 号/21 号文档）。
- 端点核验：`health=200`、`/api/v1/patients` 未授权 `401`、`/ui/` `200`。
  **应用代码与门禁通过版本 `4fefdb5` 逐字节相同，故未重启主服务**（门禁 PASS=24 依旧有效）。
- 过程插曲（备查）：同步第一次尝试因 `10.20.28.21` **ping 与 TCP 22 均不通**失败
  （3 次重试 + 60s 等待，ssh `Connection timed out`）——**服务器侧短暂不可达，非本机网络**；
  约 5 分钟后自愈，重试即成功。若再遇到：先 `Test-NetConnection 10.20.28.21 -Port 22`，
  通了再跑同步，不必改代码。
- **踩坑复现（硬规则 #1 又一次）**：把 `while read` + `sha256sum` 内联进 `ssh "..."`
  被 fish 拒绝（`Missing end to balance this while loop`）→ 一律本地落盘 `.sh` → `scp` → `bash x.sh`。
  另：**PowerShell 重定向 `>` 写清单是 UTF-16LE**，服务器 `read` 会全读成乱码（表现为全
  `MISSING`）——跨机传清单用 Python `Path.write_text(..., newline="\n")` 落 UTF-8/LF。

### 常驻服务新增（复跑 p13 前确认在跑）
- HAPI：`cd ~/uai/hapi && nohup java -jar ROOT.war --server.address=127.0.0.1 --server.port=8080 > ~/uai/logs/hapi_war.log 2>&1 &`（30s 就绪，`/fhir/metadata`）
- bridge：`cd ~/uai/src && FHIR_BRIDGE_HAPI_URL=http://127.0.0.1:8080/fhir nohup ~/uai/venv/bin/python scripts/fhir_bridge.py > ~/uai/logs/fhir_bridge.log 2>&1 &`（8091）
- PACS SCP：`cd ~/uai/src && nohup ~/uai/venv/bin/python scripts/pacs_scp.py > ~/uai/logs/pacs_scp.log 2>&1 &`（11112）
- **~/uai/.env 已设 PACS_ADAPTER=http / PACS_ENDPOINT=http://127.0.0.1:8091/reports**（报告 push 真出进程）

## Current Objective

**10-17 交付**：医生主流程**含「开新检查」与「多帧翻阅」已全通**（登录 →
新建会话/工作列表 → 多帧装载 → 帧指示器翻帧 → 判读 → 报告 → 签发 → 打印/导出，
全部真机端到端验证）。判读性能已测量+预热（重启首判分割 59.8ms，端到端 8.4s）。
**NMPA 验收材料：用户拍板留到性能数据提升、远期目标攻克之后**——当前无阻塞性剩余项，
改进方向见 feature_list 未闭合条目（LLM 模型侧提速、真实设备奇数帧长多帧的 loader 兼容、
同意撤回的消费端）。
**第二梯队进度**：①多帧分期② cine 播放（A.52.3b）②生产容量验收（A.53）③历史对比阅片（A.54）
均已交付；**第三梯队**里唯一「数据已就绪、可真机验证」的 `zero-silence-guard` 已机制化（A.58，
约定/渲染/语义三层常驻门禁）；其余为数据/口径前置（t2a 独立集、Langfuse ≥700 例）。剩余候选见
21 号路线图（含 §阶段三-A 的 t2a 数据方案）。

## 医生视角走查结论（2026-10-05 夜，下一位 Agent 先读这段）

此前所有验证只到 HTTP 层（门禁 23 项 curl + 契约对账 18 路径字符串比对），
**从未在真实浏览器里渲染过一行前端代码**。首次真实点击即发现
**临床前端此前完全不可用**，4 个 P0 全部已修并复验（commit `1ac92d5`）：

| # | 缺陷 | 根因 | 修法 |
|---|---|---|---|
| 1 | 白屏（`#root` 子节点 0） | `vendor-cornerstone` 跨 chunk 循环依赖 + Vite 8/Rolldown 求值顺序 | 从 `manualChunks` 移除 cornerstone |
| 2 | 登录永远 422 | 前端发 JSON 打到 `OAuth2PasswordRequestForm` 端点 | 改打 `/auth/login` |
| 3 | 登录后跳后端根 JSON | `window.location.href='/'` | 改用 `import.meta.env.BASE_URL` |
| 4 | 9 条二级路由全打不开 | `BrowserRouter` 缺 `basename` | `basename={BASE_URL 去尾斜杠}` |

**A.28 已闭合这2 个**（2026-10-05 下午，详见 16 号附录 A.28）：DICOM 上传入口（结论区
扫查态「选择 DICOM 影像」，装载后自动发起判读）+ 工作区导航出口（常驻条右侧三项）。
真机 Playwright 20/20 PASS，含**真传一个 DICOM 后界面自动进入「分析中」**——
`useWorkspaceFlow.start()` 第一次真正有调用方，此前判读主链没有起点。

**勘察结论值得记住**：上传端点（`POST /api/v1/imaging/dicom/upload`）与前端封装
（`imagingService.uploadDicom`）**早就写好了，只是从未被调用**。所以这类"功能缺失"
要先查是不是接线问题，而不是急着加接口。

**新增三层守卫**（下一位直接用）：
- 静态：`tests/unit/quality/test_frontend_render_guard.py`（pytest，17 例，含 5 例自测）
- 运行时：`scripts/frontend_browser_smoke.mjs`（Playwright + 真 Chrome，20 项）
  密码只能从环境变量注入：`UAI_DOCTOR_USER` / `UAI_DOCTOR_PASSWORD` / `UAI_BASE_URL`
  加 `--upload <dicom>` 可真传一份并验证自动发起判读（需 GPU 在线）
- 走查样本：`python scripts/make_test_dicom.py <路径>` 现造合规 DICOM
  （**不能取自 `dicom_storage/`**：落盘副本已被 PHI 剥离，回传必然 400）

**A.29（2026-10-06）**：补走「报告签名」这一跳时挖出**两个 P0**，均已修并加守卫 ——
①**判读结果永远等不到**：前端 `pollDiagnosisResult` 的 `maxWaitMs` 默认 30s，
而真实管线 90~300s；`timeout` 又被 `PHASE_TO_STATE` 映射回 `progress`，
于是界面**永久停在「分析中」**。A.24.1 只把服务端阈值抬到 180s，前端没跟着改。
②**报告与签名在 UI 上不可达**：`request_id` 从不交给界面，医生只能手敲 URL。
现两处都通：真机 26/26 PASS，直到**签发成功**（HSM 签名、签发人 smoke-doctor）。

**下一位先读这段**：医生主流程已全通，别再重复走查；直接看
`feature_list.json` 里 `planned` 的 4 项（NMPA 材料最急）。

**A.30（深夜）**：报告缺段落做了**知情确认**（完整性未过 → 签发先弹缺失清单，
确认后才能签；刻意不做硬禁用，口径需项目组定）；**外部字体已移除**（12 条 CSP 违规
清零，视觉零变化；要恢复设计字体走自托管）。真机走查 27/27 PASS。

**A.31（同夜）**：**影像回取已闭合** —— 后端补 GET 端点（列表 + 字节），前端显示源
改服务端副本，刷新 / 「继续检查」自动恢复最近影像（真机 27/27 PASS，审计
`dicom_retrieve` 留痕）。已登记 `ui-no-wado-retrieve-endpoint` 转 done。

**A.32（同夜）**：报告签发门禁 + 补录定稿并落地。口径（AI 拍板、依据记档于
`report_service.SIGN_BLOCKING_SECTIONS`）：缺 **findings / diagnosis**（证据段/结论段）
服务端拒签（400 E1030）；缺 **measurement_summary / clinical_actions** 走知情确认。
补录 PATCH 重渲染 + 出处记录（doctor_supplemented）。真机全闭环 28/28 PASS：
缺阻断段 → 签发被禁 → 补录 → 解禁 → 知情确认 → 已签发。

**A.33（2026-10-08）已闭合新建会话接线**：患者编号 → `GET /fhir/Patient/{id}` 一次校验
存在性 + 同意（**未签署/撤回 → 引导找质控，医生无权补签是事实**；404 → 就地建档后重新
校验，建档≠同意）；设备号录入 + localStorage 记住上次；「开始检查」带 `?session_id=`
直达工作区（旧实现 `navigate('/')` 把新会话 id 丢了）。真机端到端 6/6 PASS。
**关键坑（已写进 fhirService 注释与契约对账脚本）**：FHIR 挂在 `/fhir` 不带 `/api/v1`，
每个请求必须 `baseURL: ''` 覆盖全局前缀，否则永远 404。

**A.34 已收口可修项，剩余全部为「等外部条件」，无代码侧欠账**：
①**NMPA 材料**（项目决策 2026-10-08：延期至性能数据提升、远期目标攻克后；可借鉴 cmde.org.cn
  公开的 AI 三类证技术审评报告，系统证据映射见 A.33/A.34）；②consent 排程 handler（排程系统
  本体不存在，handler 是安全的 no-op 占位）；③UDT/faithfulness/覆盖率/甲状腺数据（语料与
  项目组决策）；④患者/设备列表端点（依赖登记数据，现为校验+录入+记忆补位）。
**医生全流程最终摸排 13/13 PASS**：`scripts/doctor_flow_walkthrough.mjs`（登录→新建会话→
装载→判读→报告→生成草稿→补录→签发闭环；报告草稿需医生点「生成草稿」，无草稿时 404+引导是正常态）。

录像：`C:\Users\ghf\Videos\US-DX-医生视角走查-20261005.webm`（2.71MB）。
走查脚本用 Playwright + 复用 `~\.agent-browser\browsers\chrome-154\chrome.exe`，
录屏需 `npx playwright install ffmpeg`。**下一位做真浏览器验证时直接复用这套**。

## A.49 端到端管线摸排（2026-10-10，下一位先读这段）

**用户指令**：「走端到端摸排，列出要摸排的管线，一一排查过去！」10 条管线
（走查/影像交互/测量标注/认证/同意撤回/设备/告警/报告分发/辅助/FHIR）逐一
真机实测，探针套件**入库 `scripts/audit/`（p0_admin_bootstrap + p4~p10，
可复跑）**：探针 71 PASS / 1 P2 观察。修复 3 真缺陷（详见 16 号 A.49.2），
甄别 4 个探针误判（别再重查：refresh 宽限窗 30s / attributes 中文 503 是
语言护栏 / FHIR 读端点要 consent 前置 / 设备同名注册已被拒绝）。

**本轮踩坑（都会再犯的那种）**：
- **build 丢 `VITE_BASE=/ui/` → 全站白屏**：每条命令都是新 shell，`$env:VITE_BASE`
  必须与 build 同一条命令；build 后先查 `dist/index.html` 资源前缀再部署。
- **登录限流**：探针登录几十次/天必撞 E1007 429——`scripts/audit/_audit_common.py`
  login 已带退避重试；写新探针直接复用它（别再裸 curl login）。
- **探针断言先对齐设计再跑**：8 个 FAIL 里 4 个是误判，先查代码定性。
- 服务器 /tmp 历史探针剩 25 个（清理命令需审批窗口未执行，见硬规则 #11）。
- smoke-admin（SYSTEM_ADMIN）账号已建（密码 ~/uai/.smoke_admin_pw，600），
  管理面探针靠它；p0_admin_bootstrap.py 可重建。

## A.50 远期第一梯队（2026-10-11）

- **语音引擎**：~/uai/models/funasr（fp32+int8 ONNX + tokens.txt 逐行 token 格式
  + test_wavs 真实语音），~/uai/src/models/funasr 是符号链接；适配器
  app/speech/features.py（numpy 特征管线）+ funasr.py（paraformer 路径）；
  功能验证 `scripts/audit/validate_asr.py`。**勿动共享 venv**（torch 被 vLLM 钉死）。
- **钉子**：多文件改动按文件 scp 容易漏（manager.py 漏发 → 修了还 400，探针抓到）；
  `awk f > f` 自截断；pydantic v2 bytes 字段吃 str 按 utf-8 编码非 base64 解码；
  CTC 连续重复折叠是正确语义。
- /tmp 自己的历史 _ 探针已清零（A.50.5）；root 所属 1 个不动（铁律 #3）。
  fish 对无匹配通配符整行中止——含通配符远端命令必须包 bash -c。
- **qdrant 未启动**（RAG 跑 pgvector 降级链路，4526 行正常返回）——设计内降级，
  feature_list `qdrant-deployment` 登记：上线后复测检索质量口径。
- **本机无 make**（WSL 有）——make 系列目标本机用 WSL 或 CI 跑。

## 凭据泄漏：已决定不轮换（A.28.10 更正 A.27.7 的建议）

`smoke-doctor` 密码明文进入过A.27 会话记录。**本轮决定不轮换**，理由与适用边界见
16 号附录 A.28.10：它是测试账号、只访问合成病例、主服务只绑回环、轮换会打断正在进行的
交付走查。**若该账号将来用于任何真实数据，必须立即轮换。**

该文件是纯密码、无 `=` 结构，别再用 `cut -d= -f1`「只看键名」—— 那等于打印密码。
本轮所有脚本已改为从环境变量注入，脚本与命令行均无密码字面值。

## 本机如何做浏览器验证（2026-10-05 新增，下一位 Agent 直接用）

主服务**只绑服务器 loopback**（硬规则 #3），所以本机必须走 SSH 隧道：

- 访问地址：**`http://127.0.0.1:8000/ui/`**
- 隧道已常驻：`~\.uai\keep_tunnel.ps1` + 计划任务 `UaiKeepTunnel`（登录自启；断了 5s 自动重连）
- 手动重连：`ssh -N -L 8000:127.0.0.1:18000 student03@10.20.28.21`
- 日志：`~\.uai\keep_tunnel.log`；删计划任务：`schtasks /Delete /TN UaiKeepTunnel /F`
- 改前端代码时：另起 `cd frontend && npm run dev`（3000 端口，vite 默认代理到 `127.0.0.1:8000`）
- 服务器重启主服务：走权威脚本 `bash ~/uai/src/scripts/start_main.sh`（**别再手写启动命令**）

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
- 分割旁路**默认开启**（`nodule_contour_in_pipeline_enabled` 代码默认 `True`，`settings.py:377`；
  A.52 服务器**运行期实测**：`GET /api/v1/health` 的旁路开关与判读结果里的
  `nodule_segmentation.available=True` 一致）。开关按**启动时**读取环境变量
  `NODULE_CONTOUR_IN_PIPELINE_ENABLED`，**改完必须重启主服务**才生效（事后改 `.env` 无效）。
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
| faithfulness 95% 门控口径 | 项目组 | **A.62.14 三口径已算齐**（n=40 同切片、判官逐样本零噪声：A 0.0906 / B 0.1258 / C 0.0836）→ 待决的是**指标语义**（L2 判「能否由知识库推出」，而结论=影像所见+指南决策）；**建议不切 gate**，切换判据四条见 `eval_gate.py --judge-mode` help |
| 甲状腺注册数据 | 管理决策 + 授权 | 无商用公开集，须自有/合作医院 |
| 核心覆盖率 90% | 真机依赖 | SoftHSM / 真实 Redis 语义 |
| ~~服务器代码与仓库同步~~ | 已解（2026-10-14） | **已完成（A.62.16）**：`git archive` 整树铺开 + 抽检 sha256 一致（`app/knowledge/version.py`）；服务器上以守卫判据复扫 **851 文件 / silent_total=0**；`/tmp/_*` 归零；**注意**：上一轮那个 `/tmp/_head_b1tail.tar` 是旧 HEAD 的归档，本轮已重打并清理 |
| 服务器上的陈旧代码（先隔离后确认） | 用户决策 | 8 个 `.py` 在仓库中已不存在，仍留在服务器 `~/uai/src`（`tar -xf` 不删文件）→ 已 `mv ~/uai/_stale_20261014/`（**保留未删**，可回退），精确 grep 确认无人引用 + 冷启通过；要彻底删除需用户批准 |
| t2a 独立验证集数据 | 项目组 / 数据方 | 现 AUC 0.8751 属开发集内部验证；方案已备（21 号 §阶段三-A），缺**独立集数据** |
| Langfuse 评测管理 ≥700 例采集 | 环境 + 排期 | 机制已就绪，瓶颈是**临床使用**：169 条里 152 条同一演练患者、独立病例仅 12 |
| SG 禁忌提示中文化 | 本机可做（可回归） | A.58.10① 登记的那串 `[cardiac] LVEF=unknown, severity=normal`。**A.62.14 抽查（未做真机复核）**：`safety_gateway` 侧禁忌文案全中文、`app/**` 并不消费 `contraindication_matches`，且 A.59 已把该串归因到**专科正文**并修掉 → 本行**疑为陈旧**；下一位若要做，先跑一次心脏真机判读确认告警文案再决定动手 |
| harness 五子系统审计 | 环境（本机缺脚本） | `~/.codebuddy/skills/harness-creator/` 目录**为空**（`validate-harness.mjs` 不存在）→ 本轮以 `bash init.sh` 四步替代（环境/状态文件/文档守卫/ruff 全通过）；**不是"审计已过 100 分"**，别在台账里那样写 |

## Files（交接涉及的路径）

- Harness：`AGENTS.md`、`progress.md`、`feature_list.json`、`session-handoff.md`、`init.sh`
- Skills：`.codebuddy/skills/remote-gpu-server-devtest/{SKILL.md,reference.md}`、`.codebuddy/skills/uai-harness-maintenance/{SKILL.md,reference.md}`
- 事实源：`docs-for-guiding generating docs/超声诊疗决策支持系统/{00-项目总览与核心指标.md,16-开发进展汇报-20260928.md}`
- 验证：`Makefile`、`pyproject.toml`、`.github/workflows/{ci.yml,security-scan.yml}`

## Next Session

1. 读 `progress.md` → 执行 `Recommended Next Step`（① CRAG 证据链修法 A/B 拍板；② L2 指标语义拍板；
   ③ 评测集靠临床使用往 700 推；④ 是否删除服务器 `~/uai/_stale_20261014/`）。
2. 若触服务器：加载 SKILL `remote-gpu-server-devtest`，先读「铁律（先读）」5 条。
3. **本轮遗留一件（等用户定）**：
   - **服务器上的陈旧代码**：8 个在仓库中已不存在的 `.py` 已隔离到 `~/uai/_stale_20261014/`
     （`app/api/v1/{device_repository,redis}.py`、`app/services/main.py`、4 个下划线助手脚本、`scripts/txt_debug.py`）；
     确认无人引用、冷启通过。**保留未删**是为了可回退，彻底删除属破坏性操作 → 需用户批准。
   - （上一轮的整树同步与 `/tmp` 清理**本轮已完成**，见 `## Blockers` 与 A.62.16。）
4. 收尾按 `AGENTS.md` 的 `End of Session` 走。**注意**：`validate-harness.mjs` 本机不存在，
   以 `bash init.sh`（Git Bash）替代；若哪天脚本回来了再补跑正式审计。

## Restart Markers（A.62 批 2-E 含真机收尾，本轮）

- [x] 后端静默**全仓归零**：`python tests/unit/quality/test_no_silent_catch.py` → `counts {}`（0 处 / 0 文件）。
- [x] **零语义改动的证据**：`pytest tests/unit -n 8 --dist=loadfile --timeout=300` → **0 failed / 6673 passed / 4 skipped**；`pytest tests/unit/quality` → **209 passed**（含 ruff 棘轮 SIM105 37→18）。
- [x] 真机：整树同步 + 服务器复扫 **851 文件 / 0 静默**；p27 甲状腺/心脏两腿各 **4/0**；刷屏缺陷修复后留痕增量 **0**；`/tmp/_*` 32 → 0。
- [x] 台账已回写：`16` 号附录 **A.62.15 / A.62.16** / `21` 号（三处状态 + 批 2 表 + 占位符 `{CONC}`）/ `progress.md` / `session-handoff.md` / `feature_list.json`（`head_commit` = `8e78aae`）。
- [x] 提交：`c808c78`（代码 134 文件）+ `109d6f6`（台账）+ `d36e307`（head_commit/状态补齐）+ `8e78aae`（刷屏修复）。
- [-] harness 正式审计：`validate-harness.mjs` 本机仍不存在（以 `pytest tests/unit/quality` 209 passed 替代，**不写作"审计满分"**）。
- [ ] 服务器 `~/uai/_stale_20261014/`（8 个陈旧 `.py`）是否删除 —— 破坏性操作，等用户定。

## Restart Markers（上一轮 A.62 批 1 尾段）

- [x] `bash init.sh`（Git Bash；WSL 的 bash 不支持 `set -o pipefail`）四步通过：环境 / 状态文件 / 文档守卫 209 passed / ruff（全仓历史债 252 项属既有）。本机**没有** `validate-harness.mjs`，正式审计脚本缺失。
- [x] `progress.md` 的 Last Updated / Current Objective / Recommended Next Step 已刷新（含 A.62 批 1 尾段）。
- [x] `feature_list.json` 状态迁移已记录（`head_commit` = `ba944ad`，含 evidence）。
- [x] 新结论已追加 `16` 号附录 **A.62.14**（三口径实测 + 判官零噪声 + 度量陷阱 + 一页纸建议）。
- [ ] 远程 `/tmp/_*` 已清、GPU 进程清单核对（嵌入×3 + vLLM 8002）—— **本轮未完成**：远端写操作卡在审批，见 `## Next Session` 第 3 条。
- [ ] 服务器整树同步（`tar -xf /tmp/_head_b1tail.tar`）—— **本轮未完成**，同上。
