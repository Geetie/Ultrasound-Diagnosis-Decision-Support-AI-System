# 核查 C · 数据与知识库：代码与数据结构实证

> 核查时间：2026-10-28
> 核查方式：**读代码 + 读数据结构定义 + 复刻纯函数逻辑跑真实 doc_id**。不采信设计文档与台账结论。
> 范围限定：数据与知识库。不涉及前端、评测指标、业务链路。
> 证据纪律：每条断言给 `path:line`。凡"未查库"的推断单独标注。
>
> 基准 commit：`c65da99`（2026-10-28）

---

## 0. 三个结论（先看这里）

| # | 结论 | 严重度 |
|---|---|---|
| 1 | **知识库没有"证据等级"这个字段。** 全仓只有一套靠 `source_doc_id` 前缀推断的 `authority.py`，而**真实语料的 doc_id 一个都不匹配那 6 个前缀**，且 `source_kind` 在建库时被丢弃、`"guidelines"`（复数）与代码认的 `"guideline"`（单数）对不上、默认 `tie_break` 在 RRF 浮点分上几乎永不触发、而且它作用在**已截断到 5 条之后**。五重失效叠加 ⇒ **权威度机制在当前语料上恒等于"全部 unknown、权重全 1.0、顺序不变"**。 | 致命（对答辩） |
| 2 | **切分做不了层级。** chunker 三种策略里只有 `semantic` 认标题，但生产建库从未用过它（默认 `sentence_boundary`）。更根本的一刀在**上游**：`build_pdf_corpus.py` 已经把 Markdown 标题解析出来了，却把标题塞进 `title` 字段、把 `section_path` 固定写成 PDF 文件名（`:116`），而 `builder` 只读 4 个字段、`title` 直接被丢。⇒ 层级信息在入库前就没了，**改 chunker 也救不回来**。 | 致命（对"结构化知识库"表述） |
| 3 | **88.5% 的文档结构上不可能有图边**（936/1057 单块文档），而后来补的 3672 条"跨文档边"是按 `section_path` 前 1 段分桶得到的 —— 对话语料的前 1 段是 `医疗对话`，等于**把同一器官下 200~433 个块互为"同主题"**。这不是主题边，是"同器官边"。 | 严重 |

---

## 1. 知识库结构（`KnowledgeChunk` 完整字段）

### 1.1 字段定义

`app/knowledge/protocols.py:75-103`，`KnowledgeChunk(BaseModel)` 共 **9 个字段**：

| 字段 | 类型 | 默认 | 说明（docstring 原文） |
|---|---|---|---|
| `chunk_id` | `str` | 必填 | `"{source_doc_id}#{chunk_index}"`，实际带 kb 版本前缀（见 §1.3） |
| `kb_version` | `str` | 必填 | `kb_v{major}.{minor}.{patch}` |
| `organ` | `str` | 必填 | 目标器官 |
| `content` | `str` | 必填 | 原始 chunk 文本 |
| `metadata` | `dict[str, Any]` | `{}` | FeatureExtractor 抽的结构化元数据 |
| `embedding` | `list[float] \| None` | `None` | bge-m3-medical 768d（DDL 是 1024，见 §1.4） |
| `source_doc_id` | `str` | 必填 | 源文档标识 |
| `section_path` | `str` | `""` | 层级路径，例 `"TI-RADS/Category 4/Subcategory 4A"` |
| `token_count` | `int` | `0` | 启发式 `len(text)//2` |

校验器只 4 条（`protocols.py:105-131`）：chunk_id 非空、content 非空、organ 非空、token_count ≥0。**没有任何字段级结构约束**（无 enum、无外键、无层级深度约束）。

### 1.2 有没有 `evidence_level` / 证据等级 / 权威度字段？

**没有。** 全仓 `grep -rn "evidence_level\|evidence_grade\|证据等级" app/` 只命中两处，都不是知识库字段：

- `app/knowledge/authority.py:19` —— 一句注释里的"证据等级分化大"（描述 literature 的来源特性）
- `tests/unit/quality/test_a91_no_fake_evidence.py:205` —— **测试源码的自我声明等级**（E1/E2/E3），与知识库无关

数据库侧同样没有（`app/models/database.py:389-419`，`knowledge_chunks` 全部列）：
`id / chunk_id / kb_version / organ / content / metadata / embedding / source_doc_id / section_path / token_count / created_at / updated_at / last_referenced_at / reference_count / dispute_count / confidence_score`。

**注意 `confidence_score` 不是证据等级**，它是 §3.3 那条衰减算出来的维护性分数（入库默认 `1.0`，`database.py:411`）。

> ⚠️ 这条直接封死一句答辩表述：**不能说"知识库按证据等级组织/分档"**。没有这个字段，也没有这个能力。

### 1.3 `chunk_id` 的真实形态（比 docstring 多一层）

docstring 写 `"{source_doc_id}#{chunk_index}"`，实际实现多了 kb 版本前缀（`app/knowledge/chunker.py:43-50`）：

```python
def _build_chunk_id(kb_version, source_doc_id, chunk_index):
    return f"{kb_version}:{source_doc_id}#{chunk_index:04d}"
```

真实样例（从 `tests/reports/hard_query_set_guideline.json` 抽出）：`kb_v1.3.0:pdf_cardiac_cardiac_echo_measurement_2016_0001#0000`

版本前缀是**必需**的，不是洁癖：`chunker.py:47-52` 的注释记了真机事故 —— 不带版本时 `chunk_id` 在版本间撞车，`builder` 的 resume 逻辑会误判"已存在"而整库跳过。

### 1.4 `section_path` 怎么填充、有没有用起来

#### 填充链路（三个脚本各写一套，互不统一）

| 来源 | `section_path` 取值 | 证据 |
|---|---|---|
| PDF 语料 | **PDF 文件名（去掉 `.pdf`）** | `scripts/build_pdf_corpus.py:113-117`：`"section_path": f"{stem}"` |
| 对话语料 | `医疗对话/{科室 label}` | `scripts/t1b_build_dialogue_docs.py:81` |
| v1.0 早期甲状腺规范 | **章节标题**（唯一一个真层级） | `scripts/server_assemble_kb_v1.sh:38`：`"section_path": title` |

三种形态混在同一个列里。真机实测印证（16 号 `:6334`）：`pdf_*` 401 块的 `section_path` **只有 8 个取值，全部是指南名**，`(organ, section_path)` 只有 8 个组、每组 11~55 篇。

#### 关键：标题本来能拿到，是在 `build_pdf_corpus.py` 里被主动丢掉的

`build_pdf_corpus.py:56-77` 的 `pdf_to_sections()` 从 pymupdf4llm 的 Markdown 里正则抓 `#{1,4}` 标题，返回 `[(section_title, body)]`。到 `:107-118` 组装文档时：

```python
for title, body in sections:                       # ← title 是真标题
    for sub_title, sub_body in split_long_section(title, body, seq):
        docs.append({
            "source_doc_id": f"pdf_{organ}_{stem}_{seq:04d}",
            "title": sub_title[:80],                # ← 写进 title
            "source_kind": args.source_kind,
            "section_path": f"{stem}",              # ← 但 section_path 只给文件名
            "content": sub_body,
        })
```

然后 `builder._discover_source_docs()`（`app/knowledge/builder.py:100-160`）只取 4 个字段：

```python
content = data.get("content", "")                   # :139
source_doc_id = data.get("source_doc_id") or ...    # :145
section_path = data.get("section_path", "")         # :146
...
docs.append(_SourceDocFile(source_doc_id=..., organ=..., content=..., section_path=...))  # :153-158
```

`_SourceDocFile` 的字段定义在 `builder.py:63-69`，**只有 4 个字段，没有 `title`**。

**⇒ 结论：标题在 `build_pdf_corpus.py:116` 写错列，在 `builder.py:152` 被丢弃。`knowledge_chunks` 里从头到尾没有章节标题。** 这是数据链断裂，不是 chunker 能力问题。

#### 有没有用起来？

检索路径上**只透传、不参与任何排序或过滤**：

- `app/rag/retriever.py:918`、`:1028`、`:1108` —— 只在构造 `KnowledgeChunk` 时填值
- `app/rag/bm25_fallback.py:142`、`:188` —— 同上（`bm25_fallback.py:144` 的 `order_by` 是 `chunk_id`，不是 `section_path`）
- `app/knowledge/graph_query.py:135`、`:371` —— SELECT 出来、填回对象

没有任何一处把它当作**过滤条件、排序键、分组键或 prompt 内容**。

> 唯一"用"它的是 `p70_cross_doc_edges.py:117-127` 的分桶（见 §4.2），而那个用法正是问题所在。

### 1.5 `metadata` dict 实际会写进哪些 key

三个写入点，合并后**最多 8 个 key**：

| 写入点 | key | 证据 |
|---|---|---|
| `chunker._to_chunk` | `strategy`（恒 `"chunker"`）、`chunk_index` | `app/knowledge/chunker.py:130` |
| `chunker.SemanticChunker` | `semantic_title`（恒 `True`） | `chunker.py:376`（**只有 semantic 策略才写，生产从未用**） |
| `feature_extractor.features_to_metadata` | `organ`、`classifications`、`measurements`、`anatomical_locations`、`udt_coverage_rate` | `app/knowledge/feature_extractor.py:118-130` |
| `builder._attach_relations` | `relations`（`[{target, relation}]`） | `app/knowledge/builder.py:286-316`（仅多块文档） |

合并逻辑在 `builder.py:419-420`：`merged_meta = {**c.metadata, **meta}`。

**没有一个 key 是 `source_kind`、`doc_type`、`evidence_level`、`authority`、`published_at` 或 `title`。**

这一条是 §3 的引子：`authority.source_kind_of()` 的第一优先级读 `metadata["source_kind"]`，而**建库流程根本不写这个键**。

---

## 2. 切分逻辑

### 2.1 三种策略与参数

`app/knowledge/chunker.py`，统一参数（`:31-33`）：

| 参数 | 值 | 含义 |
|---|---|---|
| `DEFAULT_MIN_TOKENS` | **256** | 下界，防过碎 |
| `DEFAULT_MAX_TOKENS` | **512** | 上界，卡 bge-m3 的 512 token 上下文 |
| `DEFAULT_WINDOW_CHARS` | **1000** | fixed_window 的字符窗口（≈500 token） |

token 计数是 `len(text)//2`（`:38-42`），中英混排启发式，**不是真 tokenizer**。

| 策略 | 切分规则 | 证据 |
|---|---|---|
| `fixed_window` | 纯字符窗口 1000，无语义、无边界 | `chunker.py:152-186` |
| `sentence_boundary`（**默认**） | 句子边界正则 → 累积到 512×2=1024 字符 | `chunker.py:189-303`；`create_chunker` 默认 `:337-345` |
| `semantic` | 先按 TI-RADS/LI-RADS/Category/N类 标题切节，每节再走 sentence_boundary | `chunker.py:306-381` |

### 2.2 句子边界正则的真实形态（比"句子边界"粗）

`chunker.py:82-86`：

```python
_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[.!?])\s+|(?<=[,;])\s+|(?<=[。！？])|(?<=[，；])|\n+",
)
```

**逗号、分号也是切分点**（`[，；]` / `[,;]`）。所以"切在句子边界"实际是"切在任意标点"。这对中文长句影响很大：一个 800 字的临床段落会在第一个逗号处断开累积计数。

### 2.3 有没有 overlap？

**没有。零 overlap。**

`create_chunker` 的 docstring 自己承认（`chunker.py:389-395`）：「chunker 当前无 overlap，对话语料天然短小，见 16 号 A.6」。

这也是为什么 A.64 判 `MAJORITY_SINGLE_CHUNK_DOCS` 后直接放弃切分改造 —— overlap 只对多块文档生效，对 936 个单块文档零影响（16 号 `:6009`）。

### 2.4 有没有"层级感知"能力？

**有，但装在一个从未被使用的策略里，而且就算用了也救不回来。**

1. `SemanticChunker`（`chunker.py:306-381`）确实认标题。但它的标题识别是**正则名单**（`_SEMANTIC_TITLE_RE`，`:89-99`）：`TI-RADS-\d[ABC]?` / `LI-RADS-\d[MTR]?` / `LR-\d[MTR]?` / `Category \d[ABC]?` / `Subcategory \d[ABC]?` / `\d+类`。**中文指南的一级标题「一、」「二、」不认，`##` Markdown 层级完全不认** —— 它不看 Markdown 标题语法，只看这 6 个临床术语名。

2. 生产建库从未使用它。`scripts/_kb13_build.sh:44-46` 调 `init_knowledge_base.py` 时**没有 `--strategy` 参数**，落到 `scripts/init_knowledge_base.py:65-70` 的默认值 `sentence_boundary`。全仓搜 `--strategy semantic` / `strategy="semantic"` **零命中**。

3. 就算切了，`section_path` 也是错的 —— `SemanticChunker` 写的是 `section_path = f"{base_section}/{title}"`（`chunker.py:347`），而 `base_section` 来自 `doc.section_path`，也就是 `build_pdf_corpus.py:116` 给的**PDF 文件名**。结果会是 `cardiac_echo_measurement_2016/TI-RADS 4A` 这种混搭。**而 `build_pdf_corpus.py` 已经在文档层面把内容按真标题切开了**（`pdf_to_sections`），再让 chunker 从正文里挖标题，等于在有标题的时候把标题扔了、再从正文里猜。

### 2.5 单块文档是怎么产生的

**两个成因，主因是"源文档本来就短"，不是切分策略。**

`scripts/build_pdf_corpus.py:24-51`：

- `MIN_DOC_CHARS = 120` —— 正文 <120 字符的节直接丢弃
- `MAX_DOC_CHARS = 2500` —— 超过就按段落二次聚合成多个文档（`split_long_section`）

也就是说：**PDF 语料在进入 chunker 之前，已经被"节"切成 120~2500 字符的独立文档了**。每篇文档再进 `SentenceBoundaryChunker`，256 token = 512 字符的阈值通常都碰不到，因此**一篇文档 = 一块**。

对话语料更直接：`t1b_build_dialogue_docs.py:82` 一条问答 = 一个文档，`content = f"患者提问：{title}。{ask}\n医生回答：{answer}"`，平均 131~173 token（16 号 `:6864`），**天然一篇一块**。

真机实测印证（16 号 `:5997-6003`）：

| 指标 | 值 |
|---|---|
| 块 / 文档 | **1257 / 1057** |
| 单块文档 | **936（88.5%）** |
| 2–3 块 | 116 |
| 4+ 块 | 5（最多 24 块） |
| token 中位 | **146** |
| token < 256 占比 | **68.8%** |

**⇒ 结论：88.5% 的单块文档不是 chunker 切坏的，是"文档在入库前就已经碎了"。根因在 `build_pdf_corpus.py` 的 `MAX_DOC_CHARS=2500` + 按 `#` 标题切节。改 chunker 参数（min/max/overlap）对 936 篇零影响 —— 这与 A.64 的 `MAJORITY_SINGLE_CHUNK_DOCS` 判定一致，但那条只说了"切分改造无效"，没说清**真正该改的是上游的文档切分，而不是 chunker**。**

### 2.6 一个会误导人的细节

`SentenceBoundaryChunker.chunk()` 末尾（`chunker.py:280-293`）有"把尾块合并进前一块"的逻辑：

```python
if len(chunks) >= 2 and chunks[-1].token_count < self.min_tokens:
```

**只对 `len(chunks) >= 2` 生效** —— 单块文档永远不参与合并。所以 `MIN_TOKENS=256` 这个参数对 88.5% 的文档是死参数。这条也解释了为什么"提高 min_tokens 让块变完整"这个直觉方案没用。

---

## 3. 权威度机制：为什么"零效果"（五重失效，逐行）

### 3.1 权重表（这部分本身是对的）

`app/knowledge/authority.py:74-81`：

| 来源类型 | 权重 | 设计理由（`authority.py:12-19` 原表） |
|---|---|---|
| `guideline` 指南/共识 | **1.00** | 规范化诊疗的直接依据 |
| `textbook` 教科书 | **0.95** | 权威但更新慢 |
| `literature` 期刊文献 | **0.90** | 证据等级分化大，取中性偏高 |
| `protocol` 院内规程 | **0.85** | 本院可执行，适用范围窄 |
| `case_reports` 病例报告 | **0.70** | 孤例，不能当通则 |
| `unknown` 未知 | **1.00** | **不猜**：未知来源不做惩罚 |

`unknown` 给 1.0 是刻意的（`authority.py:21-25`）：来源元数据缺失是数据问题，不是"这条知识不可信"，给低分等于用缺失数据当证据压权重。

### 3.2 第一重失效：建库不写 `source_kind`，从第一优先级就断了

`source_kind_of()` 的判定顺序（`authority.py:101-129`）：

```
metadata["source_kind"]  →  source_doc_id 前缀  →  metadata["doc_type"]  →  unknown
```

第一优先级 `metadata["source_kind"]`**永远读不到**，因为 §1.5 已证明：`builder` 合并 metadata 时不含这个键。`source_kind` 在 `builder.py:148` 只被用来**决定要不要脱敏**，用完就丢，没有进 chunk。

### 3.3 第二重失效：真实 doc_id 一个都不匹配那 6 个前缀

`_SOURCE_PREFIXES`（`authority.py:90-99`）只认：`guideline` / `textbook` / `literature` / `protocol` / `case_reports` / `case_report` / `cases`。

真实 doc_id 的 4 种形态（`build_pdf_corpus.py:113`、`t1b_build_dialogue_docs.py:78`、`server_assemble_kb_v1.sh:36`、报告里的真实 chunk_id）：

| 真实 doc_id 样例 | 是否匹配前缀 |
|---|---|
| `pdf_cardiac_cardiac_echo_measurement_2016_0001` | ❌ |
| `pdf_thyroid_thyroid_cancer_guideline_2022_0008` | ❌（**名字里有 guideline，但不在开头**） |
| `pdf_thyroid_ctirads_2021_0025` | ❌ |
| `dialogue_内科_2553` | ❌ |
| `norm2018_s01` | ❌ |

**我把 `source_kind_of()` 的纯逻辑复刻出来，对上述 5 个真实 doc_id 逐个跑过，5/5 全部返回 `unknown`、权重全部 1.0。**

（复刻方法：手工逐行照抄 `authority.py:101-129` 的判定分支，用 `metadata={"strategy":"chunker","chunk_index":0}` 构造 —— 与生产入库后的 metadata 形状一致。本机无 `structlog` 无法直接 import，故复刻逻辑而非调用原函数；逻辑与源码逐行一致。）

**⇒ 全库 1257 块，权重全部 1.0。指南与病例报告完全同权。**

### 3.4 第三重失效：`"guidelines"` vs `"guideline"` 单复数不匹配

即使侥幸写进了 metadata，也对不上：

- `scripts/build_pdf_corpus.py:84`：`--source-kind` 默认值 **`"guidelines"`**（复数）
- `scripts/server_assemble_kb_v1.sh:37`、`t1b_build_dialogue_docs.py:80`：同样写 **`"guidelines"`**（复数）
- `authority.py:65`：`SourceKind.GUIDELINE = "guideline"`（**单数**）

`SOURCE_AUTHORITY` 的键是单数，所以 `"guidelines"` 不在字典里 → `if explicit in SOURCE_AUTHORITY` 为假 → 继续往下走 → 最后 `unknown`。

（`desensitization.py:165` 判断的是 `if source_kind == "guidelines"` —— 复数。所以**下游脱敏用的是复数、上游权威度认的是单数**。`authority.py:62` 的 class docstring 还写着"与 `desensitization.py` 的 `source_kind` 对齐"—— **这句话本身就是错的，两处根本没有对齐**。）

### 3.5 第四重失效：`tie_break` 在 RRF 浮点分上几乎永不触发

`rag/service.py:432-433`：

```python
authority_mode = str((request.extra or {}).get("authority_ranking", "tie_break")).strip().lower()
```

默认 `tie_break`。`rank_indices()` 的 tie_break 分支（`authority.py:195-209`）：

```python
while end < len(reordered) and scores[reordered[end]] == scores[reordered[start]]:
```

**严格浮点相等**才重排。而分数是 RRF（`retriever.py:933-971`）：`fused(d) = sum(1.0 / (k + rank))`，`k=60`，量级 0.016~0.033，且每条命中至少来自一个腿、rank 各不相同 → **两条 chunk 的 RRF 分几乎不可能严格相等**。

`authority.py:36-38` 的注释自己也承认：「等权 RRF 分几乎不会撞（浮点），所以默认档对现有输出近乎无影响 —— 这是刻意选的」。

**⇒ 默认档的设计目标是"证明接线了，但不改输出"。所以它确实"生效了"（日志有 `authority_ranking_applied`），但临床输出零变化。**

### 3.6 第五重失效（补刀）：它作用在**已经截断到 5 条之后**

`app/rag/service.py` 的执行顺序：

```
Stage 3: rerank  →  :288  result.chunks = result.chunks[: self._rerank_output_top_k]   ← 先截断到 5
Stage 4: BM25 fallback
阈值过滤           →  :367
authority 排序     →  :435  authority_rank_indices(...)                                 ← 才重排这 5 条
grading floor      →  :487
```

`rag_rerank_output_top_k` 默认 **5**（`app/config/settings.py:440`）。

**⇒ 权威度只能在这 5 条内部换位置，不能让第 6 条（一份指南）顶掉第 1 条（一条病对话）。** 这是"权威度机制"最本质的失败：**它的作用域不包含"谁进得了 top-5"这个真正决定医生看到什么的问题。**

### 3.7 `weighted` 档有没有人用？

**没有。**

- `weighted` 需要调用方显式传 `request.extra["authority_ranking"] = "weighted"`（`service.py:433`）
- 全仓搜 `extra=` 的构造点：`app/agents/specialist/base.py:699` 一处，传的是 `structured_output_extra()`，与权威度无关
- `knowledge.py:557-563` 构造 `RetrievalRequest` 时**根本没传 `extra`**（用默认空 dict）
- `fusion/consistency.py`、`fusion/crag.py` 同样没传

**⇒ `weighted` 档在生产上是不可达的死档。全仓唯一"用"它的是单测。**

### 3.8 汇总一句话

> **权威度机制在代码里是真的（有权重表、有纯函数、有留痕、有 15 条单测），但在当前语料与当前调用方式下，它的净效果是：1257 块全部权重 1.0，top-5 内部顺序不变。**
>
> 答辩若说"知识按权威度加权"，评委会问"那病例报告和指南谁排前面"，答不上来就是当场翻车。

### 3.9 单测为什么没抓到（这条要写进答辩的"自查"里）

`tests/unit/rag/test_a88_authority_ranking.py` 共 15 个 `test_`，全部**手工注入 `metadata={"source_kind": "guideline"}`**（`:100`、`:121`、`:135`、`:145`、`:158`、`:168`）。**没有一条用真实 doc_id 形态做反证**（`grep "pdf_\|dialogue_" tests/unit/rag/test_a88_authority_ranking.py` = 0 命中）。

所以测试全绿 ≠ 生产生效。这是"测试断言当实测结果"的典型样本（AGENTS.md 硬规则 #12）。

---

## 4. 图谱

### 4.1 同文档边怎么建的

`app/knowledge/builder.py:286-316`，`_attach_relations(raw_chunks, enriched)`：

- 前提：`if len(raw_chunks) < 2: return`（`:299-300`）—— **单块文档直接跳过**
- 按 `chunk_index` 排序，为每块写 `next_section` / `prev_section`
- 用 `setdefault` 不覆盖上游已有 `relations`
- 只写 `enriched`（新建块），resume 跳过的块不动

存量库用 `scripts/kb_backfill_relations.py` 补（同规则、幂等：`metadata` 已有 `relations` 就 skip，`:65-67`）。

真机结果（16 号 `:5818`）：**321/1257 块带边、共 400 条边**；`to_update=0`（规则下已完整）。

### 4.2 什么样的文档才有边

**只有多块文档。** 936 个单块文档（88.5%）**结构上不可能有边** —— 这与 4.1 的 `len(raw_chunks) < 2` 是同一件事，16 号 `:6001` 的注释也这么写。

### 4.3 跨文档边是什么时候加的、按什么规则

`scripts/audit/p70_cross_doc_edges.py`，A.95（2026-10-26）补。规则（`:130-178`）：

```
bucket 键 = (organ, section_path 前 depth 段)，depth 默认 1        :117-127
同 bucket 内、不同 source_doc_id → 互为邻居                       :160-174
每块最多取 max_per_chunk=3 个（按 chunk_id 排序）                  :81, :173
```

真机结果（16 号 `:8194`）：`kb_v1.3.0 → 3672 条边 / 1224 块`；`kb_v1.0.0 → 1200 条边 / 400 块`。

### 4.4 这条规则的语义问题（**这是本节最该被追问的一处**）

`p70_cross_doc_edges.py:117-127` 的 `_section_prefix(section_path, depth=1)` 取前 1 段。而 §1.4 已证明 `section_path` 只有三种形态：

| 语料 | `section_path` | 取前 1 段 = | 桶内规模 |
|---|---|---|---|
| PDF | `cardiac_echo_measurement_2016` | 同名 | 11~55 篇（8 个桶） |
| 对话 | `医疗对话/肿瘤科` | **`医疗对话`** | **thyroid 433 / cardiac 223 / liver 200 块** |

**⇒ 对话语料的跨文档边 = "同一器官下的任意两个对话块互为同主题邻居"**，与内容主题完全无关。`肿瘤科` 的问答和 `外科` 的问答会被连起来。

数量对得上：`1224 块 × 3 = 3672`，**恰好是 `max_per_chunk` 的上限**，说明桶内候选远超 3、边数是"任取 3 条"截出来的，不是"确实只有 3 个同主题邻居"。

`p38 --coverage` 实测（16 号 `:6021-6027`）：候选池 **51262 条 → 每种子 125.6 个候选**，判定 `SUPERNODE_RISK`。**这就是"桶太宽"的量化证据。**

### 4.5 `--verify` 验的是什么（不是"真跨文档"）

`p70_cross_doc_edges.py:227-260` 的 `verify()` 只查两件事：

1. `count(*)` where `jsonb_typeof(metadata->'relations') = 'array'`
2. `count(*)` where `rel->>'relation' = 'cross_doc_section'`

**它不校验"这条边的另一端是不是真的不同 `source_doc_id`"**，也不校验"两端内容主题是否相关"。

16 号 `:8195` 写「验证 3672/3672 全部真跨文档」、`progress.md:36` 写「全部真跨文档」、`p71:6` 写「全部真跨文档」——**这句话的证据在 `verify()` 里找不到**。`plan_edges()` 的构造逻辑（`:169` 的 `x["source_doc_id"] != m["source_doc_id"]`）确实保证了写入时跨文档，所以结论**大概率对**，但那不是 `--verify` 验出来的，是"由生成逻辑推出来的"。**这是一条"把逻辑保证写成实测验证"的表述**，答辩时若被抓就要现场解释，很难看。

### 4.6 图查询开关与前提

- `settings.graph_query_enabled = False`（`app/config/settings.py:460`），默认关闭
- `PgVectorGraphAdapter._enabled()` 读这个开关（`graph_query.py:141-144`），关闭时返回空表
- 关闭是**预注册判据 NO_GO 的结果**，不是待办：A.65/A.66 两轮判 `NO_GO_NO_RECALL_GAIN`（`retriever.py:430-450` 注释里有完整理由）
- A.95 三臂复测仍是 `NO_GO_NO_RECALL_GAIN`：纯邻居新增 **0/136**（16 号 `:8197`），原因是 golden 的 gold 就在源块自己的文档里，任何扩边都不带来新增召回

**⇒ 图谱能力的诚实表述是：「边建好了（3672 条），图查询能展开，但**在现有评测集上测不出增益**，因为评测集本身饱和（hit@5 天花板只剩 1.47pp）且 gold 不唯一。」不能说"图谱提升了检索"。**

### 4.7 一处台账自相矛盾（会被评委抓到）

同一份 16 号里，分母写了两个数：

- `:5818`：「**321/1257** 块带边」
- `:8188`：「**4526** 块里只有 321 块有 relations」

`1257` 是 `kb_v1.3.0` 的块数（`:6003`）；`4526` 是 `vector_index_knowledge_chunks` 的行数（`:3428`，且那一行说的是 **pgvector 降级的旧表**，不是 `knowledge_chunks`）。

**两个分母不能混用。** 答辩若引用"321/4526 = 7.1%"这个比例，会把 `knowledge_chunks` 的覆盖率和 `vector_index` 的行数搅在一起 —— 而正确的比例是 `321/1257 = 25.5%`。

---

## 5. 数据源与许可

### 5.1 实际进库的是什么

`knowledge_chunks` 1257 块 / 1057 篇（16 号 `:6003`），构成（16 号 `:6857-6862`）：

| 器官 | 指南/规范 块/篇/均 tok | 对话问答 块/篇/均 tok |
|---|---|---|
| thyroid | 168 / 110 / 363 | 433 / 409 / 166 |
| cardiac | 120 / 71 / 460 | 223 / 200 / 173 |
| liver | 113 / 67 / 500 | 200 / 200 / 131 |
| **合计** | **401 / 248** | **856 / 809** |

**68% 的块（856/1257）是对话问答，不是指南。**

### 5.2 8 份 PDF 的许可（`14-外部数据源与许可合规清单.md:28-40`）

| # | 文件 | 许可状态 |
|---|---|---|
| 1 | `thyroid_cancer_norm_2018.pdf` | ⚠️ 政府规范性文件（无明确再分发许可） |
| 2 | `thyroid_cancer_guideline_2022.pdf` | ⚠️ 同上 |
| 3 | `ctirads_2021.pdf` | 🔴 期刊版权（中华医学会系列） |
| 4 | `liver_cancer_guideline_2024.pdf` | ⚠️ 政府文件 + 期刊刊载 |
| 5 | `liver_us_diagnosis_guideline_2021.pdf` | 🔴 **CNKI 下载版** |
| 6 | `cardiac_echo_measurement_2016.pdf` | 🔴 期刊/学会版权 |
| 7 | `cardiac_tte_standardization_2024.pdf` | 🔴 期刊版权 |
| 8 | `cardiac_valve_consensus_2021.pdf` | 🔴 期刊版权 |

**8 份中 0 份有明确开放再分发许可**（`14 号:42` 小结原文）。

### 5.3 对话语料（这才是最大的雷）

`14 号:44-56`：

| 项 | 值 |
|---|---|
| 条数 | 400 |
| 科室 | 肿瘤科 134 / 外科 133 / 内科 133 |
| **来源** | 🔴 **未登记 / 未核实**（无语料来源说明文件、无原始 URL、无抓取记录） |
| **许可** | 🔴 **无许可证据** |
| 处置 | 仅内部研发参考，**不得进注册路径** |

`14 号:58-60` 的结论原文：「占知识库相当比例的对话语料**来源不可追溯**。这直接回答了 Q7 的原始问题 —— "哪些能进注册路径"的答案是：**目前一份都不能**。」

### 5.4 哪些数据"进库"了、哪些只是"登记"

| 类别 | 进库 | 证据 |
|---|---|---|
| 8 份 PDF | ✅ 401 块 | 16 号 `:6857-6862` |
| 400 条对话（三器官 809 篇） | ✅ 856 块 | 同上 |
| AUL / TN3K / CACTUS / CAMUS / DDTI 等公开集 | ❌ **只在 `14 号` §2 登记，未进知识库** | 它们是**分类/分割评测集**，与 RAG 知识库是两条线 |
| US-365K | ❌ 未进知识库 | 用于 I2T 评测 |

> 这条要分开讲清楚：**"知识库"和"评测数据集"是两套东西。** 答辩若把"AUL 735 张 CC BY 4.0"当成"知识库有可商用数据"的证据，就是混淆了两条线 —— AUL 是分类模型的评测集，一张图都没进 RAG 知识库。

### 5.5 一个数量对不上的地方（待查，不要引用）

| 出处 | 说法 |
|---|---|
| `14 号:46` | 对话语料 **400 条** |
| `t1b_build_dialogue_docs.py:39` | `--max-docs` 默认 **200**（每器官） |
| 16 号 `:609-610` | liver 200 + cardiac 200 = 400，**合计 809 文档** |
| 16 号 `:6862` | 对话问答合计 **809 篇** |

`400 条`（原始语料）→ `809 篇`（实际入库文档）之间差 2 倍。可能的解释是 `t1b` 按器官各跑一次、每次 200 上限、同一批语料按关键词重复切分。**但仓库里没有生成 `dialogue_docs.jsonl` 的完整脚本，也没有该文件（`data/kb_sources/` 不存在）。所以这条链在仓库侧断了，无法核实。**

**⇒ 答辩不要引用"400 条"或"809 篇"中的任何一个作为"语料规模"的论据，除非能说清 400→809 的转换关系。**

---

## 6. 数据质量事故（608/620）的代码痕迹

### 6.1 三套数字、三个分母，都能算对，但**分母没写清**

| 数字 | 实际算式 | 分母是什么 | 出处 |
|---|---|---|---|
| **91.7%**（未见于台账） | `155/169` | 评测集全量 169 条 | 16 号 `:6487` 表格可推 |
| **98.7%（在代码/台账里）** | **`155/157`** | 169 减去 12 条降级 = **157 条非降级** | 16 号 `:6487`；`output_guard.py:3`；`base.py:424`；AGENTS.md #14 |
| **98.06%（在台账里）** | `608/620` | 全库 1071 条减去 451 条降级 = **620 条真结论** | 16 号 `:6641` |
| 97.1% | `34/35` | 40 条切片减 5 条降级 | 16 号 `:6486` |

**四个数都是对的，错的是"分母去哪了"。**

`155/157 = 98.73% ≈ 98.7%`（我复算过）。所以 98.7% 不是假数 —— 它是**"非降级样本里的回显率"**。

问题在于：`app/inference/output_guard.py:3` 和 `app/agents/specialist/base.py:424` 的注释原话是「**评测集** 98.7%（155/169）的"结论"其实是提示词示例的照抄」——

```
评测集 98.7%（155/169）   ← 注释原文
```

**`155/169` 写在那里，但它算出来是 91.7%，不是 98.7%。** 真实的分母是 157（剔除降级）。**注释把一个 91.7% 的算式，配上了 98.7% 的结论。** 谁按注释里的算式复算，都会算出 91.7%，然后怀疑这个数造假。

`progress.md:142` 也一样：写「40 条切片回显 34（**97.1%**）、全评测集 169 条回显 155（**98.7%**）」—— `34/40 = 85%`，`155/169 = 91.7%`，两个都算不出它给的比例。**它给的是"剔除降级后"的比例，但句子主语写的是"全评测集 169 条"。**

**⇒ 这是典型的「口径在分子分母之外偷偷换了一次」**，正是 AGENTS.md 数据质量事故 §坑 1「指标先看原文再信数字」的同类。答辩若引用 98.7%，评委按字面复算会得到 91.7%，你没法解释（除非你当场说清"分母是 157 不是 169"，那又需要 30 秒）。

**建议做法：答辩只用 `608/620`，并且把分母说出来** —— 「全库 1071 条判读，451 条降级，剩下 620 条真结论里 608 条在照抄提示词示例」。这句话三个数都有出处、都能当场复算。

### 6.2 生产侧防回显机制（三道，都在）

#### 第一道：生成侧拒收（真的在生产路径上）

`app/agents/specialist/base.py:415-451`，第 4b 步：

```python
echo_reason = detect_prompt_echo(llm_text)          # :427
if echo_reason:
    logger.warning("specialist_output_prompt_echo_rejected", ...)
    response = AgentResponse(
        content=prompt_echo_text(organ), confidence=0.0,
        evidence_chunk_ids=[c.chunk_id for c in chunks],
        needs_review=True, ...
    )
    return response
```

判定函数 `app/inference/output_guard.py:34-61`，三个原因码：

| 原因码 | 触发条件 | 正则 |
|---|---|---|
| `placeholder_chunk_id` | 出现 `chunk_xxxx` | `chunk_[a-z0-9]{4}\b`（`:25`） |
| `placeholder_patient_id` | `PATIENT_\d+` **且** 同时有 `# 输出` 或 `chunk_` | `:31`、`:56-58` |
| `repeated_output_header` | `# 输出` ≥2 次 | `:29`、`:59-60` |

**A.77 的更正在代码里（`output_guard.py:48-55`）**：`PATIENT_001` 单独出现**不算**模板回显（因为库里有 22 个知识块本身就含该标记，是脱敏占位符），只有与确凿特征同现才算。首版规则曾因此误判 415 条真结论。

#### 第二道：语料标记清洗（不是拒收）

`detect_patient_token_leak()`（`output_guard.py:64-83`）+ `clean_patient_token_leak()`，接在 `base.py:450-485` 的第 4c 步。

这条的处置是**清洗**（替换成「患者」），不是整条拒收 —— 因为那类结论有分级、有鉴别诊断，内容可用。

#### 第三道：提示词示例脱敏（真的改了）

`app/agents/prompts/{thyroid,cardiac,liver}.md` 三份，现在的示例格式是：

```
证据: [1], [2]（写编号，不要写具体 ID）
```

证据：`thyroid.md:92`、`liver.md:122`、`cardiac.md:94`。

`grep -rn "PATIENT_" app/agents/prompts/` = **0 命中**。原来的 `chunk_a1b2` / `患者PATIENT_001` 已全部替换。

#### 第四道（采集端）：`scripts/langfuse/collect_eval_set.py:209-216`

```python
from app.inference.output_guard import detect_prompt_echo
echo_reason = detect_prompt_echo(conclusion)
if echo_reason:
    skipped_prompt_echo += 1
```

统计进 `:264-265`，报告里打出来（`:321-322`）。

### 6.3 这些机制有没有测试

有，但**覆盖的样本形态与现实有差距**：

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| `tests/unit/inference/test_output_guard.py` | 9 个 `test_` | 三个原因码正反例、`PATIENT_` 单独不算、真证据 ID 精确匹配 |
| `tests/unit/agt/test_specialist_prompt_echo.py` | 2 个 `test_` | 回显被拒收 + 干净输出正常解析 |

**但没有任何一条测试覆盖"提示词示例改写后的真实输出形态"** —— 也就是 `证据: [1], [2]` 这种编号引用。而这正是当前 prompt 的形态（`thyroid.md:92`），也是 §6.4 那个问题所在。

### 6.4 一个**还没修完**的连带问题：证据引用在数据侧就不成立

> 本节只写数据/知识库侧的三个事实。「前端能不能渲染」属他线范围，此处不展开。

**事实一：`evidence_chunk_ids` 不是"模型引用了哪几块"，是"检索 top-3 的命中位置"。**

`app/agents/specialist/thyroid.py:227`、`liver.py:200`、`cardiac.py:266`：

```python
evidence_ids = [c.chunk_id for c in chunks[:3]]
```

`chunks` 是检索返回的前 5 条（`DEFAULT_RAG_TOP_K=5`，`specialist/base.py:105`），这里只捡前三块。**与模型实际在正文里引用了哪几块无关** —— 模型输出的是编号 `[1] [2]`（`prompts/thyroid.md:92`），从来没有把 chunk_id 写回正文。

**事实二：没有任何接口能把 chunk_id 换成原文。**

`app/api/` 全域搜 `knowledge_chunks` / `KnowledgeChunk` = **0 命中**（`app/api/v1/` 23 个 router 文件，无一个读知识库表）。

也就是说：即便前端/报告里拿到了一串 `kb_v1.3.0:pdf_..._0003#0000`，**后端没有路由能把它翻译回那段条文**。`knowledge_ids` 落库了（`diagnosis_pipeline.py:1346-1355`），但只在库里、不在任何读取界面上。

**事实三：正文里的引用是编号，不是 ID。**

`prompts/thyroid.md:92` 要求 `证据: [1], [2]（写编号，不要写具体 ID）`。所以正文与 `evidence_chunk_ids` 之间**没有可核对的键** —— 你在正文看到 `[1]`，无法知道它对应 `evidence_chunk_ids` 里的哪一条（顺序可能一致，但没有任何代码保证，也没有任何断言钉住）。

**⇒ 「结论带出处、依据可查原文」这句话在数据侧就断了三层：**
1. 正文只有编号，没有可寻址的 ID
2. 编号 → chunk 的映射，代码不保证、不校验
3. chunk → 原文的接口，不存在

**可以说的话**：「检索命中的知识块 ID 随结论落库（`knowledge_ids` 字段），可在库内回溯到该 chunk 的记录」。**不能说**：「医生能看到这条结论对应的原文条文」。

---

## 7. 对答辩的直接约束（只列数据与知识库这条线）

| # | 不能说的话 | 代码层面的事实 | 可以说的话 |
|---|---|---|---|
| 1 | 「结论带出处、依据可查原文」 | 正文只有编号 `[1][2]`；无 chunk 回表接口；`evidence_chunk_ids` 是检索 top-3 命中位置，不是模型引用 | 「检索命中的知识块 ID 随结论落库，可在库内回溯到该 chunk」 |
| 2 | 「知识库按证据等级/权威度组织」 | 无 `evidence_level` 字段；权威度五重失效，全库权重 1.0 | 「设计了按来源类型加权的机制，权重表在代码里，但当前语料没有来源元数据，默认不生效」 |
| 3 | 「结构化知识库 / 层级切分」 | 标题在 `build_pdf_corpus.py:116` 被丢；`section_path` 只有 8 个取值全是文件名 | 「语料按 PDF 章节切成 248 篇文档，每篇 120~2500 字符」 |
| 4 | 「知识图谱提升了检索」 | 3672 条边建好，但三臂 NO_GO、纯邻居新增 0/136、开关默认关 | 「边建好了、图查询能展开，但评测集饱和，测不出增益」 |
| 5 | 「知识库 1257 块」单独出现 | 856/1257（68%）是**来源不可追溯**的对话语料 | 「1257 块，其中 401 块来自 8 份指南/规范 PDF、856 块是对话问答；对话语料来源未核实，仅内部研发用」 |
| 6 | 「用了 AUL/TN3K 等公开数据集」 | 这些是**评测集**，未进知识库 | 分开讲：「知识库是 8 份指南 + 对话；评测用了 TN3K/AUL 等公开集」 |
| 7 | 「知识库已按权威性排序」 | `weighted` 档全仓零调用方，生产不可达 | 「有 `weighted` 档可显式开启，但会改变临床输出，必须单独过评测，当前未开」 |

---

## 8. 未核实项（不得补白）

| # | 事项 | 为什么没核实 | 要核实需要什么 |
|---|---|---|---|
| U1 | `knowledge_chunks` 里 `metadata` 的真实键分布（是否有历史遗留键） | 本机无 PG 连接，无法 SQL | `SELECT DISTINCT jsonb_object_keys(metadata) FROM knowledge_chunks` |
| U2 | 400 条对话语料 → 809 篇入库文档的转换关系 | `data/kb_sources/` 不在仓库，`dialogue_docs.jsonl` 不在仓库 | 找到生成该 jsonl 的完整脚本或原始 CSV |
| U3 | 全库 1257 块里 `source_kind` 是否有任何一块真的写进了 metadata | 同上，需 SQL | 同 U1 |
| U4 | `weighted` 权重档在灰度环境有没有开过 | 仓库侧零调用方，运行侧无法查 | `grep authority_ranking` 在部署环境的访问日志 |
| U5 | `confidence_score` 在真机库里的实际分布（衰减跑过没有） | 需 SQL | `SELECT min(confidence_score), max(confidence_score), count(*) FROM knowledge_chunks` |
| U6 | `p38` / `p70` 的报告是否入库 | 仓库无 `reports/kb_structure_health.json`、无 `reports/cross_doc_edges.json` | 同 §5 的 `架构真相` §5 情况：脚本在库、报告未入库 |

---

## 9. 一句话交接

**这条线能站住的只有一句：「8 份指南 + 400 条来源未核实的对话，切成 1257 块，检索命中率 hit@5 0.9853」。**
**其余全部要加定语或不说：没有证据等级字段、权威度五重失效等于没生效、标题在入库前被丢、依据全文不可回表、图谱边是"同器官边"且测不出增益。**
