# 重排器（Reranker）文献综述 · 证据报告

> 面向工程团队 · 针对场景：**3745 chunk 中文超声指南语料 + 117 条标注 query**
> 所有结论附 URL + 原文引文；无法核实者明确标注 **NOT VERIFIED**
> 编制日期：2026-11-09

---

## 0. 先读这一节：n=117 的统计功效边界

这是本报告最重要的前置约束。n=117 意味着**大部分已发表的 reranker 增益在你们这里不可能被检出**。

**配对比较的最小可检出效应（MDE，双侧 α=0.05，power=80%，因子 2.80）：**

| 指标 | 每 query 标准差假设 | 标准误 | MDE（可检出最小差异） | 95% CI 半宽 |
|---|---|---|---|---|
| nDCG@10 | SD = 0.10 | 0.92 pt | **2.6 pt** | ±1.8 pt |
| nDCG@10 | SD = 0.15 | 1.39 pt | **3.9 pt** | ±2.7 pt |
| nDCG@10 | SD = 0.20（较现实） | 1.85 pt | **5.2 pt** | ±3.6 pt |
| nDCG@10 | SD = 0.25 | 2.31 pt | **6.5 pt** | ±4.5 pt |
| Hit@10（二值，McNemar） | 不一致对 b=20,c=10 | 4.6 pt | **12.9 pt** | — |
| Hit@10（二值，McNemar） | 不一致对 b=25,c=15 | 5.3 pt | **15.0 pt** | — |

> 计算方法：`MDE = 2.80 × SD/√117`；二值用 `SE(diff)=√(p_b+p_c−(p_b−p_c)²)/√n`。这是标准配对检验公式，非文献引用，属本报告自算。

**直接推论（务必写进实验设计）：**

1. **+1 ~ +5 点的增益在 n=117 上不可分辨。** 例如 BAAI 官方 C-MTEB 表里 `bge-reranker-v2-m3` 相对一阶段 retriever 的 avg 增益仅 **+1.36 点**（70.46→71.82）—— 这在 117 条 query 上属于纯噪声。
2. **只有 ≥8~10 点量级的差异才可能被检出。** 例如官方表里 CovidRetrieval 的 **+15.98 点**（73.4→89.38）在 n=117 上是可检出的。
3. **Hit@10 更钝**：二值指标的 MDE 高达 10~15 点，因为不一致对太少。
4. **结论**：如果你们要做 A/B，**不要用 nDCG@10 的小差异下结论**；要么扩到 n≥400~500，要么只关注方向性大效应，并**预注册**你要检出的效应量。

---

## 1. Cross-encoder reranker：什么时候有用，什么时候有害

### 1.1 结论先行

| 问题 | 证据结论 |
|---|---|
| 重排深度 | **超过 ~100 个候选后，召回开始显著下降到甚至低于一阶段 retriever** |
| 弱 reranker + 大候选集 | **确认有害**，有多篇论文实测 |
| 一阶段召回天花板 | **成立**，且有形式化上界 `E[NDCG@k] ≤ Recall@|W_π|` |
| BEIR 的原始立场 | reranker 平均最好，但**延迟最高（450ms GPU）**，且**在 2/18 数据集上输给 BM25** |

---

### 1.2 证据 A：重排深度过大 → 召回崩塌（最强证据）

**论文**：*Drowning in Documents: Consequences of Scaling Reranker Inference*
Jacob, Lindgren, Zaharia, Carbin, Khattab, Drozdov（Databricks / MIT）
**URL**: https://arxiv.org/abs/2411.11767

摘要原文：
> "Our empirical results reveal a surprising trend: the best existing rerankers provide initial improvements when scoring progressively more documents, but their effectiveness gradually declines and can even degrade quality beyond a certain limit."

正文（§4.1）：
> "In the majority of cases, reranking a small set of documents, **e.g. less than 100, is an effective way to improve recall**. ... as we increase k further beyond 100, the rerankers' Recall@10 **dramatically decreases**. This trend is present in both proprietary, state of the art rerankers, as well as open source rerankers. In fact, **for many rerankers, performance at large k is worse than that of the neural retrievers**."

引言（更强的表述）：
> "Though rerankers initially help with small values for K, reranking with large K decreases recall precipitously (Figure 1), **often dropping beneath the quality of standalone retrievers**."

关于深度选择的相对排序会被 K 翻转：
> "sometimes reranking K = 50 shows that 'voyage-rerank-1' outperforms 'voyage-rerank-lite-1', but reranking with K = 500 shows **the opposite**."
> "In summary, the relative ordering between rerankers can change drastically depending on K."

**机理（对噪声敏感）**：
> "rerankers can frequently **assign high scores to documents with no lexical or semantic overlap with the query**."

**→ 对 3745-chunk 语料的含义**：
- 深度**必须卡在 top-20 ~ top-50**，不要"全库重排"或 top-500。
- 3745 个 chunk 若全量重排 = 117×3745 ≈ 44 万次 cross-encoder 前向，既慢又**掉点**。
- 深度是超参，且**不能靠别的语料的结果外推**——必须在自己语料上单独扫。

---

### 1.3 证据 B：OOD 下 reranker 会输给一阶段（负面证据）

**论文**：*A Thorough Comparison of Cross-Encoders and LLMs for Reranking SPLADE*
Déjean, Clinchant, Formal（Naver Labs）
**URL**: https://arxiv.org/abs/2403.10407

摘要：
> "Observations shift in the out-of-domain scenario, where **both the type of model and the number of documents to re-rank have an impact on effectiveness**."

正文（ArguAna 的灾难性掉点）：
> "we spotted a very strange behavior for the ArguAna dataset. Indeed, the performance tended to **(drastically) diminish when increasing the number of documents to re-rank** – e.g., from 50 (nDCG@10) **down to approximately 15** for all SPLADE models."

关于深度交互的谨慎结论：
> "We observe that the smallest model (SPLADE-v3-DistilBERT) benefits the most from the increase of k, while the others are relatively stable over the k values. ... **the impact of k also seems to depend on the dataset used, which makes any general conclusion difficult to make**."

**→ 含义**：深度效应**依数据集而变**，同一深度在 A 语料有效、在 B 语料有害。这直接支持"必须在 3745-chunk 语料上实测，不能抄别人的 100"。

---

### 1.4 证据 C：BEIR 原文对 reranking 说了什么（arXiv 2104.08663）

**论文**：*BEIR: A Heterogenous Benchmark for Zero-shot Evaluation of Information Retrieval Models*
Thakur, Reimers, Rücklé, Srivastava, Gurevych（TU Darmstadt）
**URL**: https://arxiv.org/abs/2104.08663 ｜全文: https://ar5iv.labs.arxiv.org/html/2104.08663

**摘要原文**：
> "Our results show BM25 is a robust baseline and **re-ranking and late-interaction-based models on average achieve the best zero-shot performances, however, at high computational costs**."

**方法（§3.2 v）—— 深度就是 top-100**：
> "Re-ranking model: (a) **BM25 + CE** [70] **reranks the top-100 retrieved hits from a first-stage BM25** (Anserini) model. We evaluated 14 different cross-attentional re-ranking models that are publicly available on the HuggingFace model hub and found that a **6-layer, 384-h MiniLM** cross-encoder model offers the best performance on MS MARCO."

**主要结论（§5 第 4 条）**：
> "**Re-ranking and Late-Interaction models generalize well to out-of-distribution data.** The cross-attentional re-ranking model (BM25+CE) performs the best and is able to outperform BM25 on almost all (**16/18**) datasets. **It only fails on ArguAna and Touché-2020**, two retrieval tasks that are extremely different to the MS MARCO training dataset."

**Table 2 实际数字（nDCG@10，BM25 vs BM25+CE）：**

| 数据集 | BM25 | BM25+CE | Δ |
|---|---|---|---|
| MS MARCO (in-domain) | 0.228 | **0.413** | +18.5 |
| TREC-COVID | 0.656 | **0.757** | +10.1 |
| BioASQ | 0.465 | **0.523** | +5.8 |
| NFCorpus | 0.325 | **0.350** | +2.5 |
| HotpotQA | 0.603 | **0.707** | +10.4 |
| FEVER | 0.753 | **0.819** | +6.6 |
| Climate-FEVER | 0.213 | **0.253** | +4.0 |
| SciFact | 0.665 | **0.688** | +2.3 |
| CQADupStack | 0.299 | **0.370** | +7.1 |
| **ArguAna** | **0.315** | 0.311 | **−0.4（输）** |
| **Touché-2020** | **0.367** | 0.271 | **−9.6（输）** |

Table 2 汇总行：BM25+CE 平均 **+11%** vs BM25。

**延迟（Table 3，DBPedia 1M 文档，单 query）**：
> "(1) BM25+CE — GPU **450ms**, CPU **6100ms**, Index 0.4GB" vs "(4) BM25 — CPU **20ms**"
> "The best out-of-distribution generalization performances by re-ranking top-100 BM25 documents ... come at the cost of **high latency (> 350 ms)**, being slowest at inference. In contrast, **dense retrievers are 20-30x faster (< 20ms)** compared to the re-ranking models."

**→ 含义**：
- BEIR 的立场**不是**"reranker 总是好"，而是"平均最好、但最贵、且领域差异大时会输"。
- **Touché-2020 掉了 9.6 点**——这是"领域外 reranker 负收益"的官方数字。
- 450ms vs 20ms = **22× 延迟**。超声指南问答若做在线交互，这是硬成本。

---

### 1.5 证据 D：一阶段召回天花板（形式化上界）

**论文**：*The Recall Ceiling of LLM Recommendation Reranking*（Zhaohui Wang, 2026）
**URL**: https://arxiv.org/abs/2609.27953

摘要：
> "The cause is a **recall ceiling**: realistic retrieval covers only **2–19% of relevant items at K=100** across eight datasets in three domains, imposing a **deterministic upper bound** on any closed-candidate reranker's top-k NDCG. Under leave-one-out evaluation, **E[NDCG@k] ≤ Recall@|W_π|**, where W_π is the reranker's candidate window."
> "In the low-recall regimes measured here, **improving retrieval is more consequential than increasing reranker sophistication**."

> ⚠️ 注意：这是推荐系统（Amazon 数据集）的结论，不是文本检索。**外推到你们场景需谨慎**，但"上界"这一数学关系是通用的。

**补充：*Reproducing Adaptive Reranking for Reasoning-Intensive IR***（https://arxiv.org/abs/2604.27577）
> "The classical cascading pipeline of retrieve–rerank **suffers from a bounded recall problem**, stemming from limitations of the first-stage retriever."

**BEIR Table 9 的结构性演示（本报告自算）**：
BEIR 的 BM25+CE 是"对 BM25 top-100 重排"，因此其 **Recall@100 与 BM25 完全相同，18/18 个数据集逐一相等**。这是**由实验构造决定的机械必然**，不是经验发现——但它恰好是"reranker 只能重排、不能增加召回"的最干净图解。

**→ 对 3745-chunk 语料的含义**：
- **先量化一阶段 Recall@k**。若你们的 retriever 在 k=50 时 Recall@50 只有 60%，那么 reranker 的上限就是 60%，再调 reranker 也是徒劳。
- 优先级应该是 **retriever > reranker**，除非一阶段召回已经很高（>85%）。
- 论文明确说"improving retrieval is more consequential than increasing reranker sophistication"。

---

### 1.6 证据 E：一阶段 retriever 的选择对 reranker 输出几乎无影响（饱和证据）

**论文**：*In Defense of Cross-Encoders for Zero-Shot Retrieval*
Rosa, Bonifacio, Jeronymo, Abonizio, Fadaee, Lotufo, Nogueira
**URL**: https://arxiv.org/abs/2212.06121

摘要：
> "we show that **using bi-encoders as first-stage retrievers provides no gains in comparison to a simpler retriever such as BM25 on out-of-domain tasks**."

**Table 2（monoT5-220M 重排 top-1000，BEIR nDCG@10）：**

| 1st stage | Rerank | Covid | NFC | DBP | **Touc** | Quora | SciD | SciF | FiQA | **Avg** |
|---|---|---|---|---|---|---|---|---|---|---|
| (1) BM25 | — | 0.594 | 0.321 | 0.318 | **0.442** | 0.788 | 0.149 | 0.678 | 0.236 | 0.441 |
| (2) GTR | — | 0.557 | 0.329 | 0.391 | **0.219** | 0.890 | 0.158 | 0.639 | 0.424 | 0.451 |
| (3) BM25 | monoT5 | 0.777 | 0.357 | 0.419 | **0.277** | 0.823 | 0.164 | 0.735 | 0.413 | **0.496** |
| (4) GTR | monoT5 | 0.798 | 0.342 | 0.399 | **0.267** | 0.837 | 0.170 | 0.731 | 0.420 | **0.496** |

正文：
> "Without a reranker, GTR has a slightly higher nDCG@10 than BM25 (row 1 vs 2). However, **there is no advantage in terms of nDCG@10 in using a more expensive dense model over a simple algorithm like the BM25 to provide candidates to the reranker** (row 3 vs 4)."

**两个关键读法：**
1. **avg 完全相同（0.496 = 0.496）**：一阶段换成更强的 GTR（+1.0 基线增益）在 reranker 之后**归零**。这就是"reranker 吸收了/抵消了一阶段的差异"——一种饱和。
2. **Touché-2020 上 reranker 造成 −16.5 点灾难**（0.442 → 0.277）。这是本报告中**单点最大负效应**，且在 n=117 上绝对可检出。

---

## 2. BAAI/bge-reranker-v2-m3 官方 Model Card

**URL**: https://huggingface.co/BAAI/bge-reranker-v2-m3
（本报告通过镜像 https://hf-mirror.com/BAAI/bge-reranker-v2-m3/raw/main/README.md 取得全文；内容与 HF 主站一致）

### 2.1 训练数据与语言

- **语言**：Multilingual（100+ 语言，见其基座 bge-m3，"It can support more than 100 working languages"）
- **基座**：`BAAI/bge-m3`（config.json 确认 `"architectures": ["XLMRobertaForSequenceClassification"]`, `"max_position_embeddings": 8194`, `hidden_size: 1024`, 24 层）
- **训练数据（原文）**：
  > "Our rerankers are initialized from google/gemma-2b (for llm-based reranker) and openbmb/MiniCPM-2B-dpo-bf16 (for llm-based layerwise reranker), and **we train it on a mixture of multilingual datasets**:
  > - bge-m3-data
  > - quora train data
  > - fever train data"

  ⚠️ 注意：这句话写在 **Fine-tune 章节**，字面上指 LLM-based 系列。`v2-m3`（普通 cross-encoder）的确切训练配比**卡片未单独给出** → **NOT VERIFIED**。

### 2.2 `max_length` / 候选深度 / 是否 always-on

| 项 | 官方依据 |
|---|---|
| `max_length` | 卡片示例代码用 `max_length=512`；`config.json` 里 `max_position_embeddings: 8194`（上限）。**没有"推荐值"的文字说明**。官方 C-MTEB/BEIR 评测表里 `inference length` 列写的是 **`1024`**（query+passage 拼接口径） |
| **候选深度** | 卡片 Evaluation 章节逐条写明：<br>"BEIR. **rereank the top 100 results** from bge-en-v1.5 large."<br>"CMTEB-retrieval. It **rerank the top 100 results** from bge-zh-v1.5 large."<br>"miracl (multi-language). It **rerank the top 100 results** from bge-m3."<br>→ **官方评测协议一律是 top-100**，但这**不等于**"推荐你 rerank 100 条" |
| **是否 always-on** | **卡片没有任何"应始终启用"的表述** → **NOT VERIFIED / 官方无此声明** |

### 2.3 官方基准数字（关键！来自 BAAI 官方仓库图表）

数据来源：模型卡 Evaluation 章节内嵌的官方图表，托管于
https://github.com/FlagOpen/FlagEmbedding/blob/master/research/llm_reranker/evaluation/CMTEB-retrieval-bge-zh-v1.5.png
（模型卡引用路径 `./assets/CMTEB-retrieval-bge-zh-v1.5.png`）

**C-MTEB-retrieval（重排 bge-zh-v1.5-large 的 top-100）：**

| model | inference length | **avg** | T2 | MMarco | Du | Covid | **Cmedqa** | Ecom | **Medical** | Video |
|---|---|---|---|---|---|---|---|---|---|---|
| bge-zh-v1.5 large（一阶段） | 512+512 | **70.46** | 83.99 | 79.23 | 86.31 | 73.4 | **42.57** | 65.32 | **59.55** | 73.31 |
| bge-reranker-v2-gemma | 1024 | 71.74 | 84.04 | 83.84 | 90.9 | 87.87 | 39.4 | 62.19 | 57.56 | 68.15 |
| **bge-reranker-v2-m3** | 1024 | **71.82** | 84.57 | 83.85 | 89.6 | 89.38 | **38.77** | 63.04 | **59.48** | 65.89 |
| bge-reranker-v2-minicpm-20 | 1024 | 71.47 | 82.9 | 84.11 | 89.98 | 88.26 | 37.25 | 64.42 | 57.45 | 67.37 |
| bge-reranker-v2-minicpm-28 | 1024 | **73.51** | 84.13 | 85.52 | 91.67 | 89.14 | 41.74 | 66.44 | 61.77 | 67.68 |
| bge-reranker-v2-minicpm-40 | 1024 | 73.41 | 84.1 | 85.26 | 91.67 | 89.17 | 41.96 | 66.51 | 61.45 | 67.14 |

**⚠️ 这是本报告最重要的表格之一。逐列计算 Δ（v2-m3 减去一阶段 bge-zh-v1.5-large）：**

| 任务 | Δ | 判读 |
|---|---|---|
| **avg** | **+1.36** | 总体微增 |
| **CovidRetrieval** | **+15.98** | 大幅提升 |
| MMarcoRetrieval | +4.62 | 提升 |
| DuRetrieval | +3.29 | 提升 |
| T2Retrieval | +0.58 | 基本持平 |
| **CmedqaRetrieval** | **−3.80** | **负收益** |
| **MedicalRetrieval** | **−0.07** | **持平/微负** |
| **EcomRetrieval** | **−2.28** | **负收益** |
| **VideoRetrieval** | **−7.42** | **显著负收益** |

**MIRACL（重排 bge-m3 的 top-100）：**

| model | avg | zh | en | ja | ko | ar |
|---|---|---|---|---|---|---|
| bge-m3（一阶段） | 67.91 | 61.7 | 59.6 | 72.8 | 69.9 | 78.4 |
| **bge-reranker-v2-m3** | **72.84** | **64.2** | 63.45 | 79.96 | 73.79 | 81.7 |

→ MIRACL 上 +4.93 avg，中文(zh) **+2.5**。

**BEIR（重排 bge-en-v1.5-large 的 top-100）：**

| model | avg | trec-covid | nq | **arguana** | touche | nfcorpus |
|---|---|---|---|---|---|---|
| bge-en-v1.5 large | 54.31 | 74.89 | 55.04 | **63.54** | 25.08 | 38.12 |
| **bge-reranker-v2-m3** | **55.36** | 83.39 | 69.37 | **37.7** | 33.22 | 34.85 |

→ BEIR 上仅 **+1.05** avg；**arguana 掉 −25.8 点**（63.54→37.7），nfcorpus −3.3。

### 2.4 是否有"可用于中文医学文本"的明确表述？

**答：没有。这是明确的 NOT VERIFIED。**

- 卡片全文检索 `medical` → **命中 0 次**。
- 卡片的语言能力表述只有 "Multilingual" / "Chinese and English"，**从未提及医学、临床或任何垂直领域**。
- 官方**确实报告了** `CmedqaRetrieval` / `MedicalRetrieval` 两个中文医学类任务的数字（见 2.3 表），但：
  - 那是**它自己在 C-MTEB 上的评测结果，不是适用性背书**；
  - 而且**这两个任务上 v2-m3 相对一阶段是负收益/持平**（−3.80 / −0.07）。
- **结论**：官方 model card **不支持**"bge-reranker-v2-m3 适用于中文超声医学文本"这一说法。引用它作为医学场景依据属于**超出证据范围的推断**。

### 2.5 对 3745-chunk 语料的含义

1. 卡片推荐/演示的协议是 **rerank top-100**；但结合 §1.2，**100 是评测口径，不是生产推荐**。建议从 **top-20/30** 起步，向上扫到 50，不要直接 100。
2. **avg +1.36 在 n=117 上不可检出**（见 §0）。即使真的有效，你们的实验也测不出来。
3. **中文医学两个任务上官方数字是负的** → 直接支撑你们的怀疑：这个 reranker 在你们的语料上**很可能不中性、而是轻微有害**。
4. `max_position_embeddings=8194` 但官方评测用 1024；超声指南 chunk 若较长需注意截断。**建议显式测一次 512 vs 1024**。

---

## 3. monoT5（Nogueira et al., Findings of EMNLP 2020）

**论文**：*Document Ranking with a Pretrained Sequence-to-Sequence Model*
Rodrigo Nogueira, Zhiying Jiang, Ronak Pradeep, Jimmy Lin（Waterloo）
**URL**: https://arxiv.org/abs/2003.06713 ｜全文: https://ar5iv.labs.arxiv.org/html/2003.06713
**发表**：Findings of EMNLP 2020 ✅（arXiv 页与摘要一致，标题/作者已核对）

### 3.1 是否用 "true"/"false" token？—— 是

> "The model is fine-tuned to produce the words **"true"** or **"false"** depending on whether the document is relevant or not to the query."
> "That is, "true" and "false" are the **"target words"**."
> "At inference time, to compute probabilities for each query–document pair (in a reranking setting), we apply a **softmax only on the logits of the "true" and "false" tokens**. Hence, we rerank the documents according to the **probabilities assigned to the "true" token**."

关于为何必须用这两个单 token：
> "We choose target words ("true" and "false") that are represented as **single tokens**; thus each class is represented by a single logit."
> "Other approaches, for example, reranking documents according to the logit of the "true" token or using logits of all tokens to compute the softmax, were **not effective, i.e., the retrieval metrics were close to zero**."

（有意思的消融：把 true/false 语义反转会导致大幅下降，但用 "apple"/"orange" 这类无语义极性的词反而好于 "hot"/"cold"。）

### 3.2 重排深度：top-1000

> "At inference time, we **retrieve the top 1000 documents per query**."

（MS MARCO passage dev；这是 monoT5 的原始设定，比 BEIR 的 top-100 深一个数量级。）

### 3.3 MS MARCO 数字（MRR@10, dev）

| 模型 | MRR@10 |
|---|---|
| BM25 | .184 |
| + BERT-large（monoBERT 基线） | .372 |
| + T5-base | .363 |
| + T5-large | **.383** |
| + T5-3B | .382 |

（原表 Table 1；T5-large/.383 为加粗显著项。注意 T5-3B 未收敛，作者自述"training of T5-3B did not appear to have converged yet"。）

**Robust04 零样本迁移**（Table 2）：`+T5-3B` 达到 `.384 / .510 / .601`（MAP / nDCG@20 / P@20 口径见原文表）。

### 3.4 成本/延迟权衡（原文表述）

- 训练成本（§3.2）：
  > "Training T5 base, large, and 3B take approximately **12, 48, and 160 hours** overall, respectively, on a **single TPU**."
- 推理输入长度：
  > "We use a **maximum of 512 input tokens** and **one output token**."
- 规模上限的取舍：
  > "Larger models outperforming smaller ones is an expected trend, and with T5-11B we might observe even higher MRR@10; unfortunately, **we were not able to run these experiments due to their high computational costs**."
- 分阶段的必要性与代价：
  > "Since it is **impractical to apply inference to every document in a corpus** with respect to a query, these techniques are typically applied to **rerank a list of candidates**."
  > "This gives rise to the standard **multi-stage pipeline** architecture of keyword retrieval followed by reranking using one or more machine learning models."

> ⚠️ **诚实说明**：monoT5 论文**没有给出毫秒级的 latency 基准表**（不像 BEIR 的 Table 3）。它给的是"TPU 训练小时数"+"512 token 输入"+"多阶段架构必要性"。**若要 monoT5 的端到端延迟数字，本报告 NOT VERIFIED。**

### 3.5 对 3745-chunk 语料的含义

1. **monoT5 的 1000 深度不可照搬**——那是 MS MARCO（880 万 passage）的设定，且组合了 §1.2 的"深度有害"风险。3745 chunk 全量重排 = 117×3745 次 T5 前向，T5-3B 级别不可行。
2. **"true/false 单 token" 的实现细节要抄对**：必须只对两个 token 的 logits 做 softmax，否则指标会"close to zero"（作者原话）。自己实现时这是高频 bug 来源。
3. **monoT5 的价值在数据效率**：作者报告 "T5 is able to achieve roughly **45% of the possible gain** in effectiveness over the BM25 baseline with only **4% of the training data**"，且在 2k 样本下 `BM25+T5-base = .238` vs `BM25+BERT-base = .127`。→ 如果你们考虑**用自己的 117 条标注做轻量微调**，这是支持 monoT5 架构的论据（但 117 条远少于 2k，**外推需谨慎**）。

---

## 4. Cohere Rerank 官方文档

### 4.1 "rerank top-100 candidates" —— 确认为官方口径

**来源 1：Cohere 官方定价 FAQ**（https://cohere.com/pricing）
原文：
> "A single **search unit is defined as one query with up to 100 documents** to be ranked. If any document exceeds 500 tokens (including the length of the search query), it is automatically split into multiple chunks. Each chunk is treated as an individual document and counts toward the total number of documents ranked for that search."

→ 这是**计费口径**，官方把 "100 documents/query" 当作一个标准单位，等价于把 100 定位为"典型候选深度"。

**来源 2：Cohere Rerank 3 发布博客**（https://cohere.com/blog/rerank-3）
在对比 LLM-as-reranker 的实验中：
> "Both Rerank and LLMs were given the **top 100 documents initially retrieved from BM25**."

**⚠️ 重要澄清**：当前 Cohere 官方 docs（`docs.cohere.com/docs/reranking-best-practices`）**并没有**"推荐 rerank top-100"这句话。该页只给**上限**：
> "Number of Documents | Minimum 1 | Maximum **10,000**"
> "For `rerank-v5.0-pro` and `rerank-v5.0-fast`, you can pass up to **10,000 documents** in one request."

→ 所以"top-100"在 Cohere 语境里是**计费单位 + 评测协议**，**不是**一个"最佳实践推荐值"。把这个说成"Cohere 建议 rerank 100 条"是**过度解读**。

### 4.2 质量 vs 延迟的官方表述

Cohere 官方模型表（https://docs.cohere.com/v1/docs/rerank.md）把 pro/fast 明确分工：
> "`rerank-v5.0-pro`: **Our highest-quality reranker.** ... This is better suited for complex, reasoning-heavy queries and long or **domain-specific documents** than its `fast` variant."
> "`rerank-v5.0-fast`: A light version of `rerank-v5.0-pro` ... This model is better suited for **low latency and high throughput** use-cases than its `pro` variant."
> "*`rerank-v5.0-fast`: Best for **low-latency, high-throughput workloads** such as high-volume search and agent loops."

Rerank 3 博客里的量化：
> "Rerank 3 ... shows up to **2x lower latency** compared to Rerank 2 for shorter document lengths and up to **3x improvements** at long context lengths. Comparisons computed as the time to rank **50 documents**..."

→ 官方承认存在**明确的质量-延迟 tradeoff**，并用 pro/fast 两个 SKU 把它产品化。注意其延迟基准是 **50 documents**，不是 100。

### 4.3 它是 API → 成本含义

- **计费单位**：search unit（1 query + ≤100 docs）；超 500 token 的文档会被切块，**每块单独计一个文档**。
- **数据出境**：调用即把 query + 候选文档正文发给 Cohere 云。对**医疗文本 + 患者相关 query**，这是合规硬约束（PHI/患者信息不得外传）。
- **成本随深度线性增长**：rerank 200 条 = 2 search unit/query。117 条 query × 2 = 234 units/轮，看似小；但**每次评测重跑都计费**，且生产环境 QPS 下线性累加。
- 官方成本论据（用于推销，可作参考）：
  > "The net effect makes running RAG with Rerank on Command R+ **80-93% less expensive** than other generative LLMs in the market"

### 4.4 对 3745-chunk 语料的含义

1. Cohere 的 100 是**计费/评测口径**，不能当作"科学上最优深度"。
2. **医学合规几乎一票否决**：中文超声指南正文 + 可能的患者描述 query 出境到第三方 API，在医疗场景通常不可接受。
3. 若一定要用，`rerank-v3.5`/`v3.0` 的 context 只有 4096 token，**超 2048 token 的 query 会被截断**——中文指南 chunk 容易踩这个坑。

---

## 5. 医学/中文 reranker：CMedRAG 与 BMRetriever

### 5.1 CMedRAG —— 查无此文（NOT VERIFIED）

**结论：我无法在任何学术索引中找到名为 `CMedRAG` 的论文。请勿在文档/汇报中引用它。**

多源交叉检索结果：

| 索引 | 查询 | 结果 |
|---|---|---|
| arXiv API (`export.arxiv.org`) | `all:"CMedRAG"` | **totalResults = 0** |
| arXiv 站内搜索 | `CMedRAG` | 无结果 |
| **PubMed** (NCBI eutils esearch) | `CMedRAG` | **`"count":"0"`**，且 `"quotedphrasesnotfound":["\"CMedRAG\""]`、`"outputmessages":["No items found."]` |
| **Crossref** API | `query.bibliographic=CMedRAG` | **`total-results: 0`** |
| OpenAlex | `search=CMedRAG` | count = 1，唯一命中是 2026 年 *"Previsit AI: A Retrieval-Augmented Generation for Patient Readiness in Clinical Encounters"* —— **标题里没有 CMedRAG，属全文模糊匹配，不相关** |
| HuggingFace (镜像 API) | `search=CMedRAG`（models/datasets） | 无结果 |
| Semantic Scholar | — | 返回 HTTP 429（限流），**未能完成** → 此项 **NOT VERIFIED** |

**判断**：arXiv + PubMed + Crossref 三个独立权威索引同时返回 0，证据强度足以判定"**CMedRAG 这篇论文不存在**（或至少不是任何主流索引收录的可引用文献）"。

**可能的混淆源**（供你们排查）：
- **`CMedTEB`** —— 真实存在，是 **Chinese Medical Text Embedding Benchmark**（见 5.3），拼写相近。
- **`MedRAG`** / **`BMRetriever`** / **`CMedQA`** —— 均为真实的医学检索相关工作。
- 建议核对原始出处：如果这个名词来自某份汇报或二手综述，**该引用不可信，应删除或替换**。

### 5.2 BMRetriever —— 真实存在，但它是 retriever 不是 reranker

**论文**：*BMRetriever: Tuning Large Language Models as Better Biomedical Text Retrievers*
**URL**: https://arxiv.org/abs/2404.18443
（v1: 2024-04-29；v2: 2024-10-04；arXiv API 确认标题与日期）

**是什么（摘要原文）**：
> "We present **BMRetriever, a series of dense retrievers** for enhancing biomedical retrieval via **unsupervised pre-training on large biomedical corpora**, followed by **instruction fine-tuning** on a combination of labeled datasets and synthetic pairs."
> "Experiments on **5 biomedical tasks across 11 datasets** verify BMRetriever's efficacy on various biomedical applications. BMRetriever also exhibits strong parameter efficiency, with the **410M variant outperforming baselines up to 11.7 times larger**, and the **2B variant matching the performance of models with over 5B parameters**."

**是 retriever 还是 reranker？→ Retriever（bi-encoder / dense retriever）。**
证据：HuggingFace model card 的 `pipeline_tag` 是 **`feature-extraction`**，架构为 `gpt_neox`，用途是产出 embedding 供向量检索。**不是 cross-encoder，不能对 (query, doc) 对打分。** → 它**不能**直接替代你们的 reranker。

**Checkpoint 是否公开？→ 公开 ✅**
- 论文自述："The training data and **model checkpoints are released at https://huggingface.co/BMRetriever** to ensure transparency, reproducibility..."
- 实测确认（HF 镜像 API `author=BMRetriever`）返回：`BMRetriever/BMRetriever-410M`（downloads 284，license **mit**）、`BMRetriever/BMRetriever-1B`（downloads 74）等。
- 训练数据亦公开：`BMRetriever/biomed_retrieval_dataset`。

**报了什么 benchmark？**
> "5 biomedical tasks across 11 datasets"
涉及数据集标签可见 `MedRAG/textbooks`, `MedRAG/pubmed`, `MedRAG/statpearls`, `mteb/raw_biorxiv`, `mteb/raw_medrxiv`, `ms_marco`。
**⚠️ 它没有报 C-MTEB，也没有报中文 benchmark**（标签里语言只有 `en`）。→ **中文能力 NOT VERIFIED；这基本排除了它用于中文超声语料的可行性。**

**对 3745-chunk 语料的含义**：
- 如果你们的问题是"**一阶段检索**在中文超声语料上不够好"，BMRetriever **不解决问题**（英文、非中文、且是 retriever）。
- 如果你们的问题是"**reranker** 不管用"，BMRetriever 帮不上——**它不是 reranker**。
- 唯一可能的用法：作为**英文生物医学子语料**的 retriever，与中文场景无关。

### 5.3 真正相关的那篇：CMedTEB / CARE（中文医学检索）

这是我在检索中发现的**唯一直接命中"中文 + 医学 + 检索/重排"的 2025/2026 工作**，且它正面回答了你们的问题。

**论文**：*Benchmarking and Enabling Efficient Chinese Medical Retrieval via Asymmetric Encoders*
Angqing Jiang, Jianlyu Chen, Zhe Fang, Yongcan Wang, Xinpeng Li, Keyu Ding, Defu Lian
**URL**: https://arxiv.org/abs/2604.10937 ｜代码: https://github.com/PhilipGAQ/CARE

**是什么**：
> "we introduce the **Chinese Medical Text Embedding Benchmark (CMedTEB)**, a benchmark spanning three kinds of practical embedding tasks: **retrieval, reranking, and semantic textual similarity (STS)**."
> 并配套提出 **CARE**（Chinese Medical Asymmetric REtriever）：轻量 BERT-style query encoder + LLM-based document encoder。

**是 retriever 还是 reranker？→ 主体是 retriever（embedding），但 benchmark 含 reranking 子任务。**
CMedTEB 的 Rerank 任务定义（原文）：
> "The reranking dataset is a collection of triplets **T_Rerank = {(q_i, P_i, N_i)}**, where P_i is a list sampled from positives and N_i is a list sampled from negatives."
（即 (query, 正例列表, 负例列表) 的排序任务，评测指标 nDCG@10。）

**Checkpoint 是否公开？→ 声明将开源，但需以仓库实际状态为准**
> "To facilitate future research in medical text retrieval, **we will open-source our benchmark, models, and code** at our repository: https://github.com/PhilipGAQ/CARE"
→ 论文写的是 "**will** open-source"（将来时）。**截至本报告，我未逐一验证该 repo 的文件完整性 → NOT VERIFIED。请自行 `git clone` 后核对。**

**报了什么 benchmark（Table 2，含 rerank 列）**：

| Model | Params (Q/D) | CMedv1 MAP@10 | CMedv2 MAP@10 | **Rerank nDCG@10** | STS MAP@10 | Pearson | Avg |
|---|---|---|---|---|---|---|---|
| bge-small-zh-v1.5 | 24M/24M | 80.21 | 81.69 | 44.33 | 62.30 | 70.50 | 67.81 |
| bge-base-zh-v1.5 | 102M/102M | 83.37 | 83.31 | 49.16 | 66.73 | 76.24 | 71.76 |
| bge-large-zh-v1.5 | 326M/326M | 83.23 | 85.15 | 50.32 | 67.55 | 78.95 | 73.04 |
| **bge-m3** | 568M/568M | 82.98 | 83.32 | **51.35** | 66.90 | 78.34 | 72.58 |
| Conan-embedding-v1 | 326M/326M | 89.89 | 88.77 | 52.75 | 69.31 | 81.49 | 76.44 |
| **gte-Qwen2-1.5B-instruct** | 1.78B/1.78B | 87.68 | 87.15 | **55.39** | 72.35 | 85.50 | **77.61** |
| Qwen3-Embedding-0.6B | 596M/596M | 85.58 | 86.09 | 54.42 | 70.94 | 80.42 | 75.49 |
| **CARE-0.3B-4B** | 305M/4.02B | 86.04 | 87.31 | **55.91** | 72.84 | 88.53 | 78.13 |
| **CARE-0.3B-8B** | 305M/8.19B | 88.34 | 88.86 | **56.75** | 73.67 | 87.07 | **78.94** |

**对你们最关键的一句结论**：
> "Our experiments indicate that **even advanced general-purpose embedders underperform on CMedTEB**, highlighting the benchmark's difficulty and value for domain-specific optimization."

**→ 对 3745-chunk 语料的含义**：
- 这是**唯一**直接支持"中文医学检索需要领域专门化"的量化证据。
- 注意上表里 **`bge-m3` 的 Rerank nDCG@10 = 51.35，`bge-small-zh-v1.5` 只有 44.33**——中文医学 rerank 任务上通用模型差距达 **7 点**。
- **但**：CARE 的 4B/8B document encoder 是**离线**的；你们若要复现，需 4B~8B 显存预算。论文报 32×A100 训练。

---

## 6. 为什么通用 reranker 在领域特定（中文医学）检索上可能是负收益？

### 6.1 最硬的证据：BAAI 官方自己的表就显示负收益

回到 §2.3 的官方 C-MTEB 表。**同一个 reranker（bge-reranker-v2-m3），在 9 个中文检索任务上符号相反：**

| 任务 | 一阶段 bge-zh-v1.5-large | + bge-reranker-v2-m3 | Δ |
|---|---|---|---|
| CovidRetrieval | 73.4 | 89.38 | **+15.98** ✅ |
| MMarcoRetrieval | 79.23 | 83.85 | +4.62 ✅ |
| DuRetrieval | 86.31 | 89.6 | +3.29 ✅ |
| T2Retrieval | 83.99 | 84.57 | +0.58 ≈ |
| **EcomRetrieval** | 65.32 | 63.04 | **−2.28** ❌ |
| **CmedqaRetrieval** | 42.57 | 38.77 | **−3.80** ❌ |
| **MedicalRetrieval** | 59.55 | 59.48 | **−0.07** ❌ |
| **VideoRetrieval** | 73.31 | 65.89 | **−7.42** ❌ |

**这条证据的价值极高**：它**不需要二手引述**——是厂商自己在官方 model card 里发布的数字。**同一个模型在 Covid 上 +16 点、在 Cmedqa（中文医学问答检索）上 −3.8 点。**

**→ 含义**：reranker 的收益是**极度任务/领域特定**的，不存在"通用 reranker 普遍加分"这回事。你们怀疑"通用 reranker 在中文超声语料上是负的"，**在证据上完全站得住**。

### 6.2 跨领域复现：BEIR / 其他语料上的同类现象

| 来源 | 现象 | 数字 |
|---|---|---|
| BEIR Table 2 | BM25+CE vs BM25 | ArguAna **−0.4**、Touché-2020 **−9.6** |
| Rosa et al. Table 2（monoT5-220M） | BM25 → +rerank | Touché-2020 **0.442 → 0.277 = −16.5** |
| BAAI BEIR 表 | bge-reranker-v2-m3 | arguana **63.54 → 37.7 = −25.8** |
| Déjean et al. | SPLADE + CE，加大 k | ArguAna nDCG@10 从 ~50 **掉到 ~15** |

**共同点**：掉点最惨的都是"任务形态与 reranker 训练分布差异大"的数据集（ArguAna = 找反论点；Touché = 论辩检索；VideoRetrieval = 跨模态描述）。

### 6.3 机理证据：reranker 对 query 措辞/噪声敏感

**Drowning in Documents**（https://arxiv.org/abs/2411.11767）摘要：
> "In fact, in this setting, rerankers can **frequently assign high scores to documents with no lexical or semantic overlap with the query**."

→ 这是**直接机理陈述**：cross-encoder 在候选集变大/分布偏移时，会给"完全无关"的文档打高分。对中文超声指南语料，这意味着：一个在 MS MARCO/英文通用语料上训练的 reranker，面对"孕早期超声筛查 NT 增厚的临床意义"这类 query，其"高分"可能落在词汇相近但临床无关的 chunk 上（例如同样含"NT""增厚"但讲的是其他部位）。

**关于 query 措辞敏感度的补充证据（诚实标注）**：
- 我**没有**找到专门测量"reranker 对 query paraphrase/拼写噪声鲁棒性"的顶会论文 → 该项 **NOT VERIFIED**。
- 但 **CMedTEB** 提供了间接支持（https://arxiv.org/abs/2604.10937）：其标注流程之所以用"multi-LLM voting + 临床专家验证"，正是因为
  > "Standard hard negative mining **fails in the medical domain due to topic intensity**, where abundant latent positives lead to **severe false negatives**."
  即：医学领域**同主题文档高度密集**，通用模型容易把"真相关但用词不同"的文档判为不相关（false negative），也会把"用词相同但结论不同"的判为相关。
- 同一现象在 **2610.01324**（临床病历重排）中被描述为：
  > "relevant facts may be scattered across encounters, **repeated in copied-forward notes**, or **expressed using different clinical terminology**."

### 6.4 反证：医学领域重排**确实**能有正收益

必须公允报告——医学 reranking 不是必然为负：

**论文**：*Evaluating Biomedical Reranking for LLM-Based Question Answering over Longitudinal Clinical Notes*（https://arxiv.org/abs/2610.01324）
> "**reranking increased** exact source-chunk retrieval within the top 10 items, **Hit@10 from 46.6% to 60.6%** and **mean reciprocal rank from 0.2371 to 0.3252**. With Qwen3-8B generation, local judge-assessed answer correctness increased from **44.8% to 48.6%**."

**关键条件**：他们用的是 **MedCPT cross-encoder**（**领域专门**的生物医学 reranker），不是通用 reranker。

**论文**：*A Systematic Study of Retrieval Pipeline Design for Retrieval-Augmented Medical Question Answering*（https://arxiv.org/abs/2604.07274）
> "The best-performing configuration was **dense retrieval with query reformulation and reranking** achieved **60.49% accuracy**."
> "**Domain-specialized language models were also found to better utilize retrieved medical evidence than general-purpose models.**"
> "the analysis further reveals a clear **tradeoff between retrieval effectiveness and computational cost**, with simpler dense retrieval configurations providing strong performance while maintaining higher throughput."

→ **模式非常清晰**：
- **领域专门 reranker（MedCPT）→ 正收益且很大（Hit@10 +14.0 点）**
- **通用 reranker（bge-reranker-v2-m3）在中文医学任务上 → 负/零收益**
- 这正是你们观察到的现象，且有厂商自有数据 + 独立论文双向支持。

### 6.5 对 3745-chunk 语料的含义（综合）

1. **"通用 reranker 在中文医学上负收益"不是玄学，有官方数字**：CmedqaRetrieval −3.80、MedicalRetrieval −0.07。可以直接把这个表贴进你们的决策文档。
2. **但 −3.8 点在 n=117 上不可检出**（MDE ≈5.2 点 @SD=0.20）。所以：**你们很可能"感觉它没用"但统计上证明不了**。要么扩样本，要么直接放弃这条技术路线。
3. **正确的比较对象不是"有无 reranker"，而是"通用 reranker vs 领域 reranker"**。MedCPT 的 +14.0 点 Hit@10 是**足以在 n=117 上检出**的量级（MDE ≈13 点，勉强）。但 MedCPT 是**英文生物医学**，中文超声需自行微调。
4. **如果真的要做**，路径优先级建议：
   - ① 先量化一阶段 Recall@50（若已 >85%，reranker 天花板很低）
   - ② 若要做，**必须微调**（用 117 条标注做小样本微调，参考 monoT5 的数据效率论据）
   - ③ 深度从 **top-20~30** 起扫，**绝不超过 100**
   - ④ 预注册效应量：只报告 ≥8 点的差异，其余标"未检出"
5. **CMedRAG 不可引用**（不存在）。若团队文档里有这个引用，应从权威索引核查后删除。

---

## 附录 A：证据可信度分级

| 等级 | 来源 | 本报告中的对应 |
|---|---|---|
| **A（最高）** | 同行评议论文 + 官方 model card 原始数字 | BEIR Table 2/3/9；monoT5 Table 1/2；BAAI 官方 C-MTEB/MIRACL/BEIR 图表 |
| **B** | arXiv 预印本（未确认录用） | 2411.11767、2403.10407、2212.06121、2609.27953、2604.10937、2610.01324、2604.07274 |
| **C** | 厂商博客/定价页（有商业立场） | Cohere blog / pricing |
| **—** | 无法核实 | **CMedRAG（不存在）**；BGE v2-m3 训练的精确配比；Cohere "推荐 top-100"的官方措辞 |

## 附录 B：所有引用 URL 清单

1. BEIR — https://arxiv.org/abs/2104.08663 ｜ https://ar5iv.labs.arxiv.org/html/2104.08663
2. Drowning in Documents — https://arxiv.org/abs/2411.11767
3. CE vs LLM for SPLADE — https://arxiv.org/abs/2403.10407
4. In Defense of Cross-Encoders — https://arxiv.org/abs/2212.06121
5. Recall Ceiling of LLM Rec. Reranking — https://arxiv.org/abs/2609.27953
6. Reproducing Adaptive Reranking — https://arxiv.org/abs/2604.27577
7. monoT5 — https://arxiv.org/abs/2003.06713 ｜ https://ar5iv.labs.arxiv.org/html/2003.06713
8. monoBERT（背景） — https://arxiv.org/abs/1901.04085
9. BGE reranker v2 m3 card — https://huggingface.co/BAAI/bge-reranker-v2-m3
10. BAAI 官方 C-MTEB 结果图 — https://github.com/FlagOpen/FlagEmbedding/blob/master/research/llm_reranker/evaluation/CMTEB-retrieval-bge-zh-v1.5.png
11. BAAI 官方 BEIR 结果图 — https://github.com/FlagOpen/FlagEmbedding/blob/master/research/llm_reranker/evaluation/BEIR-bge-en-v1.5.png
12. BAAI 官方 MIRACL 结果图 — https://github.com/FlagOpen/FlagEmbedding/blob/master/research/llm_reranker/evaluation/miracl-bge-m3.png
13. FlagEmbedding llm_reranker README — https://github.com/FlagOpen/FlagEmbedding/tree/master/research/llm_reranker
14. Cohere Rerank best practices — https://docs.cohere.com/docs/reranking-best-practices
15. Cohere Rerank 模型表 — https://docs.cohere.com/v1/docs/rerank
16. Cohere Rerank 3 blog — https://cohere.com/blog/rerank-3
17. Cohere pricing FAQ — https://cohere.com/pricing
18. BMRetriever — https://arxiv.org/abs/2404.18443 ｜ https://huggingface.co/BMRetriever
19. CMedTEB / CARE — https://arxiv.org/abs/2604.10937 ｜ https://github.com/PhilipGAQ/CARE
20. Biomedical reranking clinical notes — https://arxiv.org/abs/2610.01324
21. Systematic study medical RAG pipeline — https://arxiv.org/abs/2604.07274

---

## 附录 C：明确标注为 NOT VERIFIED 的项

1. **CMedRAG** —— arXiv / PubMed / Crossref 三源均为 0 命中，判定不存在。Semantic Scholar 因 429 限流未完成查询。
2. **BAAI/bge-reranker-v2-m3 的精确训练数据配比** —— model card 只在 LLM-reranker 段落给出数据集列表，未单列 v2-m3 的配比。
3. **"BGE 可用于中文医学文本"的任何官方表述** —— 卡片全文 0 次 "medical"，不存在此声明。
4. **"Cohere 推荐 rerank top-100"的官方措辞** —— 官方只有计费口径（100 docs/search unit）与评测协议（top 100 from BM25），**没有**"推荐值"表述。
5. **reranker 对 query paraphrase/拼写噪声的鲁棒性** —— 未找到专门测量的论文；仅有间接机理证据。
6. **CARE 仓库的模型权重是否已完整发布** —— 论文写 "will open-source"，未逐一验证 repo 内容。
7. **monoT5 的毫秒级推理延迟** —— 论文未提供延迟基准表。
