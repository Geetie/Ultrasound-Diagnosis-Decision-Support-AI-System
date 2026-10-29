# 核查 A · 评测与指标线

> 范围：只回答一个问题，**这套系统所有声称的「效果指标」到底怎么算出来的、能不能复现、分母是什么**。
> 前端、业务逻辑、架构拓扑不在本文范围（见其他核查）。
> 核查时间：2026-10-28｜基线 `git HEAD = c65da99`（2026-10-28）
> 方法：逐个打开 `tests/reports/*.json`（48 份）+ `reports/*.json`（3 份）+ `tests/reports/evidence_20261025/*`（6 份），
> 并把每份报告对回生成它的脚本。**本文不引用任何只在文档里出现、但报告不在库的数字。**

---

## 0. 一句话结论

**能上答辩页的指标只有 5 个，全部集中在「检索 / 分割 / 走查 / 时延」四类；分类与 faithfulness 这两类数字一个都不能上。**
最要命的一条不是数字偏乐观，而是**780 例评测集的「金标准」本身是脚本模板生成的合成标签**
（`app/inference/data/consistency_test_cases.json`，由 `scripts/server_build_eval_cases.py` 按特征模板拼出来），
以及 **faithfulness 从 0.0836 到 0.9353 跨了 11 倍，取决于判官模型、口径与样本集三件事，而这三种条件在报告里没有一次被写全。**

---

## 1. 指标全清单（按报告逐份）

> 图例：`真机` = 调用了真实服务（8001/8002/8003/8004/18000 等）；`离线` = 本地/服务器脚本直接计算，不经生产链路。
> 「在库」= 文件被 git 跟踪，换机器可拿到；「不在库」= 只存在于服务器 `~/uai/reports/`。

### 1.1 检索类

| 文件 / 来源 | 指标 | 数值 | n（分母） | 数据集 | 真机/离线 | 在库 |
|---|---|---|---|---|---|---|
| `t1_retrieval_baseline.json` | hybrid recall@10 | **0.945** | 200 queries（corpus 2000） | US-365K caption **程序化自造** golden | 离线（8004 嵌入 + 本地余弦） | ✅ |
| `t1_retrieval_baseline.json` | dense recall@10 | 0.86 | 200 | 同上 | 离线 | ✅ |
| `t1_kb_retrieval_eval.json` | hybrid recall@10 | **0.98** | 200（corpus 433） | 知识库**自指式** golden（gold = chunk 首句所属文档） | 离线 | ✅（但**无生成脚本**，见 §2.1） |
| `t1_tuning_report.json` | baseline RRF recall@10 | **0.9926**（135/136） | 136（corpus 433） | `kb_qa_golden_v1`，kb_v1.0.0 | 离线 | ✅ |
| `t1_tuning_kb_v1.0.{0,1,2}.json` | 同上 | 0.9926（三份完全相同） | 136 | 同上，语料 433/420/459 | 离线 | ✅ |
| `gate2_bge_top10.json` | hit@10 | **0.9853**（134/136） | 136（corpus 1257） | `kb_qa_golden_v1`，kb_v1.3.0 | 真机（dense-only） | ✅ |
| `retrieval_parity_20261012_012121.json` | hit@10 eval 腿 / 服务腿 | 0.9853 / 0.9853（`diff_pp=0.0`） | 136 | 同上，kb_v1.3.0 | 真机（服务腿走生产 Retriever，**dense-only**） | ✅ |
| `hard_query_set_crossdoc.json` | 无指标（只构造 query 集） | — | 200 | 自造难集 | 离线 | ✅（文件中明写 `synthetic: true`、`usage: expansion_ablation_only`） |

### 1.2 分类类（甲状腺良恶性）

| 文件 / 来源 | 指标 | 数值 | n | 数据集 | 真机/离线 | 在库 |
|---|---|---|---|---|---|---|
| `t2a_aul_holdout.json` | mal_vs_rest AUC | **0.8950**（CI 0.8712–0.9168） | **735**（435 恶性 / 200 良性 / 100 正常） | AUL 公开集（CC BY 4.0） | **真机**（8003 `/v1/classify`） | ✅ |
| `t2a_aul_holdout.json` | accuracy_3class | 0.7429 | 735 | 同上 | 真机 | ✅ |
| `t2a_v2_official.json` | mal_vs_rest AUC | **0.8751** | 111 | 内部 3 分类 test 折 | 离线 | ✅ |
| `t2a_finetune.json` | probe_auc_test | 0.8741 | 未标注 | 内部训练脚本 test 折 | 离线 | ✅ |
| `t2a_mech_compare.json` | probe_finetuned | 0.8751 | 未标注 | 同上 | 离线 | ✅ |
| `t2a_us365k_eval.json` | probe_us365k_ft | 0.8114 | 未标注 | US-365K 微调 | 离线 | ✅ |
| `t2a_malignancy_report.json` | auc（**检索式 kNN**，不是同一模型） | **0.5149** | 350（150 恶性/150 良性/50 正常） | AUL | 离线（kNN + caption 投票） | ✅ |
| `t2a_tn3k_adapt.json` | best_test_auc | 0.7051 | TN3K test | TN3K（微调适配） | 离线 | ✅ |
| `t2a_tn3k_generalization.json` | malignancy_auc | ~~0.6789~~ | ~~1228~~ | ~~TN3K~~ | — | ✅ 但 **文件首行 `_status: VOIDED`，明令不得引用** |
| `p35/p37` 真机输出（`reports/t2a_tn3k_cross_source.json`） | AUC | **0.4929**（CI 0.4438–0.5402） | 614 | TN3K 官方 test | 真机（8003） | ❌ **不在本地** |
| `t2b_cactus.json` | view_top1（心脏切面） | 0.9793 | CACTUS | 离线训练/评测 | 离线 | ✅ |
| `t2b_plane_quality_report.json` | top1_accuracy | 0.3625 | 240 | US-365K caption 切面词 | 离线 | ✅ 但**文件自述是 `LocalFake` 假实现**，`conclusion` 明写"无临床判别力" |

### 1.3 分级一致率 / 分级准确率（780 例）

| 文件 | 指标 | 数值 | n | 数据集 | 真机/离线 | 在库 |
|---|---|---|---|---|---|---|
| `p02_780_guided.json` | 分级准确率（直接分级 vs expected） | **0.4731** | 780 | **`consistency_test_cases.json`：脚本模板合成** | 真机（vLLM 官方 server） | ✅ |
| `consistency_780_a2_report.json` | 双引擎分级一致率 | **0.8051** | 780 | 同上 | 真机（llama.cpp vs vLLM） | ✅ |
| `consistency_780_report.json` | 双引擎一致率 | 0.2333 | 780 | 同上 | 真机 | ✅ |
| `consistency_780_guided_v2_report.json` | 一致率 | 0.1474 | 780 | 同上 | 真机 | ✅ |
| `consistency_780_a3_7b_report.json` / `t0b_q8` / `t0_7b_percase` | 一致率 | 见各文件 | 780 | 同上 | 真机 | ✅ |
| `p68` Q29 重测（`reports/constrained_decoding_parity.json`） | 常规档一致率 | **0.7956** | **≤157**（实际可用 157 条，非 780） | 真实判读 `double_engine_eval_set.jsonl` | 真机（8002） | ❌ **不在本地** |
| 同上 | 高风险档一致率 | **0.4088** | 同上 | 同上 | 真机 | ❌ 不在本地 |

### 1.4 faithfulness（忠实度）

| 文件 | 指标 | 数值 | n（scored） | 判官 | 生成 | 在库 |
|---|---|---|---|---|---|---|
| `t1_faithfulness_report.json` | mean_faithfulness | **0.5626** | 100（97） | Qwen2.5-7B@8002 | 8002 | ✅ |
| `t1_faithfulness_qwen3_report.json` | mean_faithfulness | **0.6596** | 100（91） | Qwen3-8B | 8002 | ✅ |
| `t1_faithfulness_v11.json` | mean_faithfulness | **0.9353** | **60（60）** | **未在文件中记录** | 未记录 | ✅ |
| `t1_faith_ab_baseline.json` | mean_faithfulness | 0.6958 | 40（37） | Qwen2.5-7B | 8002 | ✅ |
| `t1_faith_genqwen3.json` | mean_faithfulness | 0.6876 | 40（33） | Qwen2.5-7B | Qwen3 | ✅ |
| `t1_faith_qwen3judge.json` | mean_faithfulness | 0.6948 | 40（36） | Qwen3-8B | 8002 | ✅ |
| `t1_faith_thinking_judge.json` | mean_faithfulness | 0.6659 | 40（40） | thinking 判官 | 8002 | ✅ |
| `tests/reports/faithfulness_review_20261020.md`（来自 `p44`） | A 口径（判全文） | **0.0906** | 40 | Qwen3-8B@8006 | 生产结论 | ✅（报告本体在服务器） |
| 同上 | B 口径（只判「理由:」） | **0.1258** | 40 | 同上 | 同上 | ✅ |
| 同上 | C 口径（剔除派生字段） | **0.0836** | 40 | 同上 | 同上 | ✅ |
| 同上（干净样本） | 原口径 | **0.1767** | **17** | 同上 | 同上 | ✅ |

### 1.5 分割类

| 文件 | 指标 | 数值 | n | 数据集 | 真机/离线 | 在库 |
|---|---|---|---|---|---|---|
| `tn3k_seg_unet.json` | dice_mean | **0.7961**（std 0.208） | 614（官方 test，无抽样） | TN3K | 离线（GPU 训练+评测） | ✅ |
| `tn3k_seg_unet.json` | iou_mean | 0.7004 | 614 | 同上 | 离线 | ✅ |
| `seg_gt_iou_multi_cardiac_*.json` | iou_mean | **0.7388**（std 0.0758，min 0.3636） | 500 患者 | CAMUS 4CH | 离线 | ✅ |

### 1.6 端到端 / 时延 / 容量

| 文件 | 指标 | 数值 | n | 说明 | 在库 |
|---|---|---|---|---|---|
| `doctor_journey.json` | 走查通过 | **11/11** | 11（9 步 + 2 反证腿） | 真机 18000；`denominator_is_dynamic: true` | ✅ |
| `p14_dispatcher_bench`（`reports/dispatcher_bench.json`） | dispatcher P50 | **1.34s** | **6** | 真机热态；脚本头注明 N<20 时 P95 即 max | ❌ **不在本地**（脚本已修为默认落盘，尚未重跑） |
| 同上 | dispatcher P95 | 2.24s | 6 | **实为 6 次的 max**，非统计 P95 | ❌ 不在本地 |
| `p99_capacity.json` | p99 延迟 | 0.73–0.79s | 60/档 × 4 档 | **只压 8002 生成服务**，不是端到端 | ✅ |
| `concurrency_perf_report.json` | e2e p50 @并发1 | **32.23s** | **1** | 真机 18000 全链路；并发 50 时 10/50 报 timeout | ✅ |
| `t2c_e2e_*.json`（8 份） | judge_support_rate | 0.0 / 0.4444 / 0.5 / 0.6667 | **4 / 9 / 6 / 9** | caption 非金标准，文件自述"不得解读为诊断准确率" | ✅ |

### 1.7 报告数量对账

- `tests/reports/*.json` 声明 **48 份**；`git ls-files tests/reports` = **57 个文件**（含 2 个 `.md`、1 个 `.png`、6 个 `evidence_20261025/`）。
- `reports/` 本地只有 **3 份**（`concurrency_perf_report.json` + `concurrency_perf_audit.{json,md}`）。
- **明确不在本地**的关键报告 3 份：`t2a_tn3k_cross_source.json`（0.4929）、`t2a_tn3k_parity.json`（判定腿）、`constrained_decoding_parity.json`（0.7956/0.4088）、`dispatcher_bench.json`（1.34s）。**这 4 个数字全部只能在服务器复跑才能出示原始文件。**

---

## 2. 指标是怎么算出来的（逐脚本）

### 2.1 检索指标

| 指标 | 脚本 | 评测集加载 | golden 定义 | 硬编码 |
|---|---|---|---|---|
| Recall@K / MRR / nDCG | `scripts/t1_eval_retrieval.py`（`cosine_topk`/`bm25_topk`/`rrf_fuse`） | `--golden-set` 传入 | 单相关文档 → Recall@K ≡ Hit@K | 嵌入走 8004 HTTP，无硬编码权重 |
| hybrid 对照基线 | 同上，`rrf_fuse` k=60 | `us365k_retrieval_golden_v1.json` | **程序化自造**：query = caption 首句（截断 60 字），gold = 该 caption 自身 | `--corpus-size 2000` / `--num-queries 200` / `seed 20260929` 全部默认值 |
| 加权 RRF 网格 + 两阶段 rerank | `scripts/t1c_eval_retrieval.py` | `--golden-set` | **source_doc 级**（gold = 来源文档全部 chunk） | `_WEIGHT_GRID`、`_K_CONST_GRID`、`_CANDIDATE_DEPTH=100` 写死在文件头 |
| Gate 2 或分支 | `scripts/gate2_bge_top10.py` | `GOLDEN = Path("/mnt/.../kb_qa_golden_v1.json")` | 同上 source_doc 级 | **路径硬编码为服务器绝对路径**；`BGE_URL` 硬编码 8004 |
| eval 腿 vs 服务腿 | `scripts/audit/p15_retrieval_parity.py` | `GOLDEN = Path.home()/"uai"/...` | 同上 | `DIFF_LIMIT_PP=2.0` 预注册；服务腿显式 `dense-only, top_k=10` |

**关键口径问题（§3.1 详述）**：`t1_kb_retrieval_eval.json` 的 `golden_set` 字段写的是 `kb_golden_v1.json`（区别于 `kb_qa_golden_v1.json`），
但仓库里**没有任何脚本能生成或读取 `kb_golden_v1.json`**，也**没有任何脚本声明写 `t1_kb_retrieval_eval.json`** → 见 §2.6「孤儿报告」。

### 2.2 分类指标

| 指标 | 脚本 | 数据 | 判定 |
|---|---|---|---|
| AUC 0.8950（AUL 735） | `scripts/audit/p29_t2a_aul_holdout.py` | `--aul-dir`，遍历 `Benign/Malignant/Normal` 子目录 | 调 8003 `/v1/classify`；**冻结清单逐条 sha256 校验**，换集立刻失败（`build_or_verify_manifest`） |
| AUC 0.8751（111 例） | 训练脚本产出，报告 `t2a_v2_official.json` | 内部 3 分类 test | 离线；报告带 `gate: 0.75` 与混淆矩阵 |
| AUC 0.4929（TN3K 614） | `scripts/audit/p35_thyroid_cross_source_eval.py` + `p37_t2a_tn3k_parity.py` | TN3K 官方 test，冻结清单 614 条 sha256 | 复跑前逐条校验；含**标签反向腿 0.5071** 与 **Youden 腿**两条排除腿 |
| AUC 0.5149（检索式 kNN） | `t2a_malignancy_report.json` 对应脚本 | AUL 子集 150/150/50 | **不同机制**（图像嵌入 kNN + caption 投票），与 8003 分类器不可比 |
| 0.6789（已作废） | **全仓无生成脚本** | n=1228（= 614×2，与官方 614 张对不上） | 判定 `ORPHAN_UNREPRODUCIBLE`，文件已加 `_status: VOIDED` |

**重要**：`p29` 的 735 例来自 AUL 冻结清单，`t2a_aul_manifest.json` 实测计数 **735 = Malignant 435 + Benign 200 + Normal 100**，与报告 `dataset.n=735` 一致 ✅。

### 2.3 分割指标

| 指标 | 脚本 | 计算细节 | 已知乐观偏差 |
|---|---|---|---|
| DSC 0.7961 / IoU 0.7004 | `scripts/tn3k_seg_unet.py` | `evaluate()`：逐样本算 DSC/IoU 后 `np.mean` | ①原实现 `pred∩gt==0` 时记 **1.0**（"空集也算满分"）②**256px 中心裁剪后**计算，与原图全尺寸不可比 |
| IoU 0.7388 | `scripts/seg_gt_iou.py` | 患者级均值再求均值（`per_patient` 500 条） | **排序后前 500 例，非随机抽样** |
| 线上 DSC（接口返回） | `app/.../organ_segmentation.py:101` | **硬编码元数据拷贝** | **不是重算结果，不能当复跑证据** |

脚本已按 A.88 改为同时输出 `dice_mean_strict`（剔除 `empty_both`）与 `empty_mask_breakdown`，
但**入仓的 `tn3k_seg_unet.json` 是改造前快照，只有乐观口径的 `dice_mean`**。
引用 0.7961 时必须同时说「乐观口径」，否则会被读成比实际更强。

### 2.4 faithfulness

| 项 | 事实 |
|---|---|
| 计算脚本 | `scripts/t1_eval_faithfulness.py`（串行）与 `scripts/server_faith_eval_async.py`（异步版） |
| 方法论 | RAGAS faithfulness：答案拆原子断言 → 逐条对照上下文 NLI 判定 → `支持数/总断言数`；prompt 照抄 `vibrantlabsai/ragas@main`（Apache-2.0，副本在 `scripts/prompts/ragas_faithfulness.py`） |
| judge 模型 | 脚本默认 `--judge-model Qwen2.5-7B-Instruct`、`--judge-url http://127.0.0.1:8002`；`server_faith_eval_async.py` 默认 `JUDGE_MODEL=Qwen3-8B`、`JUDGE_URL=8006`。**两份默认值不同** |
| 检索腿 | `--context-top-k 5`，RRF(k=60) Top-5 |
| 语料 | `data/golden/kb_qa_golden_v1.json`。**该文件不在仓库**（`data/*` 被 `.gitignore` 排除，`git ls-files data/golden` 为空） |
| 硬编码 | 无硬编码标签；但 `_CONTEXT_TOP_K = 5`、`_CANDIDATE_DEPTH = 100` 写死在文件头 |

**踩过的坑（已写进 16 号 A.62.7）**：`t1_eval_faithfulness.py` 曾因解析器读错键名，拿不到任何元素 → 静默算成 `0/断言数`。
这说明这套流水线**失败时会静默给 0，而不是报错**，是 0.0836 那类极低分的可能来源之一。

### 2.5 端到端 / 时延

| 指标 | 脚本 | 口径 |
|---|---|---|
| 11/11 | `scripts/audit/p60_doctor_journey.py` | 9 步正向 + 2 条反证腿；`denominator_is_dynamic: true`（分母按条件追加）；A.88 前**退出码恒 0**，已改为有失败即 1 |
| P50 1.34s | `scripts/audit/p14_dispatcher_bench.py` | 热态 N=6，从 `timings_ms.dispatcher.invoke_ms` 取；**N<20 时 P95 实为 max** |
| p99 0.73–0.79s | `p99_capacity` | **只压 8002 单服务**，60 次/档；不是端到端 |
| e2e p50 32.23s | `scripts/...concurrency staircase` | 真机 18000；`RESULT_TIMEOUT_SECONDS=30` 从提交时刻起算，**把队列等待算进超时** |

### 2.6 孤儿报告（有报告、无生成脚本）

| 报告 | 判定 |
|---|---|
| `t1_kb_retrieval_eval.json` | 全仓 grep 无脚本写此文件名；其 `golden_set = kb_golden_v1.json` 也无脚本生成 → **不可复现** |
| `t2a_tn3k_generalization.json` | 文件自述 `_voided_reason`：全仓无脚本能生成 → 已作废 |
| `t1_faithfulness_v11.json` | 无脚本记录其运行参数（判官模型、KB 版本、生成端均未落盘） → **数字无法复述其条件** |
| `t1_tuning_report.json` | `kb_version` 字段写 `kb_v1.0.0`，但三份 `t1_tuning_kb_v1.0.{0,1,2}` 语料数 433/420/459 不同而 `recall@10` 完全相同（0.9926） → 疑似同一份结果换名重存 |

---

## 3. 口径冲突排查（共 **9 处**）

### 3.1 检索：同一个「命中率」有 5 个数

| 值 | 口径 | 差异原因 |
|---|---|---|
| 0.945 | US-365K caption 自造 golden，corpus 2000 | 语料是**通用 caption 池**，不是知识库 |
| 0.98 | 知识库自指 golden，corpus 433 | **gold = chunk 自己的首句** → 循环论证，天然高 |
| 0.9926 | `kb_qa_golden_v1`，corpus 433，kb_v1.0.0 | source_doc 级；语料只有 433 块 |
| **0.9853** | `kb_qa_golden_v1`，corpus **1257**，kb_v1.3.0 | **应为对外唯一口径**（Gate 2 或分支） |
| 0.9853 | 同上但走生产 Retriever 服务腿 | `diff_pp=0.0`，与 eval 腿逐位一致 |

**结论**：0.9926 与 0.9853 的差**不是模型变好变坏**，是语料从 433 块涨到 1257 块（更难）。
对外只能引 **0.9853（134/136，kb_v1.3.0，dense-only 同构对照，不含 rerank）**。

### 3.2 分类：AUC 有 5 个数

| 值 | 口径 | 能不能一起摆 |
|---|---|---|
| **0.8950** | AUL 公开集 735，真机 8003，CI [0.8712, 0.9168] | ✅ 可引，须带「公开集泛化下界」 |
| 0.8751 | 内部 3 分类 test 111 例（官方口径） | ✅ 可引，须说「内部 test」 |
| 0.8741 | 训练脚本 test 折 | ⚠️ 与 0.8751 同源，**不要当两个证据** |
| **0.4929** | TN3K 跨来源 614，真机 8003，CI [0.4438, 0.5402] | ✅ 可引（但**报告不在本地**） |
| 0.5149 | AUL 检索式 kNN，**不同机制** | ⚠️ 与 8003 分类器不可比 |
| ~~0.6789~~ | 孤儿产物，n=1228 | ❌ 已作废，引用即错 |

**三个口径的正确说法**（16 号 A.63 原话）：**开发集 0.8751 / 公开集 AUL 0.8950 / 跨来源 TN3K 0.4929，三口径分列，禁止合并引用。**

### 3.3 faithfulness：6 个数，跨 11 倍

| 值 | 判官 / 口径 / n | 差异主因 |
|---|---|---|
| **0.0836** | C 口径（剔除派生字段），Qwen3-8B@8006，n=40，**生产结论** | 结论是「影像所见 + 指南决策」的合成体，语料推不出影像所见 |
| 0.0906 | A 口径（判全文），同上 | 含分级/总分/置信度等派生字段 |
| 0.1258 | B 口径（只判「理由:」），同上 | 排除派生字段后仍低 |
| 0.1767 | C 口径，**干净样本 n=17** | n 太小，只作方向性证据 |
| 0.5626 / 0.6596 / 0.6958 / 0.6876 / 0.6948 / 0.6659 | RAGAS 标准口径，**QA 问答生成**，n=40~100 | 测的是「QA 答案能否由 Top-5 推出来」，与生产结论不是同一种文本 |
| **0.9353** | 同上，n=**60**，kb_v1.1.0，**判官与生成条件未记录** | 与 0.5626 同为 n=100 级别的同一套脚本，却差 0.37 |

**这一段是全篇最危险的地方。**
0.0836 和 0.9353 如果都被叫作「faithfulness」，任何一个懂行的评委只要问一句「你这两个数是一个东西吗」，
整场关于可信度的论述都会塌。
**正确理解**：它们是**两个不同的测量对象** —
- 0.08~0.18 那组测的是「**生产判读结论**能否被知识库支撑」，语义不匹配（影像所见本来就不在语料里）；
- 0.56~0.94 那组测的是「**QA 问答答案**能否由 Top-5 上下文推出」，是标准 RAGAS 用法。

两组都不该单独当「幻觉率 5%」的证据。0.9353 尤其不能引：它是唯一一个接近门限 0.95 的数，
但报告里连判官模型都没写，且样本从 100 缩到 60。

### 3.4 分级一致率：两套互不相容的口径

| 值 | 口径 |
|---|---|
| **0.8051** | 双引擎（llama.cpp vs vLLM）语义分级一致率，780 例合成集 |
| **0.4731** | 单引擎（vLLM）**分级准确率**，直接分级 vs `expected_grading` |
| 0.1474 | guided_v2 变体，一致率 |
| 0.2333 | 早期一轮一致率 |
| **0.7956 / 0.4088** | Q29 重测：**同引擎两次生成**一致率（常规/高风险档），≤157 条真实判读 |

**0.8051 与 0.4731 不是同一件事**（16 号已写明「与历史 80.51% 双引擎互一致率不可比」）。
而 Q29 的 0.7956 **换了口径**（同引擎两次生成，因为 llama.cpp 已弃用），
且**报告不在库、n 从 780 掉到 ≤157**。三个数字没有任何一对可以直接放在同一张表里。

### 3.5 分割：DSC 有乐观/严格两版，入仓的是乐观那版

`tn3k_seg_unet.json` 只有 `dice_mean = 0.7961`；脚本已改为产出 `dice_mean_strict`，
但**入仓快照是改造前的**。引用 0.7961 而不说「空掩膜记 1.0 的乐观口径」＝说过头。
另：接口返回的 DSC 是硬编码元数据，**不是重算**。

### 3.6 同一批「评估集」条数反复变化

| 声称 | 实际 |
|---|---|
| 780 例 | `app/inference/data/consistency_test_cases.json` 确有 780 条，但**全部由模板脚本合成** |
| 「780 例已不存在」 | `p68` 脚本头明写：A.70 清理 608 条脏数据后，当前最大评测集 169 条（157 有真实结论） |
| 169 / 700 | 152 条同源、独立病例 12 条（16 号 A.61） |
| 「干净评测样本 17 条」 | 6 个不同患者 |

### 3.7 t2c 端到端 8 份报告，judge_support_rate 从 0 到 0.6667

8 份 `t2c_e2e_*.json` 的 `n_total` 分别是 6/8/9，`n_completed` 从 1 到 9 不等，
`judge_support_rate` 有 0.0 / 0.4444 / 0.5 / 0.6667 四种。
**全部 n≤9，且每份都自述 caption 非金标准。这批数字只能证明"链路通"，不能证明任何效果。**

### 3.8 时延：同一系统有 3 个"合理"的延迟数

| 值 | 测什么 |
|---|---|
| P50 **1.34s** | 只 dispatcher 阶段（N=6 热态） |
| p99 **0.73s** | 只 8002 生成服务（60 次/档） |
| e2e p50 **32.23s** | 全链路真机（N=1，并发 1） |

三个数都对，但**指向完全不同的东西**。说"我们的延迟是 1.34 秒"而不说只算 dispatcher，是误导。

### 3.9 「780」这个数字同时指两件事

- `p02_780_guided.json` 的 780 = **合成模板用例数**；
- 早期材料里的「780 例评测」被当成**真实病例数**引用过。
两者不是一件事。**合成用例的分级准确率不能当临床准确率**。

---

## 4. 真机 vs 离线（逐个标注）

**真机**（调用真实服务，可复跑需部署环境）：
- `gate2_bge_top10.json`（8004）
- `retrieval_parity_*.json`（PG 15432 + qdrant 6333 + 8004）
- `t2a_aul_holdout.json`（8003）
- `p35/p37` TN3K 跨来源（8003）— 报告不在库
- `doctor_journey.json`（18000 全链路）
- `p14_dispatcher_bench`（18000 + vLLM）— 报告不在库
- `p68` Q29 重测（8002）— 报告不在库
- `consistency_780_*`（llama.cpp + vLLM 双引擎）
- `p02_780_guided.json`（vLLM 官方 server）
- `t1_faithfulness_*` / `t1_faith_*`（8001 生成 + 8002/8006 判官）
- `p44` 三口径（8006 判官）
- `p99_capacity.json`（8002）
- `concurrency_perf_report.json`（18000）
- `t2c_e2e_*`（全链路）

**离线**（本地/服务器脚本直接算，不经生产链路）：
- `t1_retrieval_baseline.json` / `t1_kb_retrieval_eval.json` / `t1_tuning_*`（本地余弦，只借 8004 做嵌入）
- `t2a_v2_official.json` / `t2a_finetune.json` / `t2a_mech_compare.json` / `t2a_us365k_eval.json` / `t2a_tn3k_adapt.json`
- `t2a_malignancy_report.json`（kNN 检索式）
- `tn3k_seg_unet.json` / `seg_gt_iou_*.json`（GPU 训练+评测）
- `t2b_cactus.json` / `t2b_plane_quality_report.json`（后者还是 `LocalFake` 假实现）
- `hard_query_set_*.json`（只构造 query，不打指标）

---

## 5. 结论：能上答辩页 vs 不能上

### 5.1 能上的（5 个，按推荐顺序）

| # | 指标 | 值 | 必须带上的一整句口径 |
|---|---|---|---|
| 1 | **检索 hit@10** | **0.9853**（134/136） | 「真实知识库 136 条金标，文档级命中，**dense-only 同构对照、不含重排**，kb_v1.3.0；评测集已饱和（hit@5 与 hit@10 同值）」 |
| 2 | **走查** | **11/11** | 「医生视角 9 步 + 2 条反证腿（跨会话影像被拒 400 / 无影像仅文字受理），**分母动态**」 |
| 3 | **分割 DSC** | **0.7961** | 「TN3K 官方 test 614 张全量、无抽样，256px 中心裁剪，**空掩膜记 1.0 的乐观口径**」 |
| 4 | **AUC 跨来源** | **0.4929**（n=614） | 「TN3K 官方 test，生产 8003 端点复跑，CI [0.4438, 0.5402]，接近随机。这是负结果，不是成绩」 |
| 5 | **AUC 公开集** | **0.8950**（n=735） | 「AUL 公开集 735 张，CI [0.8712, 0.9168]，**公开集泛化下界，不是注册级独立验证**」 |

> 1~3 有报告在库、可当场出示文件。
> 4、5 的报告 `t2a_tn3k_cross_source.json` **不在本地**。若要引，答辩前必须先把它拷回 `tests/reports/`，
> 否则只能说「脚本在库、可在部署环境复跑」。

### 5.2 不能上的（附原因）

| 指标 | 不能上的原因 |
|---|---|
| **faithfulness 任何一个数**（0.0836 / 0.0906 / 0.1258 / 0.1767 / 0.5626 / 0.6596 / 0.6659 / 0.6876 / 0.6948 / 0.6958 / **0.9353**） | 三套测量对象混在一起，跨 11 倍；0.9353 的判官与生成条件未记录；0.08 组语义不匹配。**一问即穿** |
| **分级一致率 0.8051 / 0.1474 / 0.2333** | 评测集是**脚本模板合成的**，不是真实病例；且与 0.4731 准确率不同口径 |
| **分级准确率 0.4731** | 同上，合成集；`expected_grading` 是建集脚本按模板分配的 |
| **Q29 0.7956 / 0.4088** | 报告不在本地；n 从 780 掉到 ≤157；口径换成"同引擎两次生成"，与历史不可比 |
| ~~0.6789~~ | 文件已 `VOIDED`，孤儿产物 |
| **0.5149**（检索式 kNN） | 与主分类器不同机制，混引会被问「你到底用哪个模型」 |
| **t2c judge_support_rate（0.0~0.6667）** | n≤9；文件自述 caption 非金标准、不得解读为诊断准确率 |
| **P95 2.24s** | N=6 的 max，不是 P95；脚本自己写明「不得单独引用」 |
| **p99 0.73s** | 只压 8002 单服务，不是端到端 |
| **t2b_plane_quality top1 0.3625** | `LocalFake` 假实现，文件自述"无临床判别力" |
| **接口返回的 DSC** | 硬编码元数据拷贝，非重算 |
| **e2e p50 32.23s** | N=1；且超时口径把队列等待算进去 |

### 5.3 三个最容易被当场问穿的地方

1. **「你的 faithfulness 是多少？」** → 如果答一个数，必然与另一个来源冲突。唯一安全答法：
   「这个指标我测过，但**测的是两种不同的东西**，而且口径还没定稿，所以我今天不报它。」
2. **「780 例是什么数据？」** → 若答"病例"，追问一句来源就穿。正确答法：
   「780 例是脚本按分级特征模板构造的**一致性回归用例**，不是真实病例；真实判读目前可用 157 条。」
3. **「0.9853 是混合检索加精排的结果吗？」** → 不是。是 **dense-only 同构对照，不含 rerank**。
   说成"完整链路"就是把对照实验说成生产指标。

---

## 6. 建议动作（答辩前，按性价比排序）

| # | 动作 | 成本 | 收益 |
|---|---|---|---|
| 1 | 把 `t2a_tn3k_cross_source.json`、`t2a_tn3k_parity.json` 从服务器拷回 `tests/reports/` | 5 min | 0.4929 从"口头承诺"变成"仓库可查"（这是全场最有说服力的负结果） |
| 2 | 把 `constrained_decoding_parity.json`、`dispatcher_bench.json` 同样拷回 | 5 min | 若被问到一致率/时延，不用现场重跑 |
| 3 | 在 `tn3k_seg_unet.json` 旁写一个 `_caliber.md`，注明"乐观口径 / 256 裁剪" | 5 min | 防止 0.7961 被读强 |
| 4 | 明确宣布 **faithfulness 不进答辩**，改用「引用可回表」类可核事实 | 0 | 消掉全篇最大风险点 |
| 5 | 统一"780 是合成用例"的表述，写进演示提纲脚注 | 10 min | 避免一条最容易被反将的硬伤 |

---

## 附录：本次核查的机械证据

```text
$ git rev-list --count HEAD        → 554
$ git ls-files tests/reports | wc -l → 57（其中 .json 48 + .md 2 + .png 1 + evidence 6）
$ git ls-files data/golden         → （空，golden 集不入库）
$ ls reports/                      → concurrency_perf_audit.{json,md}, concurrency_perf_report.json（共 3）
$ ls reports/t2a_tn3k_cross_source.json → No such file or directory
$ ls reports/dispatcher_bench.json      → No such file or directory
$ python3 -c "json.load(app/inference/data/consistency_test_cases.json)" → 780 cases, keys=[case_id, organ, prompt, expected_grading]
$ grep -rn "t1_kb_retrieval_eval" scripts/ → （无命中 → 孤儿报告）
```
