# 中文医疗（超声）RAG 检索：证据综述

> 面向 3745-chunk 中文超声指南语料的工程决策。所有结论标注 **VERIFIED / NOT VERIFIED**。
> 采集时间：2026-11-09。凡我未能拿到全文的，一律写「仅摘要级证据」或「NOT VERIFIED」，不臆测数字。

---

## 1. C-MTEB：中文检索任务到底有哪些

### 1.1 C-MTEB 是什么（官方定义）

**Claim**：C-MTEB 是 Chinese MTEB，覆盖 6 类任务、35 个数据集，其中 retrieval 类为 **8 个数据集**。

**Evidence**（arXiv 2309.07597 = C-Pack，SIGIR '24 正式论文）：

> "C-MTEB is a comprehensive benchmark for Chinese text embeddings covering 6 tasks and 35 datasets."
> — https://arxiv.org/abs/2309.07597

论文明确定义检索评测口径（与 BEIR 一致，主指标 **NDCG@10**）：

> "Retrieval. The retrieval task is presented with the test queries and a large corpus. For each query, it finds the Top-k similar documents within the corpus... we use the setting from BEIR, using NDCG@10 as the main metric."
> — 同上 PDF 正文 §3.1

论文 Figure 1 的检索分支逐项列出 8 个数据集名（**VERIFIED，逐字**）：
`CmedqaRetrieval`、`CovidRetrieval`、`DuRetrieval`、`EcomRetrieval`、`MedicalRetrieval`、`MMarcoRetrieval`、`T2Retrieval`、`VideoRetrieval`，标注 "Retrieval(8 datasets)"。

> ⚠️ **注意**：你原问题里列的 5 个任务（T2Retrieval/MMarco/Du/Covid/Cmedqa）**漏了 3 个**：`EcomRetrieval`、`MedicalRetrieval`、`VideoRetrieval`。**医疗相关是 2 个，不是 1 个**——`CmedqaRetrieval` **和** `MedicalRetrieval`。

### 1.2 官方入口 URL

| 用途 | URL | 状态 |
|---|---|---|
| **MTEB 官方 leaderboard space** | https://huggingface.co/spaces/mteb/leaderboard | VERIFIED（space README 实拉成功，标题 "MTEB Leaderboard"，`app.py`, `pinned: true`）|
| C-MTEB 数据集组织 | https://huggingface.co/C-MTEB | VERIFIED（官方 README 徽章指向）|
| C-MTEB 权威表格（含数值） | https://github.com/FlagOpen/FlagEmbedding/blob/master/research/C_MTEB/README.md | VERIFIED（`research/C_MTEB/`，注意**不在** `C_MTEB/`，旧路径 404）|
| 官方 `mteb` 包任务实现 | `mteb/tasks/retrieval/zho/cmteb_retrieval.py` @ embeddings-benchmark/mteb | VERIFIED（GitHub code search + jsDelivr 实拉成功）|

> **重要**：C-MTEB 原独立 space 已并入**统一的 mteb/leaderboard**（该 space 现覆盖 text/image/audio/video 多模态）。C-MTEB 自身榜单位于 `FlagEmbedding/research/C_MTEB/README.md` 的表格。

### 1.3 8 个检索任务的官方规格（VERIFIED，来自 research/C_MTEB/README.md）

| 任务名 | 数据是什么 | 样本数 | 上游 |
|---|---|---|---|
| `T2Retrieval` | "T2Ranking: A large-scale Chinese Benchmark for Passage Ranking" | 24,832 | arXiv 2304.03679 |
| `MMarcoRetrieval` | "mMARCO is a multilingual version of the MS MARCO passage ranking dataset" | 7,437 | github unicamp-dl/mMARCO |
| `DuRetrieval` | "A Large-scale Chinese Benchmark for Passage Retrieval from Web Search Engine" | 4,000 | EMNLP 2022 (DuReader_retrieval) |
| `CovidRetrieval` | "COVID-19 news articles" | 949 | 同上 EMNLP 2022 |
| **`CmedqaRetrieval`** | **"Online medical consultation text"**（底层数据 = CMedQAv2）| 3,999 | 同上 EMNLP 2022 |
| `EcomRetrieval` | "Passage retrieval dataset collected from Alibaba search engine systems in e-commerce domain" | 1,000 | arXiv 2203.03367 (Multi-CPR) |
| **`MedicalRetrieval`** | **"Passage retrieval dataset collected from Alibaba search engine systems in medical domain"** | 1,000 | arXiv 2203.03367 (Multi-CPR) |
| `VideoRetrieval` | "...in video domain" | 1,000 | 同上 |

官方 `mteb` 实现确认所有 8 个任务：`eval_langs=["cmn-Hans"]`、`eval_splits=["dev"]`、`main_score="ndcg_at_10"`，且 `CmedqaRetrieval` 与 `MedicalRetrieval` 的 `domains=["Medical","Written"]`。

两个医疗任务的实际 prompt（VERIFIED，来自 `cmteb_retrieval.py`）：

- `CmedqaRetrieval`: **"Given a Chinese community medical question, retrieve replies that best answer the question"** —— 注意是 *社区问答*（患者问、患者/网友答），**不是指南文本**。
- `MedicalRetrieval`: **"Given a medical question, retrieve user replies that best answer the question"** —— Multi-CPR 阿里医疗搜索，同样是 *用户回复* 语料。

> ⚠️ **语料错配警告**：这两个中文医疗检索基准的语料都是**医患问答/社区问答（Q&A）**，**没有一个是指南/教科书的说明性长文本检索**。你的 3745-chunk 超声指南正文属于 *guideline/prose*，与 CmedqaRetrieval 的 "consultation reply" 分布差异显著。**用 C-MTEB 医疗分项给你的系统选型，属于外推**。

### 1.4 关键实测：医疗检索是 C-MTEB 里最弱的一项（VERIFIED 数值）

官方表格 `2.1. Retrieval`（NDCG@10）：

| 模型 | T2Ret | MMarco | Du | Covid | **Cmedqa** | Ecom | **Medical** | Video | Avg |
|---|---|---|---|---|---|---|---|---|---|
| bge-large-zh | 84.82 | 81.28 | 86.94 | 74.06 | **42.40** | 66.12 | **59.39** | 77.19 | 71.53 |
| bge-base-zh | 83.35 | 79.11 | 86.02 | 72.07 | 41.77 | 63.53 | 56.64 | 73.76 | 69.53 |
| m3e-base | 73.14 | 65.45 | 75.76 | 66.42 | 30.33 | 50.27 | 42.80 | 51.11 | 56.91 |
| multilingual-e5-large | 76.11 | 79.20 | 85.32 | 75.51 | 28.67 | 54.75 | 51.44 | 58.25 | 63.66 |
| luotuo-bert-medium | 58.67 | 55.31 | 59.36 | 55.48 | **18.04** | 40.48 | 29.80 | 38.04 | 44.40 |
| text2vec-large-chinese | 50.52 | 45.96 | 51.87 | 60.48 | 15.53 | 37.58 | 30.93 | 42.65 | 41.94 |

**读数**：对 `bge-large-zh`，`CmedqaRetrieval` 只有 **42.40**，比总体均值 **71.53** 低 **29.1 分**；`MedicalRetrieval` 59.39 也低于均值。所有 6 个模型上 `CmedqaRetrieval` 都是**全表最低项**。

**另一个坑（VERIFIED）**：该榜单的检索是**采样语料**上跑的，不是全库：

> "For retrieval tasks, we sample 100,000 candidates (including the ground truths) from the entire corpus to reduce the inference cost."
> — research/C_MTEB/README.md

### 1.5 对 3745-chunk 超声语料的含义

1. **别用 C-MTEB 平均分做选型**。Avg 71.53 被 T2Retrieval(84.82)/DuRetrieval(86.94) 这类网络检索任务抬高；医疗两项才是你的相关域，而它们恰恰是全表最弱。选模型应看 `CmedqaRetrieval` + `MedicalRetrieval` 两列。
2. **医疗分项 42 分意味着「领域内开箱即用很危险」**。若你直接上 bge-large-zh，医疗语义召回上限受限；这就是需要 domain 微调 / hybrid / rerank 的先验理由（见 §2、§3）。
3. **基准语料 ≠ 你的语料**。基准是问答对，你是指南长文 + 层级分类（甲状腺/肝脏/心脏）。**必须自建 200-400 条域内 golden set**，不能拿 C-MTEB 数字外推验收（与你项目 AGENTS.md 硬规则 #18「禁止引用已作废测试集出结论」方向一致）。
4. **100k 采样语料**也提醒：C-MTEB 的 NDCG@10 是「在 10 万候选里排」，你的库只有 3745，**任务难度低得多**，绝对分数不可比。

---

## 2. bge-m3：三种检索模式 + 权重怎么合

### 2.1 是否 dense + sparse + multi-vector 一模型三用

**Claim**：是，单模型同时输出 dense / sparse(lexical) / multi-vector(ColBERT)。

**Evidence**（官方 model card，VERIFIED 逐字）：

> "Multi-Functionality: It can simultaneously perform the three common retrieval functionalities of embedding model: dense retrieval, multi-vector retrieval, and sparse retrieval."
> — https://huggingface.co/BAAI/bge-m3

规格表：维度 **1024**，序列长度 **8192**。

### 2.2 卡片对 sparse（lexical weight）vs BM25 的说法

**Evidence**（VERIFIED 逐字，这是卡片最关键的工程承诺）：

> "Now, you can try to use BGE-M3, which supports both embedding and sparse retrieval. **This allows you to obtain token weights (similar to the BM25) without any additional cost when generate dense embeddings.**"
> — https://huggingface.co/BAAI/bge-m3

卡片 FAQ 把 sparse 定义为词表维度向量、仅对文中出现的 token 计权，并**明确把 BM25 列为同类**：

> "Sparse retrieval (lexical matching): a vector of size equal to the vocabulary, with the majority of positions set to zero, calculating a weight only for tokens present in the text. e.g., BM25, unicoil, and splade"

> ⚠️ 措辞精度：「**similar to** the BM25」是 *类比*，不是 *超越声明*。卡片**没有**给出「sparse 优于 BM25」的任何量化结论。

**反证（VERIFIED，这一点很重要）**：卡片自己的 MLDR 对比段落说 BM25 仍然能打：

> "The results indicate that **BM25 remains a competitive baseline, especially in long document retrieval.**"
> — https://huggingface.co/BAAI/bge-m3

### 2.3 语言

**Evidence**：卡片 "Multi-Linguality: It can support **more than 100 working languages**."；论文摘要 "retrieval of more than 100 working languages." —— 两处一致，VERIFIED。

### 2.4 分数怎么合？推荐权重是多少？

**这是卡片里唯一明确的合并说明（VERIFIED 逐字）**：

```python
print(model.compute_score(sentence_pairs,
                          max_passage_length=128,
                          weights_for_different_modes=[0.4, 0.2, 0.4]))
# weights_for_different_modes(w) is used to do weighted sum:
# w[0]*dense_score + w[1]*sparse_score + w[2]*colbert_score
```
> — https://huggingface.co/BAAI/bge-m3 （Usage → "Compute score for text pairs"）

**⚠️ 这是最容易被误读的一条**：`[0.4, 0.2, 0.4]` 出现在**示例代码的默认参数位**，卡片**没有任何文字说明这是调优后的推荐值**，也没有给出调参实验或 ablation 支撑。**不要把它当成官方最佳权重**——应视为「一个能跑通的起点」。

卡片另有流程建议（VERIFIED 逐字）：

> "**We recommend to use the following pipeline: hybrid retrieval + re-ranking.**"
> "As cross-encoder models, re-ranker demonstrates higher accuracy than bi-encoder embedding model."

### 2.5 报告的 C-MTEB / MIRACL-zh 数字

**C-MTEB：NOT VERIFIED。** 我在 model card 与 arXiv 2402.03216 全文里 grep `C-MTEB`，**零命中**。bge-m3 官方材料**不报告 C-MTEB 分数**；`research/C_MTEB/README.md` 的榜单里也**没有** bge-m3 行（只有 bge-small/base/large-zh 与 m3e）。任何声称"bge-m3 C-MTEB = XX"的数字，**来源不明，不要采信**。

**MIRACL dev nDCG@10（VERIFIED，论文 Table 1，含 zh 列）**：

| Mode | Avg | **zh** | de |
|---|---|---|---|
| Dense | 69.2 | **62.7** | 56.7 |
| Sparse | 53.9 | **36.1** | 32.5 |
| Multi-vec | 70.5 | **63.7** | 58.0 |
| Dense+Sparse | 70.4 | **63.5** | 57.7 |
| All | 71.5 | **64.9** | 59.8 |

来源：https://arxiv.org/abs/2402.03216 Table 1（我按 19 列表头 `Avg, ar, bn, en, es, fa, fi, fr, hi, id, ja, ko, ru, sw, te, th, zh, de, yo` 逐列对齐核算，zh 为第 17 列）。

**读数（关键）**：
- 中文上 **sparse 只有 36.1，比 dense 的 62.7 低 26.6 分**——bge-m3 的 lexical 头在中文上显著弱于 dense。中文没有英文那样的空格分词，lexical weight 吃亏。
- 中文上 **Dense+Sparse (63.5) 反而略低于 Multi-vec (63.7)**，甚至只比 Dense 单路 (62.7) 高 0.8。三路全开 (64.9) 才相对 dense 提升 **+2.2**。
- 也就是说：**在中文场景，sparse 一路的边际贡献很小，加进去还可能拉低**。这与卡片「free bonus」的暗示有落差。

**另一条必须知道的事实（VERIFIED，卡片 News 2024/7/1）**：MIRACL 数字曾被**上调更正**，原因是实现 bug——

> "The previous test results were lower because we mistakenly removed the passages that have the same id as the query from the search results."
> — https://huggingface.co/BAAI/bge-m3

### 2.6 一个反直觉但有价值的 ablation（VERIFIED）

论文 Table 5：去掉 self-knowledge distillation 后，sparse 从 **53.9 崩到 36.7**（dense 仅 69.2→68.7）。论文自己的解读：

> "Notably, the impact is more pronounced for sparse retrieval. **Such a result also reflects the incompatibility between dense and sparse retrieval methods.** With skd, the incompatibility can be largely overcome."
> — arXiv 2402.03216 §4.4

**含义**：sparse 头是**靠蒸馏被硬拽到和 dense 对齐的**，本身与 dense 不同源、不自洽。这解释了为什么 naive 加权合并不稳。

### 2.7 对 3745-chunk 超声语料的含义

1. **三路全开要付代价**：ColBERT multi-vec 需要为每个 chunk 存多向量（3745 chunks × ~100+ token 向量 × 1024 dim），存储与检索开销远超单 dense。**在 3745 chunks 上，dense + BM25/sparse 两路 + rerank 已足够**，multi-vec 收益（中文 +1.0 vs dense+sparse）难justify成本。
2. **中文上优先信 dense，sparse 当「专有名词精确匹配补充」用**（如 `TI-RADS 4a`、`LVEF 55%`、药名、缩写），不要指望它当主力。MIRACL-zh 36.1 这个数足以劝退「sparse 单独扛」的方案。
3. **权重不要抄 0.4/0.2/0.4**。那是示例默认值。应在自建 golden set 上网格搜索；考虑中文 sparse 弱，dense 权重应显著高于 0.4。
4. **rerank 是卡片明确推荐的一环**，且卡片称 cross-encoder reranker 精度高于 bi-encoder——在 3745 的小库上，**top-50 → rerank → top-5** 成本极低，这是性价比最高的一步。

---

## 3. 医疗检索的特殊性：术语、缩写、层级

### 3.1 临床 IR 的失败模式：有权威的失败分析（VERIFIED，全文可读）

这是本报告里**最强的临床 IR 证据**——TREC 2011 Medical Records Track 的**官方失败分析**，AMIA 2012，PMC 全文可读。

**基线有多差（VERIFIED 逐字）**：

> "The best bpref performance in TRECMed was in the range 0.5–0.6, reasonably good performance for a general IR task **but not satisfactory for identifying patients in a set of clinical records.**"
> — https://pmc.ncbi.nlm.nih.gov/articles/PMC3540501/

**失败模式定性（VERIFIED 逐字，摘要）**：

> "Reasons fell into nine categories for non-relevant visits and five categories for relevant visits. **Non-relevant visits were most often retrieved because they contained a non-relevant reference to the topic terms. Relevant visits were most often infrequently retrieved because they used a synonym for a topic term.**"

**同义词/术语不匹配是头号召回杀手（VERIFIED 逐字）**：

> "**The most common feature in under-retrieved relevant visits was the use of different terminology in the notes.** A search for patients with hearing loss tended to overlook visits mentioning **deafness or hearing aids**. Spelling variations and typographical errors also fell into this category."

**否定（negation）导致假阳性（VERIFIED 逐字）**：

> "Several non-relevant visits appeared to be retrieved because the notes mentioned ruling out a diagnosis or denial of a symptom, a common component of medical records. Visits retrieved for patients with hearing loss included many that noted the **'patient denies hearing loss.'**"

**三大主题（VERIFIED 逐字）**：

> "Several themes emerged from our analysis, with **terminology differences, negation, and time aspects** having the most impact on retrieval inaccuracy."

> **对超声的直接映射**：`未见`、`排除`、`不支持`、`待排` 在超声报告里高频（"未见明显异常"、"不除外恶性"）。**纯向量检索对否定不敏感**——"有结节" 与 "未见结节" 的 embedding 余弦相似度极高。这是必须靠结构化过滤或规则层拦截的失败模式。

### 3.2 缩写/首字母词：确证是硬问题

**(a) 缩写歧义需要专门建模（VERIFIED，摘要级）**

> "**Automatic identification and expansion of ambiguous abbreviations are essential for biomedical natural language processing applications, such as information retrieval and question answering systems.**"
> — DECBAE, arXiv 1906.03360

该文从 PubMed 摘要自动采集 **950 个歧义缩写**，模型达 accuracy **0.961**、macro-F1 **0.917**。
⚠️ **仅摘要级证据**（未取全文）。但「950 个歧义缩写」这一数量级已足以说明问题规模。

**(b) 缩写匹配被列为检索最难的一类（VERIFIED，摘要级）**

> "...our models significantly outperforms all existing dense retrievers, achieving state-of-the-art results. Detailed analyses confirm our models' superiority across various match and query types, **particularly in challenging semantic matches like implication and abbreviation.**"
> — DR.EHR, arXiv 2507.18583

同文对现状的判断（对你的选型直接相关）：

> "existing models, both **general-domain and biomedical-domain, fall short** due to **insufficient medical knowledge or mismatched training corpora.**"

**(c) 中文/中英混写缩写是额外一层**：超声指南里的 `TI-RADS`、`C-TIRADS`、`BI-RADS`、`HCC`、`LVEF`、`EF`、`FNA`（细针穿刺）、`CEUS`（超声造影）大量以「中文名 + 英文缩写」并现形式出现。bge-m3 用 XLM-RoBERTa tokenizer，会把 `C-TIRADS`、`TI-RADS` 切成子词碎片，**`C-TIRADS` 与 `TI-RADS` 的后缀高度重叠 → 语义上极易混淆**——而这两者是**不同评分体系**（中国版 vs 美国版），混淆在临床上不可接受。
> ⚠️ 这一条是**我的推断（基于 tokenizer 已知行为 + 上述缩写文献）**，**我没有找到直接做「C-TIRADS vs TI-RADS 向量混淆」实验的论文 → 标 NOT VERIFIED，建议你们在自建 golden set 上实测**（构造 30 条只差评分体系名的 query，看两个体系是否互串）。

### 3.3 「领域专用 embedding 更好」——证据是矛盾的（必须如实报告）

**支持方（VERIFIED，摘要级）**：DR.EHR（2507.18583）称通用与生物医学域模型均因 "insufficient medical knowledge or mismatched training corpora" 而不够用，其专用模型在 CliniQ 上 SOTA。

**反对方（VERIFIED，摘要级，且更贴近你的场景）**：一项 EHR 检索消融研究，7 个模型、2 个 EHR 数据源、3 个检索任务：

> "We found that the choice of embedding model significantly impacts retrieval performance, with **BGE, a comparatively small general-domain model, consistently outperforming all others, including medical-specific models.** However, our findings also revealed **substantial variability across datasets and query text phrasings.**"
> "...the performance of these models on other public benchmarks **does not necessarily transfer to new domains.**"
> — arXiv 2409.15163

**结论（诚实的版本）**：
- 「临床文本检索受益于领域专用 embedding」**NOT VERIFIED 为普适结论**。有强反例：通用 BGE 在真实 EHR 上打赢了医疗专用模型。
- 但**可验证的共识是另一条**：**领域内表现不可由公开榜单外推**（"does not necessarily transfer to new domains"），且**对 query 措辞高度敏感**。
- 所以正确动作不是「买/训一个医疗 embedding」，而是**先建域内 golden set，用实测决定**。这与你的 AGENTS.md 硬规则 #18 完全一致。

### 3.4 对 3745-chunk 超声语料的含义

1. **否定是头号风险，且是结构性问题**：向量检索分不清「有/无」。建议在 chunk 元数据里带 `polarity` 或对否定句做 strip/标记；**不要指望换 embedding 解决**。
2. **缩写要建同义词表，别指望模型**：维护 `TIRADS↔TI-RADS↔C-TIRADS↔甲状腺影像报告数据系统`、`CEUS↔超声造影`、`FNA↔细针穿刺` 的显式映射，在 query 侧做归一化。这是**确定收益、零模型风险**的工程动作。
3. **层级分类要用元数据，不要用向量**：甲状腺/肝脏/心脏三器官、以及指南章节层级，是**离散标签**，用 filter 表达比用 embedding 表达可靠一个量级（见 §5）。
4. **必须自建 golden set**：至少 150-300 条，覆盖（i）同义表述（ii）中英缩写混写（iii）否定表述（iv）跨器官易混查询。这是后面 §2/§4 所有权重与开关决策的唯一依据。

---

## 4. Query Expansion 什么时候**有害**（核心问题）

### 4.1 Billerbeck & Zobel, ADC 2004 —— 存在，但结论被普遍误传

**VERIFIED：论文确实存在**，全名 *"Questioning query expansion: an examination of behaviour and parameters"*, 2004, CRPIT Vol. **27**（不是 32；曾疑为 32，实测 32 卷为该会议另一文）。

**官方摘要（VERIFIED 逐字，OpenAlex）+ 我的中文转述**：

> "In information retrieval, queries can fail to find documents due to mismatch in terminology. Query expansion is a well-known technique addressing this problem, where additional query terms are automatically chosen from highly ranked documents, and it has been shown to be effective at improving query performance. **However, current techniques for query expansion use fixed values for key parameters, determined by tuning on test collections. In this paper we show that these parameters may not be generally applicable, and more significantly that the assumption that the same parameter settings can be used for all queries is invalid.** Using detailed experiments with two test collections, we demonstrate that new methods for choosing parameters must be found. However, our experiments also demonstrate that there is considerable further scope for improvement to effectiveness through better query expansion."

**⚠️ 必须纠正一个常见误传**：这篇论文**不是**「QE 有害」的证据。它的核心结论是：
1. **固定参数不泛化**（在一个 collection 上调好的 QE 参数，换 collection 就失效）；
2. **更关键：假设「所有 query 用同一套 QE 参数」是错的** → 需要 **per-query 自适应**。

> 这实际上为 §4.3 的 **selective / adaptive QE** 提供了最早的动机，而**不是**「别用 QE」。

**⚠️ 证据等级**：**仅摘要级证据**。原文 PDF 全站拿不到——`crpit.com` 域名已被劫持（返回博彩站），Wayback 对该 PDF 返回 000，CiteSeerX 跳转失效。**全文 NOT VERIFIED**。

**同作者上一篇更直接的负结果（VERIFIED 存在，元数据）**：
- Billerbeck & Zobel, **"When query expansion fails"**, SIGIR 2003, DOI `10.1145/860435.860514`（Crossref 确认标题/作者/年份）。
- ⚠️ **仅元数据级证据**：ACM DL 403，未取全文。**具体失败机制 NOT VERIFIED**——但标题本身即是最直接的负面命题。

### 4.2 Carpineto & Romano, ACM CSUR 2012 —— 存在，但摘要不谈「何时变差」

**VERIFIED 元数据**：*A Survey of Automatic Query Expansion in Information Retrieval*, **ACM Computing Surveys 44(1):1–50, 2012**, DOI `10.1145/2071389.2071390`（Crossref + OpenAlex 双确认）。

**官方摘要（VERIFIED 逐字）**：
> "The relative ineffectiveness of information retrieval systems is largely caused by the inaccuracy with which a query formed by a few keywords models the actual user information need. One well known method to overcome this limitation is automatic query expansion (AQE)... This survey presents a unified view of a large number of recent approaches to AQE... The following questions are addressed. Why is query expansion so important to improve search effectiveness? What are the main steps involved in the design and implementation of an AQE component? What approaches to AQE are available and how do they compare? **Which issues must still be resolved before AQE becomes a standard component of large operational information retrieval systems (e.g., search engines)?**"

**⚠️ 关于「它说 QE 何时会掉点」——NOT VERIFIED**。
摘要**没有**任何关于性能退化的表述，只把 gate 到生产部署的问题留作开放问题。**全文未获取**（ACM 403、CiteSeerX 301 循环、scholar.archive.org 超时）。

> 结论：**不要把「Carpineto 综述指出 QE 会降效」当作可引用的已证事实**。它存在、它是权威综述，但**我没能验证其中关于负向结果的任何具体论断**。若要引用，请先拿到全文。

### 4.3 决定性证据：QE「提升全局均值但伤害最差 query」

这是**最强、最完整可读**的经典证据（**VERIFIED 全文摘要**，Springer 可读）：

> "**It is a well known fact that Query Expansion (QE) increases global MAP but hurts the performance on the worst topics.** A selective application of QE would thus be a natural answer to obtain a more robust retrieval system. We define two information theoretic functions which are shown to be correlated respectively with the average precision and with the increase of average precision under the application of QE. The second measure is used to selectively apply QE. This method achieves a performance similar to that with unexpanded method on the worst topics, and better performance than full QE on the whole set of topics."
> — Cronen-Townsend, Zhou & Croft, *"Query Difficulty, Robustness, and Selective Application of Query Expansion"*, ECIR 2004, DOI `10.1007/978-3-540-24752-4_10`
> https://link.springer.com/chapter/10.1007/978-3-540-24752-4_10

**这段一次性回答了三个问题**：
1. **QE 有害吗？** 是——"hurts the performance on the worst topics"（伤的是最难的 query，恰恰是 RAG 最需要它们的时候）。
2. **为什么均值看不出来？** 因为 "increases global MAP"——**均值上涨掩盖了尾部崩塌**。这正是你团队最该警惕的度量陷阱。
3. **有 selective QE 吗？** 有——用**信息论函数预测「该 query 扩了会不会涨」**，只在该扩时扩；结果 "better performance than full QE on the whole set of topics"。

> **这条与你项目的度量纪律直接呼应**：只报平均 hit@10 / faithfulness 会掩盖长尾失效；必须同时报 **worst-X%** 或分位指标。

### 4.4 QE 只在初始检索弱时有用；现代强 dense retriever 上增益消失

**Query2doc（arXiv 2303.07678）我读了全文 PDF**，它自己报告了**增益随 retriever 变强而衰减**：

> "For dense retrieval, the model variants that combine with query2doc also outperform the corresponding baselines on all metrics. **However, the gain brought by query2doc tends to diminish when using intermediate pre-training or knowledge distillation from cross-encoder re-rankers**, as shown by the 'SimLM + query2doc' and 'E5 + query2doc' results."

**OOD 上直接变负（VERIFIED 逐字）**：

> "For zero-shot out-of-domain retrieval, **the results are mixed** as shown in Table 2. Entity-centric datasets like DBpedia see the largest improvements. **On the NFCorpus and Scifact datasets, we observe a minor decrease in ranking quality.** This is likely due to the distribution mismatch between training and evaluation."

**Table 2 的负增益数字（VERIFIED，我按表逐格核对）**：

| Retriever | DBpedia | NFCorpus | Scifact | Trec-Covid | Touche2020 |
|---|---|---|---|---|---|
| BM25 | 31.3 → 37.0 (**+5.7**) | 32.5 → 34.9 (+2.4) | 66.5 → 68.6 (+2.1) | 65.6 → 72.2 (+6.6) | 36.7 → 39.8 (+3.1) |
| **SimLM** | 34.9 → 38.3 (+3.4) | 32.7 → 32.1 (**−0.6**) | 62.4 → 59.5 (**−2.9**) | 55.0 → 59.9 (+4.9) | 18.9 → 25.6 (+6.7) |
| **E5base+KD** | 40.7 → 42.4 (+1.7) | 35.0 → 35.2 (+0.2) | 70.4 → 67.5 (**−2.9**) | 74.1 → 75.1 (+1.0) | 30.9 → 31.7 (+0.8) |

**这是本报告最硬的「QE 对 dense retriever 负增益」实测**：两个已做 intermediate pretraining / KD 的强 dense retriever，在 Scifact 上都被 QE **拉低 2.9 分**；SimLM 五格中两格为负。

**其他可引的 Query2doc 自承问题（VERIFIED 逐字）**：
- 幻觉风险："However, as our analysis shows, **LLMs can sometimes generate false claims, hindering their practical application in critical areas.**"（"critical areas" 正包括医疗）
- 延迟：Table 6 —— 纯 BM25 `16ms` → `+query2doc` **`>2000ms` LLM 调用** + 177ms 索引检索。**两个数量级**。

> **对医疗 RAG 的隐含结论**：Query2doc 的收益主要来自「LLM 的世界知识补全 query 语义缺口」。但**医疗指南问答恰恰是「知识必须在库里、不能靠模型记忆」的场景**——LLM 生成的 pseudo-doc 若掺入错误医学断言，会**主动把检索推向错误证据**，而这正是 RAG 要防的事。**在这个领域，QE 的收益/风险比天然劣于开放域。**

### 4.5 2023-2025 的新证据（含负向，标注证据等级）

**(a) PRF 伤害大量 query，且「避免伤害」比「捡到收益」更值钱**（arXiv 2607.12098，*preprint*，摘要级）：

> "Pseudo-Relevance Feedback (PRF) improves retrieval effectiveness on average, but **harms a substantial fraction of queries through query drift**, an asymmetry hidden by aggregate offline metrics."
> "a participatory audit with 108 users across 43 TREC Deep Learning 2019 queries shows that **only 20.9% of queries benefit from PRF, while 25.6% suffer a degraded user experience**, and that **avoiding harm is nearly twice as valuable as exploiting successful expansion.**"

> ⚠️ **证据等级警告**：这是**预印本，未同行评审，我仅读到摘要**。且我观察到 arXiv 元数据不一致（ID 2607 但 timestamp 显示更早月份），**不能排除索引异常**。**这些百分比请勿直接写进对外材料**，需先自行核实全文。

**(b) 多 query 改写会加剧 drift**（arXiv 2605.00560，*preprint*，摘要级）：

> "...introduces a fundamental tradeoff between recall and query drift... prior methods **suffer from severe quality degradation due to drift**" (as the number of reformulations increases)

**同等级警告**：预印本 + 摘要级。

**(c) 「QE 最佳实践」类论文（arXiv 2401.06311, MuGI）是**正面**结果，但要点在「怎么合」不在「要不要扩」**（摘要级，VERIFIED 逐字）：

> "(2) **A balance between the query and pseudo-documents, and an effective integration strategy, is critical for high performance.**"

> **关键解读**：连最积极的 QE 论文也把成败归于「**query 与 pseudo-doc 的配比**」。这等于承认：**naive 全量拼接会掉点**，收益来自精细调参——**而这套调参在 3745 chunk 的小库上不具备统计功效**（见 §4.6）。

### 4.6 对 3745-chunk 超声语料的含义（本报告最实用的部分）

1. **默认关闭 QE，用数据开通**。理由链：
   - Cronen-Townsend：QE 伤最差 query；你的长尾恰是疑难病例 query。
   - Query2doc：强 dense retriever 上增益趋零甚至为负；bge-m3 属强模型。
   - Query2doc/OOD：域外/分布不匹配时 **mixed**，医疗指南正是与 LLM 预训练分布不同的封闭语料。
   - 成本：延迟 +2 个数量级，对临床交互不可接受。
2. **若一定要开，必须做 selective QE**（Cronen-Townsend 范式）：用**查询难度预测**（如 query 与 top-1 的相似度、top-k 分数方差、是否命中缩写表）决定「扩不扩」。**规则化、可解释、可回滚**，比无脑扩安全。
3. **度量必须报尾部**，不能只报均值：
   - 报 **hit@10 / MRR 的 P10 或 worst-20% 均分**，与均值并列。
   - 报**负向翻转率**：有多少 query 在开启 QE 后 hit@10 由 1→0。这个指标最能暴露 QE 危害，且在小样本上也稳定。
4. **3745 chunks 上做 QE 调参在统计上没有功效**。若你有 200 条 golden set、QE 真实增益 2 个点（Query2doc 在强 retriever 上的量级），检验功效极低 → **你无法可靠区分「QE 有效」与「噪声」**。这种情况下**保守默认（关闭）是理性选择**，而不是"试试看"。
5. **把 QE 的收益点转移到更安全的地方**：与其用 LLM 扩 query，不如
   - **缩写/同义词表归一化**（§3.4，确定性收益，零幻觉风险）；
   - **rerank**（bge-m3 卡片明确推荐，无幻觉风险）；
   - **hybrid 补 lexical**（专有名词精确匹配，见 §2）。

---

## 5. Metadata filtering：pre-filter vs post-filter

### 5.1 Weaviate：post-filter 的两个失效模式（**最清晰、可直接引用**）

**Evidence（VERIFIED 逐字）**：
> "Systems that cannot make use of pre-filtering typically have to make use of post-filtering. This is an approach where a vector search is performed first and then some results are removed which do not match the filter. This leads to **two major disadvantages**:
> - **You cannot easily predict how many elements will be contained in the search**, as the filter is applied to an already reduced list of candidates.
> - **If the filter is very restrictive, i.e. it matches only a small percentage of data points relative to the size of the data set, there is a chance that the original vector search does not contain any match at all.**"
> — https://weaviate.io/developers/weaviate/concepts/filtering

**这就是你要的「post-filter 在 filter 高度选择性时的失效模式」的权威表述**：不是「结果变少」，而是**可能一个都不剩（空结果）**——即**静默丢结果**。

**Weaviate 的解法（VERIFIED 逐字）**：
> "Filtered vector search in Weaviate is based on the concept of **pre-filtering**. This means that the filter is constructed before the vector search is performed."
> "Each shard contains an **inverted index right next to the HNSW index**. This allows for efficient pre-filtering... An inverted index... is used to create an **allow-list** of eligible candidates... A vector search is performed where the allow-list is passed to the HNSW index."

**版本事实（VERIFIED）**：> "Starting in **v1.34**, Weaviate uses the **ACORN** filter strategy as the default."

### 5.2 Qdrant：filterable HNSW + 为什么中间地带最难

**核心架构陈述（VERIFIED 逐字，最值得引用的一段）**：

> "Separately, a payload index and a vector index cannot completely address the challenges of filtered search.
> **In the case of high-selectivity (weak) filters, you can use the HNSW index as it is.
> In the case of low-selectivity (strict) filters, you can use the payload index and do a complete rescore.
> However, for cases in the middle, this approach does not work well.**
> On one hand, we cannot apply a full scan on too many vectors. On the other hand, **the HNSW graph starts to fall apart when using filters that are too strict.**
> Qdrant solves this problem by **extending the HNSW graph with additional edges based on indexed payload values.**"
> — https://qdrant.tech/documentation/concepts/indexing/ （Indexing → Filterable HNSW Index）

> 🚨 **术语陷阱，务必转告团队**：Qdrant 的 selectivity 用词与业界**相反**。
> - Qdrant 写 **"high-selectivity (weak)"** = 命中很多（弱过滤）
> - Qdrant 写 **"low-selectivity (strict)"** = 命中很少（强过滤）
> 而 Weaviate / 通常语境里 "highly selective filter" = 命中很少。
> **照 Qdrant 字面理解会把「命中少」当成「弱过滤」，直接搞反策略。** 读 Qdrant 文档时一律替换成「过滤命中率」来理解。

**⚠️ 归属更正（重要）**：你原问题把「post-filter 静默丢结果」归给 Qdrant。**实测核查结果：Qdrant 官方文档全文从未出现 "post-filter" 一词**（我对 `concepts/indexing`、`concepts/filtering` 两页 grep，命中 0）。**"post-filtering" 的清晰表述出自 Weaviate**（§5.1）。Qdrant 讲的是同一失效机制的另一个角度：**图在严格过滤下断裂（graph falls apart / collapses）**，并有实测量化（§5.3）。两者结论一致，但**引文不要张冠李戴**。

**payload index 的作用（VERIFIED 逐字）**：
> "The index is also used to **accurately estimate the filter cardinality**, which helps the **query planning** choose a search strategy."
> — https://qdrant.tech/documentation/concepts/indexing/

**建立顺序是硬约束（VERIFIED 逐字）**：
> "**Payload indexes should be created before ingesting data.** Qdrant's filterable HNSW index only benefits from additional filter-aware edges when it is generated after the payload indexes have been created. If you create a payload index after data has already been ingested, you need to [rebuild] to take advantage of the new payload indexes."

**组合过滤仍会失败（VERIFIED 逐字）**：
> "These extra edges are added for **each payload index separately, but not for every possible combination** of payload indices. As a result, **a combination of two or more strict filters might still lead to disconnected graph components.**"
> → 解法：**ACORN**（v1.16.0+），"explores not just direct neighbors (first hop), but also neighbors of neighbors (second hop) when direct neighbors are filtered out. This improves search accuracy at the cost of performance."

**为什么是「图断裂」而非「变慢」——原始设计文（VERIFIED）**：
> Qdrant 2019 原文用**渗流理论（percolation）**解释：随机移除比例 `1-p` 的节点，存在临界阈值 `pc = 1/<k>`，"below which **the network becomes fragmented** while above pc a giant connected component exists."
> — https://qdrant.tech/articles/filterable-hnsw/
> ⚠️ 该文自己声明："This article describes the **original design** for filterable HNSW, proposed in 2019. **It is not a complete description of how Qdrant performs filtered vector search today.**" —— 引用时须带此限定。

**一篇必须提醒的坑（VERIFIED）**：Qdrant 文档明说，某些 payload 字段不必建 HNSW 边（v1.17.0，`enable_hnsw: false`），因为 "sparse vector search does not use the HNSW index"。→ **若你用 sparse 路做过滤，边的优化对它无效**。

### 5.3 失败模式的**实测量化**（Qdrant 官方基准，VERIFIED 数字）

Qdrant 有一篇带 benchmark 的文章，直接给出「过滤越严格，纯图召回如何崩塌」的实测。**这比任何估算都可靠，请以此为准。**

**官方定义（VERIFIED 逐字）**：
> "`hnsw_ef`, shortened to `ef` in this article, is the number of candidates the search evaluates, so raising it improves recall and slows the query. **Selectivity is the fraction of points that pass the filter.**"
> — https://qdrant.tech/articles/filtered-vector-search-acorn/

> 这确认了我 §5.2 的提醒：Qdrant 口径下 **selectivity = 通过过滤的比例**（越大越"不挑"）。与其他厂商用法相反。

**单字段过滤，`hnsw_ef=64`（VERIFIED 原表）**：

| 过滤（selectivity） | Plain graph | Plain graph + ACORN | Filterable HNSW |
|---|---|---|---|
| 单关键词 (20%) | **62.9%** @1.6ms | 98.9% @4.4ms | 94.8% @1.2ms |
| 单关键词 (10%) | **20.6%** @1.7ms | 98.1% @4.3ms | 99.0% @1.1ms |
| 单关键词 **(1%)** | **0.1%** @1.6ms | 67.7% @4.7ms | **99.8%** @1.0ms |
| 相关字段 (10%) | 88.4% | 98.6% | 99.0% |

**双关键词 AND 组合（VERIFIED 原表，`hnsw_ef=64`）**：

| 过滤（selectivity） | Plain graph + ACORN | Filterable HNSW | Planner + ACORN |
|---|---|---|---|
| 双关键词 (4%) | 95.2% @7.7ms | **63.7%** @1.2ms | 99.9% @13.9ms |
| 双关键词 (1%) | 72.7% @6.8ms | **70.8%** @1.5ms | 100% @3.7ms |
| 双关键词 **(0.012%)** | **0.6%** @2.6ms | **1.8%** @2.6ms | **100%** @1.3ms |

**三条可直接引用的结论（全部 VERIFIED 逐字）**：

1. **严格过滤下召回近乎归零**：
   > "**The plain graph collapses as filters tighten**, and only the correlated filter holds up."
   > 单关键词 1% 时 Recall@10 = **0.1%**；双关键词 0.012% 时 = **0.6%**。

2. **两种单点修复（额外边 / ACORN）在极窄过滤上都不够**：
   > "ACORN pulls recall back at 2.1x to 2.9x the plain graph's latency, **then stalls on the 1% filter**."
   > 1% 单字段：ACORN 仅 67.7%；0.012% 双字段：ACORN 0.6%、Filterable HNSW 1.8%。

3. **真正救回来的是「planning：窄过滤直接走 payload 索引全扫」**：
   > "**Most filters need no help: the planner sends highly selective filters straight to the payload index**, and extra edges carry the broad ones on the graph."
   > 0.012% 双字段：Planner + ACORN = **100%**。
   > 机制：`full_scan_threshold` "tells Qdrant when a filtered result set is small enough to scan directly... **Qdrant skips the HNSW graph when the matching vectors fall below it.**"

**⚠️ 最该转告团队的一条（官方原话，VERIFIED）**：
> "On the default configuration here, **one filter returned 39.7% with ACORN off while every other filter stayed above 90%, and a single aggregate number would have hidden it.**"

> 这正是「**静默丢结果**」的完美例证：4% 双关键词过滤下召回仅 **39.7%**，但**没有报错、没有告警**，聚合指标也看不出来。**post-filter / 窄过滤的风险不是性能，是正确性。**

**官方给出的自查方法（VERIFIED，建议照做）**：
> "Create a payload index on every field you filter on, and leave ACORN off to start... **sample a few hundred real queries per filter shape, 500 if you want to match this benchmark. Get exact results with `exact: true`**, and score both recall and latency with ACORN off and then on."

**注意「一票否决」的适用范围**：以上是 Qdrant 在**大规模集合**（Table 1 显示单值 ~100k–200k 点）上的基准。**你的 3745 点库不会被 `full_scan_threshold` 判为「太大」，planner 会直接全扫 —— 即默认就走最稳的那条路。** 这也解释了 §5.4 的结论。

### 5.4 对 3745-chunk 超声语料的含义（**结论很反直觉，请务必传达**）

1. **3745 chunks 根本不需要 ANN/HNSW**。
   3745 × 1024 float32 ≈ **15.3 MB**。**暴力精确内积在 CPU 上 < 5ms**（numpy 一次 matmul）。
   > ⚠️ 「<5ms」是**我的量级估算，非任何文档或实测数字**（未在本会话中跑 benchmark）。请在目标机器上实测后再引用。
   你完全可以：
   - **先按元数据精确筛出候选集（pre-filter，绝对不漏）**；
   - **再对候选集做精确 dense 打分（brute force）**。
   这样**过滤导致的静默丢结果问题在你的规模上被工程性消除**——不是靠调参，是靠**放弃 ANN**。这与 Qdrant 自己的 planner 思路一致：窄过滤就该全扫（§5.3）。
2. **如果仍用 Qdrant/Weaviate，必须用 pre-filter 语义**：
   - Qdrant：payload index **必须在灌数据之前建**（否则 filterable HNSW 的附加边不生成，需重建）。
   - 多个严格 filter 组合 → 开 **ACORN**（但见 §5.3：极窄过滤 ACORN 也救不回，真正靠 planner 全扫）。
   - **按 selectivity 分档估算你的过滤**（N=3745）：`organ == "thyroid"` ≈ 1124 → **30%**，属"宽过滤"，安全；`organ + polarity` 组合 ≈ 5% → 进入 Qdrant 实测中召回崩塌区间（20% 时纯图已掉到 62.9%）；`organ + section + polarity` 三条件 ≈ 1–4% → **必须按 §5.3 的方法实测 `exact: true` 召回**，不要假设。
   - **好消息**：3745 点远小于基准里的 ~100k–200k，`full_scan_threshold` 极可能让 planner 直接全扫，从而**默认规避整个问题**——但这**依赖配置**，必须实测确认而非假设。
3. **层级分类用 filter，不要塞进向量**。甲状腺/肝脏/心脏 + 指南章节是**离散、互斥、权威的标签**。用 `filter` 表达：
   - 召回**可证明完备**（candidate set 就是全集子集）；
   - 可解释、可审计（符合你项目"辅助诊断、需医生确认"的定位）；
   - 避免「心脏问题召回肝脏结节」这类**临床上不可接受**的跨器官串扰。
   而把器官名塞进 chunk 文本让 embedding 学，是**用概率去近似一个已知的确定性约束**——纯损失。
4. **sparse + HNSW 的一个陷阱**：若你的 sparse 路也走 Qdrant，注意官方说明 "sparse vector search does not use the HNSW index"，因此为 sparse 字段建 `enable_hnsw` 是浪费（增加索引延迟与内存）。
5. **小库的正确架构（建议）**：
   ```
   query
    → [归一化层: 缩写/同义词表 → 中英对齐]         ← §3, 确定性
    → [结构化解析: organ / section / polarity]      ← §3.4, 抽得出就 filter
    → [pre-filter 精确筛候选: 通常 500-3745 条]      ← §5, 不漏
    → [dense 精确打分 (brute force, 已无 ANN 必要)]  ← §2
    → [sparse/BM25 精确匹配补充专有名词]            ← §2.7
    → [rerank top-50 → top-5]                      ← bge-m3 卡片推荐
    → [拒答/低置信判定]                             ← 医疗必需
   ```
   **没有 QE**。若要加，只加 selective QE（§4.6），且默认关。

---

## 汇总：证据等级一览

| # | 结论 | 等级 | 来源 |
|---|---|---|---|
| 1 | C-MTEB = 6 任务 / 35 数据集 | **VERIFIED 全文** | arXiv 2309.07597 |
| 2 | 中文 retrieval 恰为 8 个任务，含 2 个医疗 | **VERIFIED 全文+代码** | 论文 Fig.1 + mteb 源码 |
| 3 | 医疗检索是 C-MTEB 最弱项（bge-large-zh: Cmedqa 42.40 vs Avg 71.53） | **VERIFIED 数值** | research/C_MTEB/README.md |
| 4 | 两医疗基准语料均为**问答**，非指南文本 | **VERIFIED** | 官方 description + prompt |
| 5 | bge-m3 单模型三用、8192 tokens、100+ 语言 | **VERIFIED** | 官方 card + 论文 |
| 6 | 权重 `[0.4,0.2,0.4]` 仅为示例默认，非调优推荐 | **VERIFIED（反向确认：无佐证文字）** | 官方 card |
| 7 | bge-m3 的 C-MTEB 分数 | **NOT VERIFIED（官方不报告）** | card/论文 grep 零命中 |
| 8 | MIRACL-zh: Dense 62.7 / Sparse 36.1 / All 64.9 | **VERIFIED 数值** | arXiv 2402.03216 Table 1 |
| 9 | QE 提升全局 MAP 但伤害最差 topics | **VERIFIED 摘要全文** | ECIR 2004, DOI 10.1007/978-3-540-24752-4_10 |
| 10 | Billerbeck & Zobel ADC 2004 存在，结论是**参数不泛化+需 per-query 自适应**，非"QE 有害" | **VERIFIED 摘要** | OpenAlex |
| 11 | 同上**全文** | **NOT VERIFIED**（域名被劫持/Wayback 失败） | — |
| 12 | Carpineto CSUR 2012 存在（44(1):1-50） | **VERIFIED 元数据** | Crossref/OpenAlex |
| 13 | 该综述「关于 QE 何时降效」的具体论断 | **NOT VERIFIED**（摘要未涉及，全文付费墙） | — |
| 14 | Query2doc 在强 dense retriever 上增益衰减；OOD mixed；Scifact −2.9 | **VERIFIED 全文 PDF** | arXiv 2303.07678 Table 2 |
| 15 | QE 在两数量级延迟代价（16ms → >2000ms） | **VERIFIED** | Query2doc Table 6 |
| 16 | PRF 只帮 20.9% query、伤 25.6% | **摘要级 + 预印本，慎用** | arXiv 2607.12098 |
| 17 | 同义词/否定是临床 IR 头号失败源；最好的 bpref 仅 0.5–0.6 | **VERIFIED 全文** | PMC3540501 (AMIA 2012) |
| 18 | QE 在临床检索中引入语义错误 → 假阳性 | **VERIFIED 全文** | PMC3540501 |
| 19 | 缩写歧义是硬问题（950 个歧义缩写） | **摘要级** | arXiv 1906.03360 |
| 20 | 「领域专用 embedding 更好」 | **矛盾，NOT VERIFIED 为普适结论** | arXiv 2409.15163（反例）|
| 21 | post-filter 高度选择性时**可能一个都不剩** | **VERIFIED 官方文档** | weaviate.io filtering |
| 21b | Qdrant 文档**不含** "post-filter" 一词（归属更正） | **VERIFIED（grep 0 命中）** | qdrant.tech 两页 |
| 21c | 单关键词 1% 过滤下 plain graph Recall@10 = **0.1%**；双关键词 4% 下 39.7% 且**无告警** | **VERIFIED 官方 benchmark** | qdrant.tech/articles/filtered-vector-search-acorn/ |
| 21d | 极窄过滤只有「planner 转 payload 索引全扫」能救回（0.012% → 100%） | **VERIFIED 官方 benchmark** | 同上 |
| 22 | 过滤中间地带最难；严格过滤致 HNSW 图断裂 | **VERIFIED 官方文档** | qdrant.tech indexing |
| 23 | Qdrant selectivity 用词与业界相反 | **VERIFIED（术语核对）** | qdrant.tech indexing |
| 24 | 3745 chunks → ANN 不必要，可暴力精确 | **我的算术/推断** | 非文献（依据：15.3MB + full_scan_threshold 机制）|

---

## 引用清单（可直接点开）

1. C-Pack / C-MTEB — https://arxiv.org/abs/2309.07597
2. C-MTEB 权威表格与任务规格 — https://github.com/FlagOpen/FlagEmbedding/blob/master/research/C_MTEB/README.md
3. MTEB 官方 leaderboard — https://huggingface.co/spaces/mteb/leaderboard
4. mteb 源码（中文检索 8 任务） — https://github.com/embeddings-benchmark/mteb/blob/main/mteb/tasks/retrieval/zho/cmteb_retrieval.py
5. bge-m3 model card — https://huggingface.co/BAAI/bge-m3
6. bge-m3 论文 — https://arxiv.org/abs/2402.03216
7. Cronen-Townsend et al., ECIR 2004 — https://link.springer.com/chapter/10.1007/978-3-540-24752-4_10
8. Billerbeck & Zobel, ADC 2004 — (摘要) https://api.openalex.org/works?filter=title.search:Questioning%20Query%20Expansion
9. Billerbeck & Zobel, SIGIR 2003 "When query expansion fails" — https://doi.org/10.1145/860435.860514
10. Carpineto & Romano, CSUR 2012 — https://doi.org/10.1145/2071389.2071390
11. Query2doc — https://arxiv.org/abs/2303.07678
12. MuGI（QE 最佳实践） — https://arxiv.org/abs/2401.06311
13. TREC Medical Records Track 失败分析 — https://pmc.ncbi.nlm.nih.gov/articles/PMC3540501/
14. TREC MRT 官方概述（ACM） — https://doi.org/10.1145/2506583.2506624
15. DECBAE（缩写扩展） — https://arxiv.org/abs/1906.03360
16. DR.EHR（EHR dense retrieval） — https://arxiv.org/abs/2507.18583
17. EHR 检索消融（通用 BGE 胜过医疗专用） — https://arxiv.org/abs/2409.15163
18. Qdrant Indexing / Filterable HNSW — https://qdrant.tech/documentation/concepts/indexing/
19. Qdrant Filterable HNSW 原始设计文 — https://qdrant.tech/articles/filterable-hnsw/
19b. Qdrant 过滤召回实测基准（ACORN）— https://qdrant.tech/articles/filtered-vector-search-acorn/
20. Qdrant Filtering — https://qdrant.tech/documentation/concepts/filtering/
21. Weaviate Filtering — https://weaviate.io/developers/weaviate/concepts/filtering
22. 预印本（PRF 危害） — https://arxiv.org/abs/2607.12098 （慎用）
23. 预印本（多改写加剧 drift） — https://arxiv.org/abs/2605.00560 （慎用）
