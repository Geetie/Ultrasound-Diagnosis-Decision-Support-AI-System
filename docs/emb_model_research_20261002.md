# 图文嵌入模型替代方案调研（Ultrasound-Clip 打不通后的备选）

> **调研日期**: 2026-10-02
> **背景**: 离立项已久，本地 Ultrasound-Clip 服务链路打不通；此前口头提过去 HuggingFace 等平台寻找更新的替代模型。本报告为该次调研的落盘结论。
> **调研方式**: 联网检索（HuggingFace / GitHub / arXiv / 官方博客），关键事实均标注来源。

---

## 一、现有链路与替换约束

当前 `app/embedding/ultrasound_clip.py` 通过本地 HTTP 服务（`localhost:8003`）提供：

| 端点 | 模态 | 输出维度 |
|---|---|---|
| `/v1/embeddings` | text | 768 |
| `/v1/embeddings/image` | image | 768 |
| `/v1/embeddings/multimodal` | text+image | 768 |

**替换时的硬约束**：

1. **接口不变**：需实现 `EmbeddingService` 协议（`embed_text` / `embed_image` / `embed_multimodal` / `health_check`），最省事的做法是新写一个服务占住 8003 端口、保持协议兼容，主服务零改动。
2. **维度耦合**：`settings.embedding_dim`（默认 768）写入了协议注释，且向量索引 collection 维度与模型绑定——**换模型必须重建向量索引并全量重嵌入**，不是热插拔。
3. **中文文本**：系统对话与病历查询以中文为主。**绝大多数医学 CLIP 的文本塔是英文训练的**，直接上英文模型需要在查询链路加翻译层，或选中文原生模型。
4. **部署环境**：现有部署为单机自建（非 Azure/GCP），候选模型必须能 HF 权重下载后本地推理。

---

## 二、候选模型一览

### A. Ultrasound-CLIP（浙大，CVPR 2026）— 与本项目最相关

- **资源**：
  - 代码: <https://github.com/ZJUDataIntelligence/Ultrasound-CLIP>
  - 数据集 US-365K（36.4 万超声图文对，11,676 临床病例，覆盖全解剖区）: <https://huggingface.co/datasets/JJY-0823/US-365K>
  - 论文: <https://arxiv.org/abs/2604.01749>
- **方法**：经典双编码器 + 两个创新模块——**UDAF 引导的异质图编码器**（诊断节点+属性节点→异质 GNN→门控融合进文本嵌入）与 **UDAF 语义软标签**（9 大诊断维度：身体系统/器官/诊断结果/形状/边缘/回声性/内部特征/后方声学现象/血流信号，构建连续软先验矩阵），双目标 = 对称 CLIP 损失 + 语义损失（MSE+KL）。
- **性能**：T2I@50 **80.22%**、I2T@10 **37.45%**；多任务分类平均 59.61%（病灶边缘 84.44%）；在 4 个公开超声数据集（乳腺、胃肠等）零样本/线性探测/全微调均为最优。自称全面优于现有医疗 CLIP 基线。
- **与本项目的强协同**：
  1. **中文原生数据**（中国团队、三甲医院超声图文），天然契合中文查询链路；
  2. 其 UDAF 语义框架与本项目 vocab_v2 已接入的 UDAF 词条体系同源同构——**我们现成的 UDAF 知识资产可直接用作软标签/数据增强**；
  3. 项目已有 `scripts/finetune_ultrasound_clip.py` 微调管线，可复用其代码/数据做预训练底座替换或继续预训练。
- **风险**：**官方是否发布预训练权重未明确**（截至调研日仅确认代码+数据集开源）——需跑通其仓库确认；若无权重，需用 US-365K 自行训练（算力成本 ≈ CLIP-scale 预训练，需评估）。

### B. MedSigLIP（Google Health / HAI-DEF，2025-07）— 最省事的即插即用基线

- **资源**：HF `google/medsiglip-448`；GitHub <https://github.com/Google-Health/medsiglip>（含 HF/Vertex 快速上手 notebook）。
- **规格**：SigLIP 架构，视觉编码器 400M + 文本编码器 400M，448×448 输入，文本上限 64 tokens，双塔共享嵌入空间。
- **许可**：模型权重 Health AI Developer Foundations License（免费商用），仓库代码 Apache 2.0；单卡可部署。
- **官方支持任务**：**语义图像检索**、零样本分类、数据高效分类。
- **风险**：文本塔为英文、64 token 上限对长病历描述偏紧；训练数据以胸片/皮肤/病理/CT/MRI 等为主，超声占比未明确；中文查询需翻译层。

### C. MedImageInsight（Microsoft，arXiv:2410.06542）— 通用医学影像嵌入 SOTA

- **资源**：HF `lion-ai/MedImageInsights`（可直接下载权重、本地运行，**无需 Azure**）；`pip install medimageinsights`（Lion-AI 维护的精简封装）；论文 <https://arxiv.org/abs/2410.06542>。
- **规格**：图像编码器有 Convolutional Swin Transformer 与 DaViT 两版（配置默认 DaViT，480×480 输入），配文本编码器，支持零样本分类与嵌入生成。
- **性能**：论文称在通用医学影像嵌入任务上达到 SOTA。
- **风险**：编码器选型/维度需查论文确认（未在模型卡直接给出）；文本塔英文；医疗许可条款需逐条核对。

### D. 其他备选（次优先级）

| 模型 | 定位 | 备注 |
|---|---|---|
| BiomedCLIP（Microsoft） | 医学 CLIP 老牌基线，512 维，MIT 许可 | 稳定易得，但已是两年前模型，性能弱于 A/B/C |
| RAD-DINO（Microsoft） | 仅图像编码器 | 不满足图文对齐检索需求 |
| EchoCLIP | 心超专用 | 与本项目 cardiac 危象模块契合，但覆盖面太窄 |
| Chinese-CLIP | 中文通用图文 | 非医学训练，但可作为"中文底座 + US-365K/自有语料继续预训练"的备选路线 |
| jina-embeddings-v4 / SigLIP2 | 通用多模态嵌入 | 非医学专用，作为兜底对比项 |

---

## 三、推荐结论

**分两步走，避免一次性押注：**

1. **短期（1–2 周，低风险）**：拉起 **MedSigLIP** 作为对照基线——HF 权重直接下载、官方支持检索、单卡可跑。写一个 8003 端口协议兼容的 FastAPI 包装服务（text/image/multimodal 三端点），英文查询加一层翻译（或评估 BGE-M3 文本塔混用的可行性）。同时下载 **MedImageInsights**（pip 包 + HF 权重）跑同一评测集。用项目现有的 `scripts/evaluate_ultrasound_clip.py` 评测框架横向打分。
2. **中期（1–2 月，主攻方向）**：转向 **Ultrasound-CLIP 生态**——
   - 官方预训练权重已确认存在（OneDrive），先转存 + 实测加载；
   - 权重 + 论文方法 + US-365K + 项目自有 UDAF 词条/危象词表/70 篇超声指南语料，正好构成"通用底座 + 领域继续预训练"的数据组合，复用 `finetune_ultrasound_clip.py` 管线；
   - 其 UDAF 语义软标签方法可与本项目 vocab_v2 的同义词/属性体系直接打通，形成数据壁垒。

**不建议**：直接引入 VLM（MedGemma 等）替代嵌入服务——推理成本高一个数量级，且不产出对齐嵌入向量。

## 四、落地注意事项

- 换模型 → `settings.embedding_dim` 调整 + 向量 collection 全量重建 + 重嵌入（Qdrant/pgvector 两后端都要过回归）；
- `EmbeddingModelType` 枚举需新增类型值；
- `tests/unit/emb/`、`tests/integration/emb/` 的 768 维断言需同步更新；
- 医疗许可合规：MedSigLIP（HAI-DEF License）与 MedImageInsight 的商用条款在接入前需法务过目。

## 五、第二轮扩展调研（2026-10-02 补充）

### 1. Ultrasound-CLIP 权重问题已核实 ✅

- **官方预训练权重确实存在**：README Resources 区 "pre-trained weights: here" → OneDrive 分享链接
  `https://1drv.ms/f/c/48250be4328ce7ab/IgCnIA0--KugSoW0japEO8HaAW6koYf9YVNnB9vQiqeq_O8?e=c7xe4J`
  （注意：OneDrive 链接可能有时效性/需浏览器访问，建议尽快转存）
- **仓库定位澄清**：GitHub 仓库只含 **6 个核心组件文件**（EnhancedCLIP / graph_builder / graph_encoder / semantic_loss / similarity_processor / tag_vocab），是 open_clip 风格的**参考实现**，不含完整训练脚本与数据加载；backbone 是传入的基础 CLIP（变体未指明）。**意味着接入工作 = 权重 + 自行拼装推理代码**，工作量可控但需对照论文。
- 结论修正：中期路线从"可能需要自训"升级为"**下载官方权重 + 拼装推理**"，可行性显著提高。

### 2. HF Hub 全站枚举结果（经 hf-mirror API，国内可直连下载）

- `search=ultrasound`（按下载量前 40）：**没有超声专用的图文对比学习大模型**，全部是小规模分类/分割/VLM-LoRA 微调模型（最高仅 156 下载）。→ 超声专用 CLIP 权重基本都在论文团队手里（GitHub/OneDrive），HF hub 不是超声嵌入模型的集散地。
- `search=biomedclip`：官方 `microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224`（**22.5 万下载**）及社区版均在，可经 hf-mirror 直接下载。
- `search=medclip`：均为社区小模型，无官方级维护。

### 3. 新增候选

| 模型 | 资源 | 定位 | 备注 |
|---|---|---|---|
| **UniMed-CLIP**（PKU） | <https://github.com/muzairkhattak/UniMed-CLIP> | 统一医学 CLIP，训练/推理代码 + 预训练 checkpoint 已放出 | 覆盖多影像学科，可作为与 MedSigLIP 并列的对照基线 |
| USCL（超声视频对比预训练） | arXiv:2210.04477 | 超声视频层级对比学习 | 面向视频流，本项目以静态帧为主，仅作参考 |

### 4. 工程可行性补充

- 本机已验证 `hf-mirror.com` API 可达 → **MedSigLIP / BiomedCLIP / MedImageInsights 等权重可走镜像下载**，无需海外代理；
- Ultrasound-CLIP 权重在 OneDrive，国内下载成功率需实测，失败可考虑让有海外网络的同事转存。

## 六、来源

- <https://github.com/ZJUDataIntelligence/Ultrasound-CLIP>
- <https://huggingface.co/datasets/JJY-0823/US-365K>
- <https://arxiv.org/abs/2604.01749>
- <https://cloud.tencent.com/developer/article/2655525>（CVPR 2026 报道）
- <https://github.com/Google-Health/medsiglip> / <https://huggingface.co/google/medsiglip-448>
- <https://huggingface.co/lion-ai/MedImageInsights> / <https://pypi.org/project/MedImageInsights/> / <https://arxiv.org/abs/2410.06542>

## 八、第三轮调研补充（2026-10-02 晚，接 A.17 之后）

### 1. EchoCare 溯源补充（修正第六节"神秘模型/未鉴定来源"）

- models/echocare/model.pt 来源已确证：HF CAIR-HKISI/EchoCare（中国科学院香港创新研究院 AI 与机器人创新中心），2025-09-17 发布、12 月放权重的全开源超声基础模型。
- 架构与加载：MONAI SwinTransformer(use_v2=True, spatial_dims=2, embed_dim=128, depths=[2,2,18,2])，120.2M 参数，strict 加载成功（checkpoint 无 mask_token，官方 README 的 pop 步骤实际不需要）；预训练数据 EchoAtlas 450 万张/52 器官/23 国。
- 格式钉子：HF 仓库的 model.safetensors 实为 torch zip 格式（PK 头）——按扩展名分发的加载器会误走 safetensors 报 "header too large"；复制为 .pt 后 torch.load 即可。model.safetensors 与 model.pt 内容相同（可删冗余副本）。
- 实测（TN3K 甲状腺良恶性，100 train/60 test 冻结特征 + LR 探针，全 CPU）：AUC 0.7444 vs BiomedCLIP 0.7022——纳入对比矩阵，暂列视觉底座第一。
- 许可仍未标注（HF YAML 与 GitHub 均无 LICENSE）：开发/研究用途已按项目方拍板推进；注册商用前必须与 CAIR 确认授权。

### 2. UniMed-CLIP 溯源修正

- 第六节"UniMed-CLIP（PKU）"有误：实为 MBZUAI（mbzuai-oryx/UniMed-CLIP，arXiv:2412.10372），六模态统一医学 CLIP，checkpoint 已放 HF（另有 HF Space demo）。定位为 MedSigLIP 之外的对照候选，超声占比待查模型卡。

### 3. TN3K 数据事实修正（与 16 号 A.17 同步）

- TN3K 自带二分类标签（label4trainval.csv 2878 行 / label4test.csv 614 行，0/1）——"仅分割标注"的旧判断作废；
- README frontmatter 声明 license: mit（HF dataset card 口径）——"NC 红线"旧判断需以 14 号台账复核为准，但数据集卡片自述宽松；
- test-image/test-mask 614+614 文件为 Git LFS 指针未解引用（130 字节 "version https..."，oid sha256:407fe2b4…，TN3K.rar 真身 184,398,942 B）——test 集不可读，修复中（见 A.18）。

### 4. 候选对比矩阵（更新至 2026-10-02 晚）

| 候选 | 类型 | 权重可得 | 超声对口 | TN3K 探针 AUC | 许可 | 定位 |
|---|---|---|---|---|---|---|
| EchoCare | 纯视觉 120M | 是 hf-mirror | 高（超声专训） | 0.7444 | 未标注 | 视觉底座首选 |
| BiomedCLIP(+t2a微调) | 双塔 512d | 是 已在服 | 中（t2a_v2 AUC 0.8751） | 0.7022（零微调）/0.8751（微调） | MIT | 现役基线+I2T路线 |
| Ultrasound-CLIP | 双塔+图编码器 | 用户下载中 2.2GB | 高（US-365K 中文图文） | 待测（论文 I2T@10 37.45%） | 未标注 | 三方对比进行中 |
| MedSigLIP | 双塔 400M*2 | 是 hf-mirror | 低（超声占比不明） | 未测 | HAI-DEF可商用 | 对照候选 |
| UniMed-CLIP | 双塔 VLM | 是 HF | 低 | 未测 | 待查 | 对照候选 |
| MedImageInsight | 编码器 | 是 pip+HF | 低 | 未测 | 待核对 | 对照候选 |

注：t2a_v2 的 0.8751 是 AUL 数据集微调后口径，与 TN3K 零微调探针不同评测集，不可直接横比；同口径三方对比见 A.18。

### 5. 官方 Ultrasound-CLIP 权重上机（进行中）

- 用户经 OneDrive 下载官方权重（2.2GB）正在上传服务器，落位后做：checkpoint 结构鉴定（torch.load/keys/backbone 识别）→ 按 6 组件参考实现拼装推理（graph_encoder 所需 tag_vocab 可复用本项目 app/knowledge/data/vocab_v2_final.json——UDAF 同源体系）→ TN3K 同口径探针 + US-365K I2T 三方对比。
- 预期管理：论文口径 I2T@10 = 37.45%，距 Gate 2 的 45% 门槛尚差 7.5pp——官方权重不是保票，结论以同口径实测为准。