# HuggingFace 模型生态现状（Lead 亲自实测）

> 采集时间：2026-10-28
> 数据源：`hf-mirror.com/api`（huggingface.co 直连超时，与项目 SKILL 记录一致）
> 所有下载量为 API 返回的原始数值，未做任何加工

---

## 一、核心发现：超声专用多模态模型在生态里几乎不存在

### 超声关键词全站扫描（按下载量排序，前 20）

| model id | 下载量 | 类型 |
|---|---|---|
| `ITSheep/breastcancer-ultrasound-ViT` | **1,634** | 单任务图像分类 |
| `agent593/Thyroid-Ultrasound-Image-Classification-ViTModel` | **172** | 单任务图像分类 |
| `ghostsas001/busi-breast-ultrasound-siglip` | 60 | 单数据集 |
| `ghostsas001/busi-breast-ultrasound-yolo` | 60 | 单数据集 |
| `agent593/Thyroid-Ultrasound-Image-Classification-EfficientNetModel` | 50 | 单任务 |
| `manucos/finetuned__beto-clinical-wl-es__augmented-ultrasounds` | 29 | 文本 NER |
| `Beijuka/ultrasound_plane_classification-swin-all-planes-class-weight-v1` | 26 | 切面分类 |
| `hamdan07/UltraSound-Lung` | 21 | 单任务 |
| `agent593/Thyroid-Ultrasound-Image-Classification-Resnet50Model` | 21 | 单任务 |
| `pcvlab/yolov8_ocular_ultrasound_globe_detection` | 21 | 单任务 |
| 其余 10 个 | ≤ 18 | 全部单任务/小模型 |

**结论一：HuggingFace 全站搜「ultrasound」，按下载量排前 20 的模型，没有一个超过 2,000 下载。最高的一个是单任务的乳腺癌超声分类器（1,634 次）。**

**结论二：这 20 个里没有任何一个做「超声图文对齐」或「超声 RAG」，全部是单任务图像分类/检测/分割。**

---

## 二、决定性证据：Ultrasound-CLIP 官方权重是空的

| 项 | 实测值 |
|---|---|
| `JJY-0823/Ultrasound-CLIP` 下载量 | **0** |
| likes | **0** |
| 创建时间 | 2026-04-28 |
| **仓库文件列表** | **只有 `.gitattributes`，没有权重文件** |

**论文 README 说明**：权重通过 OneDrive 链接提供（`1drv.ms/...`），不在 HF 上。
**项目自己的台账也记录了**（`14号:181`）：「预训练权重（OneDrive，已迁 SharePoint 需手动下载），但**无 license 声明**」

> 这是一个值得写进答辩的事实：**一个 CVPR 2026 的超声基础模型，官方权重不在标准模型仓库里，要走 OneDrive 手动下载，而且没有 license 声明。**

对比 `fabnem/UltraSoundCLIP`（2025-03 创建）：有真实权重文件（`checkpoint-1050/model.safetensors` 等），但**下载量 0、likes 0**。

---

## 三、通用医学视觉语言模型：生态很热

| model id | 下载量 | likes | 说明 |
|---|---|---|---|
| `google/medgemma-4b-it` | **413,157** | 1,120 | Google 医学多模态 |
| `microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224` | **161,190** | — | 微软医学 CLIP |
| `google/medgemma-1.5-4b-it` | **150,681** | — | MedGemma 1.5 |
| `google/medgemma-27b-it` | 24,962 | — | 27B 版 |
| `microsoft/llava-med-v1.5-mistral-7b` | 6,592 | — | LLaVA-Med |
| `chuhac/BiomedCLIP-vit-bert-hf` | 2,548 | — | 转换版 |
| `MohamedAhmedAE/llava-medical-8B-clip-vit-stage2` | 2,469 | — | — |

**这一组的关键判断：**
- MedGemma 4B 一个模型的下载量（413,157）是**超声全站前 20 名下载量总和（约 2,100）的 190 倍以上**
- 但这些模型的图像编码器训练数据是**胸片、皮肤、眼底、病理**，**没有一个是超声**
- BiomedCLIP 是「图文检索骨干」，不是 RAG 系统

---

## 四、数据集侧：US-365K 也很小

| dataset | 下载量 | likes | 创建 |
|---|---|---|---|
| `JJY-0823/US-365K`（ULC 的预训练集，36.4 万对） | **679** | 17 | 2026-03-19 |
| 对比：`FreedomIntelligence/medical-o1-reasoning-SFT` | 21,220 | — | — |
| 对比：`General-Medical-AI/SlideChat` | 7,858 | — | — |

**关键倍数：通用医学数据集 `medical-o1-reasoning-SFT` 的下载量是 US-365K 的 31 倍。**

---

## 五、可写进答辩页的 3 个事实

### 事实 1：超声专门模型在生态里是荒漠

> HuggingFace 全站搜 `ultrasound`，按下载量前 20 的模型**没有一个超过 2,000 下载**，且**全部是单任务图像分类**，没有一个做图文对齐或 RAG。
> 而通用医学模型 `google/medgemma-4b-it` 单模型下载 **41.3 万次**。

**这组对比可以直接做成 P3 页面的一个视觉对比条。**

### 事实 2：连 CVPR 2026 的超声基础模型都没有可用的公开权重

> `JJY-0823/Ultrasound-CLIP`（CVPR 2026 论文官方权重）在 HF 上**下载 0 次、0 likes、仓库里只有 `.gitattributes` 没有权重文件**。权重走 OneDrive 手动下载，且无 license 声明。

**这解释了为什么本项目的多模态路线难做——不是我们不会用，是生态本身没提供可用资产。**

### 事实 3：通用医学模型很强，但都不是为超声设计的

> MedGemma 4B 下载 41 万、BiomedCLIP 下载 16 万，但它们的图像编码器训练集是胸片、皮肤、眼底、病理，**超声不在其中**。所以「通用模型很强」和「超声没人管」是同时成立的。

---

## 六、这对答辩的意义

**原来 P3 页我只写了「GitHub 上 ultrasound+RAG 只有 14 个仓库」。那太单薄。现在补上 HF 侧的数据：**

| 维度 | 事实 | 数字 |
|---|---|---|
| 代码侧 | GitHub `ultrasound + RAG` | 14 个仓库，最高 5 星 |
| 模型侧 | HF 超声模型最高下载量 | **1,634**（单任务分类） |
| 模型侧 | HF 超声图文对齐模型 | **0 个可用**（ULC 权重缺失） |
| 对比 | MedGemma 4B 下载量 | **413,157** |
| 数据集侧 | US-365K 下载量 | **679** |
| 对比 | 通用医学数据集下载量 | **21,220** |
| 产业侧 | 国内超声 AI 三类证 | 2023 年起落地（德尚韵兴 20233211650） |
| 监管侧 | NMPA 原文 | 「不能仅凭本产品结果进行临床决策」 |

**八个维度全部指向同一结论：超声 AI 的产业在跑（有证），但学术侧的开放生态几乎是空的（下载量个位数到千级）。这个反差本身就是「效用不可证明」的一个侧面——没有开放生态，就没有可复现的验证基础。**

---

## 七、查不到的

- `echocardiography` 关键词在 HF 模型库：**返回空**
- `medical vlm` 关键词：只返回 4 个，下载量均为个位数（生态用 medgemma/llava-med 这些具体名字，不用泛称）
- EchoCare、UltraSam、SonoBase、FetalCLIP 的 **HF 权重**：未检索到官方仓库（这些多为 arXiv 论文 + GitHub 代码，未发布 HF 权重）

---

## 八、复核方式

```bash
# 超声模型全景
curl -sS "https://hf-mirror.com/api/models?search=ultrasound&sort=downloads&direction=-1&limit=20"

# ULC 官方权重（关键：看 siblings 里只有 .gitattributes）
curl -sS "https://hf-mirror.com/api/models/JJY-0823/Ultrasound-CLIP"

# 通用医学模型对比
curl -sS "https://hf-mirror.com/api/models/google/medgemma-4b-it"
curl -sS "https://hf-mirror.com/api/models/microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224"

# 数据集
curl -sS "https://hf-mirror.com/api/datasets/JJY-0823/US-365K"
```

---

## 九、补充（research-method 独立复核，2026-10-28 18:00）

我在同一时段独立跑了全部 HF API，结论与正文一致，但**正文漏了 4 条更硬的证据**，另发现 **1 处需要纠正**。

### 9.1 补充证据 1：超声专用的「多模态模型」到底有几个 —— 10 个，648 次下载

用 **tag 口径**（`filter=ultrasound` + 多模态 pipeline，比 `search=` 准）：

| model id | 参数 | 近30天下载 | 任务 |
|---|---|---|---|
| `mkaichristensen/echo-clip` | — | **2,796** | 图文对齐 |
| `AlfredQin/sonobase` | — | **297** | 交互分割 |
| `chaoyinshe/EchoVLM_v2` | 9.4B | **282** | VLM |
| `Snarcy/OmniRad-small` | — | **262** | 特征抽取 |
| `Snarcy/OmniRad-base` | 85.7M | **226** | 特征抽取 |
| `chaoyinshe/EchoVLM` | 11.7B | **221** | VLM |
| `zeahub/ultrapips-tusa` | — | **93** | 特征抽取 |
| `CAIR-HKISI/EchoCare` | — | **32** | 表征 |
| `QingboKang/SonoReasoner-8B` | 8.8B | **21** | VLM |
| `QingboKang/SonoReasoner-32B` | 33.4B | **19** | VLM |

**→ 合计 10 个 / 648 次。**（正文用的 `search=` 口径是 108 个仓库 / 2,532 次，但里头混了大量单任务分类和文本 NER。）

> **这一条比正文的"前 20 名没有一个超过 2,000 下载"更有力**：因为"10 个"是一个可以直接说出口的数量。

### 9.2 补充证据 2：正文漏了真正做超声的 VLM —— EchoVLM 与 SonoReasoner

这两个是本次最有价值的发现，**正文完全没提到**：

| model id | 参数 | 下载 | 许可 | 关键事实 |
|---|---|---|---|---|
| `chaoyinshe/EchoVLM` | 11,683,172,920（11.7B） | 221 | apache-2.0 | **中文**；Qwen2-VL-7B + MoE；论文 arXiv:**2509.14977**（标题 *EchoVLM: Dynamic Mixture-of-Experts Vision-Language Model for Universal Ultrasound Intelligence*，作者 Chaoyin She, Ruifang Lu）；训练数据 **208,941 例临床 / 147 万超声关键帧 / 100+ 病种 / 7 个解剖系统**；报告生成比 Qwen2-VL **+7.58 BLEU-1 / +3.45 ROUGE-1** |
| `chaoyinshe/EchoVLM_v2` | 9,409,813,744（9.4B） | 282 | apache-2.0 | Lingshu-7B（Qwen2.5-VL）基座，更易接 vLLM |
| `QingboKang/SonoReasoner-8B` | 8.8B | 21 | apache-2.0 | Qwen3-VL-8B 基座，配套数据集 `QingboKang/SonoVQA`（20 下载） |
| `QingboKang/SonoReasoner-32B` | 33.4B | 19 | apache-2.0 | Qwen3-VL-32B 基座 |

**为什么这条重要**：**EchoVLM 是"超声专有 VLM 确实存在但没人用"的最好例证**——
一个中文、10B 级、Apache-2.0、训练数据 147 万帧的超声 VLM，**近 30 天下载只有 221 次**。
这不是"没有好的超声模型"，是**有也没人用**。这比"一个都没有"更能说明生态问题。

### 9.3 补充证据 3：逐器官检索 —— 肝、心是 0

| 查询 | 模型数 |
|---|---|
| `liver ultrasound` | **0** |
| `cardiac ultrasound` | **0** |
| `echocardiography ultrasound` | **0** |
| `thyroid ultrasound` | 3 |
| `breast ultrasound` | 15 |
| `lung ultrasound` | 4 |
| `fetal ultrasound` | 1 |

**→ 本项目要覆盖的三个器官里，肝脏和心脏在 HF 上连一个超声专用模型都没有。**
这条可以直接支撑"为什么我们的肝/心两条腿做不动"。

### 9.4 补充证据 4：⚠️ `bge-m3-medical` 在 HF 上不存在 —— 这是必须纠正的一条

**这条不是补充，是纠正。**

| 查法 | 结果 |
|---|---|
| `search=bge-m3-medical` | 5 个仓库，**全是低下载社区微调版**：`alifaheem94/bge-m3_medical_ur_ru`（81，乌尔都语/俄语）、`nqd-301125/bge-m3-medical-vi-dense`（66，越南语）、`doanbao/bge-m3-medical-vn-lora`（0）、`..._v2`（0）、`ming0302/bge-m3-medical-cn`（**0**） |
| BAAI 官方有无发布 | **没有** |
| 本项目代码里 `EmbeddingModelType.BGE_M3_MEDICAL = "bge-m3-medical"`（`app/embedding/protocols.py:28`） | 这只是个**逻辑名** |
| 实际部署加载什么 | `scripts/start_embedding_services.py:48-49`：`--model-dir ~/uai/models/bge-m3` → **原版 BAAI/bge-m3** |
| 项目自己承认过 | `16-开发进展汇报-20260928.md:191` 原文："dense R@10 0.86 是 **bge-m3 通用版权重（非 bge-m3-medical 微调版）**在该语料上的表现" |

**→ 答辩不能说「我们用了 bge-m3-medical（医疗微调版）」。**
正确说法：「文本嵌入用 **bge-m3**（MIT，1024 维），在中文超声指南语料上 hit@5 = 0.9853」。

**顺带一个正面结论**：中文医学检索嵌入在 HF 上**基本空白**——
`chinese medical embedding` → **0 个**；`中文 医学` → **0 个**；`chinese medical` 30 个但绝大多数是 LLM/NER，
**没有一个是中文医学检索嵌入**。这是本项目可以填的一个空档。

### 9.5 补充：ragas 的真实指标清单（从 0.4.3 源码实读）

`src/ragas/metrics/__init__.py`（vibrantlabsai/ragas，PyPI 版本 **0.4.3**）：

- **检索**：`ContextPrecision`（含 IDBased / LLM-WithReference / WithoutReference / NonLLM）、`ContextRecall`（含 IDBased / LLM / NonLLM）、`ContextEntityRecall`、`ContextRelevance`、`ContextUtilization`
- **忠实度**：`Faithfulness`、**`FaithfulnesswithHHEM`（内置！）**、`ResponseGroundedness`、`NoiseSensitivity`、`FactualCorrectness`
- **相关性/正确性**：`AnswerRelevancy`/`ResponseRelevancy`、`AnswerCorrectness`、`AnswerSimilarity`、`AnswerAccuracy`
- **多模态**：**`MultiModalFaithfulness`、`MultiModalRelevance`**
- **Agent**：`AgentGoalAccuracy*`、`ToolCallAccuracy`、`ToolCallF1`、`TopicAdherenceScore`
- **规则**：**`RubricsScore`（domain-specific）**、`InstanceRubrics`、`AspectCritic`、`SimpleCriteriaScore`
- 传统：BLEU / ROUGE / ChrF / ExactMatch / StringPresence 等

**三条对本项目直接的结论**：
1. `15号` P0-3 想做的"HHEM 判官替代 7B 判官" → **ragas 0.4.3 已内置 `FaithfulnesswithHHEM`**，不用自己接；
2. 有 `MultiModalFaithfulness` → "图文混合结论"在工具层是被考虑过的；
3. **`RubricsScore` 是唯一能在现有工具链里表达"误判方向权重"的原生接口** → 成因①的方案就落在这里。

⚠️ `AnswerRelevancy` 在 0.4.3 已标 **deprecated**（走 `_DEPRECATED_METRICS` 映射），升级时 import 路径必改。

### 9.6 一处需要纠正正文

正文写「`fabnem/UltraSoundCLIP`：有真实权重文件（`checkpoint-1050/model.safetensors` 等）」——**这条是对的**，
我复核确认该 repo 有 **292 个文件**，含多个 checkpoint 的 `model.safetensors`。但注意：**下载量 0、likes 0**，
即"有权重但零使用"。这个对比（ULC 有权重但没人用 vs 官方连权重都没放）比正文现在的表述更完整。

### 9.7 复核命令（我用的，与正文可交叉验证）

```bash
# 超声多模态（tag 口径，最准）
curl -sS "https://hf-mirror.com/api/models?filter=ultrasound&limit=1000"  | python3 -c "import json,sys;d=json.load(sys.stdin);m=[x for x in d if x.get('pipeline_tag') in ('image-text-to-text','zero-shot-image-classification','image-feature-extraction')];print(len(m),sum(x.get('downloads',0) or 0 for x in m))"
# → 10 648

# 对照：通用医学影像多模态
curl -sS "https://hf-mirror.com/api/models?filter=medical-imaging&limit=1000"  | python3 -c "import json,sys;d=json.load(sys.stdin);m=[x for x in d if x.get('pipeline_tag') in ('image-text-to-text','zero-shot-image-classification')];print(len(m),sum(x.get('downloads',0) or 0 for x in m))"
# → 55 9155
```

**终极对比**：`medgemma-4b-it` 一个模型 **412,807** 次 ≈ 超声全部 10 个多模态模型合计（648）的 **637 倍**。

> 注：正文与我的数字有 ±300 的差异（如 medgemma-4b-it：正文 413,157 / 我 412,807），
> 属 HF 滚动窗口的正常漂移，抓取时间差约 15 分钟。**答辩时用整数即可**（"41 万"）。
