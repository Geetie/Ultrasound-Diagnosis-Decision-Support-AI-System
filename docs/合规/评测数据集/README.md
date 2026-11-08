# 评测数据集 v2 —— manifest 规范与三分法

> 建档：2026-11-08｜触发：全量核查 14 个自造测试集，发现无一能用于验收（见 16 号 A.166）
> 纪律：**没有 manifest 的数据集不得进评测**。manifest 缺字段 = 该集不合格。

## 一、为什么要有 manifest

历史教训：`kb_qa_golden_v1` 用了半年、报了 98.53%，后来才发现含影像术语只有 11.8%、
gold 是"找回自己来源的文档"。**没有一个地方记录过"这个集是怎么造的、能测什么、不能测什么"**，
于是错误口径一路传播。

manifest 的作用就是：让任何人拿到数据集时，**先看能测什么，再看数字**。

## 二、manifest 必填字段

每个数据集目录下必须有 `manifest.json`：

```json
{
  "dataset_id": "v2-retrieval-zh-thyroid",
  "version": "1.0.0",
  "created_at": "2026-11-08T00:00:00+08:00",
  "purpose": "中文检索评测（医生视角 query → chunk 级 gold）",
  "organ": ["thyroid"],
  "n_items": 120,

  "provenance": {
    "source": "public | internal | synthesized | hybrid",
    "public_datasets": [
      {"name": "AUL", "license": "CC-BY 4.0", "commercial_ok": true, "url": "..."}
    ],
    "internal_source": "生产判读 1056 条中 314 条有结论的，人工筛 120 条",
    "synthesis_method": "无 / 描述模板（须说明模板来源）",
    "annotators": ["医生A（甲状腺专科）"] ,
    "annotation_protocol": "双人独立标注，分歧由第三人裁决"
  },

  "quality_checks": {
    "duplicate_rate": 0.0,
    "template_pollution_rate": 0.0,
    "imaging_term_coverage": 0.78,
    "self_contradiction_count": 0,
    "human_reviewed_ratio": 1.0
  },

  "valid_for": ["检索 hit@k", "检索 MRR", "检索 NDCG"],
  "NOT_valid_for": ["分级准确率（无临床金标准）", "漏诊率（无随访数据）"],
  "known_limitations": ["仅覆盖甲状腺", "query 均来自同一家医院"],

  "metrics_produced": [
    {"metric": "hit@10", "value": null, "measured_at": null, "n": null}
  ],

  "maintainer": "lead",
  "supersedes": ["kb_qa_golden_v1（已作废，见 A.166）"]
}
```

## 三、按来源三分法

### ① 公开集（直接用，只需登记许可）

> **怎么下载 / 怎么校验 / 实测验收数字 → 见 [下载清单.md](下载清单.md)**（逐集 URL、命令、sha256）。

| 集 | 器官 | 许可 | 可商用 | 规模 | 标签 | 本地状态 |
|---|---|---|---|---|---|---|
| AUL | 肝 | CC-BY 4.0 | ✅ | 735（全量 11468） | 良恶性 + 分割 | ✅ 已下 |
| TN3K | 甲状腺 | CC BY-NC 3.0 | ❌ 仅评测 | **3493 图 + 3493 掩码** | 结节分割 + 良恶性 | ✅ **已下 2026-11-08** |
| DDTI | 甲状腺 | Open Access（无 SPDX） | ⚠️ 按仅评测 | **480 图 / 390 例 + TI-RADS** | 分割 + TI-RADS + 影像征象 | ✅ **已下 2026-11-08** |
| TG3K | 甲状腺 | CC BY-NC 3.0 | ❌ | 3583 帧（未核实） | 视频/多帧 | ❌ gated 需账号 |
| TNSCUI | 甲状腺 | CC BY-ND 4.0 | ⚠️ 可商用但**禁改作** | 3644 | 分割 | ❌ 官方公开源无数据 |
| CACTUS | 心脏 | CC BY 4.0 | ✅ | 37736 帧 | 切面分类 | ✅ 已下 |
| CAMUS | 心脏 | CC BY-NC-SA 4.0 | ❌ | 500 例 | 分割 + LVEF | ✅ 已下 |
| EchoNet-Dynamic | 心脏 | Non-commercial | ❌ | 10030 | LVEF/容积 | ❌ 需申请 |

登记目录：`docs/合规/数据源台账/open-access-catalog-20260929.csv`（96 个集）。

**纪律**：`commercial_ok=false` 的集，`usage` 必须写 `evaluation_only`，
且**不得**用于训练或进入产品交付物。

### ② 必须自己造（4 项，全卡在医生标注）

| 数据集 | 卡点 | 目标规模 | 状态 |
|---|---|---|---|
| 临床文本 → 判读金标准 | **需医生标注** | ≥200 例（三器官） | 未开始 |
| 中文检索评测集（chunk 级） | 可半自动 + 人工筛 | ≥100 query，≥50% 含影像术语 | 可立即开始 |
| 报告话术质量评审集 | 需医生 5 分制 | ≥100 份 | 未开始 |
| 随访/病理结局 | 需临床随访 | ≥300 例 | 未开始 |

### ③ 已作废（不得再用）

`kb_qa_golden_v1` / `kb_golden_v1` / `us365k_retrieval_golden_v1` /
`clean_eval_set_20261020` / `double_engine_eval_set` /
`double_engine_eval_set_v3` / `restored_non_echo_20261021` /
`consistency_test_cases`

服务器侧已在这些文件旁写 `.DEPRECATED.md` sidecar；
仓库内 `consistency_test_cases.json` 加了 `DEPRECATED` 字段。

## 四、质量自查（造完必须跑）

| 检查 | 阈值 | 怎么查 |
|---|---|---|
| 重复率 | **必须 0** | `len(set(texts)) / len(texts) == 1.0` |
| 模板污染率 | **≤10%** | 统计前缀重复 ≥3 的组 |
| 影像术语覆盖 | **按类型区分**（见下） | 关键词命中率 |
| 自相矛盾病例 | **必须 0** | 对立词对检测（清晰/欠清晰、规则/欠规则…） |
| 人工复核比例 | **100%**（金标准类） | 标注记录 |


### 影像术语覆盖率：按数据集类型区分（A.171）

**同一个指标，不同类型方向相反**：

| 类型 | 判据 | 为什么 |
|---|---|---|
| `retrieval`（检索集） | **≥50%** | 查询要像医生写的影像描述，否则测不出系统能力 |
| `plain_language`（患者解释） | **≤40%** | 好解释按定义就该通俗；术语越多越不像给病人看的 |
| `skip` | 不判 | 如纯标注清单 |

用法：`python scripts/audit/p79_dataset_quality_gate.py --dataset x.jsonl --field text --kind plain_language`

**这个区分是实测倒逼出来的**：B3 患者解释黄金集覆盖率 0.22，按检索集口径判 FAIL，
但读原文后确认那些解释写得很好——是**口径不适用**，不是数据有缺陷。
（同一份数据，`retrieval` 判 FAIL、`plain_language` 判 PASS，测试里专门钉了这条。）

任何一项不达标 → **该集不得进评测**，不得以"先跑起来再说"为由放行。

## 五、相关脚本

- `scripts/audit/p67_public_hard_set.py` —— 从公开集构造 gold 唯一的难集（已支持 aul/tn3k/cactus/camus）
- 待建：`scripts/audit/p79_dataset_quality_gate.py` —— 按上表四项做自动检查
