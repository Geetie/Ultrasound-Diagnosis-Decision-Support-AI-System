---
name: tech-upgrade-completion-剩余项全清
overview: 完成 15 号技术升级规划全部剩余项：P0-4 依赖安全清理（附录 D 机械替换）、P0-5 P0 安全项验证、P0-2 约束解码 780 例重测收尾、P0-3 判官验收、Langfuse 接入激活、评测并发 bug 修复与 Self-RAG 重试降级、KB 语料扩展 liver/cardiac、alembic 迁移文件、15 号文档修正，全部测试绿后分批提交。
todos:
  - id: explore-contracts
    content: 用 [subagent:code-explorer] 探查 780 例评测脚本入口与数据集、obs 端点认证范围、t2c_eval workers 推导、langfuse .env 键名
    status: completed
  - id: p04-deps
    content: P0-4：按附录 D 机械替换 jose→PyJWT、清 passlib 挂名、更新守卫测试，跑 sec/quality 子集
    status: completed
  - id: alembic-selfrag
    content: 新增 alembic 迁移（embedding vector(1024)）；Self-RAG 零分即降级修正 + 单测更新
    status: completed
  - id: p05-verify
    content: P0-5 验证：obs 认证与 DICOM fail-closed 安全域回归全绿；EngineCore 处置固化进重启脚本
    status: completed
    dependencies:
      - p04-deps
  - id: langfuse-on
    content: Langfuse 激活：.env 配 OTLP 端点与密钥，重启主服务，确认 trace 上报可见
    status: completed
    dependencies:
      - explore-contracts
  - id: eval-concurrency
    content: 修 t2c_eval 并发未生效 bug（workers 推导），验证日志显示并发 6
    status: completed
    dependencies:
      - explore-contracts
  - id: kb-v110
    content: 用 [skill:remote-gpu-server-devtest] 扩展 KB：对话数据产 liver/cardiac 文档，建 kb_v1.1.0 灌数，三器官检索冒烟
    status: completed
    dependencies:
      - explore-contracts
  - id: p02-780-retest
    content: 用 [skill:remote-gpu-server-devtest] 跑 780 例一致性重测（guided decoding），对照 95% 门控如实出数归因
    status: completed
    dependencies:
      - p05-verify
      - langfuse-on
      - kb-v110
  - id: p03-faithfulness
    content: 用 [skill:remote-gpu-server-devtest] 跑 faithfulness（Qwen3-8B 判官）+ T1c 子集人工抽检相关性对照
    status: completed
    dependencies:
      - p02-780-retest
  - id: docs-final-commit
    content: 15 号修正（llama.cpp 弃用+状态落账）、16 号 A.10、SKILL 回写；全量测试绿 + pip-audit + 分批提交
    status: completed
    dependencies:
      - p03-faithfulness
---

## 用户需求

"全部执行"——按医学知识资产自审清单，执行三项加固（顺序 1→2→3）：

1. **UDAF 词表升级接入**：把手搓的 225 条分级同义词升级为 vocab_v2_final.json（1,650 独立词条，四层溯源：S=UDAF 生产词/A=THUOCL+语料双证/B=仅词表/C=LLM 抽取待复核），接入 UDAFVocabulary 生产加载链路，query_enhancer/tokenizer_utils/feature_extractor/udt_parser 四个消费方自动受益。
2. **幻觉检测医学词表重建**：hallucination_detector.py 的恶性/良性关键词表从手搓枚举改为 vocab_v2 A 层对齐；补建 cardiac 危象词表（心包填塞/主动脉夹层等，现 cardiac 恶性词集为空 set() 是安全缺口），危象词用 LLM 从心超指南语料抽取生成。
3. **禁忌症规则补引用**：contraindication_rules_safety.yaml 15 条手写规则逐条补 source_reference（对应器官穿刺/操作规范条款），severity 保持现状待临床复核标注。

收尾：测试回归（5632 后端套件相关子集 + 前端不受影响）、文档 A.16、提交推送 GitHub、同步服务器并重启主服务。

## 技术方案

### 任务 1：vocab_v2 接入 UDAF

- `vocabulary.py` 新增 `_init_extended_vocabulary()`：读取 `data/udt/vocab_v2_final.json`（路径支持环境变量 `UDT_VOCAB_V2` 覆盖，默认服务器绝对路径），按 tier 过滤接入范围（S/A/B 层直接注册；C 层默认不注册——待复核，可用 `UDT_INCLUDE_C=1` 开关控制）
- 术语注册复用既有 `self._add_mapping(standard, synonyms, related, category)`：v2 词条无 standard/synonym 结构，采用 **term 自映射**（standard=term，synonyms=[]），category 按来源编码（`thuocL_a`/`thuocL_b`），避免污染既有 echogenicity/boundary 等语义类目
- organ 归属：v2 按器官分桶，注册时通过既有 organ 字段或 category 前缀携带器官信息，保证 `get_terms_by_organ` 语义不回归
- 数据文件随仓库走：vocab_v2_final.json 从服务器拷回本机入 `app/knowledge/data/`（与代码同仓库分发，服务器 git archive 同步自然到位）
- 回归验证：`pytest tests/unit/rag/ tests/unit/knowledge/ -q` 全绿 + `get_all_synonyms` 数量断言（225 → 显著增长）+ query_enhancer 冒烟

### 任务 2：幻觉检测词表重建

- 新增 `data/udt/crisis_terms_cardiac.json` 资产：用运行中的 vLLM 8002（Qwen2.5-7B）对心超指南语料（kb_src_v13/cardiac 的 cardiac_tte_standardization 等文档）做危象术语抽取（prompt 口径：危重/急症/需立即临床干预的超声所见），频次≥2 + 人工口径白名单兜底（心包填塞/主动脉夹层/室间隔穿孔/腱索断裂/人造瓣膜功能障碍等）
- `hallucination_detector.py` 改造：
- `_MALIGNANT_DESC_KEYWORDS` 等枚举改为从 JSON 资产加载（模块级加载、失败回退内置默认集——安全组件不许因文件缺失而失效）
- cardiac 分支：`_CARDIAC_MALIGNANT_DESC_KEYWORDS`（空集）替换为危象词表；cardiac 的"恶性结论"口径改为"危象结论"
- 保持 detect() 接口与 DetectionIssue 结构不变（消费方零改动）
- 回归：`pytest tests/unit/llm/test_consistency.py tests/unit/sec/test_regression_fixes.py -q`

### 任务 3：禁忌症规则补引用

- `contraindication_rules_safety.yaml` 每条规则新增 `source_reference` 字段（如甲状腺 FNA：中国甲状腺结节穿刺活检规范；肝脏活检：肝穿刺活检规范；负荷超声：相应指南章节），`severity` 不改动（保留"待临床复核"语义）
- SafetyGateway 加载侧若对未知字段报错需同步放行（pydantic/yaml 解析处确认 extra 字段容忍性）
- 回归：SG 相关单测 `pytest tests/unit/sg/ -q`

### 收尾

- 16 号文档追加 A.16；commit + push origin；git archive 同步服务器 + 重启主服务 + 健康核验（18000/9001/8003/8002/8006）