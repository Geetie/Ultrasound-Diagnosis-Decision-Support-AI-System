# 六大 Agent Skills 仓库 · 完整技能清单（说人话版）

> **核实时间**：2026-10-28　｜　**方法**：六个仓库全部拉取到本地，逐个读 `SKILL.md` 原文（不是看 README 二手描述）
> **规模**：共核实 **258 个 SKILL.md**，覆盖 6 个仓库
> **每条技能都回答 5 件事**：一句话是什么 / 什么时候用 / 能干什么 / 外部 Key / 自带资源

## 先看这张表：这六个仓库根本不是同一种东西

很多人把这六个当"六个技能库"平铺着看。实际上其中 **两个根本不是技能库**，先分清再决定装不装：

| # | 仓库 | 它到底是什么 | SKILL.md | 怎么用 |
|---|---|---|---:|---|
| 1 | [anthropics/skills](https://github.com/anthropics/skills) | Anthropic 官方**技能样例集**，Agent Skills 格式事实标准的出处 | **20** | `/plugin marketplace add anthropics/skills` |
| 2 | [vercel-labs/skills](https://github.com/vercel-labs/skills) | ⚠️ **不是技能库**，是 `npx skills` 这个 CLI（技能包管理器 + skills.sh 生态搜索器） | 3（2 个是它自己的 CI） | 装 CLI，然后用它去装别人的技能 |
| 3 | [Jensen-Yao/agents-skills](https://github.com/Jensen-Yao/agents-skills) | 个人技能库，**体量最大**，ML / 科研 / 工程全覆盖 | **167** | 拷目录即可，DSH / Claude Code 通用 |
| 4 | [mattpocock/skills](https://github.com/mattpocock/skills) | 资深工程师的**工作流方法论**集（不是工具箱，是做事方式） | **38** | 先跑 `setup-matt-pocock-skills` |
| 5 | [xingtu1996/dsh-xingtu-skills](https://github.com/xingtu1996/dsh-xingtu-skills) | 打包成 **DSH 标准插件**的技能包，一条命令装完 | **30** | `dsh plugin add dsh-xingtu-skills` |
| 6 | [bruc3van/awesome-dsh-plugin](https://github.com/bruc3van/awesome-dsh-plugin) | ❌ **不是技能库**，是 2 万+ DSH **插件索引 / 订阅源** | **0** | 当"去哪儿找插件"的导航用 |

> **同名技能提醒**：`prototype` 这个技能在 **Jensen-Yao** 和 **mattpocock** 两个仓库各有一个，内容完全不同（前者是"一次做一件 UI 的出 3 个方向让用户挑"，后者是"从第一天就标明是原型、跳过抛光、结论折回真代码"）。每条技能标题后面都标了所属仓库，别认错。
>
> 一句话总结：**要现成技能 → 3（最多）或 1（最权威）或 5（最省事）；要做事方法 → 4；要逛生态 → 2 和 6。**

## 关于"外部 Key"，先给你一个总判断

我把 258 个技能逐个回原文查了凭证要求。**结论：绝大多数技能不需要任何外部 Key。**

- **真正必须自己去申请 Key 的，集中在 5 类**：
  - 学术数据库：Zotero（`ZOTERO_API_KEY`）、Scopus、Web of Science、Semantic Scholar
  - 实验追踪 / 可观测：W&B、SwanLab、LangSmith、Phoenix
  - 云 GPU / 云平台：Lambda Labs、Modal、NGC（NVIDIA）
  - 向量库上云：Pinecone
  - LLM 生图 / 生音频：OpenAI、Gemini
- **大量"看起来要 Key"的其实是本地服务**：vLLM、Ollama、Qdrant 本地模式的 `api_key` 都是 `"not-needed"` 占位符，**不要傻乎乎去申请**。
- **有些技能原文明确写"不需要 Key"**：`sci-extract`、`sci-ppt`、`sci-search` 的主体数据源（OpenAlex / Semantic Scholar 免费层）、`slack-gif-creator`（只生成不上传，不需要 Slack token）。
- **每一节里的「外部 Key / 依赖」字段是以 SKILL.md 原文为准的**，比这份总表更细，有冲突以条目为准。

---


---

## 1. anthropics/skills — Anthropic 官方技能样例集（20 个）

> 仓库：`anthropics/skills`（Anthropic 官方 Agent Skills 仓库）。下面按仓库里的顺序逐个说人话。
> 一句话提醒：大部分技能是 Apache-2.0 开源示例；只有 docx / pdf / pptx / xlsx 是 source-available（非开源），`doc-coauthoring` 和 `template-skill` 则连 LICENSE 文件都没有。许可证细节见各自条目。

#### `academy-guide` <sub>anthropics/skills</sub>

- **一句话**：当用户问"这东西怎么用/怎么上手"时，在正常回答后面顺手推荐一条 Claude Academy 的课程或教程。
- **什么时候用**：用户问 "how do I…"、"getting started with"、"教我用"、"Claude 能做什么"；问 artifacts / projects / skills / plugins / connectors / MCP 怎么用；要把 Claude 推广到团队、班级或公司；要培训材料、入职内容、学习资源。反例：用户正忙着干活、只想要结果时不要用。
- **能干什么**：
  - 先正面把问题答完，再在结尾自然补一句推荐——推荐是补充，不能替代答案
  - 只在"强匹配"时推荐（判断的是意图，不是话题），最多 2 条；弱相关就闭嘴，宁可不提
  - 每次会话现拉一次 `catalog.json`，并按 `staleAfter` / `generatedAt` 判断是否过期；拉不到、拉错、过期，就只给产品 hub 或资源库链接
  - 不许编课程：只能用 catalog 里的原始 URL，不许把 tutorial 的链接"改成" course
- **外部 Key / 依赖**：无需外部 Key。但需要能联网 fetch `academy.claude.com` 的 catalog JSON；拉不到会自动降级为只给 hub 链接（并且对用户静默）
- **自带资源**：仅 LICENSE.txt（课程清单不内置，是远端拉的，避免过期）

#### `algorithmic-art` <sub>anthropics/skills</sub>

- **一句话**：先写一份"算法艺术哲学"（.md），再用 p5.js 把它实现成一个带随机种子和参数滑杆的单文件交互网页。
- **什么时候用**：用户要生成艺术 / generative art / algorithmic art / 流场 / 粒子系统，或明确说"用代码做艺术"。
- **能干什么**：
  - 产出 4-6 段算法哲学 .md：给这场"运动"起名，说清它如何通过计算过程、噪声、粒子、场、参数变化来表达
  - 产出单个自包含 HTML artifact：p5.js（CDN）+ 内联算法 + 参数面板，浏览器直接打开就能玩
  - 固定用种子保证可复现（同种子必然同结果），自带上一张/下一张/随机/跳转种子按钮
  - 强调原创，不复制在世艺术家的作品
- **外部 Key / 依赖**：无需外部 Key；页面从 cdnjs 加载 p5.js（原文写 "p5.js from CDN"），所以打开时需要能访问该 CDN
- **自带资源**：`templates/viewer.html`（必读的 HTML 骨架，UI/品牌部分固定不许改）、`templates/generator_template.js`（p5.js 代码结构参考）

#### `brand-guidelines` <sub>anthropics/skills</sub>

- **一句话**：把 Anthropic 官方品牌色和字体套到你的产物上（原文的技术细节以 python-pptx / PPT 场景为主）。
- **什么时候用**：需要 Anthropic 那种 look-and-feel、要按公司品牌色/字体/视觉规范出图出稿时。
- **能干什么**：
  - 给出准确色值：深 `#141413`、浅 `#faf9f5`、中灰 `#b0aea5`、浅灰 `#e8e6dc`；强调色 橙 `#d97757`、蓝 `#6a9bcc`、绿 `#788c5d`
  - 标题用 Poppins（24pt 及以上），正文用 Lora；字体缺失时自动回退 Arial / Georgia
  - 非文字图形轮流使用三种强调色，保持品牌感
  - 上色走 python-pptx 的 `RGBColor`，保证跨系统颜色一致
- **外部 Key / 依赖**：无需外部 Key；建议环境预装 Poppins / Lora 字体（不装也能跑，只是回退）；示例代码依赖 python-pptx
- **自带资源**：仅 LICENSE.txt（色值和字体规则都写在 SKILL.md 里，没有色板文件）

#### `canvas-design` <sub>anthropics/skills</sub>

- **一句话**：先写一份"视觉设计哲学"（.md），再据此做出一张海报/艺术画级别的 .pdf 或 .png。
- **什么时候用**：用户要海报、艺术画、静态平面设计，或"帮我做个好看的设计稿/封面"。
- **能干什么**：
  - 产出 4-6 段视觉哲学：命名一个"运动"，从空间、形态、色彩、材质、比例、构图几个维度讲清表达方式
  - 产出单页高视觉密度作品（.pdf 或 .png），还支持按需扩展成多页
  - 强制从 `./canvas-fonts` 里挑字体，让排版本身成为画面的一部分，而不是"排字"
  - 强制第二遍只做减法式精修（不加新元素），逼近"博物馆级"而不是"AI 图"
- **外部 Key / 依赖**：无需外部 Key；原文未说明用什么库/工具渲染 PDF/PNG，只规定了输出格式和字体目录
- **自带资源**：`canvas-fonts/`（54 个 .ttf 字体 + 27 份 OFL 授权文本）

#### `claude-api` <sub>anthropics/skills</sub>

- **一句话**：一份"照着写就不会写错"的 Claude API / 官方 SDK 参考手册，覆盖 7 种语言加 cURL。
- **什么时候用**：代码里出现 Claude/Anthropic（`Claude`、`anthropic`、`@anthropic-ai`、`claude-*`、`us.anthropic.*`）；问 LLM 的价格/选型/限流/缓存，且明令"不许凭记忆答"；或任务明显是 LLM 形态但没说哪家（agent / MCP / tool use / RAG / LLM 判官 / 多智能体）。**跳过条件**：项目明确在用 OpenAI / Gemini / Cohere / Ollama（先 grep 一遍再决定）。
- **能干什么**：
  - 按语言给可直接抄的 SDK 用法：Python、TypeScript、Java、Go、PHP、Ruby、C#，外加 cURL
  - 模型 id、定价、参数、流式、tool use、结构化输出、Batch、Files API、prompt caching、token 计数
  - 模型迁移：从老模型升到 Opus 5.5 / Sonnet 5.5 等的破坏性变更清单（prefill 被移除、强制 tool_choice 报 400 等）
  - 另外还带 Managed Agents、Admin API、eval 搭建/审计/hillclimb、成本优化、prompt 审计等专题
- **外部 Key / 依赖**：Anthropic API Key（`ANTHROPIC_API_KEY`，或 `ANTHROPIC_AUTH_TOKEN`）；走云也可以——Bedrock / Vertex / Microsoft Foundry / Claude Platform on AWS 用各自云凭证，联合身份（WIF）则要一组 `ANTHROPIC_*` 环境变量（federation rule id、org id、service account id、identity token）
- **自带资源**：8 个语言目录（python / typescript / java / go / php / ruby / csharp / curl）+ `shared/` 里 50 份专题文档（迁移、缓存、计费、错误码、Managed Agents、eval 等，另有报告构建脚本 `.mjs` 和两个 Python 探针），整个技能共 96 个文件

#### `discernment-nudge` <sub>anthropics/skills</sub>

- **一句话**：在你给出"用户可能真要照着做"的答案之后，追加 2-3 个针对本次内容的具体追问，提醒他核一下事实、假设和缺失背景。
- **什么时候用**：答案里有金钱/时间/概率之类的估算；给了高后果领域建议（商业、健康、法律、财务、职业、人际）；给了用户可能拿去引用的事实判断；走了一遍多步推理；或替他起草了要用的文档。一次会话最多触发一次。
- **能干什么**：
  - 在答案末尾用固定开场 `A few things worth a second look:` 追加 2-3 条 bullet（纯文本，不加标题、不加 emoji）
  - 每条必须指向答案里的具体数字、具体推理步骤或缺失的上下文，禁止"请核实这些事实"这种空话
  - 明确划出不该用的场合：闲聊、纯教学、只做格式转换、用户自己写代码跑、用户已经要求你复核、用户说"我只要结论"
- **外部 Key / 依赖**：无需外部 Key
- **自带资源**：仅 LICENSE.txt

#### `doc-coauthoring` <sub>anthropics/skills</sub>

- **一句话**：把"写文档"拆成三段引导流程：先收上下文，再逐节打磨，最后用一个没有上下文的"新读者"试读挑盲点。
- **什么时候用**：用户要写 PRD / 技术方案 / 决策文档 / RFC / 提案，或明显要开始一项成文写作任务。
- **能干什么**：
  - 阶段一收上下文：先问文档类型、读者、读完要产生什么影响、有没有模板，再让用户一次性把背景"倒"出来
  - 阶段二逐节打磨：每节先 5-10 个澄清问题 → brainstorm 5-20 个选项 → 用户挑选 → 起草 → 手术式改写
  - 阶段三读者测试：先预测 5-10 个读者会问的问题，再交给一个无上下文的 Claude 实例试答，看哪些答不上来
  - 可选接 Slack / Teams / Google Drive / SharePoint / MCP 直接拉材料；没有就让用户粘贴
- **外部 Key / 依赖**：无需外部 Key；要用集成取材料则需要对应的 MCP / connector
- **自带资源**：无（这个技能连 LICENSE 文件都没有，只有 SKILL.md）

#### `docx` <sub>anthropics/skills</sub>

- **一句话**：Anthropic 文档能力的 Word 生产实现——建 / 改 / 读 .docx 与 .dotx，包括修订痕迹和批注。**注意：source-available，不是开源**（仓库 README 明说这四个文档技能是"source-available, not open source"，license 标注 Proprietary）。
- **什么时候用**：任何 .docx / .dotx 的活：做带目录、标题层级、页码、信笺抬头（letterhead）的正式报告；从 Word 里抽内容重排；插图换图；查找替换；处理 tracked changes 或批注；把内容做成一份"报告/备忘录/信件/模板"；把 .doc 转 .docx。
- **能干什么**：
  - 新建：写 docx-js（npm `docx`）脚本，绕开一堆坑——默认是 A4、表格要双份宽度、`ShadingType.CLEAR` 不能写 SOLID、列表要用 numbering 不能用 `•`、TOC 标题必须用内置 `HeadingLevel`、禁止 `\n`
  - 改已有文件：`unzip` → 改 `word/document.xml` → `zip`（docx-js 打不开已有文件）；`merge_runs.py` 把被 Word 拆碎的 run 合并，让文字能被搜到
  - 修订与批注：`--author` 校验每一处改动都被 `<w:ins>/<w:del>` 包住；`comment.py` 一次写好批注所需的 6 个交叉链接文件
  - 渲染校验：soffice 转 PDF → pdftoppm 出图 → 真的用眼睛看一遍版式
- **外部 Key / 依赖**：无需外部 Key。依赖 npm 包 `docx`（预装）、`pandoc`、LibreOffice（soffice）、`pdftoppm`（Poppler）。**许可证是 Proprietary**：不能提取到服务外、不能再分发、不能做衍生作品
- **自带资源**：`scripts/`——`comment.py`、`accept_changes.py`、`merge_runs.py`、`office/validate.py`（XSD 校验）、`office/soffice.py`，以及批注用的 OOXML 模板 XML 和 39 份 OOXML schema

#### `frontend-design` <sub>anthropics/skills</sub>

- **一句话**：给"做出来别像 AI 默认模板"的前端视觉指导——怎么定调性、怎么选字体、怎么排版面。
- **什么时候用**：要从零做 UI，或要把现有界面重做，需要审美方向 / 排版 / 字体决策时。
- **能干什么**：
  - 先从题材出发定方向（产品是什么、给谁看、页面主要任务是什么），再动手配色
  - 给一份反套路清单：点名当前 AI 生成设计的高频"套路脸"——奶油底 + 衬线大标题 + 砖红点缀、近黑底 + 荧光绿、全圆角同款卡片、每个标题上方的 ALL-CAPS eyebrow、`A · B · C` 式元信息
  - 两遍流程：先出 token 系统（4-6 个命名色 hex + 字体角色 + ASCII 线框），对照 brief 自检"换个题目是否也成立"，再写代码
  - 收尾纪律：只在一处用全力、其余克制；响应式、键盘焦点、减少动效、对比度都要达标；文案也要像设计的一部分那样精简
- **外部 Key / 依赖**：无需外部 Key
- **自带资源**：仅 LICENSE.txt（纯方法论，没有模板或脚本）

#### `internal-comms` <sub>anthropics/skills</sub>

- **一句话**：按公司喜欢的固定格式写内部沟通稿——3P 周报、全员 newsletter、FAQ、状态汇报、事故复盘。
- **什么时候用**：要写 3P（Progress / Plans / Problems）更新、公司 newsletter、FAQ、状态报告、给领导层的更新、项目更新、incident report 时。
- **能干什么**：
  - 先判断稿件类型，再加载 `examples/` 里对应的那份格式指南，照着它的格式、语气、取材要求写
  - 3P 要求 30-60 秒读完，并按团队规模调整颗粒度（小团队写到"修了哪些 bug"，全公司写到"招了 20 人"）
  - newsletter 要求带大量链接：Slack 高互动消息、Google Drive 文档、全员邮件、All-Hands 会议材料
  - 类型对不上现有指南时，先问清格式，不硬套
- **外部 Key / 依赖**：无需外部 Key。想自动取材最好有 Slack / Email / Google Drive / Calendar 的访问权限；没有就直接问用户要素材
- **自带资源**：`examples/` 4 份格式指南——`3p-updates.md`、`company-newsletter.md`、`faq-answers.md`、`general-comms.md`

#### `mcp-builder` <sub>anthropics/skills</sub>

- **一句话**：从零写一个高质量 MCP server 的完整流程指南（Python / FastMCP，或 Node / TypeScript）。
- **什么时候用**：要把某个外部 API / 服务包成 MCP server 给 LLM 用；或要评审 / 重构已有 MCP 工具的设计。
- **能干什么**：
  - 四阶段流程：调研规划 → 实现（工具命名、入参 schema、分页、错误信息）→ review 与测试 → 写 10 道评测题
  - 工具设计准则：统一前缀 + 动词命名（如 `github_create_issue`）、描述精简、返回聚焦可分页、错误信息要直接给出下一步；API 全覆盖和"高层工作流工具"之间要权衡，拿不准时优先保覆盖面
  - 技术选型建议：TypeScript + Streamable HTTP（远程）/ stdio（本地）；给 Zod 和 Pydantic 两套 schema 写法
  - 用 MCP Inspector（`npx @modelcontextprotocol/inspector`）实跑，再用脚本跑评测验证 LLM 真能用
- **外部 Key / 依赖**：引导流程本身无需外部 Key；但跑评测脚本要 `pip install anthropic mcp` 并设置 `ANTHROPIC_API_KEY`；TypeScript 侧需要 Node / npm
- **自带资源**：`reference/` 4 份（`mcp_best_practices.md`、`python_mcp_server.md`、`node_mcp_server.md`、`evaluation.md`）；`scripts/`（`evaluation.py`、`connections.py`、`example_evaluation.xml`、`requirements.txt`）

#### `pdf` <sub>anthropics/skills</sub>

- **一句话**：Anthropic 文档能力的 PDF 生产实现——读、拆、合、转、加水印、加解密、填表单、OCR 的实操手册。**注意：source-available，不是开源**（license 标注 Proprietary）。
- **什么时候用**：任何 .pdf 相关的活：抽文字和表格、多份合并、按页拆分、旋转、加水印、新建 PDF、填 PDF 表单、加解密、抽图、扫描件 OCR。只要用户提到 .pdf 或要产出一个 .pdf，就用它。
- **能干什么**：
  - pypdf 做基本操作（合并 / 拆分 / 旋转 / 读元数据 / 加密 / 水印），pdfplumber 抽文字和表格（能直接转 DataFrame）
  - reportlab 生成 PDF——注意上下标要写 `<sub>` / `<super>` 标签，不能用 Unicode 上下标字符（会变黑方块）
  - 命令行兜底：`pdftotext`、`qpdf`、`pdftk`、`pdfimages`、`pdftoppm`
  - 表单是重头：先 `check_fillable_fields` 判断有没有可填字段；可填走 `fill_fillable_fields`，不可填走坐标标注 + `check_bounding_boxes` 校验 + 可视化验证图
- **外部 Key / 依赖**：无需外部 Key。主要依赖 pypdf、pdfplumber、reportlab（`reference.md` 里还介绍了 pypdfium2 和 JS 的 pdf-lib）；命令行要 qpdf、poppler-utils（`pdftotext`/`pdfimages`）、可选 pdftk；OCR 另需 `pytesseract` + `pdf2image`。**许可证是 Proprietary**，不可再分发
- **自带资源**：`reference.md`（进阶库、批处理、排错）、`forms.md`（表单填写完整流程）、`scripts/` 8 个脚本（表单字段探测、表单结构抽取、坐标校验、验证图生成、标注填充、PDF 转图等）

#### `pptx` <sub>anthropics/skills</sub>

- **一句话**：Anthropic 文档能力的 PowerPoint 生产实现——从零建 deck、按模板改 deck、读内容、做视觉 QA。**注意：source-available，不是开源**（license 标注 Proprietary）。
- **什么时候用**：任何 .pptx / .potx 参与的场合：做幻灯片 / pitch deck；抽取 deck 里的文字（哪怕只是要拿去写邮件或摘要）；改现有 deck；合并或拆分幻灯片；处理模板、版式、演讲者备注、批注。
- **能干什么**：
  - 新建：写 pptxgenjs 脚本，附一长串会静默毁文件的坑（颜色不能带 `#`、阴影 offset 不能负、combo chart 必须同时给 `valAxes` 和 `catAxes`、`letterSpacing` 其实是 `charSpacing`）
  - 改 / 套模板：先用 `thumbnail.py` 出缩略图挑版式，再 unzip 改 `ppt/slides/slideN.xml`；`add_slide.py` 复制页（会做全套登记），`clean.py` 清掉没引用的部件
  - 设计规范：给了 10 套示例配色，要求配色贴合主题、每页必须有视觉元素，禁用标题下划线和装饰色条这类"AI 味"元素
  - 强制 QA 三步：`markitdown` 查内容残留、`validate.py` 查 XML/图表错误、转图逐页看溢出和重叠
- **外部 Key / 依赖**：无需外部 Key。依赖 npm `pptxgenjs`（预装）、`markitdown[pptx]`、Pillow、defusedxml、lxml、LibreOffice、`pdftoppm`；做图标还要 react-icons / react / react-dom / sharp。**许可证是 Proprietary**
- **自带资源**：`scripts/`——`thumbnail.py`、`add_slide.py`、`clean.py`、`office/validate.py`、`office/soffice.py` 及 OOXML 校验器

#### `skill-creator` <sub>anthropics/skills</sub>

- **一句话**：写技能这件事本身的技能——从零起草 skill、跑测试用例、做定量评测、按反馈迭代，最后打包成 `.skill`。
- **什么时候用**：要新建 skill、改已有 skill、给 skill 加 eval、跑 benchmark、或优化 description 让触发更准。
- **能干什么**：
  - 访谈式起草：先问清"这技能让 Claude 会做什么 / 什么时候触发 / 输出长什么样 / 要不要测试用例"，再填 name、description 等字段
  - 评测闭环：同一轮里并行跑"有技能"和"无技能"两组子代理做基线对比，存 `timing.json`，再让 grader 子代理按断言打分（`grading.json` 字段必须是 `text`/`passed`/`evidence`）
  - 一定要先给人生成 eval viewer（`generate_review.py`）看结果，再自己动手改——原文特意强调"BEFORE evaluating inputs yourself"
  - 收尾：`package_skill.py` 打包；description 优化用 `run_loop.py` / `run_eval.py`
- **外部 Key / 依赖**：无需外部 Key；需要 Python 环境跑脚本；并行跑用例和盲测需要 subagents；description 优化需要 Claude Code 的 `claude -p` CLI（在 Claude.ai 上跑不了，原文明确说跳过）
- **自带资源**：`scripts/`（`run_eval`、`run_loop`、`improve_description`、`aggregate_benchmark`、`generate_report`、`package_skill`、`quick_validate` 等）、`agents/` 3 份子代理说明（grader / comparator / analyzer）、`references/schemas.md`、`eval-viewer/`（`generate_review.py` + `viewer.html`）、`assets/eval_review.html`

#### `slack-gif-creator` <sub>anthropics/skills</sub>

- **一句话**：用 PIL 逐帧画动画、导出成符合 Slack 尺寸和体积要求的 GIF（emoji 128×128，消息 480×480）。
- **什么时候用**：用户要"给 Slack 做个 GIF"、"做个表情包动图"、"把这张图做成会动的"。
- **能干什么**：
  - 给出 Slack 的硬性参数：emoji 128×128、消息 480×480、FPS 10-30、颜色数 48-128、emoji 尽量控制在 3 秒内
  - `GIFBuilder` 负责拼帧 + 按 emoji 场景优化保存
  - `validators` 直接判断"这个 gif 能不能发 Slack"
  - 画面质量指导：线条至少 `width=2`、背景用渐变、形状多层叠加；明确不用 emoji 字体（跨平台不可靠）
- **外部 Key / 依赖**：无需外部 Key，也不需要 Slack 账号或 token——原文只负责生成和校验 GIF，未涉及上传。pip 装 `pillow`、`imageio`、`imageio-ffmpeg`、`numpy`
- **自带资源**：`core/`（`gif_builder.py`、`validators.py`、`easing.py`、`frame_composer.py`）、`requirements.txt`

#### `theme-factory` <sub>anthropics/skills</sub>

- **一句话**：10 套现成的配色 + 字体主题，选一套套到你的 PPT / 文档 / HTML 产物上，不够用还能现造一套。
- **什么时候用**：产物已经有内容但"不好看"、需要统一视觉风格、要给整份 deck 换主题时。
- **能干什么**：
  - 先把 `theme-showcase.pdf` 给用户看（只展示，不许改），让用户挑
  - 选定后读 `themes/` 里对应的主题文件，把 hex 和字体配对贯彻到全篇，保证对比度和可读性
  - 10 套主题：Ocean Depths、Sunset Boulevard、Forest Canopy、Modern Minimalist、Golden Hour、Arctic Frost、Desert Rose、Tech Innovation、Botanical Garden、Midnight Galaxy
  - 都不合适就按同样格式现造一套，先给用户确认再套用
- **外部 Key / 依赖**：无需外部 Key
- **自带资源**：`themes/` 10 份主题定义（色板 hex + 标题/正文字体配对 + 适用场景）、`theme-showcase.pdf` 总览图

#### `web-artifacts-builder` <sub>anthropics/skills</sub>

- **一句话**：搭一个 React + Tailwind + shadcn/ui 工程，把复杂交互页面最后打包成单个 HTML 文件当 artifact 用。
- **什么时候用**：要做需要状态管理、路由或大量现成组件的复杂 claude.ai HTML artifact；简单单文件 HTML/JSX 不需要它。
- **能干什么**：
  - `init-artifact.sh` 一键生成工程：React 18 + TypeScript + Vite + Tailwind 3.4.1 + shadcn/ui 主题 + `@/` 路径别名 + 40 多个 shadcn 组件（打在 tar.gz 里）
  - `bundle-artifact.sh` 用 Parcel 打包，把 JS / CSS / 资源全部内联成 `bundle.html`，可单文件分享
  - 明确的反"AI slop"要求：别用满屏居中、紫色渐变、统一圆角、Inter 字体
  - 测试是可选项：先交付给用户看，有问题再测，别为了测试拖慢交付
- **外部 Key / 依赖**：无需外部 Key；需要 Node 18+（脚本会自动检测并 pin Vite 版本），首次运行会装 parcel / html-inline 等打包依赖
- **自带资源**：`scripts/init-artifact.sh`、`scripts/bundle-artifact.sh`、`scripts/shadcn-components.tar.gz`

#### `webapp-testing` <sub>anthropics/skills</sub>

- **一句话**：用 Python Playwright 脚本打开本地网页做测试、截屏、抓 console，并用一个辅助脚本帮你起停服务器。
- **什么时候用**：要验证本地前端功能、复现 UI 问题、截图看看渲染成什么样、抓浏览器日志时。
- **能干什么**：
  - `with_server.py` 托管一个或多个本地服务（比如后端 3000 + 前端 5173），跑完自动清理
  - 分情况处理：静态 HTML 直接读文件找选择器；动态应用先 `networkidle` 再侦察 DOM / 截图，拿到真实选择器再操作
  - 强制 headless chromium，并强调必须先等 `networkidle` 再检查 DOM（否则必然拿到空壳）
  - 配套 examples 演示元素发现、`file://` 静态页自动化、console 日志抓取
- **外部 Key / 依赖**：无需外部 Key；脚本靠 Python Playwright（`from playwright.sync_api import sync_playwright`）驱动 chromium（安装方式原文未说明）。注意：这些脚本要当黑盒用，先跑 `--help`，不要先读源码（源码很大会占满上下文）
- **自带资源**：`scripts/with_server.py`、`examples/`（`element_discovery.py`、`static_html_automation.py`、`console_logging.py`）

#### `xlsx` <sub>anthropics/skills</sub>

- **一句话**：Anthropic 文档能力的 Excel 生产实现——建表改表、写公式、强制重算验证，还带一套财务建模惯例。**注意：source-available，不是开源**（license 标注 Proprietary）。
- **什么时候用**：.xlsx / .xlsm / .xltx / .csv / .tsv 是主要输入或输出时：加列、算公式、格式化、画图、清洗脏数据、表格格式互转；或者用户随口提"我下载里那个 xlsx"。**交付物必须真是一个表格文件才用**——最终要 Word、HTML 报告或数据库管道就别用。
- **能干什么**：
  - 三条路线：openpyxl 建 / 改（公式和格式）、pandas 批量进出数据、markitdown 快速看表（但没有单元格坐标，别拿它规划编辑）
  - 强制 `recalc.py`：openpyxl 写的公式没有缓存值，必须让 LibreOffice 算一遍；`status` 是 `errors_found` 就不许交付
  - 公式避坑：优先 Excel-2007 时代函数；`TEXTJOIN`/`IFS` 等要加 `_xlfn.` 前缀；`XLOOKUP`/`XMATCH`/`FILTER`/`SORT`/`UNIQUE`/`SEQUENCE` 一律禁用（LibreOffice 算不了，会写出 `#NAME?`）
  - 财务模型惯例：蓝字硬编码 / 黑字公式 / 绿字跨表引用 / 红字跨文件引用、负数用括号、百分比存小数、每个假设单独占一格
- **外部 Key / 依赖**：无需外部 Key。依赖 openpyxl、pandas、markitdown（预装）、LibreOffice（soffice）。**许可证是 Proprietary**
- **自带资源**：`scripts/recalc.py`（强制重算并报错）、`scripts/office/validate.py`、`scripts/office/soffice.py` 及 OOXML 校验器

#### `template-skill` <sub>anthropics/skills</sub>

- **一句话**：这不是一个真技能，就是技能模板骨架——frontmatter 里一句 "Replace with description…"，正文只有一行 "`# Insert instructions below`"（全文 140 个字符，正文 30 个字符）。
- **什么时候用**：你要自己写一个新 skill、想照抄最小结构时（位置在仓库 `template/SKILL.md`，不在 `skills/` 下）。
- **能干什么**：
  - 示范一个 skill 的最小结构：YAML frontmatter（`name` + `description`）+ 正文指令
  - 除此之外没有任何能力，装上也不会让 Claude 多会一件事
- **外部 Key / 依赖**：无需外部 Key
- **自带资源**：无

---

### 这一组整体怎么样

- **适合谁**：想给 Claude 装"专业手感"的个人开发者和小团队，尤其是天天跟 Office 文件打交道的人——docx / pdf / pptx / xlsx 这四个是 Anthropic 真正在 Claude 里跑的生产实现，照着学怎么把技能写成可执行流程，参考价值最高（但**是 source-available，不是开源，不能拿去再分发**）。
- **优先级**：第一梯队给 docx / pdf / pptx / xlsx（真能干活）+ skill-creator（想自己造技能就绕不开它）；第二梯队 claude-api、mcp-builder、webapp-testing（写代码的直接收益）；第三梯队是纯方法论/风格类（frontend-design、brand-guidelines、theme-factory、internal-comms、doc-coauthoring、discernment-nudge），装了不吃亏但不装也行。
- **注意**：这个仓库很多技能默认你在一台装好 Node / Python / LibreOffice / pandoc / poppler 的机器上；纯网页版 Claude 上，需要 CLI 或子代理的能力（如 skill-creator 的 description 优化）会直接不可用。`template-skill` 不是技能，别当技能装。


---

## 2. vercel-labs/skills — 技能 CLI 与生态搜索器（3 个 SKILL.md）

### ⚠️ 先讲清楚：这个仓库**不是技能集合**，它本身就是一个 CLI 工具

这是整份目录里最容易被误会的一个。打开 README 第一句就是 "The CLI for the open agent skills ecosystem" —— 它不是一个装满技能让你挑的货架，而是**那个用来装技能的包管理器**。

- **它是什么**：npm 包名就叫 `skills`（当前 1.7.4），提供 `npx skills` 命令。bin 里挂了两个别名：`skills` 和 `add-skill`。MIT 许可，Vercel Labs 出品。
- **它干什么**：从 GitHub / GitLab / Azure Repos / 任意 git URL / 本地目录把技能**装**进你的编码 agent（Claude Code、Codex、Cursor、OpenCode 等数十个，`docs/agents.md` 里有完整表格和各自的安装路径）；也能更新、删除、列出、检查版本。
- **它怎么找技能**：`npx skills find <关键词>` 会去 **skills.sh** 的 `/api/search` 接口搜（一次最多回 20 条），这是"技能生态的搜索器"。所以它的价值在**生态入口**，不在仓库里那几份 markdown 文件。
- **它的源码就是 CLI 本体**：`src/` 下是 `add.ts`、`find.ts`、`use.ts`、`list.ts`、`remove.ts`、`installer.ts`、`source-parser.ts`、`sync.ts`、`agents.ts`、`skill-lock.ts` 等等，`tests/` 下是对应测试。仓库里的 `package.json`、`tsconfig.json`、`bin/cli.mjs`、`build.config.mjs` 都是这个 CLI 自己的构建产物，**跟"你装的技能"没有关系**。
- **它确实会往你机器上写东西**：本地锁文件 `skills-lock.json`（建议提交进仓库）；全局锁文件 `~/.agents/.skill-lock.json`。

**所以对"装技能"这件事有意义的东西只有两样**：

| 位置 | 是什么 | 对用户有没有用 |
|---|---|---|
| `skills/find-skills/SKILL.md` | 唯一一个**面向用户、可安装的真技能** | ✅ 有用（而且 `skills add` 成功后 CLI 会一次性问你要不要装它） |
| `.automations/pr-labeling/`、`.automations/triage/` | 这个仓库**自己的 CI 自动化**（给自家 PR 打标签 / 挑 bug fix） | ❌ 不要装，装了也用不了 |

下面按这个顺序讲。

---

### 一、真·技能：`skills/find-skills`（唯一一个）

#### `find-skills` <sub>vercel-labs/skills</sub>
- **一句话**：让你的 agent 学会"去找技能"——你随口说"有没有办法做 X"，它会先去 skills.sh 生态里搜一圈，而不是硬着头皮自己写。
- **什么时候用**：你说"怎么做 X"而 X 是个可能有现成技能的常见任务；你说"找个技能做 X / 有没有做 X 的技能"；你说"你能不能做 X"而 X 是很专精的能力；你表示想给 agent 扩能力；你想找工具/模板/工作流；你抱怨"要是有个东西帮我搞设计/测试/部署就好了"。
- **能干什么**：
  - **先查榜再搜**：动手 `npx skills find` 之前，先看 [skills.sh](https://skills.sh/) 的安装量排行榜，判断这个领域是不是已经有公认的好技能（举的例子：`vercel-labs/agent-skills` 的 React / Next.js / web design 各 10 万+ 安装，`anthropics/skills` 的前端设计与文档处理也是 10 万+）。
  - **拆需求再搜索**：先把你的话拆成"领域（React / 测试 / 设计 / 部署）+ 具体任务（写测试 / 做动画 / 审 PR）+ 是否常见到值得有技能"，再决定关键词；给了三组对照（"怎么让 React app 更快" → `npx skills find react performance`；"帮我做 PR review" → `npx skills find pr review`；"我要写 changelog" → `npx skills find changelog`），并建议换同义词重试（deploy → deployment / ci-cd）。
  - **带质量门槛地推荐**：明确写了"**不许只凭搜索结果就推荐**"，必须看三条——安装量（**优先 1K+，低于 100 要警惕**）、来源信誉（`vercel-labs` / `anthropics` / `microsoft` 这类官方源更可信）、GitHub star（仓库 <100 star 要存疑）。推荐时要说清：技能名和它做什么、安装量与来源、可复制的安装命令、skills.sh 上的链接。
  - **问你要不要装**：`npx skills add <owner/repo@skill> -g -y`（`-g` 装到用户级全局，`-y` 跳过确认）；搜不到就老实说没找到，并提出直接用通用能力帮你做，或建议 `npx skills init` 自己造一个。另附一张常见分类表（Web 开发 / 测试 / DevOps / 文档 / 代码质量 / 设计 / 生产力）当搜索提示。
- **外部 Key / 依赖**：**无需外部 Key**。需要 Node.js 22.20+ 和 `npx`；需要联网（搜索走 `https://skills.sh/api/search`，可用 `SKILLS_API_URL` 改指向）；安装 GitHub 私有仓库时才需要 `GITHUB_TOKEN`/`GH_TOKEN`（可选，装了能提高 API 限额）。它通常怎么到你手上：`npx skills add` 装成功后 CLI 会弹一次**一次性提示**问要不要装 `find-skills`，装到你刚选的那批 agent 上（排除 replit）；`-y` 或非交互终端跳过，拒绝过也不再问。
- **自带资源**：无（单文件 SKILL.md，没有脚本、模板或子文档）。

---

### 二、仓库自己的自动化：`.automations/`（2 个，**不是给你装的技能**）

这一节请当成"参观"，不是"采购清单"。

先看 `.automations/config.yml`，全文就四行，写明这是 **"Agent Factory settings for vercel-labs/skills"**，owner 是 `github:quuu`。也就是说：这两个目录归 vercel-labs 自己的 PR 流水线用，跑在 Vercel 的 "Factory" 上（`docs/factory-board.md` 说明：两个 workflow 共用注入的 `board` 工具和 `experimental-board` skill，共享空间名 `pr-quality`；部署方通过 `EXPERIMENTAL_BOARD_URL` 和 OIDC 选服务，**仓库配置里没有任何凭据或 board URL**）。

它们之所以也是 `SKILL.md`，是因为它们各自被自己的 `automation.md` 在运行时加载 —— 是"自动化流程的提示词"，不是"给你装进 agent 的技能"。

#### `.automations/pr-labeling` <sub>vercel-labs/skills</sub> → `pr-labeling`
- **一句话**（仓库内部自动化）：给新开/更新的 PR 按改动类型打一个标签。
- **什么时候用**：只在 `github.pull_request.opened` / `github.pull_request.synchronize` 事件触发时，由 Vercel Factory 自动跑。
- **能干什么**：
  - 用 `read_pull_request` 读标题、描述、现有标签、改动 patch；若 head commit 已经不是触发那次的了，**直接停**，让更新的运行去分类。
  - 已有 `bug` / `documentation` / `enhancement` 就停手（尊重维护者或上一轮的选择）。
  - 从 patch 判断主改动类型选一个：`documentation`（只改文档/示例/说明，不动 CLI 行为）、`bug`（修正既有 CLI 行为，含安装、更新检查、源解析、agent 探测、安全检查）、`enhancement`（新增或有意扩展 CLI 行为、命令、agent 支持、集成支持）。
  - patch 被截断 / 混杂 / 不足以有把握时**不打标签**，且**不许只看标题猜**；最后只调用一次 `add_label`。
- **外部 Key / 依赖**：不需要你配任何 Key——它依赖 Factory 注入的 `read_pull_request` 和 `add_label` 两个 GitHub 动作（见 `automation.md` 的 `actions:` 声明），以及注入的 `experimental-board` / `board` 工具。
- **自带资源**：无。**你不能装它**——你的机器上没有这些注入工具，装进 agent 只会得到一条永远失败的操作指令。

#### `.automations/triage` <sub>vercel-labs/skills</sub> → `triage`
- **一句话**（仓库内部自动化）：从新 PR 里挑出"真的在修 bug、且有像样回归测试"的那些，打 `triage:bug-fix` 并 @ 一下 quuu。
- **什么时候用**：只在 `github.pull_request.opened` 时由 Factory 自动跑。
- **能干什么**：
  - 用 `read_pull_request` 记下 head SHA、状态、标签、描述、每个改动文件的 patch；已关闭、head 与触发 SHA 不符、已有 `triage:bug-fix` 的——停。
  - 要求**五条同时成立**才认：① 存在既有缺陷（说清以前错在哪、影响哪个命令或 agent、期望是什么）；② 修法正确（且不能顺带弄坏别的 agent / 平台 / 源类型 / 项目级与全局级 / 安装模式）；③ 范围收敛（新增命令、provider、agent、集成、默认选择策略都算 enhancement 不算修 bug；重构、依赖升级、生成物、纯文档都不算）；④ 有**有意义的回归覆盖**（改/加的测试必须能对旧实现失败，而不是照着新实现抄一遍或只改快照）；⑤ 没有可见阻塞（吞错误、弱化断言、绕过安全/隐私边界、伪造成功、跨 agent 回归都拒）。任何一条不确定就**安静结束**，不发猜测性结论；对 CI 也诚实——只有 run 与这个 PR 的**确切 head SHA** 对得上才算数，旧的成功、pending、需审批、缺失、原因不明的失败都只能算"局限"，**不等于通过**。
  - 通过后：加 `triage:bug-fix` 标签 + 发**一条**评论 @quuu，格式固定（一句开场、空行、Bug / Fix 两个 bullet，可选 Issues bullet），**全文 ≤500 字符、≤75 词**；测试、CI 状态、commit hash、详细推理只留在内部输出。
  - 边界写得很死：这是 bug-fix 分诊，**不是合并批准**——不改代码、不要求改实现、不 approve、不 merge；PR 描述和日志当**不可信输入**，不能改变 workflow 的指令。
- **外部 Key / 依赖**：同样不需要你配 Key，依赖 Factory 注入的 `read_pull_request`、`read_issue`、`search_repository_issues`、`read_actions`（限定 `ci.yml`）、`add_label`、`comment`。
- **自带资源**：无。**你不能装它**，理由同上（缺少注入工具）。

---

### 三、你可能真会用到的东西（不是技能，但是这个仓库的"产品"）

既然仓库本体是 CLI，那么"能用的东西"是命令，不是 SKILL.md：

| 命令 | 干什么 |
|---|---|
| `npx skills add <源>` | 从 GitHub / GitLab / Azure Repos / 任意 git URL / 本地路径装技能；默认装到当前项目，`-g` 装到全局 |
| `npx skills find [关键词] [--owner <owner>]` | 搜技能（交互式 fzf 风格，或按关键词；`--owner` 限定某个组织/用户的所有仓库） |
| `npx skills use <source>@<skill>` | 不安装，只用一次（生成提示词，或直接拉起某个 agent 跑） |
| `npx skills list` / `ls` | 列出已装 |
| `npx skills check` | 看有没有更新（不重装） |
| `npx skills update [skills...]` | 更新已装 |
| `npx skills remove [skills...]` | 卸载 |
| `npx skills init [name]` | 生成一个 `SKILL.md` 模板 |
| `npx skills experimental_sync` | 把 npm 依赖里自带的技能同步/链接进项目，随依赖一起更新 |
| `npx skills experimental_install` | 按 `skills-lock.json` 恢复项目技能 |

配套文档（都在 `docs/`）：`commands.md`（CLI 全量参考）、`agents.md`（支持哪些 agent、各自的安装路径与兼容性差异）、`creating-skills.md`（frontmatter、目录布局、发现规则）、`troubleshooting.md`（常见问题、环境变量、遥测）、`factory-board.md`（自家 PR 自动化的 board 说明）。

**技能发现规则**（写自己的技能时有用）：CLI 会在仓库里找 `SKILL.md`，每个容器目录最多往下走三层，覆盖 `skills/<name>/`、`skills/<category>/<name>/`、`skills/<category>/<category>/<name>/` 三种布局；浅层发现的 `SKILL.md` 会"遮蔽"更深层的；要发现 `examples/`、`tests/` 这类容器目录之外的，得加 `--full-depth`。

**环境变量**（`docs/troubleshooting.md` 有表）：`INSTALL_INTERNAL_SKILLS=1`（显示并安装标了 `metadata.internal: true` 的内部技能）、`DISABLE_TELEMETRY=1` / `DO_NOT_TRACK=1`（关遥测）、`GITHUB_TOKEN` / `GH_TOKEN`（可选的 GitHub API token）、`SKILLS_API_URL` / `SKILLS_DOWNLOAD_URL`（改搜索与下载端点）、`SKILLS_CLONE_CONCURRENCY`、`SKILLS_CLONE_TIMEOUT_MS`、`SKILLS_DEBUG`。

---

### 小结（适合谁 / 怎么用 / 优先级）

- **适合谁**：想给自己的编码 agent 装/管技能的人，以及想知道"生态里有没有现成轮子"的人。**不适合**想找一堆现成技能直接用的人——这里的技能就 1 个。
- **怎么用**：装好 Node 22.20+ → 项目里跑 `npx skills add <owner/repo>` 选技能和 agent（目标仓库用 `-g` 全局）→ 顺便把 `find-skills` 装上，之后就能自然地说"帮我找个做 X 的技能"；想找东西直接 `npx skills find <词>`。
- **优先级**：`find-skills` 值得装（它是生态的入口，装一次以后搜索就是对话式的）；CLI 本身是**基础设施级**的，值得放进工具箱；`.automations/` 两个**不要碰**，那是人家仓库的 CI，装进你的 agent 只会得到一堆永远失败的 `read_pull_request` 调用。
- **一句话记住**：这个仓库的价值在 `npx skills` 这条命令和它背后的 skills.sh 生态，不在仓库里那 3 份 SKILL.md。


---

## 3. Jensen-Yao/agents-skills — 167 个技能的个人库（最大）

> **这是六个仓库里体量最大的一个**：167 个 active 技能，按仓库自带的 10 个分类整理（分类名与数量照 `README.md`「技能索引」）。
> 每个技能在原有一句话基础上展开了 5 个字段。仓库里每个技能都标注了是 **指导型**（教 agent 怎么用某个库/API）还是 **可执行型**（自带脚本能跑）——这个区别对"装完能不能直接用"影响很大。


### 学术文献（19 个）

#### `cnki-advanced-search` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 在知网做「高级检索」，用字段组合把结果筛干净。
- **什么时候用**：只知道一堆条件（某作者 + 某刊 + 2020 年以后 + 只要北大核心），普通关键词搜索搜不准的时候。
- **能干什么**：
  - 按作者、标题、期刊、日期范围、来源类别（SCI / EI / CSSCI / 北大核心）组合过滤
  - 一次 `evaluate_script` 同时提交检索并抓回结果，不用 `wait_for` 等页面
  - 给出旧版检索界面的可用选择器（表单字段、来源类型勾选框、结果行）
- **外部 Key / 依赖**：无需 API Key。需要 Chrome DevTools MCP server + 已打开的 Chrome；原文未明确要求登录（但遇到滑块验证码需人工完成）。
- **自带资源**：无（只有 `agents/openai.yaml` 元数据）

#### `cnki-download` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 从知网论文详情页触发 PDF / CAJ 下载。
- **什么时候用**：已经定位到某篇论文，要把它下到本地的时候。
- **能干什么**：
  - 自动判断页面给了 PDF 还是 CAJ 下载链接，触发对应格式
  - 先检查登录态（识别 `icon-notlogged` 元素），未登录直接告诉你「先去登录」
  - 识别滑块验证码并中止操作，提示人工过验证
- **外部 Key / 依赖**：无需 API Key，但**强依赖浏览器登录态**——原文写明「User must be logged in to CNKI with download permissions」，且下载权限取决于你所在机构的订阅。另需 Chrome DevTools MCP。
- **自带资源**：无

#### `cnki-export` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型（带一个 Python 脚本）—— 把知网论文导出并推进 Zotero，或存成 RIS 引文文件。
- **什么时候用**：搜到一批论文想直接进文献库，或者要把引文数据交给别的工具时。
- **能干什么**：
  - 单篇：从论文详情页取导出数据
  - 批量：从**检索结果页**直接调导出接口，不用逐篇点进去（原文强调这是首选，省 ~3 次调用/篇）
  - 走 Zotero 本地 Connector API（`127.0.0.1:23119`）写入，或输出 RIS 文件
- **外部 Key / 依赖**：无需 API Key / 无需 Zotero 账号密码。但**必须本机开着 Zotero 桌面版**（脚本打的是它 127.0.0.1:23119 的本地接口，返回 0 即 Zotero 没启动）；知网侧需浏览器登录态 + Chrome DevTools MCP。脚本仅用 Python 标准库。
- **自带资源**：`scripts/push_to_zotero.py`（推 Zotero，带幂等 sessionID）

#### `cnki-journal-index` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 查某本期刊被哪些数据库收录、什么级别、影响因子类指标。
- **什么时候用**：纠结「这本刊是不是核心 / 能不能算成果」的时候。
- **能干什么**：
  - 抓期刊详情页的「该刊被以下数据库收录」区块
  - 打开「统计与评价」标签页取评价数据
  - 整理成中文可读的期刊名 + 收录库 + 指标清单
- **外部 Key / 依赖**：无需 API Key，需 Chrome DevTools MCP；部分评价数据页需登录/机构权限（原文未逐项写明，遇到时按提示登录）。
- **自带资源**：无

#### `cnki-journal-search` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 按刊名 / ISSN / CN 号 / 主办单位找期刊。
- **什么时候用**：想找某本特定期刊，或想看看某个单位主办了哪些刊。
- **能干什么**：
  - 四种检索入口（刊名、ISSN、CN、主办单位）
  - 一次脚本调用同时检索并解析结果（刊名、被引、下载量等）
  - 输出带链接的期刊列表，方便接着点进期刊详情
- **外部 Key / 依赖**：无需 API Key，需 Chrome DevTools MCP；原文未明确要求登录。
- **自带资源**：无

#### `cnki-journal-toc` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 浏览某刊某期的目录，必要时把「原版封面+目录」PDF 下下来。
- **什么时候用**：要快速扫一期里有哪些文章，或者要交「期刊封面目录页」扫描件当证明材料。
- **能干什么**：
  - 在期刊详情页选期次，一次脚本抓出该期全部论文
  - 按「刊名 年份 期号」的格式组织输出
  - 走「原版目录浏览」进阅读器，再点下载拿封面+目录 PDF
- **外部 Key / 依赖**：无需 API Key，需 Chrome DevTools MCP；**下载原版目录 PDF 需要登录**（原文提示下载失败就让用户登录）。
- **自带资源**：无

#### `cnki-navigate-pages` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 知网检索结果页的翻页与排序小工具。
- **什么时候用**：搜完发现第一页不够看，或者想按被引/下载量重排的时候。
- **能干什么**：
  - 上一页 / 下一页 / 跳到指定页码
  - 按相关度、日期、被引、下载、综合排序（底层参数 `relevance`/`date`/`CF`/`DFR`/`ZH`）
  - 全部操作只用一次 `evaluate_script`，不截图不等加载
- **外部 Key / 依赖**：无需 API Key，需 Chrome DevTools MCP + 当前页已是知网结果页。
- **自带资源**：无

#### `cnki-paper-detail` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 把一篇知网论文页的元数据榨干。
- **什么时候用**：需要某篇论文的完整著录信息（写参考文献、做文献卡片）时。
- **能干什么**：
  - 提取标题、作者、机构、摘要、关键词、基金、分类号
  - 识别「网络首发」状态并标注
  - 有基于快照（snapshot）的兜底解析路径，选择器失效时不至于全崩
- **外部 Key / 依赖**：无需 API Key，需 Chrome DevTools MCP；验证码需人工过。
- **自带资源**：无

#### `cnki-parse-results` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 把当前知网结果页解析成结构化论文列表。
- **什么时候用**：已经搜出结果，需要干净的标题/作者/期刊/日期/被引表格时。
- **能干什么**：
  - 逐行抽取标题、作者、来源、日期、被引、下载量、链接
  - 校验当前页确实是知网结果页（URL 含 `kns.cnki.net` 且有「N 条结果」）
  - 提供备用的快照解析方案
- **外部 Key / 依赖**：无需 API Key，需 Chrome DevTools MCP + 停在结果页。
- **自带资源**：无

#### `cnki-search` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 知网最基础的关键词检索。
- **什么时候用**：只想按主题词找论文，不需要复杂过滤的时候。
- **能干什么**：
  - 关键词检索并解析结果（标题、作者、来源、日期、被引、下载、链接）
  - 直接拿结果里的 `href` 跳转（原文提醒：别点链接，会开新标签页白烧 3 次调用）
  - 结果页可直接批量导出到 Zotero
- **外部 Key / 依赖**：无需 API Key，需 Chrome DevTools MCP；原文未明确要求登录（下载/导出才需要）。
- **自带资源**：无

#### `cnki-researcher` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 上面 10 个 CNKI 技能的「总调度」，把零散动作串成一条文献研究流水线。
- **什么时候用**：任务不是单一动作，而是「搜一批 → 挑几篇 → 看细节 → 下 PDF → 导 Zotero」这种组合流程时。
- **能干什么**：
  - 按任务路由到 `$cnki-search` / `$cnki-advanced-search` / `$cnki-paper-detail` / `$cnki-download` / `$cnki-export` / `$cnki-journal-*` / `$cnki-navigate-pages`
  - 处理机构登录与校外访问（原文写死走「机构登录 → 国防科技大学 NUDT」，**换单位必须改这一条**）
  - 规定浏览器操作节奏：不快速连翻页，遇到登录/权限/验证码立刻停
  - 统一输出规范：总数、页码标记、编号标题、作者、来源、日期、被引、下载、URL
- **外部 Key / 依赖**：无需 API Key；需 Chrome DevTools MCP（原文要求先确认环境里有这个 server，没有就明确说跑不了）；**知网账号/密码不由技能保存**，要用户自己在 Chrome 里登。
- **自带资源**：无（只有 `agents/openai.yaml`）

#### `scholar-ppt-cn` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：中文学术 PPT 的「先规划再出片」工作流——先做逐页规划表和视觉族蓝图，确认样板后才生成可编辑 PPTX（指导型：本质是一套流程规范和 20 份规则文件，出片靠宿主环境的工具链）。
- **什么时候用**：用户要把论文、学位论文、开题/答辩报告、组会 PPT 做成中文版；或给一份参考模板/截图/视觉稿要求"照着这个风格重建"；或对已成品说"第 3 页图太小 / 字体不对，修一下"。
- **能干什么**：
  - 先产出「制作规划表」，把每页映射到叙事章节、源素材、核心信息、版式原型，让用户在动手前就能否掉方向。
  - 产出「原型族 + 变体蓝图」，锁定一套视觉系统后再扩展成整本，避免每页各画一套骨架。
  - 可从用户给的模板抽取 Template DNA（版式节奏、字体、配色、可复用页面结构）。
  - 交付可编辑 PPTX，并做渲染预览 + 对比拼图 QA + 可编辑性重建。
- **外部 Key / 依赖**：无需外部 Key。纯模板路线完全离线；只有走「AI 生图做整页样板」这条路线时才需要宿主环境自带的图像生成模型（原文只写"image model 充当视觉设计师"，未指定具体服务商与 Key）。
- **自带资源**：`assets/templates/scholar-ppt-cn-reference-template.pptx`（默认参考模板）、`references/` 下 20 份规则文件（规划表、蓝图、模板 DNA、CJK 排版、可见文字过滤、图片生成效率、代码本地执行等）、`agents/openai.yaml`。

#### `sci-extract` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：论文精读三件套——AI 直接按 Heilmeier 框架分析、脚本抽 6 个结构化字段、脚本自动抓论文原文落盘（可执行型 + 指导型，两种脚本都在目录里）。
- **什么时候用**：用户甩来论文 PDF、arXiv 链接、DOI 或一段论文文本，要求"读一下 / 总结 / 提取要点 / 批判性评审"；或是综述、系统综述、元分析、范围综述要按"范围地图"方式拆。
- **能干什么**：
  - Mode 1：不跑脚本，AI 按改良版 Heilmeier 七问（问题、方法、结果、创新、应用、局限等）直接写分析；综述类换成范围地图模板。
  - Mode 2：`extract_core_insights.py` 从本地 PDF 抽 6 个字段（研究问题/方法/关键结果/创新/应用/局限）+ 置信度，支持 JSON/Markdown/CSV、批量并行、`--paper-type auto|research|review`。
  - Mode 3：`harvest_paper.py` 按 DOI/PMID/PMCID/arXiv ID 或标题抓全文，落成一个目录：`raw.md`、`metadata.json`、`fulltext.xml`、`figures/`、`paper.pdf`，并留 `_analysis_prompt.md` 给你写 `analysis.md`。
  - 抓取过程如实标注"哪些部分拿不到"（付费墙论文只给元数据 + 摘要 + 可达 PDF）。
- **外部 Key / 依赖**：Mode 3 抓取**不需要 LLM API Key**（原文明确：脚本不调用任何语言模型，"你"就是那个模型，也不应向用户索要 API Key）。Crossref / OpenAlex / Semantic Scholar / PubMed / Europe PMC / arXiv 无需任何 Key；Web of Science、Scopus、Springer、Elsevier、IEEE 的 Key 为**可选**，配在 `~/.aut_sci_write/.env`（`python harvest_paper.py --sources` 可查已配了哪些）。Python 依赖：Mode 2 需 `PyMuPDF`、`pdfplumber`、`numpy`；Mode 3 需 `requests`，裁图时用 `PyMuPDF`。
- **自带资源**：`extract_core_insights.py`、`harvest_paper.py`、`harvester/` 包（metadata / identify / fulltext / jats / figures / oa_package / render / env 等模块）、`README.md`、`_meta.json`。

#### `sci-figure` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：从学术 PDF 里把图、子图抠出来存成高清 PNG 的命令行工具（可执行型，装完就有 `sh-sci-fig` 命令）。
- **什么时候用**：用户说"提取图片 / 截取文献图片 / 提取子图 / 要补充图 / 方案图 / 示意图 / extract figure"；论文写作、答辩 PPT 需要原图。
- **能干什么**：
  - 认 Fig / Figure / Scheme / Chart / Supplementary Figure / Extended Data Figure 及中文"图 / 方案 / 示意图 / 附图 / 补充图"。
  - 子图标签支持 `(a)` `(A)` `a)` `(i)` `(1)` `a.` 等多种写法，可按子图单独出图。
  - 三种引擎按优先级回退：PyMuPDF 原生抠图 → 连通域 CV（矢量图/彩色图）→ 图注锚定兜底；`--strategy hybrid|native|cv` 可选。
  - 可配 DPI（默认 600）、输出 png/jpg、自动去白边、加标注框、渲染整页对照、手动 `--bbox` 纠正。
- **外部 Key / 依赖**：无需外部 Key。需先 `pip install -e .` 装包；**必须装 Tesseract OCR**（Windows `winget install UB-Mannheim.TesseractOCR` / Linux `apt install tesseract-ocr` / macOS `brew install tesseract`）。Python 依赖：`pdfplumber`、`PyMuPDF`、`opencv-python`、`Pillow`、`pytesseract`、`numpy`。注意 License 是 **AGPL-3.0-or-later**（因链接 PyMuPDF）。
- **自带资源**：`sci_figure/` 源码包（pdf_parser / region_detector / column_detector / caption_detector / subfigure_splitter / native_extractor / annotator / cli）、`scripts/extract_figure.py`、`tests/`、`setup.py`、`requirements.txt`、`README.md`、`README_CN.md`、`CHANGELOG.md`、`_meta.json`。

#### `sci-html` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：把论文/大纲/Markdown 变成浏览器里能翻页的学术幻灯片网页（可执行型，带 `generate_html_deck.py` 和完整前端模板）。
- **什么时候用**：用户要"HTML 版幻灯片 / 网页报告 / 可点击的 paper 汇报 / 离线 HTML deck"；或明确不要 .pptx 而要能发链接的网页。
- **能干什么**：
  - `paper` 子命令一条龙：PDF → 结构化洞见 → 抽图 → 生成 `index.html` + `deck.json` + `assets/style.css` + `assets/deck.js` + `assets/figures/`（固定输出一个目录，不做单文件 HTML）。
  - 自动判论文类型：原创研究强调问题/方法/结果/创新/应用/局限；综述强调范围/分类/证据基础/共识/争议/空白/未来方向，也可 `--paper-type` 强制。
  - 内置导航：点击、左右方向键、空格、Home/End、F 全屏、URL hash 定位（`#/3`）、章节页（`layout: section` + `subtitle: 01`）。
  - 排版细节自动化：长标题自动缩放、少要点页去序号、稀疏页补主题纹理、`**重点**` / `==重点==` 变红强调。
- **外部 Key / 依赖**：无需外部 Key（原文明确：不要为本技能创建 skill 级 `.env`，除用户自装的可选依赖外全程离线）。核心出片只用 Python 标准库；`paper` 流程需 `PyMuPDF`、`pdfplumber`、`numpy`、`opencv-python`、`Pillow`、`pytesseract`，Tesseract OCR 为可选（只影响子图 OCR）。
- **自带资源**：`scripts/generate_html_deck.py`、`src/sci_html/`（models / parser / renderer / paper_pipeline / integrations）、`templates/`（index.html.j2、deck.js）、`themes/`（academic-blue.css、journal-dark.css）、`examples/`（样例大纲与幻灯片）、`tests/`、`pyproject.toml`、`requirements.txt`。

#### `sci-ppt` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：论文 PDF 直接变学术答辩 PPTX，自带图片高清提取、LaTeX 公式转透明 PNG、中英双语排版（可执行型，`aut_sci_ppt` 是可 import 的包）。
- **什么时候用**：用户要"做 PPT / 生成 PPT / 论文转 PPT / 答辩 PPT / 开题报告 / 组会汇报 / 推免汇报"，且确实要 `.pptx` 文件（要网页版请转 `sci-html`）。
- **能干什么**：
  - `auto_generate_ppt("paper.pdf", author=..., advisor=...)` 一条命令走完 PDF 解析 → 抽图 → 排版 → 出 pptx；也支持纯文本大纲 `create_ppt(...)`。
  - 固定版式语言：章节用 `1. 标题`、要点用 `- 要点`，主色 `#1E3A5F`、创新点红 `#EE0000`，禁止文字溢出与图压字。
  - 图片走 600 DPI 高清提取且最小宽度 ≥300px，明确禁止从 PDF 二进制流硬抠低清位图。
  - LaTeX 公式异步渲染成**透明背景** PNG 再嵌入。
- **外部 Key / 依赖**：**PDF→PPT 流程不需要 API Key**（原文明确）；但渲染公式需要联网访问 `codecogs.com`。Python 依赖：`python-pptx`、`pyyaml`、`PyMuPDF`、`Pillow`、`matplotlib`、`requests`、`lxml`。
- **自带资源**：`src/aut_sci_ppt/`（academic_parser / pdf_extractor / layout_engine / generator / paginator / parser / interactive / templates）、`references/rules.md`（13 条排版与行为铁律）、`references/style-spec.md`、`pyproject.toml`、`requirements.txt`。

#### `sci-review` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：写文献综述、写审稿回复（rebuttal）的固定套路，外加一个能自动查结构和查禁用词的校验脚本（指导型为主 + 1 个可执行校验器）。
- **什么时候用**：要起草综述大纲、提炼研究空白、回应审稿人意见、写 response letter、修学术写作语气。
- **能干什么**：
  - 综述四段式：Introduction（背景/问题/空白/贡献）→ Methodology（分类体系、方法类别、对比维度）→ Challenges（现象→成因→方向）→ Conclusion（洞见 + 路线图）。
  - Rebuttal 三条目：复述审稿人关切（中立）→ 用证据回应 → 给出具体改稿计划（章节/表/图/附录/实验）。
  - 语气纠偏：禁用"reviewer misunderstood / the reviewer is wrong / obviously"一类对抗性措辞，改成"我们将在文中澄清"。
  - `validate_review_output.py` 按 `--case literature-review|rebuttal` 校验必需章节名与禁用词，并有 `tests/golden_cases.json` 金标用例（定义"应具备哪些特征"而非逐字复刻）。
- **外部 Key / 依赖**：无需外部 Key，纯本地文本工作。校验脚本用 Python 标准库。
- **自带资源**：`scripts/validate_review_output.py`、`tests/golden_cases.json`、`templates/literature-review-template.md`、`templates/rebuttal-template.md`、`references/`（section-guide、rebuttal-guide、word-choice）、`_meta.json`。

#### `sci-search` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：一次查询同时打七个学术库（arXiv / PubMed / Web of Science / Springer Nature / Scopus / Semantic Scholar / OpenAlex），顺手补上期刊分区和影响因子（可执行型，`sci_search.py`）。
- **什么时候用**：用户要"搜文献 / 查文献 / 找论文 / 最新论文 / 影响因子 / 期刊分区 / SCI 检索"，或要按影响因子筛高质量论文。
- **能干什么**：
  - 七源聚合搜索，`--source` 可单选某一库或 `all`；WoS 返回 SCI 收录 + 被引次数，Scopus 返回 cited-by。
  - 自动补 JCR 分区与 IF，并高亮 Nature / Science / Advanced Materials 这类顶刊。
  - 支持 `--limit` 控制条数、`--output results.md` 导出 Markdown 报告。
  - 内置 `journal_db.json` 期刊指标库（当前 16 本，含 JCR 分区、IF、中科院分区、出版社）。
- **外部 Key / 依赖**：**Key 全部可选，缺 Key 只影响对应源**。配在统一文件 `~/.aut_sci_write/.env`：`WOS_API_KEY`（Clarivate WoS Starter，免费申请）、`SPRINGER_API_KEY` / `SPRINGER_OA_API_KEY`（OA 会回退用前者）、`SCOPUS_API_KEY`（Elsevier，需机构权限）、`SEMANTIC_SCHOLAR_API_KEY`（可选，仅为提额）、`NCBI_API_KEY` / `NCBI_EMAIL` / `NCBI_TOOL`（PubMed 提额）、`OPENALEX_EMAIL`（polite pool）、`ZOTERO_USER_ID` / `ZOTERO_API_KEY`（Zotero 联动）。arXiv / PubMed / Semantic Scholar / OpenAlex **无需 Key**。
- **自带资源**：`sci_search.py`、`journal_db.json`、`_meta.json`。

#### `sci-zotero` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：用命令行操作你的 Zotero 文献库——按 DOI/ISBN 加条目、批量补开放获取 PDF、列条目、搜条目（可执行型，`zotero.py`）。
- **什么时候用**：用户说"同步 Zotero / 把这篇加到文献库 / 帮我找这些条目的 PDF / 在 Zotero 里搜一下"。
- **能干什么**：
  - `items` 列出顶层条目；`add-doi [DOI]` / `add-isbn [ISBN]` 按标识符新增文献。
  - `fetch-pdfs` 自动找开放获取 PDF 并挂到对应条目上。
  - 支持个人库（User ID）与群组库（Group ID）两种目标。
- **外部 Key / 依赖**：**必须要 Key**——`ZOTERO_API_KEY` 加上 `ZOTERO_USER_ID`（个人库）或 `ZOTERO_GROUP_ID`（群组库），统一写在 `~/.aut_sci_write/.env`。
- **自带资源**：`zotero.py`、`_meta.json`。


### 训练与推理（39 个）

#### `huggingface-accelerate` <sub>Jensen-Yao/agents-skills</sub>（仓库目录 `accelerate`）
- **一句话**：指导型 —— 给任意 PyTorch 训练脚本加分布式支持，官方说法是「4 行代码 + 一条命令」。
- **什么时候用**：单卡脚本想直接跑多卡/多机，又不想为 DDP、FSDP、DeepSpeed 各写一套代码时。
- **能干什么**：
  - 用 `Accelerator()` 统一设备放置，省掉到处写 `.to('cuda')`
  - 混合精度：FP16（带梯度缩放）、BF16（更稳）、FP8（H100+）
  - 交互式 `accelerate config` 选 DeepSpeed ZeRO-2/3 或 FSDP，代码一行不改
  - 梯度累积、按机器/卡数自动切分 dataloader
- **外部 Key / 依赖**：无需外部 Key。需 `pip install accelerate`（+ `torch`，要走 DeepSpeed 还要 `pip install deepspeed`）；多卡/多机要有对应 GPU 与网络环境。
- **自带资源**：`references/` 3 篇（自定义插件、性能调优、Megatron 集成）

#### `awq-quantization` <sub>Jensen-Yao/agents-skills</sub>（仓库目录 `awq`）
- **一句话**：指导型 —— 激活感知的 4-bit 权重量化，号称 3 倍加速、精度掉得很少（MLSys 2024 最佳论文）。
- **什么时候用**：显存不够想塞 7B–70B，或者要上 vLLM 做生产推理时。
- **能干什么**：
  - 直接加载别人量化好的 AWQ 权重（`AutoAWQForCausalLM`）
  - 量化自己的模型：`quant_config` 配置 + pileval 校准集，存盘后可复用
  - 四种 kernel 后端选型：GEMM（批推理）、GEMV（单 token）、Marlin（Ampere+ 最快）、ExLlamaV2（AMD 兼容）
  - 与 HF Transformers / vLLM 集成，vLLM 能自动识别 AWQ 权重
- **外部 Key / 依赖**：无需外部 Key。需 `pip install autoawq`（要 CUDA kernel 加速加 `autoawq[kernels]`）；**硬性要求 CUDA 11.8+、算力 7.5+**，Marlin 需 Ampere 及以上（A100/H100/RTX 40 系）。HF 上的量化模型多为公开权重，原文未提需 token。
- **自带资源**：`references/advanced-usage.md`、`references/troubleshooting.md`

#### `axolotl` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 用 YAML 配置微调 LLM 的「专家级说明书」。
- **什么时候用**：不想手写训练循环，想改 YAML 就开跑 LoRA/QLoRA/DPO 的时候。
- **能干什么**：
  - YAML 驱动的全流程配置（模型、数据集、并行、优化器、保存）
  - 支持 100+ 模型、LoRA/QLoRA、DPO/KTO/ORPO/GRPO、多模态
  - 给出常见 YAML 片段与 dataset 格式约定
  - 说明多卡/长序列时 `context_parallel_size` 该取多少这类容易踩的配置
- **外部 Key / 依赖**：无需外部 Key。需装 `axolotl`、`torch`、`transformers`、`datasets`、`peft`、`accelerate`、`deepspeed`；要 GPU（原文未给具体显存档位）。**下载 Llama 等受限模型时需要 HF 账号登录**（原文未明写，按 HF 规则来）。
- **自带资源**：`references/` 4 篇（api / dataset-formats / index / other）；`scripts/`、`assets/` 仅为空占位，**没有可执行脚本**

#### `quantizing-models-bitsandbytes` <sub>Jensen-Yao/agents-skills</sub>（仓库目录 `bitsandbytes`）
- **一句话**：指导型 —— 用 bitsandbytes 把 LLM 压到 8-bit 或 4-bit，显存省 50–75%。
- **什么时候用**：单张消费级卡想跑大模型，或者要 QLoRA 微调的时候。
- **能干什么**：
  - 一行 `load_in_4bit=True` / `load_in_8bit=True` 装模型
  - 4-bit 格式选择：NF4（QLoRA 常用）、FP4；8-bit 用 INT8
  - QLoRA 微调：4-bit 底座 + LoRA，可训练参数约 0.06%
  - 8-bit 优化器（`PagedAdamW8bit` 等）省优化器状态显存
- **外部 Key / 依赖**：无需外部 Key。需 `pip install bitsandbytes transformers accelerate`；**CUDA 版本要和 bitsandbytes 编译版本对上**（原文专门列了 CUDA 不匹配的排查）；纯 CPU 跑不了，需要 NVIDIA GPU。
- **自带资源**：`references/` 3 篇（显存优化、QLoRA 训练、量化格式）

#### `deepspeed` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— DeepSpeed 分布式训练的「大部头参考书」（原文 14 万字符，是全批最长的）。
- **什么时候用**：训练规模上了一定量级，要 ZeRO 分片、流水线并行、CPU/NVMe offload 的时候。
- **能干什么**：
  - ZeRO 0/1/2/3 各阶段与参数/梯度/优化器状态分片、offload 配置
  - 混合精度 FP16 / BF16 / FP8、1-bit Adam、稀疏注意力
  - 通信优化（bucket 大小、overlap）、梯度累积、课程学习
  - 配套工具：Flops Profiler、通信 logger、Monitor（TensorBoard / W&B / Comet / CSV）
- **外部 Key / 依赖**：训练本身无需 Key。需 `deepspeed`、`torch`、`transformers`、`accelerate` + 多卡 NVIDIA 环境。**只有日志才涉及外部账号**：想写 W&B 要装 `wandb` 并登录，写 Comet 要 Comet API key（原文明确提醒「别把 Comet key 写进代码」）。用 BF16 要 A100 这类硬件支持。
- **自带资源**：`references/` 9 篇官方文档摘编（配置 JSON、MoE、MoS 蒸馏、Flops Profiler、监控、MII 等）

#### `optimizing-attention-flash` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜教你把注意力换成 Flash Attention——长序列下 2–4 倍加速、显存降到原来的 1/10 到 1/20。
- **什么时候用**：训练或推理 transformer 且序列超过 512 token；标准注意力已经 OOM；想用滑动窗口注意力或 Multi-Query/GQA；有 H100 想试 FP8（FlashAttention-3）。
- **能干什么**：
  - 三条工作流：① 用 PyTorch 2.2+ 原生 `F.scaled_dot_product_attention` 原地替换；② 装 `flash-attn` 库用完整特性（可变长、滑动窗口、GQA）；③ H100 上转 FP8 走 FA-3，约 1.2 PFLOPS、比 FP16 再快 1.5–2 倍。
  - 给出**替换后的验证方法**：同一输入跑两遍，fp16 下最大差值应 < 1e-3，否则说明布局或 head 维度弄错了。
  - 明确"什么时候别用"：序列 < 256 token 时开销不划算，512–2K 只有 2–3 倍收益。
- **外部 Key / 依赖**：**无需外部 Key**。要 NVIDIA Ampere+（A100/A10/A30）或 AMD MI200+，CUDA 12.0+（最低 11.8）；`pip install flash-attn`，torch ≥ 2.2。
- **自带资源**：`references/benchmarks.md`、`references/transformers-integration.md`。（原文正文里还引用了 `advanced-features.md`、`algorithm.md`，这两个文件**并未随仓库提供**。）

#### `gguf-quantization` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜把 HuggingFace 模型转成 GGUF 再量化到 2–8 bit，好在 CPU / Apple Silicon / 消费级显卡上用 llama.cpp 跑起来。
- **什么时候用**：笔记本、Mac M 系列、没有 NVIDIA 卡的机器要跑本地模型；需要 Q2_K 到 Q8_0 的灵活档位；要把模型丢进 Ollama / LM Studio / text-generation-webui。
- **能干什么**：
  - 从源码编译 llama.cpp 的三种姿势：纯 CPU、CUDA、Metal（Apple Silicon）。
  - HF→GGUF 转换 + 量化全流程；讲清 K-quant 与旧式方法的区别，以及 **Q4 及以下必须配 imatrix**（重要性矩阵）才不掉点。
  - `llama-cpp-python` 的加载、生成、流式调用，以及 OpenAI 兼容 server（能直接用 openai 客户端指过去）。
  - 按硬件给优化建议：Metal 层数、CUDA `-ngl` offload、CPU 的 AVX2/AVX512 与线程数。
- **外部 Key / 依赖**：**无需外部 Key**。要自己编译 llama.cpp；`llama-cpp-python>=0.2.0`。下 gated 模型时原文用 `huggingface-cli download`，**未明确要求 token**。
- **自带资源**：`references/advanced-usage.md`、`references/troubleshooting.md`。

#### `gptq` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜训练后 4-bit 量化（GPTQ），把 70B 塞进消费级显卡：显存省 4 倍、困惑度只掉 <2%、推理快 3–4 倍。
- **什么时候用**：显存不够装大模型（70B/405B）；想直接加载社区预量化模型；要做 GPTQ + LoRA 的 QLoRA 微调（原文举例：单张 A100 80G 训 70B）。
- **能干什么**：
  - AutoGPTQ 完整链路：装包 → 载模型 → 准备校准数据 → 量化 → 保存 → 推 HuggingFace。
  - 三种 kernel 后端怎么选：ExLlamaV2（默认最快）、Marlin（Ampere+，A100/H100 上快 2 倍）、Triton（仅 Linux）。
  - 三套量化配置（标准 4-bit / 高精度小 group / 3-bit 大 group）与多卡 `device_map`、CPU offload、批量推理。
  - 和 transformers / PEFT 的对接写法。
- **外部 Key / 依赖**：**无需外部 Key**（推 Hub 才要 HF 账号）。需 `auto-gptq`、`transformers`、`optimum`、`peft`，以及 NVIDIA GPU。
- **自带资源**：`references/calibration.md`、`references/integration.md`、`references/troubleshooting.md`。

#### `grpo-rl-training` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型（附可跑样例）**｜用 TRL 做 GRPO 强化学习微调——不靠人工偏好对，靠你自己写的"奖励函数"教模型按格式答题、按正确性答题。
- **什么时候用**：要强制输出格式（XML 标签、JSON、结构化推理）；要教可验证任务（数学、代码、事实核查）；要提升思维链；手上**没有**高质量偏好对（有的话该用 DPO/PPO）。
- **能干什么**：
  - 四步落地：数据集准备 → 奖励函数实现 → 训练配置 → 模型装载与训练（含 LoRA 省显存）。
  - 奖励函数设计思路 + 三个实例（正确性、格式、分档给分），以及多阶段训练（先练格式、再练正确性）和自适应奖励缩放。
  - 提前告诉你"训练曲线长这样是正常的"（GRPO 的 loss 行为和 SFT 不一样，别看到 loss 不降就慌），并给踩坑速查表。
  - LoRA 合并与推理示例。
- **外部 Key / 依赖**：**无需外部 Key**。需 `transformers>=4.47`、`trl>=0.14`、`datasets>=3.2`、`peft>=0.14`、`torch`。日志默认 `report_to="wandb"`——不改就要 W&B（可改 `"none"`）。
- **自带资源**：`templates/basic_grpo_training.py`（训练模板）、`examples/reward_functions_library.py`（奖励函数库）、`README.md`。

#### `hqq-quantization` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜半二次量化（HQQ）——**不用校准数据**，几分钟就能把模型压到 4/3/2 bit（极限能到 1-bit）。
- **什么时候用**：没有校准集又急着量化；要极限压缩；要配 vLLM 或 HF Transformers 部署；想量完再挂 LoRA 微调。
- **能干什么**：
  - 单层 `HQQLinear` 到整模型量化；**7 种推理后端**（pytorch / pytorch_compile / aten / torchao_int4 / gemlite / bitblas / marlin）按硬件挑。
  - 按层混精度（比如 attention 用 4-bit、mlp 用 3-bit）；group size 调优。
  - HuggingFace 保存/推送、vLLM 服务、PEFT/QLoRA 微调三条落地路径。
  - 与 GPTQ/AWQ 的取舍：HQQ 胜在"零校准 + 快"，要极致精度还是走校准类方法。
- **外部 Key / 依赖**：**无需外部 Key**。需 `hqq>=0.2.0`、`torch>=2.0`；要有 GPU 才有意义。
- **自带资源**：`references/advanced-usage.md`、`references/troubleshooting.md`。

#### `knowledge-distillation` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜知识蒸馏——把大模型（老师）的能力搬进小模型（学生），常见 70B→7B 还能留 90%+ 性能。
- **什么时候用**：要降推理成本；要把 GPT-4 级别能力迁到开源小模型；要造领域专用小模型；想用大模型合成数据喂小模型。
- **能干什么**：
  - 温度缩放 + 软标签/硬标签加权损失（`alpha` 怎么配）。
  - forward KL 与 reverse KL（MiniLLM）的区别与选择：分类任务偏 forward，生成任务偏 reverse。
  - 三种策略：logit 蒸馏、两阶段蒸馏（先蒸再任务微调）、多老师蒸馏。
  - 完整训练脚本 + 老师/学生模型大小配比建议 + 评测对比写法。
- **外部 Key / 依赖**：**无需外部 Key**。需 `transformers`、`torch`、`datasets`、`accelerate`；老师模型越大越吃显存（示例用 Llama-2-70B 当老师）。
- **自带资源**：`references/minillm.md`。

#### `implementing-llms-litgpt` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜Lightning AI 的 LitGPT——20+ 架构（Llama / Gemma / Phi / Qwen / Mistral）的**单文件干净实现**，拿来读架构或做 LoRA/QLoRA 微调。
- **什么时候用**：想读懂某个模型的实现而不是被抽象层绕晕；要单卡 LoRA 微调；要从零预训练小模型；要转 checkpoint 再包成 API。
- **能干什么**：
  - 四套工作流：自定义数据微调（全参需 40GB+ / LoRA 16GB 够）、单卡 LoRA（Phi-2 约 8–12GB）、从零预训练（含 FSDP 多卡与 SLURM 脚本）、转换与部署（流式、批推理、8/4-bit 量化 + FastAPI）。
  - 与 Axolotl / TRL / Megatron / HF Transformers 的取舍说明。
  - 常见报错（OOM、bf16 支持、数据没 tokenize）的处理。
- **外部 Key / 依赖**：**无需外部 Key**（gated 模型如 Llama 要 HF 登录并授权）。需 `litgpt`、`torch`、`transformers`；CUDA 11.8+ / ROCm / Apple MPS。
- **自带资源**：`references/custom-models.md`、`distributed-training.md`、`supported-models.md`、`training-recipes.md`。（正文提到的 `scripts/prepare_dataset.py`、`convert_lit_checkpoint.py` 属 LitGPT 仓库自带，**不在本技能目录**。）

#### `llama-cpp` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜llama.cpp——**没有 NVIDIA 卡也能跑 LLM**：CPU、Apple Silicon、AMD/Intel GPU 都行。
- **什么时候用**：纯 CPU 机器；M1/M2/M3/M4 Mac；AMD 或 Intel 显卡（无 CUDA）；边缘设备（树莓派、嵌入式）；想要不依赖 Docker/Python 的简单部署。
- **能干什么**：
  - 各平台编译：Metal（Apple）、CUDA（NVIDIA）、ROCm（AMD）、CPU 指令集优化。
  - GGUF 量化档位怎么挑（平衡 / 最快 / 最高质量 / 70B+ 超大模型），以及混合 CPU+GPU offload。
  - OpenAI 兼容 server + 客户端调用；批处理；用 grammar 约束出合法 JSON。
  - 性能基准参考（原文：M3 Max 跑 Llama 2-7B Q4_K_M 约 40–60 tokens/s）。
- **外部 Key / 依赖**：**无需外部 Key**。需 `llama-cpp-python`；NVIDIA 不是必需。
- **自带资源**：`references/optimization.md`、`quantization.md`、`server.md`。

#### `llama-factory` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型（内容偏薄的文档索引卡）**｜LLaMA-Factory 微调导航：WebUI 无代码、100+ 模型、2–8 bit QLoRA、多模态。
- **什么时候用**：要用 LLaMA-Factory 但忘了命令或配置项；想不开代码走 WebUI；要查某个功能在官方文档的哪一节。
- **能干什么**：
  - 本质是一张**目录**：指向 `references/` 里从官方文档抓来的 getting_started / advanced / other 四份（共约 1477 行）。
  - 按三种读者给阅读路径：新手（getting_started）、查特定功能（advanced）、找代码示例。
  - 讲怎么刷新这份技能（重跑抓取脚本）。
  - ⚠️ **诚实说明**：SKILL.md 正文只有约 2.5k 字符，Quick Reference 里写着 "patterns will be added as you use the skill"——**真正的内容全在 references，正文教不了你东西**。
- **外部 Key / 依赖**：**无需外部 Key**。要真跑得自己装 `llmtuner`、`torch`、`transformers`、`datasets`、`peft`、`accelerate`、`gradio`。
- **自带资源**：`references/getting_started.md`（349 行）、`advanced.md`（1055 行）、`other.md`、`index.md`、`_images.md`。（正文里提到的 `scripts/`、`assets/` 目录**实际为空/不存在**。）

#### `llamaguard` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜Meta 的 7–8B 审核模型——判"用户输入"或"模型输出"安不安全，输出形如 `unsafe\nS3`，6 类风险（S1 暴力仇恨 … S6 犯罪策划），准确率 94–95%。
- **什么时候用**：要给 LLM 应用加输入/输出过滤；要按细类打风险标签；要接 vLLM / FastAPI / NeMo Guardrails / SageMaker。
- **能干什么**：
  - transformers 直接推理：`apply_chat_template` → 生成 → 解析 "safe/unsafe + 类别码"。
  - vLLM 批量过滤（原文：延迟 500ms → 50ms，双卡再快 2 倍）；FastAPI 包成 HTTP 服务返回 `{"safe": false, "category": "S6"}`。
  - 输入侧 / 输出侧两条工作流，以及与 NeMo Guardrails 串成自动拦截。
  - 显存不够用 8-bit 量化（原文：14GB → 7GB）；还能取 “unsafe” token 的概率做阈值判定。
- **外部 Key / 依赖**：**需要 HuggingFace 登录**——原文明确写 "Login to HuggingFace (required)"，还要在模型页接受 license（即 gated 模型，要 HF 账号 + token）。需 `transformers`、`torch`，vLLM 可选。
- **自带资源**：**无**（纯 SKILL.md，全文约 9.2k 字符）。（正文引用了 `references/benchmarks.md`、`custom-categories.md`、`deployment.md` 三份，**仓库里没有这三个文件**。）

#### `llamaindex` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜以"数据"为中心的 RAG 框架：300+ 连接器灌数据 → 建索引 → 查答案，附 agent 与多模态。
- **什么时候用**：私有文档问答；要把多来源（目录/网页/GitHub/数据库/API）灌进知识库；要带记忆的多轮 RAG；要把 query engine 当工具给 agent 用。
- **能干什么**：
  - 5 行最小 RAG；四种索引（Vector / List / Tree + 保存与加载）。
  - Query engine / Retriever / Chat engine 三层，含流式与元数据过滤。
  - Agent 把 query engine 包成工具，让它自己决定"查文档还是算数"。
  - 接 Chroma / Pinecone / FAISS；多模态 RAG（图文混检）与评估（相关性、忠实度）。
- **外部 Key / 依赖**：**无需外部 Key**（接 OpenAI/Anthropic/Pinecone 时，那些服务各自的 Key 另算）。需 `llama-index`。
- **自带资源**：`references/agents.md`、`data_connectors.md`、`query_engines.md`。

#### `long-context` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜把 transformer 的上下文窗口撑长：RoPE、YaRN、ALiBi、位置插值（Position Interpolation）四条路线。
- **什么时候用**：要处理 32k/64k/128k+ 的长文档；要把预训练模型扩到超出原窗口；要自己实现/替换位置编码。
- **能干什么**：
  - 四种方法逐一给原理 + 代码 + 对比表（谁适合从零训、谁适合低成本扩、谁最省 token）。
  - HF 里的具体配置：改 RoPE base frequency、YaRN scaling、动态缩放、position interpolation。
  - 讲透一条铁律：**扩窗后必须微调**，不微调直接降质；并给最小微调步数与"8k→16k→32k 逐步扩"的建议。
  - 微调数据要长（PG-19、arXiv、长会话、拼起来的仓库文件），短文档学不到长上下文。
- **外部 Key / 依赖**：**无需外部 Key**。需 `transformers`、`torch`，`flash-attn` 可选（省显存）。
- **自带资源**：`references/extension_methods.md`、`fine_tuning.md`、`rope.md`。

#### `mamba-architecture` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜Mamba 状态空间模型——O(n) 复杂度、推理快 5 倍、能跑百万 token、**不要 KV cache**。
- **什么时候用**：序列超长（100K+ token）；显存紧、腾不出 KV cache；要做流式应用（每 token 恒定内存）；要线性扩展。
- **能干什么**：
  - 配置并跑 Mamba-2 语言模型；加载 HF 上的预训练 Mamba（130M–2.8B）。
  - Mamba-1（d_state=16）vs Mamba-2（d_state=128，多头）的差别与选型。
  - 和同规模 Transformer 做基准对比（吞吐、显存、质量）。
  - 常见问题：`causal-conv1d` 装不上、CUDA 版本不匹配、和 transformers 版本冲突。
- **外部 Key / 依赖**：**无需外部 Key**。需 `mamba-ssm`、`causal-conv1d`、`torch`、`transformers`；NVIDIA CUDA 11.6+。
- **自带资源**：`references/architecture-details.md`、`benchmarks.md`、`training-guide.md`。（正文还提到 `mamba2-details.md`、`performance.md`、`selective-ssm.md`，**这三份仓库里没有**。）

#### `training-llms-megatron` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜NVIDIA Megatron-Core——2B 到 462B 参数的训练框架，靠张量/流水线/序列/上下文/专家并行把 GPU 榨到 47% MFU（H100）。
- **什么时候用**：模型大到单卡放不下（原文两处口径：描述里说 >1B，When to use 里说 >10B）；要极限 GPU 效率；要训 MoE；想用 Nemotron / LLaMA / DeepSeek 同款生产框架。
- **能干什么**：
  - 3D 并行训 LLaMA 式模型：给 70B 的完整配置 + 单机 8 卡 + SLURM 多机两种启动方式。
  - MoE 并行度怎么算（TP × PP × EP × DP 凑够卡数，原文以 Mixtral 8x7B 得 32 卡为例）。
  - Transformer Engine 自动 FP8；micro-batch 与重计算的吞吐调优顺序。
  - "MFU 低于 30% 怎么查"的排查清单。
- **外部 Key / 依赖**：**无需外部 Key**。需 `megatron-core`、`torch`、`apex`、`transformer-engine`；NVIDIA Ampere+（A100/H100/B200），FP8 要 Hopper/Ada/Blackwell。推荐 Docker。
- **自带资源**：`references/benchmarks.md`、`parallelism-guide.md`、`production-examples.md`、`training-recipes.md`。

#### `miles-rl-training` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜miles（slime 的生产级分支）——企业级 RL 训练：FP8/INT4 大 MoE、训练-推理比特级对齐、投机 RL 提吞吐。
- **什么时候用**：要训 1TB+ MoE（DeepSeek V3、Qwen3-MoE）；要 FP8 或 INT4 量化感知训练；要求训练和推理结果**逐比特一致**；要投机解码加速 RL rollout。
- **能干什么**：
  - 大 MoE 训练分步清单：环境 → FP8 block scaling（稳定优先）→ 配置 → 验证清单。
  - 投机 RL 怎么开：小 draft 模型出候选 token + 可选在线 MTP 训练，附预期加速比。
  - 三个高频故障的处置：FP8 训练崩、draft 漂移、训练-推理不一致（R3 rollout routing replay）。
  - 集群资源与 Megatron 并行度的配置参考。
- **外部 Key / 依赖**：**无需外部 Key**。需 `sglang-router>=0.2.3`、`ray`、`torch>=2.0`、`transformers>=4.40`；强烈建议 Docker；多机多卡大显存。
- **自带资源**：`references/api-reference.md`、`troubleshooting.md`。

#### `model-merging` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜用 mergekit 把几个微调过的模型"拌"成一个，**不用重新训练**，CPU 上几分钟出结果。
- **什么时候用**：想把数学 / 代码 / 聊天几个专长合到一个模型里；想快速造模型变体做实验；想避免灾难性遗忘。
- **能干什么**：
  - 五种合并法及适用场景：Linear（3+ 模型简单平均）、SLERP（2 个模型平滑插值）、Task Arithmetic、TIES-Merging、DARE（去冗余）。
  - YAML 配置结构逐字段讲；分层合并（不同层用不同模型）、"合并出 MoE"、tokenizer 合并。
  - 权重要不要归一、density 怎么从保守往激进调、合并系数怎么搜。
  - 合并后评测，再量化（GGUF/GPTQ）上架 HuggingFace。
- **外部 Key / 依赖**：**无需外部 Key**（推 Hub 要 HF 账号）。需 `mergekit`、`transformers`、`torch`；**合并不需要 GPU**。
- **自带资源**：`references/methods.md`、`examples.md`、`coefficient-tuning.md`、`evaluation.md`。

#### `model-pruning` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜剪枝压模型：Wanda / SparseGPT 一次性剪到 50% 稀疏，精度掉 <1%，**不用重训**。
- **什么时候用**：不重训就要压模型；要 50% 稀疏还几乎不掉点；要在支持稀疏的硬件上真提速（N:M 2:4）；要上移动端/边缘。
- **能干什么**：
  - Wanda（权重 × 激活，只需小校准集）与 SparseGPT（二阶信息，质量最好）两套一次性剪枝代码。
  - 三种稀疏形态的取舍：非结构化（质量好但**不提速**）、结构化（硬件友好但掉点多）、N:M 半结构化（NVIDIA 稀疏张量核能快 2 倍）。
  - 渐进剪枝 / 分层剪枝 / 迭代剪枝+微调三种策略。
  - 剪完的评测对比（原文典型值：50% 稀疏下 Wanda <1%、SparseGPT <0.5%、纯幅度剪枝 2–3%）。
- **外部 Key / 依赖**：**无需外部 Key**。需 `transformers`、`torch`；跑校准要 GPU；N:M 提速需 NVIDIA A100 这类稀疏张量核。
- **自带资源**：`references/wanda.md`。

#### `moe-training` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜训 MoE（混合专家）：同样的算力换更大容量——Mixtral 8x7B 那样 47B 参数只激活 13B。
- **什么时候用**：算力有限但要大模型；要复现 Mixtral / DeepSeek-V3 / Switch 这类稀疏架构；要做专家并行；想靠稀疏激活降推理延迟。
- **能干什么**：
  - DeepSpeed MoE 的配置与训练脚本（含 Mixtral 式 top-2 路由）。
  - 路由机制讲清三种：token 选专家、top-k、以及"专家选 token"的 BASE 层（天然负载均衡）。
  - 负载均衡 aux loss、容量因子（训练调低、评测调高）、token dropping。
  - 经验值：专家数量按模型规模给区间、**MoE 学习率要比 dense 低 3–6 倍**、损失系数怎么调。
- **外部 Key / 依赖**：**无需外部 Key**。需 `deepspeed>=0.6`、`transformers`、`torch`、`accelerate`；多卡。
- **自带资源**：`references/architectures.md`、`training.md`、`inference.md`。

#### `nanogpt` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型（教学向）**｜Karpathy 的 nanoGPT——约 300 行复现 GPT-2（124M），学 transformer 最合适的入口。
- **什么时候用**：想从零理解 GPT 怎么工作；想在 CPU 上 5 分钟看到训练结果；要改 transformer 结构做实验；要教别人。
- **能干什么**：
  - 字符级 Shakespeare：CPU 5 分钟 / T4 1 分钟出结果。
  - 8 卡 DDP 复现 GPT-2 124M（OpenWebText 预处理约 1 小时），含完整架构与训练超参。
  - 微调预训练 GPT-2；自定义数据集（给 `prepare.py` 模板：字符映射 / 分词 / 切分 / 存 bin）。
  - 什么时候该换 HuggingFace Transformers 或 Megatron。
- **外部 Key / 依赖**：**无需外部 Key**（`wandb` 可选，不填就不传日志）。需 `torch`、`numpy`、`transformers`、`datasets`、`tiktoken`、`wandb`、`tqdm`。
- **自带资源**：`references/architecture.md`、`data.md`、`training.md`。

#### `fine-tuning-serving-openpi` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜微调并部署 Physical Intelligence 的机器人策略（pi0 / pi0-fast / pi0.5），JAX 或 PyTorch 后端都行。
- **什么时候用**：要把 pi0 系列适配到自己的数据集（LeRobot / RLDS）；要 JAX→PyTorch 转 checkpoint；要给 ALOHA / DROID / LIBERO 起 policy 推理服务；norm stats 或显存炸了。
- **能干什么**：
  - 空白机器从零搭环境（`git clone --recurse-submodules` + `uv sync`，含 LFS 跳过技巧）。
  - JAX 微调 LeRobot 数据；PyTorch 训练与 checkpoint 互转。
  - 起 WebSocket policy server，客户端 `infer(observation)` 直接拿动作序列。
  - **一条铁律**：改配置或数据集后必须重跑"算 norm stats → 训练 → 起服务 → 验推理"四步闭环。
- **外部 Key / 依赖**：**无需外部 Key**（权重从 HF / `gs://openpi-assets` 拉；HF 上 gated 的要登录授权）。需 `uv>=0.4`、`jax>=0.4.30`、`torch>=2.1`、`transformers>=4.53.2`；推理 1×A100/H100 约 24GB，JAX 微调约 40–60GB。
- **自带资源**：`references/checkpoints-and-env-map.md`、`config-recipes.md`、`pytorch-gotchas.md`、`remote-client-pattern.md`、`training-debugging.md`。（正文提到的 `scripts/train.py`、`serve_policy.py`、`compute_norm_stats.py` 是 **openpi 仓库**的脚本，不在本技能目录。）

#### `openrlhf-training` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜OpenRLHF——Ray + vLLM 加速的 RLHF 框架，PPO / GRPO / RLOO / DPO 一套搞定，比 DeepSpeedChat 快 2 倍。
- **什么时候用**：7B–70B+ 模型做 RL；要 vLLM 推理加速 rollout；有多机 Ray 集群；想在一个框架里切四种算法。
- **能干什么**：
  - 完整 RLHF 流水线：SFT → 奖励模型 → PPO。
  - GRPO（省掉 critic 模型，省显存）、DPO（省掉奖励模型）各自的启动命令。
  - 四种算法怎么选（最大控制力 vs 最省资源）与适用场景对照。
  - Docker 起环境、colocate 模式显存冲突的解法（拆开分卡）。
- **外部 Key / 依赖**：**无需外部 Key**。需 `openrlhf`、`ray`、`vllm`、`torch`、`transformers`、`deepspeed`；多卡，建议 Docker。
- **自带资源**：`references/algorithm-comparison.md`、`custom-rewards.md`、`hybrid-engine.md`、`multi-node-training.md`。

#### `fine-tuning-openvla-oft` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜微调 / 评测 OpenVLA-OFT 机器人策略：连续动作头（L1 或 diffusion）+ LoRA + FiLM，跑 LIBERO 仿真和 ALOHA 真机。
- **什么时候用**：要复现 OpenVLA-OFT 论文结果；要训自定义 VLA 动作头；要给 ALOHA 起 server-client 推理；遇到归一化、LoRA 合并、跨 GPU 的怪问题。
- **能干什么**：
  - 环境搭建（版本钉死）、LIBERO 上评测预训练 checkpoint、LIBERO 上微调。
  - ALOHA 上训 OpenVLA-OFT+，含"关键不变量"清单——**长评测前先验配置对齐**，否则跑半天白跑。
  - 常见坑：归一化统计、LoRA 合并顺序、多卡通信、缓存目录写到小分区。
- **外部 Key / 依赖**：**无需外部 Key**（wandb 可选，但示例命令带 `--wandb_entity/--wandb_project`）。**版本卡得很死**：`torch==2.2.0`、`peft==0.11.1`、`tensorflow==2.15.0`、`robosuite==1.4.0`；无头渲染要 `MUJOCO_GL=egl` 且设 `MUJOCO_EGL_DEVICE_ID`。
- **自带资源**：`references/libero-workflow.md`、`aloha-workflow.md`、`config-troubleshooting.md`、`paper-and-checkpoints.md`。（正文提到的 `scripts/finetune.py`、`deploy.py` 等属 **OpenVLA 仓库**，不在本目录。）

#### `peft` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：用 LoRA / QLoRA 等 25 种以上方法只训练不到 1% 参数来微调大模型（指导型：教 agent 写 PEFT 训练代码，不自带脚本）。
- **什么时候用**：消费级显卡（4090 / A100）微调 7B–70B；显存是硬约束；想一个底座挂多个任务适配器；QLoRA 单卡 24GB 上 70B。
- **能干什么**：
  - LoRA 标准流：加载底座 → 配 `LoraConfig`（r / alpha / target_modules）→ 训练 → 只存 6MB 适配器。
  - QLoRA：4-bit 量化底座 + LoRA + 梯度检查点，70B 塞进单张 24GB。
  - 超参经验值：`alpha = 2 * rank`，以及 Llama / Mistral / Qwen / GPT-2 / Falcon / BLOOM 各自的 target_modules 清单。
  - 适配器加载、合并进底座（部署时零适配器开销）、推 Hub、多适配器服务。
- **外部 Key / 依赖**：无需外部 Key。依赖 `peft>=0.13.0`、`transformers>=4.45.0`、`torch>=2.0.0`、`bitsandbytes>=0.43.0`（QLoRA 才需要后者）。
- **自带资源**：`references/advanced-usage.md`、`references/troubleshooting.md`。

#### `rwkv` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：RWKV 这个"训练像 Transformer、推理像 RNN"的架构的用法与原理——线性时间、无限上下文、没有 KV cache（指导型）。
- **什么时候用**：要超长上下文、要常数级推理显存、要 CPU/边缘侧跑大模型，或想比较 RWKV 与 Transformer。
- **能干什么**：
  - 给出 RWKV 的安装（含 `pytorch-lightning==1.9.5`、deepspeed、ninja 这套版本敏感组合）与加载推理示例。
  - 演示 GPT 模式（并行训练）与 RNN 模式（逐 token 推理 + 状态 state 传递）两种调用姿势。
  - 覆盖 RWKV-7（2025-03）与 state 管理细节，参考资料含架构说明与状态管理。
- **外部 Key / 依赖**：无需外部 Key（模型权重需自行获取，原文未给下载渠道要求）。依赖 `rwkv`、`torch`、`transformers`，另需 `pytorch-lightning==1.9.5`、`deepspeed`、`wandb`、`ninja`。
- **自带资源**：`references/architecture-details.md`、`references/rwkv7.md`、`references/state-management.md`。

#### `sglang` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：主打 RadixAttention 前缀缓存的高性能推理服务框架，擅长结构化输出（JSON/正则/文法约束）和 agent 多轮复用前缀（指导型）。
- **什么时候用**：要 JSON/regex 受控解码；agent 工作流里 system prompt 和工具定义被反复重放；多轮对话共享上下文；想比 vLLM 在"前缀高度重复"场景更快。
- **能干什么**：
  - 一页命令起服务（`python -m sglang.launch_server`），含 `--tp 4` 张量并行与自动开启的 Radix 缓存。
  - 结构化生成三种玩法：`sgl.gen(..., regex=)` 正则约束、JSON schema、多选/枚举约束。
  - Agent 侧：函数调用、多轮会话、OpenAI 兼容接口（`sgl.OpenAI("http://localhost:30000/v1")`）。
  - 讲清楚 RadixAttention 为什么能省算力（1000 token 系统提示里只算新增的 2 个 token）。
- **外部 Key / 依赖**：无需外部 Key（本地起服务，OpenAI 兼容客户端的 `api_key` 只是占位 `EMPTY`）。依赖 `sglang`、`torch`、`transformers`，装 `sglang[all]`，可选 FlashInfer 加速。
- **自带资源**：`references/deployment.md`、`references/radix-attention.md`、`references/structured-generation.md`。

#### `simpo` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：SimPO——不需要参考模型的偏好对齐方法，比 DPO 更省资源、效果更好（AlpacaEval 2.0 +6.4）（指导型）。
- **什么时候用**：想对齐模型偏好，但不想再加载一份 reference model；显存/算力紧张；要在 DPO / PPO 之外找个更简单的方案。
- **能干什么**：
  - 给可直接跑的 YAML 配置模板（Mistral 7B / Llama 3 8B），关键超参写明：`beta` 2.0–10.0、`gamma_beta_ratio` 0.5、`loss_type: sigmoid|hinge`。
  - 强调学习率必须落在 3e-7 ~ 1e-6（写错基本训练崩）。
  - 走 HuggingFace alignment-handbook 的 `run_simpo.py` + DeepSpeed ZeRO-3 启动。
- **外部 Key / 依赖**：无需外部 Key。依赖 `torch`、`transformers`、`datasets`、`trl`、`accelerate`，另需克隆 `alignment-handbook` 与装 `flash-attn`。
- **自带资源**：`references/datasets.md`、`references/hyperparameters.md`、`references/loss-functions.md`。

#### `slime` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：清华 THUDM 的 RL 后训练框架，Megatron-LM 负责训练、SGLang 负责高吞吐 rollout，GLM-4.5/4.6/4.7 就是这么训的（指导型）。
- **什么时候用**：要 Megatron 原生训练 + SGLang 推理的组合；要自定义数据生成流程和灵活数据缓冲；训 GLM / Qwen3 / DeepSeek V3 / Llama 3；做研究级 RL scaling。
- **能干什么**：
  - 三条工作流：标准 GRPO、异步训练、多轮 agentic 训练。
  - 数据缓冲（data buffer）系统：prompt 初始化与样本存储自己可控。
  - 完整参数表：`--actor-num-gpus-per-node`、`--rollout-num-gpus`、`--advantage-estimator grpo`、`--n-samples-per-prompt`、KL 系数等。
  - 给 Docker 与源码两种安装路径（Docker 镜像 `slimerl/slime` 是最省事的那条）。
- **外部 Key / 依赖**：无需外部 Key。依赖 `sglang-router>=0.2.3`、`ray`、`torch>=2.0.0`、`transformers>=4.40.0`（容器镜像里已备）。
- **自带资源**：`references/api-reference.md`、`references/troubleshooting.md`。

#### `speculative-decoding` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：三类"猜 token 再校验"的推理加速法——草稿模型投机解码、Medusa 多头、Lookahead 前瞻解码，1.5–3.6× 提速且不损质量（指导型）。
- **什么时候用**：实时应用（聊天、代码补全）延迟太高；想不提模型规格就提速；算力有限但要更快出 token。
- **能干什么**：
  - 标准投机解码：transformers 里配 `assistant_model` 草稿模型，附不同草稿模型尺寸的实测加速比。
  - Medusa：给目标模型挂多个解码头 + 树状注意力，一次验证多条候选链。
  - Lookahead：Jacobi 迭代并行生成 n-gram 候选，无需额外训练。
  - 生产落地方案对比与选型建议（含 vLLM 内置投机解码）。
- **外部 Key / 依赖**：无需外部 Key。依赖 `transformers`、`accelerate`；Medusa / LookaheadDecoding 需从 GitHub 克隆后 `pip install -e .`；可选装 `vllm`。
- **自带资源**：`references/medusa.md`、`references/lookahead.md`。

#### `torchforge` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：Meta 的 PyTorch 原生 agentic RL 库——把"算法"和"基础设施"彻底拆开，改算法不用碰分布式（指导型）。
- **什么时候用**：要干净的 RL 抽象（GRPO/DAPO/SAPO 各写百来行就能试）；不想引入 Ray；要 Monarch + TorchTitan 的扩展路径。注意它实验性质，API 会变。
- **能干什么**：
  - 三条工作流：数学推理 GRPO、自定义 loss、多卡分布式。
  - 内置 loss：GRPO、DAPO、CISPO、GSPO、SAPO。
  - 架构分层讲清：应用层 / 算法层 / 基础设施层（TorchTitan 训练 + vLLM 推理 + TorchStore 权重同步）。
  - 给两条安装路：`./scripts/install.sh`（自动处理 PyTorch nightly 与依赖）与 ROCm 版 `./scripts/install_rocm.sh`——注意这两个脚本在 torchforge 源码仓里，本技能目录并不自带。
- **外部 Key / 依赖**：无需外部 Key。依赖 `torch>=2.9.0`、`torchtitan>=0.2.0`、`vllm`、`monarch`。
- **自带资源**：`references/api-reference.md`、`references/troubleshooting.md`。

#### `torchtitan` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：PyTorch 官方的大模型预训练平台，4D 并行（FSDP2 + TP + PP + CP）一把梭（指导型）。
- **什么时候用**：从 8 卡到 512+ 卡预训练 Llama 3.1 / DeepSeek V3 / 自定义模型；要用 Float8、torch.compile、分布式 checkpoint 这些新东西。
- **能干什么**：
  - 一条命令开训：`CONFIG_FILE=.../llama3_8b.toml ./run_train.sh`。
  - 4D 并行的组合方式与各自显存/吞吐权衡，含 TP 下 `TORCH_NCCL_AVOID_RECORD_STREAMS=1` 这类坑。
  - 支持模型清单（Llama 3.1 8B/70B/405B、DeepSeek V3 MoE 16B–671B、GPT-OSS、Qwen 3、Flux）与 H100 性能基准。
  - checkpoint 转换、自定义模型接入、Float8 训练各有专页。
- **外部 Key / 依赖**：需要 **HuggingFace token**（下载 Llama 3.1 等受限模型的 tokenizer/权重，`--hf_token`），这不是 API Key 但属外部凭证。依赖 `torch>=2.6.0`、`torchtitan>=0.2.0`、`torchao>=0.5.0`。
- **自带资源**：`references/checkpoint.md`、`references/custom-models.md`、`references/float8.md`、`references/fsdp.md`。

#### `trl-fine-tuning` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：HuggingFace TRL 全家桶——SFT 指令微调、DPO 偏好对齐、PPO/GRPO 奖励优化、奖励模型训练，一个库全覆盖（指导型）。
- **什么时候用**：要做 RLHF；要把模型对齐到人类偏好；要 SFT → DPO → 奖励模型这条标准后训练流水线。
- **能干什么**：
  - `SFTTrainer` 三行起训；`DPOTrainer` + `DPOConfig(beta=0.1)` 做偏好对齐。
  - PPO / GRPO 走奖励优化；奖励模型单独训练。
  - 各训练器的关键参数、显存占用表、与 PEFT/LoRA 组合方式。
- **外部 Key / 依赖**：无需外部 Key。依赖 `trl`、`transformers`、`datasets`、`peft`、`accelerate`、`torch`。
- **自带资源**：`references/sft-training.md`、`references/dpo-variants.md`、`references/online-rl.md`、`references/reward-modeling.md`。

#### `unsloth` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：Unsloth 微调加速的查手册型技能——训练快 2–5 倍、显存省 50–80%（指导型，正文是自动从官方文档抓来的索引，干货全在 references）。
- **什么时候用**：要在单卡上尽量快地微调 Llama / Mistral / Gemma / Qwen / gpt-oss；遇到 Unsloth 的 API 用法或报错要查。
- **能干什么**：
  - 提供 Unsloth 官方文档的全文镜像（按主题分页），agent 用 `view` 按需读取对应章节。
  - 覆盖 LoRA/QLoRA 优化、各类模型的加载与训练配方、常见问题。
  - 注意：SKILL.md 本身只有骨架（"Quick reference patterns will be added as you use the skill"），真正可用信息靠查 references。
- **外部 Key / 依赖**：无需外部 Key。依赖 `unsloth`、`torch`、`transformers`、`trl`、`datasets`、`peft`。
- **自带资源**：`references/llms-full.md`（约 1.0 MB 全文）、`references/llms-txt.md`（约 790 KB 分页文档）、`references/llms.md`、`references/index.md`（分类索引，136 页）。

#### `verl` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：字节 Seed 的 RL 训练库（HybridFlow 论文实现），后端随便换、算法一大把，训到 671B（指导型）。
- **什么时候用**：大规模 RLHF / GRPO / PPO 后训练；要在 FSDP、FSDP2、Megatron-LM 与 vLLM、SGLang、HF 之间自由切换后端；要多轮工具调用的 agentic RL；要视觉语言模型 RL。
- **能干什么**：
  - 一行命令起 GRPO：`python3 -m verl.trainer.main_ppo algorithm.adv_estimator=grpo ...`。
  - 算法覆盖 PPO、GRPO、DAPO、RLOO、ReMax、REINFORCE++、SPIN、SPPO。
  - 三条工作流：数学推理 GRPO、带 critic 的 PPO、Megatron 大规模（需先 `pip install mbridge` 并转 Megatron 格式）。
  - LoRA RL、序列并行、专家并行、多轮工具调用等进阶项。
- **外部 Key / 依赖**：无需外部 Key。依赖 `verl>=0.3.0`、`torch>=2.0.0`、`ray>=2.41.0`、`vllm>=0.8.2`、`transformers>=4.40.0`（可选 WandB / TensorBoard 看曲线）。
- **自带资源**：`references/api-reference.md`、`references/troubleshooting.md`。

#### `vllm` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：生产级 LLM 推理服务标配——PagedAttention + 连续批处理，吞吐比原生 transformers 高一个量级（指导型）。
- **什么时候用**：要部署 OpenAI 兼容的 LLM API；要在有限显存下服务模型；要量化（GPTQ/AWQ/FP8）与张量并行；要做压测和容量规划。
- **能干什么**：
  - 离线批量推理（`LLM(...).generate(...)`）与在线服务（`vllm serve`）两套入口。
  - 生产部署清单：`--gpu-memory-utilization 0.9`、`--max-model-len`、`--tensor-parallel-size`、`--quantization awq`、前缀缓存、Prometheus 指标端口。
  - 验收指标明确：TTFT < 500ms、吞吐 > 100 req/s，并给 locust 压测方案。
  - 量化、显存优化、常见报错各有专页。
- **外部 Key / 依赖**：无需外部 Key（本地服务，OpenAI SDK 的 `api_key='EMPTY'` 只是占位；拉取 gated 模型可能需要 HuggingFace 登录，原文未明确要求）。
- **自带资源**：`references/server-deployment.md`、`references/quantization.md`、`references/optimization.md`、`references/troubleshooting.md`。


### 评估与基准（3 个）

#### `evaluating-code-models` <sub>Jensen-Yao/agents-skills</sub>（仓库目录 `bigcode-evaluation-harness`）
- **一句话**：指导型（给命令为主）—— 用 BigCode 的评测框架在 HumanEval / MBPP / MultiPL-E 等 15+ 基准上跑代码模型 pass@k。
- **什么时候用**：要横向比几个代码模型的编码能力，或者要给自己的模型出一份「行业标准口径」的评测数字时。
- **能干什么**：
  - 标准评测：`accelerate launch main.py --model ... --tasks humaneval --n_samples 200` 出 pass@k
  - 多语言：MultiPL-E 覆盖 18 种语言，先 `--generation_only` 生成、再进 Docker 里执行（安全隔离）
  - 指令微调模型：配 `--instruction_tokens` 和 chat template
  - 批量对比多个模型（原文给了 `eval_models.sh` 思路）
- **外部 Key / 依赖**：**评测 gated 模型/数据集时需要 HuggingFace 登录**——原文明确写 `401 Unauthorized` 要先 `huggingface-cli login` 并用 `--use_auth_token`。其余：需 GPU + `transformers`/`accelerate`/`datasets`；执行生成的代码要 `--allow_code_execution`（有风险，建议按原文用 Docker 隔离）。
- **自带资源**：`references/` 3 篇（benchmarks、custom-tasks、issues 排错）

#### `evaluating-llms-harness` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜EleutherAI 的 lm-evaluation-harness——一次跑 60+ 学术基准（MMLU、HumanEval、GSM8K、TruthfulQA、HellaSwag），学界报数就认这套。
- **什么时候用**：论文要报标准分；要横向比几个模型；训练途中想看 checkpoint 涨没涨；要被复现——统一 prompt、统一口径。
- **能干什么**：
  - 单基准 / 多基准一次跑，以及只跑子任务（MMLU 57 个学科挑着跑）。
  - 训练过程中按 checkpoint 批量评测 + 结果汇总画曲线。
  - **vLLM 加速**：原文实测 MMLU 7B 从约 2 小时压到 15–20 分钟。
  - 模型与 tokenizer 必须匹配、任务名写错、batch size 撑爆显存等常见问题。
- **外部 Key / 依赖**：**无需外部 Key**。需 `lm-eval`、`transformers`，`vllm` 可选（要快就装）；要 GPU。
- **自带资源**：`references/benchmark-guide.md`、`custom-tasks.md`、`api-evaluation.md`、`distributed-eval.md`。

#### `nemo-evaluator-sdk` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜NVIDIA NeMo Evaluator——18+ 评测工具、100+ 基准，容器化跑在本地 Docker / Slurm HPC / 云上，结果能导 MLflow / W&B。
- **什么时候用**：要在 Slurm 集群上批量评；要"同一容器、同一天、同一结果"的可复现评测；要一次比多个模型并导出报表；要评 VLM 或安全基准。
- **能干什么**：
  - 一份 YAML 跑标准基准；`--limit-samples` 先小样本快测；作业状态查询、列表、终止。
  - Slurm 后端（配置 + `sacct` 查状态）、Docker 后端、云后端切换。
  - 多模型对比 + 导出（MLflow / 本地 JSON / W&B）。
  - Python API 直接调（`EvaluationConfig` + `EvaluationTarget`），能指向任意 OpenAI 兼容端点。
- **外部 Key / 依赖**：**需要 Key**——`NGC_API_KEY`（拉 `nvcr.io` 容器、用 NVIDIA Build，原文写 "Required"）；`HF_TOKEN` 对部分基准（GPQA、MMLU）必需；个别任务还要 `JUDGE_API_KEY`。软件上需 `nemo-evaluator-launcher>=0.1.25` + **Docker**，Python 3.10–3.13。
- **自带资源**：`references/configuration.md`、`execution-backends.md`、`adapter-system.md`、`custom-benchmarks.md`。


### 研究与写作（22 个）

#### `brainstorming-research-ideas` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 用 10 套结构化框架帮你把「想做个啥」逼成「值得做的问题」。
- **什么时候用**：刚开一个新方向、要换赛道，或者手上工作想找个新角度时。
- **能干什么**：
  - 10 个构思框架：问题优先 vs 方案优先、抽象阶梯、矛盾挖掘、跨领域类比、「什么变了」原则、失败分析、简洁性检验、利益相关者轮换、组合分解、"讲给别人听"测试
  - 三阶段流程：发散（多产候选）→ 收敛（过滤排序）→ 精炼（打磨胜出者）
  - 框架选择指南 + 常见构思陷阱（如「没人做过 X」但其实没人需要 X）
- **外部 Key / 依赖**：无需外部 Key，无需任何库。
- **自带资源**：无

#### `ccf-common` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— CCFA 家族的共享治理层：路由表、触发注册表、任务模式、交接规范、来源登记、产物契约。
- **什么时候用**：**只在维护或审计 CCFA 技能本身时用**，日常写论文/审稿不要单独调它（原文明确划了这条界）。
- **能干什么**：
  - 定义每次任务的输出位置、唯一工作目录、整合责任人
  - 维护 `references/routing.md` 协作路由、`skill-trigger-registry.yaml` 触发注册表
  - 规定跨版本连续性（continuity）、交接模式、偏好设置
  - 自带一组自查脚本：Markdown 链接、路径隐私（绝对路径/用户名泄漏）、来源登记、v0.4 结构校验
- **外部 Key / 依赖**：无需外部 Key。脚本是**可执行型**但要求 Python 3 + `pyyaml`（`check_sources.py` 里 `import yaml`）；`test_configurator.js` 需要 Node。
- **自带资源**：`references/` 12 个、`scripts/` 9 个（6 个检查脚本 + 3 个测试）、`assets/configurator/`（一个本地打开的 HTML 配置器）

#### `ccf-experiment-designer` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 把「一个主张」翻译成「能证明它的实验设计」。
- **什么时候用**：要定数据集、基线、指标、消融，或者要出结果表结构的时候。
- **能干什么**：
  - 建立 claim → evidence 矩阵：每个主张配什么数据集/基线/指标/判别性消融
  - 区分「已完成的对比」和「计划中的方法」，计划值绝不当证据
  - 结果表 schema：单位、种子、置信区间、指标方向、方法版本一律保留，缺失值显式留空
  - 只在真改了实验代码时才要求冒烟测试，且不进发表证据
- **外部 Key / 依赖**：无需外部 Key；设计过程不需要跑实验。
- **自带资源**：`references/evidence-design.md`、`references/result-templates.md`

#### `ccf-humanization` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 所有 CCFA 技能的**第一道预检**：把「防御性废话」洗成直给的学术表达。
- **什么时候用**：任何走 CCFA 流程的任务开头（研究/审稿/检索/实验/图表/维护都算），或者单独要求「论文人性化 / 去防御性」时。
- **能干什么**：
  - 逐段还原「这段到底想说啥」，重修句与句之间的因果/证据连接（不是加连接词了事）
  - 删除跨章节重复的免责声明，但不删真实的不确定性
  - 明确「警告」只用于真正无法从已有证据判断的决策，且**不许把警告塞进正文当挡箭牌**
  - 中英文改稿示例 + 标点/术语偏好；可用 `check_prose_quality.py` 定位可疑句
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：`references/humanization-policy.md`、`references/experiment-discipline.md`

#### `ccf-idea-optimizer` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 把粗糙的研究想法养成「问题—洞见—机制—证据」完整的 idea 卡。
- **什么时候用**：有个模糊方向想具象化，或者想判断这个方向还有没有救的时候。
- **能干什么**：
  - 补全 intake（决策、领域、阶段、资源、时间线、可用资料）
  - 用文献把「时效性」和「最近邻工作」落到实处，借来的想法和推测的空白分开标注
  - 前沿构思：需要突破时一次产 3–5 个真正不同的候选（可 refine/combine/transfer/invert）
  - 明确区分「当前弱点」与「发展潜力」，给出有条件的 rescue 路线
- **外部 Key / 依赖**：无需外部 Key；需要联网检索时走公共安全词，禁止把未发表草稿粘进搜索框（原文有隐私条款）。
- **自带资源**：`references/` 8 篇（idea-intake、frontier-ideation、problem-method-blueprint、research-taste 等）

#### `ccf-idea-reviewer` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 给研究想法打分：值不值得做、新不新、洞见够不够。
- **什么时候用**：你自己或别人问「这个 idea 靠谱吗 / 创新够不够 / 值得投吗」时。
- **能干什么**：
  - 归一化 problem → gap → insight → mechanism 四段式
  - 六维概念评分（含权重与置信度），低置信 ≠ 低分
  - 多视角专家面板（重复问题合并成稳定 ID），结论落在 accept-to-develop / revise / pivot-with-rescue-route / abandon / needs-literature-search
  - 区分「致命概念缺陷」和「可修的缺口」
- **外部 Key / 依赖**：无需外部 Key；新颖性判断需公开检索，禁止用私有草稿当查询词。
- **自带资源**：`references/` 5 篇（strict-idea-review、rubric、calibration、expert-panel、source-notes）

#### `ccf-integrity-auditor` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 查你的 claim / 数字 / 引用有没有站不住的地方。
- **什么时候用**：投稿前自查「这个数字三处写一样吗」「这条引用真的支持这句话吗」时。
- **能干什么**：
  - claim 分级：supported / partially supported / unsupported / overstated / unclear
  - 数字一致性：跨正文、表、图、图注、摘要、结论对齐，用确定性算术核对差值与单位
  - 引用审计：**光有元数据不算支持**，要回原文看上下文
  - 输出带严重级别 + 可安全改写的建议 + 下一棒交给谁
- **外部 Key / 依赖**：无需外部 Key；核引用需能访问原始来源（联网或本地 PDF）。
- **自带资源**：无（只有 `agents/openai.yaml`）

#### `ccf-literature-monitor` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 定期扫 arXiv / OpenReview / 会议源 / 实验室 / 竞品，看有没有人跟你撞车。
- **什么时候用**：手上有一个在做的 idea 或已投稿的论文，想周期性做「新颖性巡查」时。
- **能干什么**：
  - 按 venue-watch 模式扫 OpenReview、proceedings、录用列表、官方会议页
  - 产出带日期的证据 + 与上次扫描相比的增量变化
  - 给可执行信号：RELAX（可以放心）/ RESEARCH（要补调研）/ FOLLOW-UP（得跟进）
  - 事实化报告，不夸大威胁也不轻描淡写
- **外部 Key / 依赖**：无需外部 Key；需要联网检索能力（arXiv / OpenReview 均为公开站点）。
- **自带资源**：`references/monitoring-workflow.md`、`references/report-template.md`

#### `ccf-literature-searcher` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 找文献、找先行工作、找数据集和 benchmark，并把引用候选核实掉。
- **什么时候用**：写 related work、找 baseline、给某个 claim 补证据的时候。
- **能干什么**：
  - 多簇查询 + 按 DOI / arXiv ID / 归一化标题去重
  - 分级打分（仅在需要决策时打，避免无意义评分）
  - 只信原文/主页面，不信搜索结果摘要；论文读不到要标「覆盖缺口」而不是当成不存在
  - 可选落盘成 `papers.csv` 之类可复用结构
- **外部 Key / 依赖**：无需外部 Key；需联网检索。个别付费库（如 Web of Science）取决于你所在机构权限，**原文未要求特定 API key**。
- **自带资源**：`references/search-and-scoring.md`、`references/report-template.md`

#### `ccf-paper-reviewer` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 审稿：只看主张、证据、写作有什么硬伤，**不替你改稿**。
- **什么时候用**：投稿前想被狠批一顿，或者真要给别人的稿件写审稿意见时。
- **能干什么**：
  - 三种报告规格：科学审稿 14 节 / 写作审稿 9 节 / 简报 5 节
  - 七维评分 + 每条低分必须给扣分理由和修复条件
  - 版本对比（v1 vs v2 到底进步在哪）
  - 对每条 major/critical 批评，强制检查「作者最强的反驳段落在哪」，站不住就撤回该条
- **外部 Key / 依赖**：无需外部 Key；补新颖性/benchmark 证据时会调 `ccf-literature-searcher` 联网。
- **自带资源**：`references/` 15 篇（rubric、venue-review-styles、reviewer-panel、writing-review 子目录等）+ `scripts/validate_version_comparison.py`

#### `ccf-paper-to-exemplar` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型（带一个转换脚本）—— 把一篇论文 PDF 拆成「写作范例卡片」并登记进个人范例库。
- **什么时候用**：看到一篇写法很好的论文，想把它的叙事/摘要/引言套路沉淀下来复用。
- **能干什么**：
  - 跑 `scripts/convert.py` 抽 PDF 全文与结构
  - 按固定小节生成卡片：Story Pattern、Abstract Moves、Introduction Moves 等
  - 把卡片注册到范例库索引，供 `ccf-paper-writer` 引用
- **外部 Key / 依赖**：无需外部 Key；**脚本需要 `pymupdf`**（`pip install pymupdf`，缺了会直接报错退出）。
- **自带资源**：`scripts/convert.py`（可执行）

#### `ccf-paper-writer` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— CCFA 里真正动笔的那个：起草、润色、压缩、改成演示稿。
- **什么时候用**：要从研究材料写初稿，或者要改摘要/某一段/整篇压缩到页数内时。
- **能干什么**：
  - 六种模式：`polish`（段落）/ `draft section` / `draft manuscript`（可交「结果待补」稿）/ `submission-fit`（按 venue 页数收口）/ `compress` / `presentation`
  - 先写实质内容、后调页数——原文明确「只说了投哪个会不等于要压缩」
  - 110 个会议/期刊的 venue guide + 29 张范例卡 + 3 篇参考论文 PDF
  - 引用流程（`citation-workflow.md`）+ 压缩规则 + 表格样式 + 长度预算
- **外部 Key / 依赖**：无需外部 Key。`scripts/check_prose_quality.py` 只依赖标准库；`convert_pdf_to_card.py` 依赖 `pymupdf`。LaTeX 成稿要本机有编译引擎。
- **自带资源**：`references/` 224 个文件（venue-guides 110、exemplars 29 张卡 + papers、写作规则若干）、`scripts/` 2 个、`paper_ref/` 3 篇 PDF

#### `ccf-pipeline-orchestrator` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型（带一个实验循环脚本）—— 研究项目的「项目经理」：拆阶段、定门禁、管 `ccfa.yaml` 状态。
- **什么时候用**：一次要给一整条研究流水线（想法→实验→写作→投稿）排期和定验收标准时。
- **能干什么**：
  - 从目标倒推缺失前置条件，给每道门禁定义「需要什么证据 / 通过条件 / 阻塞项 / 谁负责」
  - 维护 `ccfa.yaml` 的 stage / gate 字段（只在授权时改）
  - 长任务连续性：交接包、恢复上下文、核对真实产物再续跑
  - `scripts/research_loop.py` 提供「有界、前台、可留证」的实验执行契约
- **外部 Key / 依赖**：无需外部 Key。`research_loop.py` **纯标准库**、只支持前台命令；不做沙箱，权限与评测独立性由调用方自负。
- **自带资源**：`references/` 4 篇（含 autonomous-research、workflow-planning 子目录）、`scripts/research_loop.py` + 其测试

#### `ccf-project-scaffolder` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型（带模板文件）—— 一键把 CCF 项目的目录骨架、LaTeX 模板、`ccfa.yaml` 铺好。
- **什么时候用**：新开一个论文项目，不想手工建 source/assets/cache/build 那一堆目录时。
- **能干什么**：
  - 按 venue 选模板（走 `ccf-paper-writer` 的 venue-guides 索引）
  - 只建需要的目录，保留既有布局，不迁移/覆盖用户文件
  - 从 `assets/ccfa.yaml` 复制项目状态文件，只填用户给的元数据，未知字段留占位
  - 支持 dry-run：只报「打算建什么」，不落盘
- **外部 Key / 依赖**：无需外部 Key；成稿编译要本机 LaTeX 引擎（可用才编译，不强制）。
- **自带资源**：`assets/ccfa.yaml` 模板

#### `ccf-rebuttal-writer` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 写 rebuttal / 作者回应 / 修订台账 / 重投迁移方案。
- **什么时候用**：审稿意见回来了要回嘴，或者被拒要换会重投时。
- **能干什么**：
  - 把审稿意见按 reviewer / 问题类型 / 严重度 / 手里有的证据 / 应答策略拆组
  - 先打最痛的：soundness、novelty、缺证据、假设错误、多位审稿人共同关切
  - 维护**一份**修订台账（承诺改哪、改没改、跨轮分数变化）
  - 4 套 LaTeX 应答模板（通用、按审稿人、AC 聚焦、精简）
- **外部 Key / 依赖**：无需外部 Key。`assets/templates/*.tex` 要用需要 LaTeX 环境。
- **自带资源**：`assets/templates/` 4 个 `.tex` 模板 + `references/` 4 篇（策略、清单、台账、模板说明）

#### `ccf-skill-forger` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 写/审/维护 Codex 技能本身的「元技能」。
- **什么时候用**：要给 Codex 新建一个 skill，或者 CCFA 家族技能要改结构、查冲突、做发布校验时。
- **能干什么**：
  - 判断内容该放 `SKILL.md` 还是 `references/` / `scripts/` / `assets/`
  - 规范 frontmatter description（触发词全写这里，正文只在命中后才加载）
  - 维护家族一致性：SKILL.md、agent prompt、注册表、共享规则、文档互相对账
  - 收尾跑 `check_path_privacy.py`，把本地绝对路径/用户名换成 `$CODEX_HOME`、`$HOME` 或相对路径
- **外部 Key / 依赖**：无需外部 Key。可选依赖 `skill-creator` 的 `init_skill.py` 初始化器（本机有就用）。
- **自带资源**：`references/` 4 篇（design-checklist、patterns、local-commands、svg-style-guide）

#### `ccf-submission-checker` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 投稿前的「安检门」：模板、页数、匿名、PDF 构建、元数据、可复现性。
- **什么时候用**：离截稿不远，要把投稿包过一遍的时候。
- **能干什么**：
  - `package-check`：真读 TeX/PDF/构建日志，不信「文件存在」就推断编译成功
  - 页数按**具体会议/年份/轨道**算，匿名性、字体、元数据、必需表格逐项过
  - `artifact-check`：代码/数据/模型/环境/种子/硬件/许可证/访问限制/README 的可复现清单
  - 区分 pass / fail / 不适用 / 未验证；查不到官方规则就标「未验证」而不是当通过
- **外部 Key / 依赖**：无需外部 Key；要能读构建产物，本机最好有 LaTeX 引擎（缺工具按「未验证」处理，不算失败）。
- **自带资源**：无（只有 `agents/openai.yaml`）

#### `ccf-visual-composer` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型（带绘图配方库）—— CCFA 里负责「把图和表画得能看」。
- **什么时候用**：论文里的数值图、可视化表格、方法/架构图要重画或美化时。
- **能干什么**：
  - 数值图：`resources/python/ccfa_plot_recipes.py` 提供可复用配方（共享尺度、实体配色、物理字号）
  - 架构图：默认走 GPT Image 2 生成，或走纯 SVG / 可编辑 PPTX 重建
  - 版式：面板/浮动体/图注位置、配色与无障碍、图标体系
  - 渲染后 QA：真去看导出文件的排版、裁切、字号、可读性
- **外部 Key / 依赖**：无需自带 API Key——**图像生成用「宿主已验证的生成能力」**，原文要求不得把未知后端冒充 GPT Image 2。Python 绘图配方只用标准库 + 你自选的绘图库。
- **自带资源**：`references/` 16 个（python-plot-recipes、architecture-diagram-generation、editable-pptx、palette-and-accessibility、render-qa 等）、`resources/python/ccfa_plot_recipes.py`、`scripts/test_plot_recipes.py`

#### `creative-thinking-for-research` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 把认知科学的 8 套创造力框架套到 CS/AI 选题上。
- **什么时候用**：想找「真正新」的方向，而不是在已有框架里挪一格的时候。
- **能干什么**：
  - 8 框架：组合创造（Bisociation）、问题重述、类比推理（结构映射）、约束操控、否定/反转、抽象阶梯、相邻可能、Janusian 辩证思维
  - 4 阶段协议：测绘空间（15min）→ 制造扰动（30min）→ 深化线索（30min）→ 评估（15min）
  - 常见创作阻塞与解锁法（含大量「如果去掉这个假设会怎样」的例子）
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：无

#### `ml-paper-writing` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型（带 LaTeX 模板）**｜写顶会 ML 论文（NeurIPS / ICML / ICLR / ACL / AAAI / COLM）的全流程：从代码仓库起稿、搭论证、核引用、到 camera-ready。
- **什么时候用**：手上有个带实验结果的 repo 要变成论文；要改某一节；要补 related work 和引用；要从一个会议的模板迁到另一个；准备 final 版本。
- **能干什么**：
  - **核心铁律：绝不凭记忆编引用**（原文：AI 生成引用的错误率约 40%）——必须走 API 搜→验证→取 BibTeX，查不到就留 `\cite{PLACEHOLDER_...}` 并明确告诉作者。
  - 从 repo 起稿的七步（读 README / 结果目录 / configs / 已有 bib → 定贡献 → 找文献 → 定结构 → 迭代）。
  - **六个会议**（NeurIPS / ICML / ICLR / ACL / AAAI / COLM）的格式要求速查（页数、camera-ready 增量、特殊要求）+ 各会议 LaTeX 模板 + 模板踩坑（复制正文时把别人的 `.sty` 也带过去、页数算法不同）；审稿人视角：他们实际先读哪几段、常见拒稿理由；投稿/重投的格式转换流程。
  - 可选装 Exa MCP 增强文献检索。
- **外部 Key / 依赖**：**无需外部 Key**。免费 API 即可（Semantic Scholar 有免费档、arXiv、Crossref/`habanero`）；Exa MCP 是**可选**加速项。要本地编译需 LaTeX（`latexmk`/`bibtex`）。
- **自带资源**：`templates/` 里 **6 套会议模板**（`neurips2025`、`icml2026`、`iclr2026`、`acl`、`aaai2026`、`colm2025`）+ `references/writing-guide.md`、`checklists.md`、`citation-workflow.md`、`reviewer-guidelines.md`、`sources.md`。

#### `presenting-conference-talks` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：把已完成的论文变成会议宣讲幻灯片——同时出 Beamer LaTeX PDF 和可编辑 PPTX，带演讲者备注和讲稿（指导型，模板都在 references 里）。
- **什么时候用**：准备 oral / spotlight / poster-talk / invited talk；ML 与系统类会议（OSDI/SOSP/ASPLOS/NeurIPS/ICML 等）。
- **能干什么**：
  - 按报告时长定页数：poster-talk 5–8 页、spotlight 8–12、oral 15–22、invited 25–40，并给"每分钟约 1 页"的经验公式。
  - 每类 talk 的逐页结构模板（问题 / 方法 / 评测亮点 / demo / 架构走查）。
  - 两套输出：Beamer（metropolis 主题、数学排版好、可版本管理）与 python-pptx（临场改得动、能套公司模板）。
  - 演讲者备注写法与时间分配指导。
- **外部 Key / 依赖**：无需外部 Key。Python 依赖 `python-pptx>=0.6.21`；Beamer 路线需本机 LaTeX 工具链（原文未显式列为依赖，属隐含要求）。
- **自带资源**：`references/slide-templates.md`（Beamer 与 python-pptx 的完整可复制模板）。

#### `systems-paper-writing` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：系统类论文（OSDI/SOSP/ASPLOS/NSDI/EuroSys）的段落级写作蓝图——每节占多少页、每段写什么、审稿人怎么挑刺（指导型）。
- **什么时候用**：写 10–12 页系统论文；要规划页数预算与段落结构；投稿前自查；回应系统会议审稿。
- **能干什么**：
  - 12 页蓝图：摘要 0.25 页、Intro 1.5–2、Background 1–1.5、Design 3–4、Implementation 0.5–1、Evaluation 3–4、Related Work 1、Conclusion 0.5，并标明 USENIX 12 页 / ASPLOS 11 页的差异。
  - 摘要 5 句法、Intro 的"问题→空白 G1..Gn→核心洞见→贡献"四段式（含 hzwer Move 1/2/3 与 Irene Zhang 的 X/Y/Z 公式）。
  - 各会议差异对照、投稿检查清单、审稿人视角指南。
  - 附带 4 个会议的 LaTeX 模板工程（含 `.sty` 与 `references.bib`）。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：`references/`（section-blueprints、writing-patterns、checklist、reviewer-guidelines、systems-conferences）、`templates/osdi2026`、`templates/sosp2026`、`templates/asplos2027`、`templates/nsdi2027`（各含 main.tex + references.bib + 官方样式）。


### 设计与前端（19 个）

#### `academic-plotting` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型（给可直接套的生成代码模板）—— 给 ML 论文出「出版级」插图。
- **什么时候用**：论文要放架构示意图或实验结果图，但不想画成默认 matplotlib 灰蓝样子的时候。
- **能干什么**：
  - 两条工作流分开：**架构/系统图**走 Gemini 文生图；**数据图**走 matplotlib/seaborn 自动选图型
  - 从论文章节里自动抽取系统组件与关系，拼 6 段式提示词
  - 内置策展配色与视觉风格，避免「企业模板感」
  - 数据图侧：图型选择、坐标轴/图例/错落排版规范
- **外部 Key / 依赖**：**Workflow 1（架构图）需要 `GEMINI_API_KEY`**（Google AI Studio 免费可申请），原文要求从环境变量读、禁止硬编码；Workflow 2（数据图）不需要 Key。库：`matplotlib>=3.8`、`seaborn>=0.13`、`numpy`、`google-genai>=1.0`。
- **自带资源**：`references/` 3 篇（data-visualization、diagram-generation、style-guide）

#### `animation-vocabulary` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 反向词典：把「那个弹一下的效果叫啥」翻译成准确术语。
- **什么时候用**：想跟设计师/AI 描述一个动效却叫不出名字，或者要搜「这叫什么」的时候。
- **能干什么**：
  - 按感觉检索术语：入场退场、编排队列、位移变换、状态转换、滚动、反馈、缓动、弹簧
  - 每次给「最佳匹配 + 1–2 个近似词 + 差异一句话」
  - 覆盖实现层词汇：keyframes、view transition、jank、掉帧、合成、`will-change`、layout thrashing
  - 只在词表内回答，没有就说没有，不硬编
- **外部 Key / 依赖**：无需外部 Key（纯术语表，知识来源 Emil Kowalski 的动画理念）。
- **自带资源**：无

#### `app-shell-ui` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型（带 12 个可打开的效果 demo）—— App Shell / macOS 风桌面工具外壳的设计规范。
- **什么时候用**：明确要做「后台工具/控制台/聊天/邮件」这类带左侧导航的应用外壳，或营销站时（两种模式分开）。
- **能干什么**：
  - 先判 surface：**App Mode**（登录后工具，持久左导航 + 任务画布）还是 **Website Mode**（营销/文档/作品集）
  - 强制明暗双主题 token（`:root[data-theme]`），禁止硬编码 `#fff`/`#000`
  - 页面组装顺序、组件层级、单滚动区约束等「不可妥协项」
  - 附带大量可直接看的 HTML demo 与 85 张素材图
- **外部 Key / 依赖**：无需外部 Key；无需构建（纯前端规范）。`scripts/sync_top50_assets.py` 只是素材同步工具。
- **自带资源**：`references/` 5 篇（tokens、components、layouts、top50-components、web-frontend）、`demos/` 12 个 HTML（+1 个共享 `_shell.css`）、`assets/` 85 张素材图、`scripts/` 1 个

#### `apple-design` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— Apple 那套「流体界面」手感：手势、弹簧、拖拽、动量、底部面板。
- **什么时候用**：明确要 Apple 风格，或要做手势驱动的交互动效时。**不适合当通用 UI 打磨技能**。
- **能干什么**：
  - 17 节原则：即时响应、1:1 直接操控、可打断性（原文称最重要）、用弹簧不用固定时长、速度交接、动量投影、空间一致性、手势方向暗示、橡皮筋边界，再延伸到材质/多模态反馈/减弱动效/排版
  - 工程细节：记录 `pointermove` 速度历史、并行识别手势、避免用 CSS transition 做手势动画
  - 附「手感检查清单」与逐帧流畅度要求
- **外部 Key / 依赖**：无需外部 Key；纯 Web 实现建议（CSS / Pointer Events / rAF / Motion 之类弹簧库）。
- **自带资源**：无

#### `baseline-ui` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 一份「防 AI 味 UI」的硬规则清单（间距、层级、排版、小布局）。
- **什么时候用**：被 `ui-skills-root` 选中，或你显式点名做窄范围 UI 清理时。
- **能干什么**：
  - Stack 规矩：Tailwind 默认值优先、JS 动画用 `motion/react`、类名逻辑用 `cn`
  - 组件规矩：键盘/焦点行为用成熟 primitive（Base UI / React Aria / Radix），禁止手搓
  - 动效规矩：非显式要求不加动画；只动 `transform`/`opacity`；不超 200ms；尊重 `prefers-reduced-motion`
  - 排版 / 布局 / 性能 / 可访问性各一组 MUST / SHOULD / NEVER
- **外部 Key / 依赖**：无需外部 Key；假定项目用 Tailwind + React 生态。
- **自带资源**：无

#### `chinese-plot-labels` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 一条很窄的硬规则：画图默认用中文标题、轴标签、图例。
- **什么时候用**：agent 要写/改 Python、Java、C++ 的绘图代码，而用户没指定别的语言时。
- **能干什么**：
  - 强制中文：标题、副标题、xyz 轴、色条、图例、系列注释、图内保存的图注
  - 覆盖 Matplotlib/Seaborn/Pandas/Plotly、JFreeChart/JavaFX、matplotlib-cpp/gnuplot/OpenCV/Qt Charts
  - 保留专有名词/变量名/单位/符号，单位用括号：`时间 (s)`
  - 给出中文字体配置（`SimHei`/`Microsoft YaHei`/`Arial Unicode MS`）和负号修复
- **外部 Key / 依赖**：无需外部 Key；需要本机有中文字体（否则导出图会变方框）。
- **自带资源**：无（只有 `agents/openai.yaml`）

#### `create-design-md` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 从一个代码仓库或一个网址，反推出一份 `DESIGN.md`。
- **什么时候用**：要给现有产品补设计规范文档，让后来的 agent 知道「这套 UI 的 token 是什么」时。
- **能干什么**：
  - 两种模式：**Repository 模式**（读源码/token，证据最硬）、**URL 模式**（读渲染后的 DOM 与计算样式）
  - 抽取 token、排版尺度、组件归属、设计理由，再压成扁平 token schema
  - 硬门禁：拿不到证据的字段一律不写，绝不把「一次出现」写成「全站规则」
  - 只写 `DESIGN.md`，不动产品源码、依赖、配置
- **外部 Key / 依赖**：无需外部 Key。**URL 模式需要能渲染浏览器**（读 DOM/计算样式/多断点），拿不到渲染访问就要求用户给截图或源码。
- **自带资源**：无

#### `diagram-design` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**可执行型**｜把架构图/流程图/时序图/ER 图等 **45 种图**画成带品牌感的**自包含 HTML（内联 SVG）**，也能吃进 draw.io / Mermaid / Excalidraw 源文件重绘。
- **什么时候用**：读者看图比看段落更省力的时候（架构、数据流、状态机、泳道、甘特、Sankey、Wardley、组织树、时间线……）；要把别人的 `.drawio` 或 Mermaid 变成好看的网页图；要深色/浅色两版。
- **能干什么**：
  - 先选"语义模式"（行为/状态/风险）再选"视觉类型"，避免为画而画。
  - **强制设计系统**：语义色板、4px 网格、角色化字号、连接线规则（每条线独立接点、不穿节点、标签必须有不透明底），复杂度预算（一张图最多几个节点）。
  - 出图前过"品味门"清单 + `scripts/self_check.py` 自动检查（可访问性 SVG 契约、内容是否溢出 viewBox、单文件安全）。
  - 导出 PNG / SVG；带无障碍 SVG 契约（title/desc、reduced-motion）。
- **外部 Key / 依赖**：**无需外部 Key**。需 Python 3（跑 `scripts/*.py`）；导出 PNG/SVG 要本机有渲染工具。**注意它带 `disable-model-invocation: true`**——不会被自动选中，得由 `figure-router` 派单或用户点名。首次在新项目用会卡一个"风格指南门"：默认纸色/墨色/强调色没改过会**停下来先问你**，防止把默认皮肤当品牌皮肤发出去。
- **自带资源**：`assets/`（**217 个**示例 HTML，每种图型都有 dark/full 版本）、`references/`（**63 份**，含 `type-*.md` 逐图型说明 + `style-guide.md` + `semantic-patterns.md` + `import-*.md`）、`scripts/`（6 个：`self_check.py`、`export_png.py`、`export_svg.py`、`drawio_extract.py`、`mermaid_extract.py`、`excalidraw_extract.py`）。⚠️ 正文提到的 `verify-geometry.py`、`verify-motion.py` 只在"仓库 checkout"里存在，**装好的技能目录里没有**。

#### `drawio-skill` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**可执行型**｜产出**可编辑的 `.drawio` 文件**（不是死图片），还能从代码/IaC/SQL/OpenAPI 反向生成图，并做同步、查询、评审、导出。
- **什么时候用**：用户说要 draw.io / diagrams.net；要"以后还要继续改"的图；有 Terraform / K8s / compose / SQL / OpenAPI / Proto / GraphQL 想直接变架构图；要一条命令生成 exec/system/deployment/dataflow/security 多视角。
- **能干什么**：
  - 统一入口 `scripts/diagramctl.py`：`build`（从 IR / 代码 / TF / K8s / compose 建图）、`sync`（改代码后增量更新但**保留手工排版**）、`views`（一套源出五种视角）、`query`（两点间路径）、`test`（架构规则/策略校验）、`whatif`（抽掉某组件看影响面）、`review`、`story`（生成可点走的导览页）。
  - Mermaid → 原生 `.drawio`（draw.io ≥30）；大型图（15+ 节点）走自动布局，不许手摆。
  - 导出规则写死：草稿 PNG **不能**加 `-e`、成品 PNG/SVG/PDF 才加；`--width` 和 `-s` 不许同时用。
  - CI 门禁（GitHub Actions 校验架构规则 / 视觉 diff）+ PR 里贴 before/after/diff 图；栅格化反解：截图 / 白板照 / 老 PNG → 可编辑图。
- **外部 Key / 依赖**：**无需外部 Key**。核心功能只要 **Python 3（纯标准库）**；原生导出需要本机装 **draw.io 桌面版**；自动布局需要 **Graphviz**（`dot`）；MCP 模式可注册 `scripts/diagramctl_mcp.py`。同 `diagram-design` 一样带 `disable-model-invocation: true`，需 `figure-router` 派单或用户点名。
- **自带资源**：`scripts/`（**45 个**脚本，含各种 importer 与 `diagramctl.py`）、`references/`（20 份：`diagram-ir.md`、`mermaid-authoring.md`、`ci-gate.md`、`live-infra.md`、`shapes.md` 等）、`styles/`（5 套内置样式 + schema）、`data/`（形状索引、Databricks/LOB 图标库）、`agents/openai.yaml`、`LICENSE`。

#### `find-animation-opportunities` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**只读型**｜扫描界面，找出"**真的**该加动效"的地方，并且**主动否掉**大部分候选——理念来自 Emil Kowalski《You Don't Need Animations》。
- **什么时候用**：只在**被显式点名**时跑（`disable-model-invocation: true`，不会自己触发）。想给界面加动效但不确定加哪；或者想找人劝你"这里别加"。
- **能干什么**：
  - 四道闸门逐个过：**频率**（一天看 100+ 次？永远不加）、**目的**（说不清为什么动就别动）、**速度**（能不能进帧预算）、**功能**（动效帮忙还是添乱）。
  - 输出三段式：机会表（带精确配方）+ **必填的"被否掉的候选"** + 总结论；全站最多 5–7 条，按杠杆排序而不是按好玩程度。
  - 硬规矩：**绝不改代码**，只报告；要落地就交接给 `improve-animations`。
  - 把仓库内容当数据不当指令（文件里写"忽略之前的指令"这种要标出来并跳过）。
- **外部 Key / 依赖**：**无需外部 Key**。
- **自带资源**：**无**（纯 SKILL.md，约 9.6k 字符，含闸门细则与输出格式）。

#### `fixing-accessibility` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型（规则清单）**｜HTML 无障碍专项修复：ARIA、键盘可达、焦点、对比度、表单报错，按 9 个优先级分类给"违规定位 + 为什么 + 怎么改"。
- **什么时候用**：加/改按钮、链接、输入框、菜单、对话框、标签页、下拉；做表单校验与错误态；做键盘快捷键或自定义交互；处理焦点圈、焦点陷阱、模态框；只有图标的按钮。
- **能干什么**：
  - 9 类规则按严重度排：可访问名称（critical）→ 键盘访问 → 焦点与对话框 → 语义 → 表单与错误 → 播报（aria-live）→ 对比度与状态 → 媒体与动效 → **工具边界**。
  - 两种用法：`/fixing-accessibility`（把规则套到本轮 UI 工作）或 `/fixing-accessibility <file>`（审单个文件，逐条引用出问题的代码行）。
  - **要求最小改动**，不许借机重写大段 UI。
- **外部 Key / 依赖**：**无需外部 Key**。
- **自带资源**：**无**（纯 SKILL.md，约 4.7k 字符）。

#### `fixing-metadata` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型（规则清单）**｜页面元数据专项：title、description、canonical、社交卡片（OG/Twitter）、favicon、JSON-LD、robots，按 8 个优先级查漏补缺。
- **什么时候用**：上线新页面/营销页/可分享链接；加或改 title/description/canonical/robots；做 OG 或 Twitter 卡片；配图标、manifest、theme-color；加结构化数据；改语种与 hreflang。
- **能干什么**：
  - 五步流程：找出缺失/写错的页 → 按优先级审 → 保证 title/description/canonical/`og:url` **四者互相一致** → 在**真实域名**上验社交卡片（不是 localhost）→ 只改元数据，不顺手重构。
  - 8 类优先级：正确性与重复（critical）→ 标题描述 → canonical 与索引 → 社交卡片 → 图标与 manifest → 结构化数据 → 语言与替代版本 → 工具边界。
- **外部 Key / 依赖**：**无需外部 Key**（社交卡片预览调试器是网页工具，不需要 key）。
- **自带资源**：**无**（纯 SKILL.md，约 4.4k 字符）。

#### `fixing-motion-performance` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型（规则清单）**｜动画卡顿专项：布局抖动、非合成属性、滚动联动动效、昂贵模糊，9 类规则逐个说"为什么慢、怎么改"。
- **什么时候用**：加/改 CSS/WAAPI/Motion/rAF/GSAP 动画；重构卡顿的过渡；做滚动关联动效或滚动显现；给 layout、filter、mask、渐变、CSS 变量做动画；审带 `will-change`/transform/测量的组件。
- **能干什么**：
  - 先给渲染步骤术语表（composite / paint / layout），让"慢在哪一层"说得清。
  - 9 类规则：绝不做的事（critical）→ 选对机制 → 测量 → 滚动 → 绘制 → 图层 → 模糊与滤镜 → View Transitions → 工具边界。
  - **不许换动画库**：必须在现有技术栈里改。
  - 同样支持"套用到本轮"或"审某个文件并引用具体代码行"两种用法。
- **外部 Key / 依赖**：**无需外部 Key**。
- **自带资源**：**无**（纯 SKILL.md，约 5.6k 字符）。

#### `improve-animations` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**只读型**｜对整个代码库做动效审计，产出**按优先级排的发现 + 可直接交给别人执行的实施方案**，自己一行代码不改。
- **什么时候用**：只在**被显式点名**时跑。觉得"整个产品动效都有点不对劲"但说不清哪里；要一份能派给别的 agent 去干的动效改造计划。
- **能干什么**：
  - 四阶段：Recon（先摸清动效都住哪：全局 CSS 变量、Tailwind 配置、keyframe、transition/animate 属性、手势处理）→ 并行审计 → 过滤排序并与用户确认 → 写方案。
  - 产出**自包含**的方案（执行者没有本次对话的上下文，方案必须自己能跑）。
  - 硬规矩：**不与修改型技能在同一次执行里混用**；不许发明平行于现有设计 token 的缓动/时长/弹簧参数。
- **外部 Key / 依赖**：**无需外部 Key**。需能读代码库。
- **自带资源**：`AUDIT.md`（**审计手册**：8 类审计项 + 每类的目标数值表，例如"一天看 100+ 次的交互——永不加动画"、"进入/退出用 ease-out"、"悬停/变色用 ease"）、`PLAN-TEMPLATE.md`（方案模板）。

#### `improve-ui` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**只读型**｜拿某个产品界面去对照**它自己**的设计体系做审计，写出实施方案——严格只读，只允许在 `design-plans/` 下建文件。
- **什么时候用**：只在被显式点名或由 `ui-skills-root` 选中时跑。想系统性审一块界面但不想让人乱改；"这个页面看着不对，说清哪儿不对"。
- **能干什么**：
  - 六步：选定**一块**界面（不贪多，不把整个仓库当一个产品）→ 重建它实际的样式系统（追 routes/layouts/compositions/共享组件/variants/tokens）→ 举证 → 过滤发现 → 出报告 → 只为你选中的改动写方案。
  - **取证纪律**：只有通过渲染、import、props、解析后的配置、CSS 继承或实际加载的产物证明过的关联才算数；"名字像""token 相似""目录挨着"一律不算。
  - 没有设计文档**不算**发现项；草稿/提案/迁移清单都当"未来意图"，除非已明确采纳且是当前状态。
  - 边界：不改产品源码、不装依赖、不跑格式化、不 commit/push、不改设计文档（要改的写进方案交给执行者）。
- **外部 Key / 依赖**：**无需外部 Key**。
- **自带资源**：`references/plan-template.md`、`agents/openai.yaml`。

#### `pick-ui-library` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：一张"什么需求用什么前端库"的精选对照表（Emil Kowalski 口味），只做选型不写实现（指导型 · 查表；`disable-model-invocation: true`，只能显式调用）。
- **什么时候用**：用户问"toast 用什么库 / 拖拽用什么 / 虚拟列表用什么 / 状态管理用什么"；或 `ui-skills-root` 判定这是依赖选型问题而派活过来。**普通 UI 实现过程中不要自动触发**。
- **能干什么**：
  - 按任务给唯一推荐：UI 原语 → base-ui、命令面板 → cmdk、Toast → Sonner、OTP 输入 → input-otp；动效 → motion、数字滚动 → NumberFlow、图表实时流 → Liveline / 常规 → recharts、拖拽 → dnd kit、长列表 → Virtuoso、状态 → zustand、className → clsx / 变体 → cva、主题切换 → next-themes。
  - 先看 `package.json`：已装了清单里的库就直接用，装了竞品只提醒不擅自换。
  - 附「常见错配」清单（手搓 Toast、div 假下拉、重渲染做数字动画、千行列表不分页等）。
- **外部 Key / 依赖**：无需外部 Key，也无需安装（纯查表）。
- **自带资源**：无附属文件，清单内嵌在 `SKILL.md`。

#### `prototype` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：一次只做一件 UI 的"发散探索"——造 3（最多 5）个真正不同的方向，挂在一个视觉选择器后面让用户翻着挑（指导型 · 流程；`disable-model-invocation: true`，只能显式调用）。
- **什么时候用**：用户说"给我几个完全不同的版本挑 / 这个卡片/Toast/按钮我想看看不同方向"。**不用于**评审已有 UI、也不是常规清理或实现既定方向。
- **能干什么**：
  - 六阶段流程：界定范围 → 摸清技术栈与设计 token → 定方向（每个方向必须能说出差异轴：布局/密度/性格/动效/交互模型）→ 搭选择器 → 验证交付 → 选中后合并。
  - 硬规则：探索期绝不碰生产代码；每个变体都得能单独上线；选择器长相固定不许改；选定后删掉原型面。
  - 变体质量底线：入场 `ease-out`、UI 动效 < 300ms、只动 `transform`/`opacity`、处理 reduced-motion。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：`PICKER.md`（选择器的 HTML/CSS/JS 规范，要求逐字复制）。

#### `review-animations` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：专挑动画/动效毛病的严格评审——十条不可谈判标准，逐条对着 diff 量（指导型 · 评审；`disable-model-invocation: true`，只能显式调用）。
- **什么时候用**：有一个具体的动效改动要评审；想按高工艺标准挑问题。**不用于**通用 UI 评审、大范围审计或写实现。
- **能干什么**：
  - 十条标准：动效要有理由、按出现频率决定要不要动（键盘操作 100+/天 → 永远不加）、入场用 `ease-out`（UI 上 `ease-in` 直接判问题）、UI 动效 < 300ms、弹出层从触发点缩放（不许从 `scale(0)`）、可中断、只动 GPU 属性、`prefers-reduced-motion` 要照顾、进出场不对称、与产品性格一致。
  - 升级触发条件、补救优先级、分层输出格式与明确的"通过/不通过"判据。
  - 默认倾向"挑刺"，通过是挣来的，不是默认给的。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：`STANDARDS.md`（缓动曲线、时长表、spring 配置、手势、clip-path、性能、无障碍的精确数值与出处）。

#### `ui-skills-root` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：所有 UI 相关任务的唯一自动入口路由器——先判断是不是 UI 活，再用 CLI 挑**最小够用**的那个 UI 专项技能（指导型 · 路由）。
- **什么时候用**：任何 UI/前端工作开工前；目标清楚就先选上下文，不清楚就先问一句。
- **能干什么**：
  - 七步协议：判断是否 UI → 否就回 `no skill needed` → 定类别 → 用 CLI 查该类目 → 选最小集 → 只加载选中的 → 按上下文实现。
  - 选量纪律：优先 1 个，最多 3 个；只读审计类和会改代码的类**不许同批混用**。
  - CLI 面：`ui-skills start|categories|list --category|get <slug>`；遇到 slug 歧义用带命名空间的写法（如 `emilkowalski/prototype`）。
- **外部 Key / 依赖**：无需外部 Key。依赖 `ui-skills` CLI（全局装，未装则回退 `npx ui-skills`，需要 Node.js/npm）。
- **自带资源**：无附属文件，只有 `SKILL.md`。


### 数据与检索（11 个）

#### `chroma` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 开源嵌入式向量库，四五个函数就能做语义检索。
- **什么时候用**：本地/自建 RAG、文档检索、语义搜索，不想一开始就上云服务时。
- **能干什么**：
  - 建集合、加文档（自动 embedding）、查询、按元数据过滤
  - 三种部署：内存 `Client()`、本地持久化 `PersistentClient(path=...)`、`chroma run` 起服务
  - 默认 embedding 用 `sentence-transformers` 的 `all-MiniLM-L6-v2`，也可换 OpenAI / HuggingFace 函数
  - 与 LangChain / LlamaIndex 集成
- **外部 Key / 依赖**：默认**无需 Key**（本地模型自动跑）。**只有显式换成 OpenAI embedding 函数时才需要 OpenAI key**。库：`pip install chromadb`（+ `sentence-transformers`）；JS 版 `npm install chromadb @chroma-core/default-embed`。
- **自带资源**：`references/integration.md`

#### `faiss` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜Meta 的向量相似度检索库——**只管纯向量**（不带元数据），能撑到十亿级，GPU 上快 10–100 倍。
- **什么时候用**：要做 k-NN / 语义检索且数据量大；要压榨吞吐和延迟；离线批量处理 embedding；**不需要**按元数据过滤（那就该用 Chroma/Pinecone/Qdrant）。
- **能干什么**：
  - 四种索引怎么选：Flat（最准最慢）、IVF（聚类近似，要训练）、HNSW（质量/速度平衡，免训练）、PQ（省内存 16–32 倍）。
  - 索引保存/加载、GPU 单卡与多卡、`nprobe` 调优。
  - 和 LangChain / LlamaIndex 对接成 vector store。
  - 度量选择：L2 距离 vs 内积（归一化后即余弦）。
- **外部 Key / 依赖**：**无需外部 Key**。需 `faiss-cpu` 或 `faiss-gpu`、`numpy`。
- **自带资源**：`references/index_types.md`。

#### `nemo-curator` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜NVIDIA 的 GPU 加速数据治理：文本/图/视频/音频的去重、质量过滤、PII 脱敏、NSFW 检测，靠 RAPIDS 横向扩。
- **什么时候用**：要从网页抓取（Common Crawl）炼训练数据；要给大语料去重（原文：模糊去重快 16 倍，8TB RedPajama v2 实测）；要按质量分筛掉垃圾；要给多模态数据打分。
- **能干什么**：
  - 四段流水线：质量过滤（30+ 启发式：词数、重复度、URL 占比）→ 去重（精确 / MinHash+LSH / 语义 embedding）→ PII 脱敏 → 分类器打分筛低质。
  - 多模态：图片审美打分 + NSFW + CLIP embedding；视频切场景抽片段；音频 ASR 后按 WER 和时长筛。
  - 多 GPU 集群初始化和分布式处理，附性能与成本对比。
- **外部 Key / 依赖**：**无需外部 Key**。需 `nemo-curator[text_cuda12]`（或 `[all_cuda12]`）+ `cudf`、`dask`、RAPIDS；CPU 版能装但慢很多；GPU 加速必须要 NVIDIA。
- **自带资源**：`references/deduplication.md`、`filtering.md`。

#### `nnsight-remote-interpretability` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜nnsight——在**不改模型代码**的前提下读写神经网络内部（激活、梯度），还能"同一份代码"直接丢到 NDIF 远端跑 70B+ 大模型。
- **什么时候用**：本地没卡但要研究大模型内部；要做 activation patching / causal tracing；要做跨 prompt 的激活共享；要研究任意 PyTorch 架构（不只 transformers）。
- **能干什么**：
  - `LanguageModel` 包装 + `trace` 上下文 + proxy 对象：把中间激活"捞"出来，模型跑完再分析。
  - **五套工作流**：激活分析（找 top 预测）、激活修补（干净激活塞进脏跑）、NDIF 远程执行（`remote=True`，代码不用改）、跨 prompt 激活共享、梯度分析。
  - 踩坑清单：模块路径各模型不同、忘了 `.save()` 出上下文就取不到、远端超时、vLLM 不支持梯度。
- **外部 Key / 依赖**：**本地跑无需 Key**；走 NDIF 远端**需要 API Key**（`NDIF_API_KEY`，在 login.ndif.us 注册后取）。需 `nnsight>=0.5`、`torch>=2.0`。
- **自带资源**：`references/api.md`、`tutorials.md`、`README.md`。

#### `pinecone` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：全托管向量数据库，零运维、自动扩容，专门给生产 RAG 用（指导型）。
- **什么时候用**：生产环境 RAG / 推荐 / 大规模语义检索；不想自己运维向量库；要 p95 < 100ms 低延迟；要稠密 + 稀疏混合检索和元数据过滤。
- **能干什么**：
  - 建索引（serverless 推荐 / pod 型）、批量 upsert、带元数据过滤的查询、命名空间隔离。
  - 混合检索：稠密 + 稀疏向量一起查。
  - 与 LangChain、LlamaIndex 的接法。
  - 和 Chroma / FAISS / Weaviate 的选型对比。
- **外部 Key / 依赖**：**必须有 Pinecone API Key**（托管 SaaS，`Pinecone(api_key=...)`），且需联网。Python 依赖 `pinecone-client`。
- **自带资源**：`references/deployment.md`。

#### `pyvene` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：斯坦福 NLP 的声明式因果干预框架——用一份 dict 配置就对模型做激活替换、因果追踪、交换干预训练（指导型）。
- **什么时候用**：做因果追踪（ROME 式定位）、激活补丁、IIT 交换干预训练；要检验"某层某位置是否因果相关"；要能复现/分享的实验配置。
- **能干什么**：
  - `IntervenableModel` + 干预配置：指定组件（attention/MLP/resid）、层、位置、干预类型（replace/swap/collect）。
  - 四条工作流：ROME 式因果追踪（层×位置热力图）、IOI 电路分析的激活补丁、可训练干预的 IIT、DAS 低秩旋转找可解释子空间。
  - 模型转向（如 Honest LLaMA）——加载预训练 steering 干预并在生成时施加。
  - 干预方案可存本地、可传 HuggingFace 复现。
- **外部 Key / 依赖**：无需外部 Key。依赖 `pyvene>=0.1.8`、`torch>=2.0.0`、`transformers>=4.30.0`。
- **自带资源**：`references/README.md`、`references/api.md`、`references/tutorials.md`。

#### `qdrant` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：Rust 写的向量检索引擎，可本地 Docker 也可云托管，过滤能力强、支持一记录多向量（指导型）。
- **什么时候用**：生产 RAG 要低延迟；要"向量 + 元数据"混合过滤；要分片副本水平扩容；要私有化部署数据不出门；要稠密 + 稀疏多向量。
- **能干什么**：
  - 建 collection、按 payload 字段过滤检索、批量 upsert。
  - 一记录多向量（dense / sparse / multi-dense）与量化（scalar / product / binary）省内存。
  - 部署形态三条路：本地 Docker、自建集群（Raft 共识 + 分片副本）、Qdrant Cloud。
  - payload 索引、性能调优与常见故障排查。
- **外部 Key / 依赖**：本地 Docker 部署**无需 Key**；连 **Qdrant Cloud 需要 `api_key`**（`QdrantClient(url=..., api_key=...)`）。Python 依赖 `qdrant-client>=1.12.0`。
- **自带资源**：`references/advanced-usage.md`、`references/troubleshooting.md`。

#### `ray-data` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：Ray 的数据管道库——CPU/GPU 流式处理超大数据集，从笔记本一路扩到几百节点（指导型）。
- **什么时候用**：数据集 > 100GB；要分布式预处理 / 分布式 ETL；批量推理；多模态数据（图像/音频/视频）加载；数据和内存装不下需要流式。
- **能干什么**：
  - 读 Parquet/CSV/JSON/图像，`map_batches` 链式变换，写回 Parquet/S3。
  - GPU 加速 transform（`num_gpus=1` 让批处理跑在卡上），含图像预处理示例。
  - 批量推理模式：每 worker 加载一次模型再流式打分。
  - 与 Ray Train / PyTorch / TensorFlow 的衔接。
- **外部 Key / 依赖**：无需外部 Key（对象存储的访问凭证视存储后端而定，原文未要求）。依赖 `ray[data]`、`pyarrow`、`pandas`。
- **自带资源**：`references/transformations.md`、`references/integration.md`。

#### `sentence-transformers` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：本地跑句向量/文本向量/图像向量的标准框架，5000+ 预训练模型、100+ 语言（指导型）。
- **什么时候用**：给 RAG 建索引要 embedding；语义相似度、语义搜索、聚类、分类；要多语言或领域专用模型；想完全本地跑、不调 API。
- **能干什么**：
  - 基本编码 + 语义搜索（`encode` → 余弦相似度 → top-k）。
  - 批量编码与性能调优、模型选择指南（速度/质量/多语言/多模态各自的推荐）。
  - 在自己的数据上微调 embedding 模型。
  - 与 LangChain / LlamaIndex 的接法。
- **外部 Key / 依赖**：无需外部 Key（模型从 HuggingFace 下载，本地推理）。依赖 `sentence-transformers`、`transformers`、`torch`。
- **自带资源**：`references/models.md`。

#### `sentencepiece` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：与语言无关的分词器——把文本当原始 Unicode 处理，BPE / Unigram 两种算法，T5、ALBERT、XLNet、mBART 都用它（指导型）。
- **什么时候用**：多语言建模、中日韩文本；要可复现的确定性词表；要在原始文本上直接训分词器（不做预分词）；要轻量部署（6MB 内存、5 万句/秒）。
- **能干什么**：
  - 训词表：`--vocab_size`、`--model_type bpe|unigram`、`--character_coverage` 等关键参数怎么设。
  - 编码/解码选项（`--extra_options`、byte fallback、`--add_dummy_prefix` 这些影响中文效果）。
  - 常见用法：加载 `.model`、批量 encode、与 transformers 集成。
  - 与其他分词方案的取舍对比。
- **外部 Key / 依赖**：无需外部 Key。依赖 `sentencepiece`、`transformers`（C++ 版需 CMake 自行编译）。
- **自带资源**：`references/algorithms.md`、`references/training.md`。

#### `transformer-lens` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：机制可解释性的事实标准库——用 HookPoint 抓取和篡改 transformer 每一层激活（指导型）。
- **什么时候用**：要逆向理解模型学到的算法；做激活补丁/因果追踪；看注意力模式与信息流；分析 induction head、IOI 这类电路；缓存中间激活做分析。
- **能干什么**：
  - `HookedTransformer.from_pretrained(...)` + `run_with_cache` 拿到全部激活，`to_tokens` / `to_single_token` 处理 token 对齐。
  - 工作流一：激活补丁（clean vs corrupted，逐层逐位置替换，看 logit 恢复比例）。
  - 工作流二：IOI 电路分析；工作流三：induction head 检测。
  - 与 SAELens 打通（用 SAE 解释某层特征）。
- **外部 Key / 依赖**：无需外部 Key；但加载 **gated 模型（LLaMA / Mistral）需要 `HF_TOKEN`**（原文给 `os.environ["HF_TOKEN"] = "your_token"`）。依赖 `transformer-lens>=2.0.0`、`torch>=2.0.0`。
- **自带资源**：`references/README.md`、`references/api.md`、`references/tutorials.md`。


### 安全与对齐（4 个）

#### `bugbounty-workflow` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型（带侦察脚本 + 报告模板）—— 授权范围内的漏洞挖掘全流程：授权确认 → 侦察 → 挖洞 → 最小影响验证 → 出报告。
- **什么时候用**：用户明确说「挖洞 / 测目标 / 打点 / 提交 SRC / 补天」并且**有授权范围**时。
- **能干什么**：
  - 阶段 0 强制先确认授权与范围（无授权直接终止）
  - `scripts/recon.sh <domain>`：子域枚举、crt.sh、历史 URL、存活探测、指纹、Nuclei 扫描，缺工具自动跳过
  - 挖洞清单：CMS 批测、边缘资产、Web 漏洞三类 checklist
  - 报告：按补天/SRC 模板出稿，证据打包成提交附件
- **外部 Key / 依赖**：无需 API Key。需要安全工具链（`subfinder`、`httpx`、`nuclei`、`gau`、`nmap`，Go 系 `go install`）；**可选**用户自有的 FOFA / Quake 账号做网络空间测绘；Windows 需 Git Bash 或 WSL2。铁律：禁 DoS、禁删改数据、禁锁账号、禁高频扫描。
- **自带资源**：`scripts/recon.sh`、`checklists/` 3 份、`templates/` 报告模板 + 补天 docx、`reference/butian-submission.md`

#### `constitutional-ai` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— Anthropic 的「宪法 AI」：先自我批评+修订做 SFT，再用 AI 反馈做 RLAIF，不用人标偏好。
- **什么时候用**：要让模型变「无害」但没人手标偏好数据，或者想复现 Claude 那套安全训练流程时。
- **能干什么**：
  - 阶段一 SL：模型对自己的回答按「宪法」批评 → 修订 → 用修订后的回答做监督微调
  - 阶段二 RLAIF：同一 prompt 采多条回答 → 模型自己判 A/B 更好 → 训奖励模型 → PPO
  - 思维链式批评（先讲理由再选 A/B），提升判断可解释性
  - 多模型投票降低单模型偏见
- **外部 Key / 依赖**：无需外部 Key（AI 反馈由本地模型产出，不是调 API）。库：`transformers`、`torch`、`trl`。**硬件要求明确**：推荐 A100/H100；7B 的 SL 阶段 1×A100 40GB，RL 阶段 2×A100 40GB（policy + reward model）。
- **自带资源**：无

#### `nemo-guardrails` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜NVIDIA 的运行时护栏：用 Colang 2.0 写规则，在 LLM 外面套一层"进门查、出门查"——越狱检测、输入输出校验、事实核查、幻觉检测、PII 过滤、毒性检测。
- **什么时候用**：要给已上线的 LLM 应用加可编程的安全规则；要一条流水线里同时管多种风险；要低延迟（原文：T4 上就能跑）。
- **能干什么**：
  - 用 Colang 定义"用户说了什么 → 机器人怎么拒"，`LLMRails` 包住你的 LLM 即生效。
  - 五种组合：越狱检测（在到达模型前就拦）、self-check 输入/输出、带检索的事实核查、Presidio 做 PII 打码、接 LlamaGuard。
  - 与"纯模型类"方案（LlamaGuard、OpenAI Moderation）的分工说明。
- **外部 Key / 依赖**：**无需外部 Key**（护栏本身；背后那个 LLM 的 key 另算；接 ActiveFence/Presidio 属可选集成）。需 `pip install nemoguardrails`。
- **自带资源**：**无**。⚠️ 正文引用了 `references/colang-guide.md`、`integrations.md`、`performance.md`，**这三份在仓库里不存在**——涉及 Colang 细节时只能靠正文那点示例。

#### `prompt-guard` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：Meta 的 86M 小分类器，专门判断"这是不是在提示注入 / 越狱"，三种标签：BENIGN / INJECTION / JAILBREAK（指导型）。
- **什么时候用**：要在把用户输入喂给 LLM 前先过滤；要检查第三方数据（网页、API 返回、RAG 文档）里有没有藏指令；给 RAG 建安全层。
- **能干什么**：
  - 三个工作流：用户输入越狱检测、第三方数据注入检测（含"嵌入命令 / 隐藏指令 / 上下文投毒"）、RAG 文档批量过滤。
  - 阈值建议表：高安全 0.3（98.5% TPR / 5.2% FPR）、均衡 0.5、低摩擦 0.7。
  - 分层防御思路：Prompt Guard（越狱）→ LlamaGuard（内容审查）→ LLM 处理 → 输出校验。
  - 硬件/性能参考：GPU 0.8–2ms/请求、500–1200 req/s；FP16 显存约 550MB。
- **外部 Key / 依赖**：**原文未明确要求 Key**（从 HuggingFace 加载 `meta-llama/Prompt-Guard-86M`，未提 token；该模型在 HF 上属受限仓库，实际下载是否要登录原文没说）。依赖 `transformers`、`torch`。模型 License 为 Llama 3.1 Community License。
- **自带资源**：无附属文件，代码示例与阈值表都在 `SKILL.md`。


### 机器人（1 个）

#### `evaluating-cosmos-policy` <sub>Jensen-Yao/agents-skills</sub>（仓库目录 `cosmos-policy`）
- **一句话**：指导型（给完整命令）—— 在 LIBERO / RoboCasa 仿真里跑 NVIDIA Cosmos Policy（视觉-语言-动作模型）的评测与延迟剖析。
- **什么时候用**：要复现 Cosmos Policy 的机器人操作评测，或者在无头 GPU 集群上配 EGL 渲染跑仿真时。
- **能干什么**：
  - LIBERO 评测：smoke（1 trial，5–10 分钟）到 full（50 trials，2–4 小时）
  - RoboCasa 评测：单任务到全任务（4–8 小时），要先下载 kitchen 资产
  - 空白机器/集群启动流程：Docker 容器 + `uv` 装依赖 + EGL 环境变量四件套
  - 推理延迟剖析
- **外部 Key / 依赖**：无需 API Key（模型权重 `nvidia/Cosmos-Policy-*` 从 HF 拉，原文未提 token）。**必须 GPU**：A40/A100，LIBERO ~16GB VRAM、RoboCasa ~18GB；需 Docker + `torch>=2.1`、`mujoco>=3.0`、`robosuite>=1.4`、Cosmos 专用 RoboCasa fork、`cosmos-policy` 源码。无头环境要一起设 `CUDA_VISIBLE_DEVICES` / `MUJOCO_EGL_DEVICE_ID` / `MUJOCO_GL=egl` / `PYOPENGL_PLATFORM=egl`。**装好后要下仿真资产（占空间、走网络）**。
- **自带资源**：`references/libero-commands.md`、`references/robocasa-commands.md`


### 框架与平台（12 个）

#### `crewai-multi-agent` <sub>Jensen-Yao/agents-skills</sub>（仓库目录 `crewai`）
- **一句话**：指导型 —— 多智能体编排框架：给每个 agent 一个角色、给每个 task 一个产出，然后组队干活。
- **什么时候用**：要把一个复杂任务拆给几个「专职 agent」协作完成，或者需要顺序/分层执行 + 记忆时。
- **能干什么**：
  - 三件套：Agents（角色/目标/背景故事）、Tasks（工作单元）、Crew（编排 + 执行）
  - 顺序（sequential）与分层（hierarchical，带 manager LLM）两种流程
  - `crewai` CLI 建项目骨架、`crewai install/run`
  - 50+ 内置工具，也可自定义工具、接 MCP
- **外部 Key / 依赖**：**需要 LLM 提供方的 key**——默认 OpenAI（`OPENAI_API_KEY`），也可换 Anthropic / Azure / 本地 Ollama；`crewai[tools]` 里的部分工具另有自己的 key（如 DALL-E 要 OpenAI key）。库：`crewai>=1.2.0`、`crewai-tools>=1.2.0`。
- **自带资源**：`references/` 3 篇（flows、tools、troubleshooting）

#### `dspy` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜斯坦福 NLP 的"声明式 LM 编程"：你只写**签名**（输入→输出）和**模块**，prompt 交给优化器自动调。
- **什么时候用**：受够了手写 prompt 试来试去；要多组件 AI 流水线还能维护；有标注数据想自动提升效果；要搭模块化 RAG / agent / 分类器。
- **能干什么**：
  - 四个核心概念：Signature（声明输入输出）、Module（`Predict` / `ChainOfThought`）、Optimizer（用数据和指标自动调 prompt/示例）、组合成多步系统。
  - 四种常用模式：结构化输出、断言驱动的优化、自一致性、带重排的检索。
  - 多 provider 配置（Anthropic / OpenAI / Ollama 本地）、按任务分模型（检索用便宜模型、推理用强模型）。
  - 存/载优化后的程序；自定义指标与评测。
- **外部 Key / 依赖**：**要 Key**——本体不绑厂商，但必须配一个 LM：Anthropic（`ANTHROPIC_API_KEY`）或 OpenAI（`OPENAI_API_KEY`）；想完全免费可指 Ollama 本地模型。需 `pip install dspy`。
- **自带资源**：`references/modules.md`、`optimizers.md`、`examples.md`。

#### `guidance` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜微软研究院的受约束生成：用正则和文法**把 LLM 的输出卡死**，保证吐出来的 JSON/XML/邮箱/日期一定合法。
- **什么时候用**：输出格式必须 100% 合法；要在一堆固定选项里选（`select()`）；要写多步流程还想控制每一步；讨厌"模型又多加了一个空格/引号"。
- **能干什么**：
  - 正则约束（邮箱、日期、电话）、`select()` 枚举、文法（JSON grammar）。
  - **Token healing**：自动回退一个 token 重新生成，修掉"the capital of France is␣␣Paris"里的双空格。
  - 上下文管理器组织 system/user/assistant 消息；Guidance 函数复用。
  - 五种模式：JSON 生成、分类、多步推理、ReAct agent、数据抽取。
- **外部 Key / 依赖**：**要 Key**（用云端时）——Anthropic（`ANTHROPIC_API_KEY`）或 OpenAI（`OPENAI_API_KEY`）；**用本地模型则不需要**（`pip install guidance[transformers]` 或 `[llama_cpp]`）。
- **自带资源**：`references/constraints.md`、`backends.md`、`examples.md`。

#### `instructor` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜让 LLM 直接产出**经过 Pydantic 校验**的结构化对象——校验不过自动带上错误信息重试，不用自己写解析和兜底。
- **什么时候用**：要从文本里抽字段；要类型安全地拿复杂 JSON；要流式拿部分结果；要在 OpenAI / Anthropic / Ollama 之间切但保持同一套写法。
- **能干什么**：
  - `response_model` 定义结构 → 拿回真对象（不是字符串）；字段可选/枚举约束。
  - **自动重试**：校验失败会把 "Validation error: age - field required" 回喂给模型重抽（默认最多 3 次）。
  - 流式部分结果（列表逐项生成）、Union 类型、运行时动态建模、自定义 mode（`ANTHROPIC_TOOLS` / `TOOLS` / `JSON` 兜底）。
  - 五种常用模式：文本抽取、分类、多实体抽取、结构化分析、批量处理。
- **外部 Key / 依赖**：**要 Key**（取决于你用哪家）——OpenAI / Anthropic 各自的 key；本地 Ollama 填个占位串即可（原文 `api_key="ollama"  # Required but ignored`）。需 `instructor`、`pydantic`。
- **自带资源**：`references/providers.md`、`validation.md`、`examples.md`。

#### `langchain` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜最主流的 LLM 应用框架：模型抽象、链、ReAct agent、记忆、RAG、500+ 集成，换个 provider 只改一行。
- **什么时候用**：要快速搭 chatbot / 问答 / RAG / 自主 agent；要在 OpenAI、Anthropic、Google 之间随意切；要接一堆向量库和文档加载器；要 LangSmith 追踪。
- **能干什么**：
  - 四层核心：Models（统一抽象 + 流式）、Chains（prompt 模板串起来）、Agents（工具调用 + ReAct）、Memory（多轮）。
  - 完整 RAG 六步（加载 → 切块 → embedding → 向量库 → retriever → QA 链）+ 带记忆的对话式 RAG。
  - 高级 agent：结构化输出、**并行工具调用**、流式展示每一步。
  - 向量库（Chroma/Pinecone/FAISS）、文档加载器（网页/PDF/GitHub/CSV）、切分器（递归/代码感知/语义）。
- **外部 Key / 依赖**：**要 Key**——至少一个模型 provider 的 key（`langchain-openai` / `langchain-anthropic` …）；`LANGCHAIN_API_KEY` 只是**开 LangSmith 追踪时**才需要。需 `langchain` + 对应 provider 包，Python 3.10+。
- **自带资源**：`references/rag.md`、`agents.md`、`integration.md`。

#### `langsmith-observability` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜LLM 可观测性平台：把每次调用的输入/输出/延迟/花费记下来，能建数据集跑评测、也能盯线上。
- **什么时候用**：调 LLM 应用调不出来（到底是 prompt 问题还是检索问题）；要按数据集系统评模型输出；要盯生产系统的延迟和成本；要给 AI 功能做回归测试。
- **能干什么**：
  - `@traceable` 装饰器或 OpenAI 包装器，自动上报 trace；项目/运行层级组织。
  - 数据集 + 评测：建数据集、加样本、跑评估器（内置 + 自定义）。
  - prompt Hub 拉取/复用；异步客户端；用户反馈回收；采样与敏感字段隐藏。
  - pytest 集成 + CI 里跑评测。
- **外部 Key / 依赖**：**要 Key**——`LANGSMITH_API_KEY`（云服务；自托管则配自己的地址）。需 `langsmith>=0.2`；LangChain 应用设 `LANGSMITH_TRACING=true` 即自动追踪。
- **自带资源**：`references/advanced-usage.md`、`troubleshooting.md`。

#### `mlflow` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜跟框架无关的 ML 全生命周期平台：记实验（参数/指标/产物）→ 模型注册表（带版本和阶段）→ 部署 → 复现。
- **什么时候用**：跑了一堆超参组合记不住哪个好；要管"哪个版本的模型在生产"；要把模型从实验推到 staging/production；要跨 PyTorch / sklearn / Keras / HF 统一记录。
- **能干什么**：
  - 基础追踪（experiment / run / params / metrics / artifacts / model）与**autologging**（一行开启，PyTorch Lightning、sklearn、XGBoost、HF 都支持）。
  - 模型注册表：注册、版本、阶段迁移（staging → production → archived）、按阶段加载。
  - 复杂条件搜 run；本地 `mlflow models serve` 起服务；导出 SageMaker / Azure ML。
  - 自建 tracking server（Postgres 后端 + artifact 存储）。
- **外部 Key / 依赖**：**无需外部 Key**（默认本地文件存储、`localhost:5000` UI 即可用）。要远端后端才需要对象存储/数据库凭据（`boto3`、`sqlalchemy`）；要部署到 SageMaker/Azure 才需要那两家的账号。
- **自带资源**：`references/tracking.md`、`model-registry.md`、`deployment.md`。

#### `outlines` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：结构化生成库——用下推自动机在采样阶段就把不合法的 token 掐掉，保证输出一定是合法 JSON / XML / 正则 / 代码（指导型）。
- **什么时候用**：模型必须吐合法 JSON 或匹配某个 schema 时；要用 Pydantic 模型直接当输出类型；要不改模型就强制格式；要零额外开销（不是生成完再重试）。
- **能干什么**：
  - Pydantic 模型 → JSON Schema → CFG → FSM，逐 token 过滤非法候选，并在只剩一个合法 token 时快进。
  - 三种生成器：多选（`generate.choice`）、JSON（`generate.json`）、正则（`generate.regex`）。
  - 后端可选：Transformers、llama.cpp（GGUF）、vLLM；OpenAI 仅有限支持（部分特性不可用）。
  - 常见模式：信息抽取、二分类/多分类、结构化数据生成。
- **外部 Key / 依赖**：无需外部 Key（默认本地模型）。仅当走 OpenAI 后端时示例里才出现 `api_key`，且原文标注"limited support"。依赖 `outlines`、`transformers`、`vllm`（按后端选装）、`pydantic`。
- **自带资源**：`references/backends.md`、`references/examples.md`、`references/json_generation.md`。

#### `phoenix` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：Arize 的开源 LLM 可观测平台——追踪、评估、数据集、实验、实时监控，可完全自托管（指导型）。
- **什么时候用**：LLM 应用出问题要按 trace 逐层排查；要在数据集上跑评估；要监控生产 LLM；要对比不同 prompt / 模型；不想被 SaaS 厂商绑定。
- **能干什么**：
  - 基于 OpenTelemetry 的追踪：接 OpenAI / LangChain / LlamaIndex / Anthropic，也可手动建 span。
  - LLM-as-judge 评估器（内置 + 自定义），支持在数据集上批量跑并把结果回写 Phoenix。
  - 数据集版本化、实验对比、Playground 多模型试 prompt。
  - 部署选择：本地 notebook 内嵌、命令行服务、PostgreSQL 或 SQLite 存储。
- **外部 Key / 依赖**：自托管本地跑**无需 Key**（默认 SQLite 临时库）。仅当开启鉴权时需要 `PHOENIX_ENABLE_AUTH=true` + `PHOENIX_SECRET`（JWT 签名密钥）+ `PHOENIX_ADMIN_SECRET`；用 PostgreSQL 时配 `PHOENIX_SQL_DATABASE_URL`；另有 `PHOENIX_PORT` / `PHOENIX_HOST` / `PHOENIX_GRPC_PORT` / `PHOENIX_WORKING_DIR`。Python 依赖 `arize-phoenix>=12.0.0`（可选 `[embeddings]`、`arize-phoenix-otel`）。
- **自带资源**：`references/advanced-usage.md`、`references/troubleshooting.md`。

#### `swanlab` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：国产开源实验追踪工具，可云端、可本地、可自托管，图表和多媒体日志都支持（指导型）。
- **什么时候用**：要记录指标/配置/标签并画训练曲线；要对比多次 run；不想依赖国外托管 SaaS；要和 PyTorch / Transformers / Lightning / Fastai 集成。
- **能干什么**：
  - `swanlab.init(project=..., config=...)` + `swanlab.log({...})` 的基本记录流。
  - 三种部署模式：云端、`mode="local"` 本地、自托管（配 host）。
  - 多媒体日志：图像、音频、文本、GIF、点云、分子。
  - 与四大框架的集成写法。
- **外部 Key / 依赖**：本地模式**无需 Key**；云端或自托管需要 `SWANLAB_API_KEY`（`swanlab login` 或 `swanlab.login(api_key=...)`）。依赖 `swanlab>=0.7.11`、`pillow>=9.0.0`、`soundfile>=0.12.0`；本地看板另需 `swanlab[dashboard]`。
- **自带资源**：`references/integrations.md`、`references/visualization.md`。

#### `tensorboard` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：Google 的 ML 可视化老牌工具——曲线、直方图、模型图、embedding 投影、性能剖析（指导型）。
- **什么时候用**：要看 loss/accuracy 曲线；要用直方图查梯度/权重分布；要对比多次实验；要看模型结构图；要做 t-SNE/PCA embedding 投影；要 profile 性能瓶颈。
- **能干什么**：
  - PyTorch `SummaryWriter` 与 TensorFlow/Keras callback 两套接法。
  - 记录标量、直方图、图像、文本、模型图、embedding。
  - 多 run 对比（按 `--logdir` 分目录）与 HParams 面板。
  - TensorBoard Profiler 的用法与常见瓶颈定位。
- **外部 Key / 依赖**：无需外部 Key（纯本地，浏览器看 `localhost:6006`）。依赖 `tensorboard`、`torch`、`tensorflow`（按框架选装）。
- **自带资源**：`references/visualization.md`、`references/integrations.md`、`references/profiling.md`。

#### `weights-and-biases` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：W&B——自动记录实验、实时看训练、sweep 调超参、管模型注册表（指导型）。
- **什么时候用**：要跨机器/跨人共享实验记录；要跑自动化超参搜索；要做数据集/模型的血缘追踪；团队协作。
- **能干什么**：
  - `wandb.init` / `wandb.log` 自动记录指标、配置、系统资源。
  - Sweeps：网格/随机/贝叶斯搜索配置与早停。
  - Artifacts：数据集与模型版本化、血缘追踪。
  - 100+ 框架集成（PyTorch / TF / HuggingFace 等）。
- **外部 Key / 依赖**：**需要 `WANDB_API_KEY`**（`wandb login` 会生成/写入；也可直接 `export WANDB_API_KEY=...`）。Python 依赖 `wandb`。定价：公开项目免费（含 100GB），学生/研究者免费学术版。
- **自带资源**：`references/sweeps.md`、`references/artifacts.md`、`references/integrations.md`。


### 工具与CLI（37 个）

#### `autoresearch` <sub>Jensen-Yao/agents-skills</sub>（仓库目录 `0-autoresearch-skill`）
- **一句话**：指导型 —— 端到端「自主科研」编排器：双循环（内循环跑实验、外循环综合并改方向），自己是项目经理、领域技能负责执行。
- **什么时候用**：想扔一个研究问题给 agent 让它自己跑几天、自己迭代实验、最后出报告和论文时。
- **能干什么**：
  - 建工作区：`research-state.yaml`、`research-log.md`、`findings.md`、`literature/`、`experiments/{假设}/`
  - 内循环：围绕明确优化目标快速做实验；外循环：综合结果、识别模式、决定转向
  - 强制先设「墙钟循环」保活：Claude Code 用 `/loop 20m`，OpenClaw 用 20 分钟 cron
  - 文献回补（Exa MCP / Semantic Scholar / arXiv）、进度报告（HTML/PDF）、最后交接给论文写作技能
- **外部 Key / 依赖**：无需自有 API Key，但**必须有能持续跑 agent 的宿主**（Claude Code 的 `/loop` 或 OpenClaw 的 `cron.add`），否则一轮就停。可选：Exa MCP（没有可跳过）；`pip install semanticscholar`、`pip install arxiv`。算力「有啥用啥」，无 GPU 就降规模跑 CPU。
- **自带资源**：`references/` 3 篇（agent-continuity、progress-reporting、skill-routing）、`templates/` 4 个（research-state.yaml、findings.md、research-log.md、progress-presentation.html）

#### `evolving-ai-agents` <sub>Jensen-Yao/agents-skills</sub>（仓库目录 `a-evolve`）
- **一句话**：指导型 —— 用 LLM 驱动的进化算法自动优化 agent（提示词、技能、工作区），可对基准刷分。
- **什么时候用**：想造「会自我改进的 agent」，或者要在某个 benchmark 上自动调 agent 的提示词/技能时。
- **能干什么**：
  - 三行跑起来：`Evolver(agent=..., benchmark=...).run(cycles=10)`
  - 三个可插拔接口：Agent（实现 `solve()`）、Benchmark（`get_tasks()`/`evaluate()`）、Engine（`step()`）
  - 每次改动都有 git 版本化历史，可复现、可回滚
  - 内置 SWE-bench Verified、MCP-Atlas 等基准配置
- **外部 Key / 依赖**：**需要 LLM 提供方凭据**——Anthropic 设 `ANTHROPIC_API_KEY`，走 Bedrock 则配 AWS 凭据（原文在排错节明确要求）。库：`pip install a-evolve`（`[anthropic]`/`[all]` 带对应支持）、`pyyaml>=6.0`。
- **自带资源**：`references/` 8 篇（api、architecture、design-patterns、examples、tutorials、issues、releases 等）

#### `alibabacloud-find-skills` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型（走 curl 调公开 HTTP API）—— 搜阿里云的 agent 技能市场，按类目浏览，需要时帮你装。
- **什么时候用**：想找「阿里云有没有现成 skill 能干 X」，或者要按类目逛技能市场时。
- **能干什么**：
  - 意图/关键词检索（`searchMode=semantic`）、列类目、取单个 skill 详情
  - 多意图拆解：一个复合需求拆成多个可检索单元分别搜
  - 搜不准时自动改写查询词再试，再不行才报「没有」
  - 需要安装时走 `npx skills add` 或 `npx clawhub install`
- **外部 Key / 依赖**：**无需 Key**——直接 `curl` 打公开接口 `https://agentexplorer.aliyuncs.com`（原文明确要求不要用阿里云 CLI 或装 agentexplorer 插件）。装技能需要 Node/npx；联网必需。注意：查询词会发给外部服务，原文提醒别带密钥/内部信息。
- **自带资源**：`references/` 7 篇（agentexplorer-api、search-examples、curl-shell-compatibility、npx-skills-agents 等）、`skill-card.md`、`_meta.json`

#### `audiocraft-audio-generation` <sub>Jensen-Yao/agents-skills</sub>（仓库目录 `audiocraft`）
- **一句话**：指导型 —— Meta 的音频生成库：MusicGen 文生音乐、AudioGen 文生音效。
- **什么时候用**：要给视频/演示配一段「描述生成」的 BGM，或者造音效、做旋律条件续写时。
- **能干什么**：
  - 文本 → 音乐（MusicGen，支持旋律条件）
  - 文本 → 音效（AudioGen）
  - 两种用法：`audiocraft` 原生库，或 HuggingFace Transformers 的 `MusicgenForConditionalGeneration`
  - 生成参数：时长、guidance scale、采样
- **外部 Key / 依赖**：无需外部 Key（模型从 HF 公开权重拉）。库：`audiocraft`（或 `transformers`+`torch`+`torchaudio`）、`torch>=2.0`。**要 GPU 才实用**：musicgen-small FP32 ~4GB / FP16 ~2GB，medium ~8GB，large ~16GB。
- **自带资源**：`references/advanced-usage.md`、`references/troubleshooting.md`

#### `blip-2-vision-language` <sub>Jensen-Yao/agents-skills</sub>（仓库目录 `blip-2`）
- **一句话**：指导型 —— BLIP-2：冻结的图像编码器 + 冻结的 LLM，中间只训一个 Q-Former，做图像描述/视觉问答。
- **什么时候用**：要给一批图自动写描述、做 VQA、图文匹配，又不想微调大模型时。
- **能干什么**：
  - 图像描述（captioning）、视觉问答（VQA）、图文检索/匹配
  - 零样本能力强；骨干全冻结，省训练成本
  - 两种调用：HuggingFace Transformers（推荐）或 Salesforce 官方 LAVIS
  - 四个规格可选：`opt-2.7b` ~4GB、`opt-6.7b` ~8GB、`flan-t5-xl` ~5GB、`flan-t5-xxl` ~13GB
- **外部 Key / 依赖**：无需外部 Key（`Salesforce/blip2-*` 为公开权重）。库：`transformers>=4.30`、`torch>=1.10`、`Pillow`、`accelerate`；走 LAVIS 则 `pip install salesforce-lavis`。有 GPU 才快。
- **自带资源**：`references/advanced-usage.md`、`references/troubleshooting.md`

#### `cli-anything` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型（带安装/生成脚本）—— 给 GUI 软件或源码仓库造一个「CLI harness」，让 agent 能用命令行操作它。
- **什么时候用**：想让 Codex 去驱动一个没有 API 的桌面软件（GIMP 之类），或者要给某个仓库生成可复用的 CLI 封装时。
- **能干什么**：
  - 五种模式：Build（造）/ Refine（改）/ Test / Validate / List（列出已装的）
  - 完整方法论在 `references/HARNESS.md`，每种模式有独立规范
  - 生成的 Python harness 格式固定，配套 REPL 皮肤、预览包、skill 生成器
  - 预览协议（PREVIEW_PROTOCOL）、会话锁、时码精度、PyPI 发布等指南
- **外部 Key / 依赖**：无需外部 Key。资源缺失时会去 clone `https://github.com/HKUDS/CLI-Anything`；安装脚本 `scripts/install.sh` / `install.ps1`；需要 Python。
- **自带资源**：`references/` 15 个（HARNESS.md、commands/、guides/、docs/）、`scripts/` 6 个、`tests/` 1 个

#### `cli-creator` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— 从 API 文档 / OpenAPI / curl 示例 / SDK / 网页应用，造一个「能在任何仓库里跑、输出稳定 JSON」的 CLI。
- **什么时候用**：需要一个可组合的命令行工具去读写某个服务的资源，而不是每次手搓 curl 时。
- **能干什么**：
  - 先选运行时：默认 **Rust**（单二进制、装到 `~/.local/bin`），需要官方 SDK/浏览器自动化就 **TS/Node**，数据科学/本地文件处理用 **Python**
  - 定命令契约：list / resolve / read / write / raw 逃生口，`--json` 下错误也必须机器可读
  - 认证优先级：环境变量（如 `GITHUB_TOKEN`）→ 用户配置文件 → `--api-key` 仅一次性测试
  - 附配套 skill（companion skill）让 agent 会用这个 CLI
- **外部 Key / 依赖**：**不给具体服务的 key**，但设计上要求按服务标准环境变量注入（示例 `GITHUB_TOKEN`）；实际用哪个服务就配哪个 key。工具链：`cargo` / `node`+`pnpm` / `python3`+`uv`（原文开工先 `command -v` 探一遍）。
- **自带资源**：`references/agent-cli-patterns.md`

#### `clip` <sub>Jensen-Yao/agents-skills</sub>
- **一句话**：指导型 —— OpenAI CLIP：零样本图像分类、图文相似度、跨模态检索。
- **什么时候用**：没有标注数据却想给图片分类，或者要做「以文搜图」/内容审核时。
- **能干什么**：
  - 零样本分类：给出候选标签文本，算图文相似度取最大
  - 图像/文本 embedding 抽取（归一化后算余弦相似度）
  - 五种规格：RN50、RN101、ViT-B/32（推荐）、ViT-B/16、ViT-L/14
  - 支持批量与 GPU 加速
- **外部 Key / 依赖**：无需外部 Key。库：`pip install git+https://github.com/openai/CLIP.git` + `torch torchvision ftfy regex tqdm`（或走 `transformers`）。CPU 能跑但慢，**GPU 快 10–50 倍**。原文提醒：标签要具体，模糊词效果差。
- **自带资源**：`references/applications.md`

#### `dramaclaw` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**操作型（流程编排 + 本机排障手册，另带一个看门狗脚本）**｜按官方流水线操作 / 检查 / 配置 / 排障 / 继续 DramaClaw（虾导/虾料/虾塘/虾镜/虾画/虾格/虾条）的短剧视频项目。
- **什么时候用**：用户提到 DramaClaw、NovelVideo 或任何一个"虾 X"；要建项目、同步 CLI/API/Web 状态、配模型与存储；要生成或修一集作品；说"继续/接着做"。
- **能干什么**：
  - **十道确认闸门**：文本 → 风格 → 资产 → 剧集规划 → 脚本/Beat → 草图 → 首帧 → 音频 → 视频 → 合成。每道都要"确认过"才算过——**"已生成/已选中"不等于"用户批准"**。
  - 说"继续"时先去读真实流水线状态，找**最早未完成**的那道闸门，只推进对应的下一步，不许把"没审"当"通过"。
  - 虾画规则：输出都只是候选，必须显式写回到指定槽位才算正式资产；写完要重读主线状态，不能假设已同步。
  - 本机（`F:\DramaClaw`）专属约束：`uv` 优先、PowerShell 是 GBK 不许内联中文、8780 走看门狗、5173 是别的应用（本项目前端 5174）、虾画要写个人画布 `user_local_17cvc3s` 而不是 `default`；**上游 `git pull` 有雷**——本地有未提交修复，先看 `LOCAL-PATCHES.md`，用 `fetch && rebase` 而非 `pull --ff-only`，更新后重跑自检。
- **外部 Key / 依赖**：**无需外部 Key**（本地项目；它调用的模型/接口由 DramaClaw 自己的配置管）。依赖：本机 `F:\DramaClaw` 仓库、`uv`、ffmpeg、Python venv、跑在 8780 的 API。
- **自带资源**：`references/`（**14 份**：`product-manual.md` 产品手册、`official-skill.md`、`official-api-reference.md`、`official-async-tasks.md`、`official-playbook-init/episode/resume.md`、`local-patches-and-pitfalls.md` 等）、`scripts/run-api-watchdog.cmd`（API 看门狗，自动重启 + 注入 ffmpeg PATH）、`patches/dramaclaw-local-fixes.patch`（本地修复补丁）、`agents/openai.yaml`。

#### `figure-router` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**纯路由**｜画图需求的**唯一自动入口**：它自己不画，只判断该交给哪个画图技能，然后**只加载一个**。
- **什么时候用**：用户说"画个图 / 绘图 / 作图 / 论文图 / 架构图 / 流程图 / 示意图 / 数据图表 / ER 图 / 时序图"，但没说是哪种技术路线。
- **能干什么**：
  - 路由表：实验数据图 → `academic-plotting`；品牌化 HTML 图 → `diagram-design`；可编辑 `.drawio` / 代码转图 → `drawio-skill`；CCFA 管线内出图 → `ccf-visual-composer`；**从 PDF 里抠现成图** → `sci-figure`。
  - 平局规则按"最终要什么格式"定：要能继续改 → draw.io；要好看的网页图 → diagram-design；要 matplotlib/PDF → academic-plotting。
  - 只在**真歧义**时问一句（比如"画个架构图"但没说要不要可编辑）。
- **外部 Key / 依赖**：**无需外部 Key**。（真正干活的那个技能有什么依赖，由它自己负责。）
- **自带资源**：**无**——纯 SKILL.md，全文约 2.2k 字符，一张路由表 + 一段平局规则，是本批里最轻的一个。

#### `hatch-pet` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**可执行型**｜从角色设定图、品牌线索或参考图做出 **Codex 兼容的动画宠物**——8×9 精灵表 + QA 接触表 + `pet.json` 打包，全流程自带确定性脚本。
- **什么时候用**：想给 Codex 做一个自定义宠物；要非像素风的角色；要公司/客户吉祥物；要一整套带动画的 8×9 图谱。
- **能干什么**：
  - 视觉生成**全部外包给 `$imagegen`**（自己不直接调图像 API）；确定性部分自己干：镜像补 `running-left`、抽帧、校验每一行、拼总图、出接触表与动效预览。
  - 品牌发现：只有给了品牌/公司名而没有具体形象时，才派一个轻量 subagent 联网找 2–4 个官方来源，产出简报（并明确标注哪些是推断）。
  - **存储与配额控制**：一个视觉任务一个 worker、worker 只回 `selected_source`/`qa_note`、父 agent 只审最终接触表——避免 rollout 里塞满 base64。
  - 修复流程：拿规范底图 + 原参考 + 接触表 + 具体失败说明，只重做坏的那一行。
- **外部 Key / 依赖**：**无需外部 Key**——走内置 `image_gen` 工具即可；**只有**在用户显式要求改用 CLI 兜底路径时才需要 `OPENAI_API_KEY`（并要用户二次确认）。需 Python 3 跑脚本、`jq`；依赖已安装的 `$imagegen` 技能。
- **自带资源**：`scripts/`（**8 个**：`compose_atlas.py`、`validate_atlas.py`、`extract_strip_frames.py`、`inspect_frames.py`、`make_contact_sheet.py`、`render_animation_previews.py`、`prepare_pet_run.py`、`derive_running_left_from_running_right.py`）、`references/codex-pet-contract.md`、`qa-rubric.md`、`animation-rows.md`、`LICENSE.txt`、`agents/openai.yaml`。

#### `huggingface-tokenizers` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜HF 的高速分词器（Rust 内核）：**1GB 文本 20 秒内**分完，支持 BPE / WordPiece / Unigram，还能自己训词表。
- **什么时候用**：分词成了流水线瓶颈；要从零训自己的 tokenizer；要字符级对齐（token ↔ 原文位置）；要处理超长语料的批处理。
- **能干什么**：
  - 载预训练分词器、编码/解码、批量 padding 与 truncation。
  - 训自定义 BPE：配置 trainer（词表大小、特殊 token）→ 喂文件/迭代器（大数据流式）→ 保存。
  - 完整流水线四步（normalization / pre-tokenization / model / post-processing），BERT/GPT-2/RoBERTa 各自动加什么特殊 token。
  - **对齐追踪**：拿到每个 token 的 `(start, end)` 偏移，做高亮或证据定位必需；转成 `PreTrainedTokenizerFast` 给 transformers 用；多进程处理大语料。
- **外部 Key / 依赖**：**无需外部 Key**（从 Hub 拉公开分词器不需要登录）。需 `tokenizers`、`transformers`、`datasets`。
- **自带资源**：`references/algorithms.md`、`pipeline.md`、`training.md`、`integration.md`。

#### `imagegen` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**可执行型（双模式）**｜生成或编辑**位图**（照片、插画、纹理、精灵图、样机、透明底抠图）；默认走宿主内置的 `image_gen` 工具，CLI 脚本只是兜底。
- **什么时候用**：要一张新图；要参考图做风格/构图变体；要改图（局部重绘、换天换光、换背景、去物体、合成、透明底）；要一批素材。
- **能干什么**：
  - **模式一（首选）**：内置 `image_gen` 工具，**不需要 `OPENAI_API_KEY`**；配套内置保存路径策略（生成后由你自己搬到项目里，别指望传路径参数）。
  - **模式二（兜底）**：`scripts/image_gen.py` 的 `generate` / `edit` / `generate-batch`，需要 `OPENAI_API_KEY`（若装了 `$image-gen-fuck` 伴生技能，其 wrapper 会临时注入 key，**就别再让用户去设全局 key**）。
  - 透明图路线：先用内置模式生成**纯色 chroma-key 背景**，再用 `remove_chroma_key.py` 本地抠（自动取色、软遮罩、去边缘溢色）；真·原生透明只有 CLI 的 `gpt-image-1.5` 支持，且**降级到它必须先问用户**。
  - 提示词增强与用例分类（精确 slug）、按素材类型给不同写法；并明确"不该用"的场景——改现有 SVG/图标体系、画简单图形/线框/图标、直接 HTML/CSS/canvas 就能做的，都别用图像生成。
- **外部 Key / 依赖**：**默认无需 Key**（内置工具）；**CLI 兜底才要** `OPENAI_API_KEY`（可选 `OPENAI_BASE_URL` 指向兼容网关）。需 Python（跑 `scripts/`）；会与 `$imagegen`/`$image-gen-fuck` 伴生技能协作。
- **自带资源**：`scripts/image_gen.py`、`scripts/remove_chroma_key.py`、`references/prompting.md`、`sample-prompts.md`、`cli.md`、`image-api.md`、`codex-network.md`、`assets/`（示例图）、`agents/openai.yaml`、`LICENSE.txt`。⚠️ 目录里还躺着两个 `SKILL.md.bak-*` 备份文件（约 2026-06-20 的旧版），属仓库清理残留。

#### `karpathy-guidelines` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型（行为准则）**｜四条短规则专治 LLM 写代码的老毛病：过度设计、乱改邻居代码、悄悄替你拿主意、不知道什么时候算完。
- **什么时候用**：写、审、重构任何代码时都可以挂着；尤其是"AI 又给我写了一大坨没用的抽象"的时候。
- **能干什么**：
  - **先想再写**：假设要明说；有多种解释就摆出来别静默选一个；有更简单的方案就直说、该反驳就反驳；不清楚就停下来问。
  - **简单优先**：不加没要的功能、不给一次性代码造抽象、不做没要求的"灵活性"、不为不可能发生的场景写错误处理；**200 行能压到 50 行就重写**。
  - **外科手术式改动**：不动相邻代码/注释/格式、不重构没坏的东西、跟随既有风格；只清理**自己**造成的孤儿 import/变量；发现无关死代码只提醒不删。
  - **目标驱动**：把任务翻译成可验证目标（"加校验"→"先写非法输入的测试再让它过"）；多步任务列"步骤 → 验证方式"。
- **外部 Key / 依赖**：**无需外部 Key**。纯文本准则，无任何依赖。
- **自带资源**：**无**——纯 SKILL.md，全文约 2.5k 字符（本批第三短，只比 `figure-router`、`llama-factory` 长），四条规则两分钟读完。

#### `lambda-labs-gpu-cloud` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜Lambda Labs 的 GPU 云：预留或按需租机、SSH 直连、持久化文件系统、最多 512 卡的多机集群，预装好 PyTorch/CUDA 那套（Lambda Stack）。
- **什么时候用**：要独占 GPU 实例（不是共享抢占）；训练要跑几小时到几天；要持久存储（重启不丢 checkpoint）；要 16–512 卡 InfiniBand 集群；不想自己装 CUDA。
- **能干什么**：
  - 控制台开机器 → SSH → 验证 Lambda Stack（GPU / PyTorch / CUDA 版本）。
  - Python API（`lambda_cloud_client`）：列实例类型、开机、列运行中、销毁、管 SSH key。也给了 `curl` 版本。
  - 持久化文件系统：创建、挂载、checkpoint 存这里（本地 SSD 快但会丢）。
  - 多卡训练（`torchrun` 8 卡）、JupyterLab 端口转发、1-Click Clusters 的 Slurm 多机训练；外加选卡与省钱建议（长期跑用预留、推理用便宜的 A10）。
- **外部 Key / 依赖**：**要 Key**——`LAMBDA_API_KEY`（控制台生成，用它调 API 或 `curl -u`）；SSH 需要本地密钥对。需 `lambda-cloud-client>=1.0`，以及账号里有钱。
- **自带资源**：`references/advanced-usage.md`、`troubleshooting.md`。

#### `libreoffice-local-install` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**操作型（本机路径手册）**｜这台 Windows 机器上**已装好的 LibreOffice**怎么用命令行调：文档转换、导 PDF、渲染、打印——**别再下载重装**。
- **什么时候用**：任务里出现 LibreOffice / `soffice` / 无头转换 / DOCX·XLSX·PPTX·ODF 互转；需要那个可执行文件的确切路径；渲染器报"找不到 soffice"。
- **能干什么**：
  - 给定全部固定路径：安装根 `F:\LibreOffice`、CLI **`F:\LibreOffice\program\soffice.com`**、GUI `soffice.exe`、捆绑 Python、`unopkg.com`、Codex 渲染器的 shim。
  - 无头转 PDF 的标准写法：**绝对路径进、先建输出目录、检查退出码、再验产物存在**；要保真就指定导出 filter（`pdf:writer_pdf_Export` / `pdf:calc_pdf_Export` / `pdf:impress_pdf_Export`）。
  - 六条操作规矩：优先用固定 CLI 路径（`soffice.com` 才保留控制台输出与退出码）、自动化必须 `--headless`、**同时查退出码和产物**（有些错误只打在控制台文字里）、共享默认 profile 时**串行执行**、只有可执行文件真的没了才允许重装并先报告、profile 别落在 `C:\WINDOWS\TEMP`。
- **外部 Key / 依赖**：**无需外部 Key**。依赖就是**本机已安装的 LibreOffice**（原文记录版本 `26.2.4.2`）+ Windows PowerShell。
- **自带资源**：`agents/openai.yaml`。（纯路径与规程说明，无脚本。）

#### `llava` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜LLaVA——CLIP 视觉编码器 + Vicuna/LLaMA 的图文助手：看图对话、VQA、图像描述。
- **什么时候用**：要做视觉问答；要多轮"接着问这张图"；要图像描述/字幕；要文档图理解；要视觉指令跟随。
- **能干什么**：
  - 载模型（7B–34B）、载图、按 LLaVA 的对话模板拼 prompt（`DEFAULT_IMAGE_TOKEN` 占位 + 图像张量），生成回答。
  - CLI 单图提问、多轮交互、Gradio 网页界面。
  - 常见任务示例：图像描述、VQA、文字化"目标检测"、场景理解、文档理解。
  - **自定义训练两阶段**（特征对齐 558K 图文对 → 视觉指令微调 150K），以及 4/8-bit 量化省显存。
- **外部 Key / 依赖**：**无需外部 Key**，但 LLaVA 权重在 HF 上是 gated 的，**要登录 + 接受许可**才能下。需 `transformers`、`torch`、`pillow`，从 LLaVA 仓库安装；显存 7B 约 14GB、13B 约 28GB、34B 约 70GB（`load_4bit=True` 约降到 1/4）。
- **自带资源**：`references/training.md`。（正文里的 `scripts/v1_5/pretrain.sh`、`finetune.sh` 属 **LLaVA 仓库**，不在本目录。）

#### `local-models` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**操作型（本机服务清单）**｜这台 Windows 工作站上**已经跑着的本地模型**怎么用：CosyVoice3 配音、bge-m3 嵌入、Ollama、musicgen、LM Studio——先查技能里那张"能力/端口/状态"总览表，再决定动谁。
- **什么时候用**：用户提"本地模型 / 本地配音 / 本地 TTS / CosyVoice / 11436 / 11435 / bge-m3 / ollama / gemma / 本地音乐生成 / musicgen / LM Studio"；或者任务真需要在本机生成语音、向量、音乐。
- **能干什么**：
  - **CosyVoice3-0.5B（11436）**：OpenAI 兼容 `POST /v1/audio/speech`，带参考音频就零样本克隆声线；先探活再决定要不要拉起看门狗；约 3–5GB 显存，跑别的模型前先停它。
  - **bge-m3（11435）** 与 **Ollama（11434，gemma3:4b）**：前者 llama-server 常驻出 1024 维 embedding，后者用 `ollama run` / `ps` / `stop` 按需加载、用完卸载。
  - **musicgen-small / LM Studio**：前者模型已下但**运行库未装**，只有明确要生成音乐时才在独立 venv 里装（且和 CosyVoice 抢显存，不能同时跑）；后者只有 GUI，**别自己猜接口**。
- **外部 Key / 依赖**：**无需外部 Key**（全是本机自托管服务）。强依赖本机环境：`D:\Desktop\模型管理` 下的 venv、RTX 5060 Laptop 8GB 显存、各服务端口常驻。
- **自带资源**：**无**（纯 SKILL.md，但内容全是可直接抄的 PowerShell 命令）。

#### `ml-training-recipes` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜跨领域的 PyTorch 训练"菜谱"：选架构、定超参、调学习率、混合精度、OOM 怎么救、loss 炸了怎么查。
- **什么时候用**：要训/微调神经网络；loss 突然飙或变 NaN；显存不够；GPU 利用率上不去；不知道该用什么优化器/调度器；要系统地做实验管理。
- **能干什么**：
  - **Scaling law 速算**：Chinchilla 约 20 token/参数；给了 125M→7B 的 token 数对照表；`FLOPs ≈ 6ND`；数据重复超约 4 轮收益递减。
  - 优化器（AdamW、Muon）与学习率调度（时间式 / cosine decay）的经验规则；混合精度与 `torch.compile`。
  - 显存与性能：meta device 初始化、MFU 计算（给了 H100/A100/4090 的 TFLOPS 参考）、**OOM 的解法按顺序给**。
  - **超参搜索优先级**（先调什么后调什么）+ "2025 默认配方"；再按领域给评测指标（LLM / 视觉 / 扩散 / 医学影像 / 蛋白药物 / 空间组学 / 基因组）；附调试清单：loss 爆炸/NaN、训练慢/MFU 低、平台期、**静默失败**、该盯哪些指标。
- **外部 Key / 依赖**：**无需外部 Key**。需 `torch>=2.0`；要真跑得自己备 GPU。
- **自带资源**：`references/architecture.md`、`scaling-and-selection.md`、`optimizers.md`、`experiment-loop.md`、`domain-specific.md`、`biomedical.md`。

#### `modal-serverless-gpu` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**指导型**｜Modal 无服务器 GPU 云：Python 里直接声明"我要一张 H100"，跑完自动缩到 0，**没任务就没账单**。
- **什么时候用**：偶发/突发 GPU 需求不想养机器；要把模型部署成自动扩容的 API；要跑批处理或定时任务；要按秒计费。
- **能干什么**：
  - 装饰器式定义：`@app.function(gpu="H100")` 就跑，`modal deploy` 变常驻 API。
  - GPU 规格写法：单卡、指定显存版本、最多 8 卡、**带降级链**（`["H100","A100"]`）、任意可用卡。
  - 容器镜像（pip / CUDA 基础镜像 / 系统包）、持久化存储（Volume）、FastAPI/ASGI 端点、**动态批处理**。
  - 密钥管理（`modal.Secret`）、定时调度（cron）、冷启动缓解与模型加载最佳实践、本地调试与看日志。
- **外部 Key / 依赖**：**要账号**——`modal setup` 走浏览器授权（生成 token）；用 HF 私有模型时再建 `HF_TOKEN` secret。需 `modal>=0.64`，以及云端计费账号。
- **自带资源**：`references/advanced-usage.md`、`troubleshooting.md`。

#### `officecli` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：**操作型（依赖外部 CLI）**｜用一个 `officecli` 单文件程序读写改 `.docx` / `.xlsx` / `.pptx`——**不需要装 Office**。
- **什么时候用**：要创建 Office 文档；要检查格式、找问题、校对；要加图表；要批量改属性；要让 AI 直接操作文档结构。
- **能干什么**：
  - **分层策略 L1 读 → L2 DOM 编辑 → L3 原始 XML**，永远优先用高层；全部支持 `--json` 结构化输出。
  - L1：`view` 多种视图、`get`、`query`、**稳定 ID 寻址**；`get <file> selected` 能读用户在浏览器里点中的形状（"把刚选的这些改红"）。
  - L2：`set` 改属性、`find` 按格式/正则匹配并替换（docx 支持**修订模式**的查找替换）、`add` 加元素/克隆、数据透视表、排序、`--after find:X` 文本锚点插入、`move/swap/remove`、`batch` 一次保存多个操作。
  - **Mark**：把编辑做成"待审提案"，用户点了才生效；并强调**先查帮助再动手**（`officecli help docx paragraph` 一次胜过瞎试），常驻模式（默认 60s 空闲、显式 `open/close` 可到 12min）避免文件锁冲突。
- **外部 Key / 依赖**：**无需外部 Key**。要装 `officecli` 本体（`curl -fsSL https://d.officecli.ai/install.sh | bash`，或 Windows PowerShell 脚本）；文档说"单二进制、无依赖、不需要装 Office"。
- **自带资源**：**无**（纯 SKILL.md，约 26k 字符，全是命令语法与坑）。

#### `playwright` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：从终端驱动真实浏览器的 CLI 自动化——开页面、点元素、填表单、快照、截图、抓数据（可执行型，自带 wrapper 脚本）。
- **什么时候用**：要自动化某个网页操作流程；要抓页面数据；要复现/调试一个 UI 流程；要给页面截图取证。**不要**转去写 `@playwright/test` 测试文件，除非用户明确要。
- **能干什么**：
  - `open / snapshot / click / type / press / screenshot` 这套命令流（用元素 ref 定位，如 `click e15`）。
  - 表单填写与提交、多标签页操作、用 trace 调试整个 UI 流程。
  - 自带 wrapper 脚本，即使没全局装 `playwright-cli` 也能用。
- **外部 Key / 依赖**：无需外部 Key。**必须能拿到 `npx`**（Node.js/npm）；全局装 `@playwright/cli` 是可选项。使用时先 `command -v npx` 自检，没有就停下来让用户装 Node。
- **自带资源**：`scripts/playwright_cli.sh`（wrapper）、`references/cli.md`、`references/workflows.md`、`agents/openai.yaml`（图标与默认提示）、`assets/`（logo）、`LICENSE.txt`、`NOTICE.txt`。

#### `ppt-master` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：重型可编辑 PPTX 生产流水线——生成、重建页面视觉、做 Brand/Style/Layout/Deck 模板工作区、填原生模板、成品美化、加旁白/动画/自播放视频（可执行型，99 个脚本 + 23 份流程文档）。
- **什么时候用**：要生成或美化 PPTX；要把图片/PDF/文档转成可编辑幻灯片；要做可复用的品牌/版式模板；要给成品加旁白音频、动画、自播放视频。通常由 `ppt-router` 选中或用户显式点名。
- **能干什么**：
  - 三条顶层路由，一次只走一条：**Generate PPTX**（含 Image→PPTX、Beautify、默认、Quick 四种 profile）／**Create Template**（做可复用模板工作区）／**Edit Native PPTX**（保留原生设计只改内容/备注/动效，未改页面字节级还原）。
  - SVG 一键转原生 DrawingML 形状（`svg_to_pptx.py`），支持合并形状、文字轮廓、图表。
  - 素材入口很宽：PDF / docx / xlsx / html / epub / ipynb / 网页 / 图片都能转 Markdown。
  - 旁白：edge-tts 默认免费出音频，也可接云端 TTS；还能配字幕、动效计划、音视频合成。
- **外部 Key / 依赖**：核心生成**不需要 Key**（大部分工具只用 Python 标准库）。按需才配：AI 生图需 `IMAGE_BACKEND` + 对应厂商 Key（openai / gemini / qwen / zhipu / volcengine / minimax / stability / bfl / ideogram / siliconflow / fal / replicate / openrouter / modelscope 等）；云端 TTS 需 ElevenLabs / MiniMax / Qwen / CosyVoice 的 Key；图库搜索 Pexels / Pixabay 的 Key（Openverse、Wikimedia 免 Key）。Python 依赖见 `requirements.txt`：`python-pptx`、`PyYAML`、`XlsxWriter`、`skia-pathops`、`uharfbuzz`、`edge-tts`、`PyMuPDF`、`mammoth`、`markdownify`、`ebooklib`、`nbconvert`、`openpyxl`、`Pillow`、`numpy`、`requests`、`beautifulsoup4`、`curl_cffi`（可选）、`google-genai`、`flask`。
- **自带资源**：`scripts/`（99 项，含 svg_to_pptx、pptx_to_svg、pptx_intake、video_* 系列、visual_review、source_to_md 等）、`workflows/`（23 份 md，含 routing、三路由、10 个 stage、governance）、`templates/`（brands 22、charts 36、layouts 9、icons 7、decks 4、scaffolds 2、schemas 2）、`references/`（42 项：executor-* 系列、动画、画布格式、artifact 归属、图片生成/检索/版式规范等）、`.env.example`、`requirements.txt`、`LICENSE`、`SPONSORS*.md`。

#### `ppt-router` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：演示文稿任务的唯一自动入口——只做路由，自己一张幻灯片都不产出（指导型 · 路由）。
- **什么时候用**：用户提"做/生成/重建/美化/换风格/套模板/填模板 PPT、幻灯片、演示文稿、课件、答辩、汇报、slide deck、talk"——先过它，再决定用哪个专项技能。
- **能干什么**：
  - 一表四选一：中文论文/学位论文重建 → `scholar-ppt-cn`；论文 PDF 转答辩/组会幻灯 → `sci-ppt`；通用可编辑 PPTX（生成/美化/模板填充/旁白视频）→ `ppt-master`；已完稿论文的会议宣讲 → `presenting-conference-talks`。
  - 若宿主 harness 自带原生演示技能（如 ZCode `presentations:pptx`、Codex 演示技能），普通创建/编辑请求优先让位给原生。
  - 平局规则：按用户点名的输出格式定；仍模糊就问一句，不猜。
- **外部 Key / 依赖**：无需外部 Key，也不依赖任何包（纯路由表）。
- **自带资源**：无附属文件，只有 `SKILL.md`。

#### `pytorch-fsdp2` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：教 agent 把 FSDP2（`fully_shard`）正确接进训练脚本——初始化、分片、混精/卸载、分布式 checkpoint 全都要对（指导型，附 12 份 PyTorch 官方资料笔记）。
- **什么时候用**：模型（参数+梯度+优化器状态）一张卡装不下；要 DTensor 的逐参数分片（比 FSDP1 更可检查）；以后要叠 DeviceMesh 做 DP+TP 组合。
- **能干什么**：
  - 五条"必须遵守的契约"：`torchrun` 启动、自底向上 `fully_shard()`、调 `model(input)` 而不是 `model.forward(input)`、分片后再建优化器、用 DCP 存 checkpoint。
  - 分步流程 + 两套可复制清单：给老脚本加 FSDP2、给 FSDP2 补 DCP 存取。
  - `reshard_after_forward` 的内存/性能权衡、meta device 建模型、混精与 offload 配置。
  - 调试清单：先查什么、常见报错怎么修。
- **外部 Key / 依赖**：无需外部 Key。依赖 `torch`（用带 FSDP2 与 DCP 的新版本）。
- **自带资源**：`references/` 12 份（fully_shard API、FSDP2 教程、DCP 概览/recipe/异步、DeviceMesh、FSDP1 API、DDP 笔记、TP 教程、torchtitan 与 Ray Train 的 FSDP2 例子等）。

#### `pytorch-lightning` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：PyTorch 的高层封装——Trainer 一个类把训练循环、分布式、回调全接管（指导型）。
- **什么时候用**：想少写样板代码又不想丢灵活性；要笔记本上写的代码原样跑到超算；要自动 DDP/FSDP/DeepSpeed；要回调、日志、检查点这些内置最佳实践。
- **能干什么**：
  - 三步改造：`LightningModule` 组织代码 → 建 DataLoader → `Trainer.fit()`。
  - 工作流：从原生 PyTorch 迁移、验证/测试、DDP 多卡（同一个脚本加 `--devices 8`）、回调监控、学习率调度。
  - `training_step` 里返回 loss（不是自己 backward）这类易踩误区的对照示例。
  - 与 Optuna / Ray Tune / WandB 做超参搜索的接法。
- **外部 Key / 依赖**：无需外部 Key。依赖 `lightning`、`torch`、`transformers`（超参搜索的第三方集成按需装）。
- **自带资源**：`references/callbacks.md`、`references/distributed.md`、`references/hyperparameter-tuning.md`。

#### `ray-train` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：跨集群的分布式训练编排——把单机训练脚本几乎不改地扩到上千节点（指导型）。
- **什么时候用**：要多机多卡训超大模型；要弹性扩缩容与容错；要 Ray Tune 做分布式超参搜索。
- **能干什么**：
  - `TorchTrainer` + `ScalingConfig(num_workers=8, use_gpu=True)`，`train.torch.prepare_model` / `prepare_data_loader` 自动分设备。
  - 从单 GPU 脚本迁到多机的对照改法（原代码 vs Ray 版本逐步对比）。
  - 内置容错、检查点、弹性扩缩；多节点网络与启动配置专页。
  - 与 Ray Tune 的超参搜索组合。
- **外部 Key / 依赖**：无需外部 Key（多节点需要可用的 Ray 集群/网络）。依赖 `ray[train]`、`torch`、`transformers`。
- **自带资源**：`references/multi-node.md`。

#### `saelens` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：训和用稀疏自编码器（SAE）把模型激活拆成一条条可解释特征（指导型）。
- **什么时候用**：要找模型里"某个概念对应的特征"；要研究叠加（superposition）与特征几何；要做特征级 steering / 消融；要看安全相关特征（欺骗、偏见、有害内容）。
- **能干什么**：
  - 工作流一：加载预训练 SAE，跑激活，看每个 token 位置上激活最强的前几个特征。
  - 工作流二：在自己的模型/层上训一个 SAE（含架构选择与训练配置）。
  - 工作流三：特征分析与 steering——找最大激活样本、做特征消融看行为变化。
  - 与 Neuronpedia 打通查特征解释；附 SAE 架构对照表与关键类速查。
- **外部 Key / 依赖**：无需外部 Key。依赖 `sae-lens>=6.0.0`、`transformer-lens>=2.0.0`、`torch>=2.0.0`，Python 3.10+。
- **自带资源**：`references/api.md`、`references/tutorials.md`、`references/README.md`。

#### `segment-anything` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：Meta 的 SAM——零样本分割图里任何物体，用点、框或已有掩码当提示（指导型）。
- **什么时候用**：不训模型就要分割任意物体；做交互式标注工具；给别的视觉模型造训练数据；要自动生成图里所有物体的掩码；处理医学/卫星这类特定域图像。
- **能干什么**：
  - 交互式分割：给一个点或一个框，返回掩码（含多掩码候选与置信度）。
  - 自动全图分割（`SamAutomaticMaskGenerator`），可调网格密度与阈值。
  - 批量推理、ONNX 导出（可部署到浏览器/边缘设备）。
  - 三种规格取舍：ViT-B 最快、ViT-L 折中、ViT-H 最准。
- **外部 Key / 依赖**：无需外部 Key。但**权重需手动下载**（如 `wget .../sam_vit_h_4b8939.pth`，ViT-H 约 2.4GB、ViT-L 1.2GB）。安装走 GitHub 源 `pip install git+https://github.com/facebookresearch/segment-anything.git`，可选 `opencv-python`、`pycocotools`、`matplotlib`，或改用 `transformers` 加载。依赖 `torch>=1.7.0`。
- **自带资源**：`references/advanced-usage.md`、`references/troubleshooting.md`。

#### `skypilot` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：多云调度器——同一份任务描述，自动挑最便宜的云和机型跑，挂 spot 也能自动恢复（指导型）。
- **什么时候用**：训练/批处理要跨 AWS、GCP、Azure、K8s、Lambda、RunPod 等 20+ 家跑；想用 spot 省 3–6 倍钱又要自动恢复；要统一接口避免被单一云绑死。
- **能干什么**：
  - 一份 YAML 描述资源与命令，`sky launch` 起任务，`sky status` 看集群，`sky serve up` 部署推理端点。
  - 成本优化：自动选最便宜的云/区域；spot 实例 + 自动恢复 + checkpoint。
  - 多节点分布式训练（gang scheduling）、文件挂载与存储、托管的长期作业。
  - 调试与常见故障排查。
- **外部 Key / 依赖**：**不需要各家的 API Key，但需要云凭证**——先用 `sky check` 检查 AWS/GCP/Azure/K8s 的凭证是否配好。任务内可传 `HF_TOKEN`、`WANDB_API_KEY`（走 `envs:` 或 `secrets:`）。Python 依赖 `skypilot>=0.7.0`（按云装 `skypilot[aws,gcp,azure,kubernetes]`）。
- **自带资源**：`references/advanced-usage.md`、`references/troubleshooting.md`。

#### `stable-diffusion` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：用 HuggingFace Diffusers 跑 Stable Diffusion——文生图、图生图、修补、ControlNet 空间约束、LoRA 风格（指导型）。
- **什么时候用**：要从文字出图；要做风格迁移/图像增强；要 inpainting 补掉某块区域或 outpainting 扩画布；要生成同图变体；要搭自定义扩散流水线。
- **能干什么**：
  - 三种主任务：文生图、图生图、inpainting，含关键参数（`num_inference_steps`、`guidance_scale`、`strength`、负向提示词）。
  - ControlNet：用边缘、姿态、深度做空间条件控制。
  - LoRA 适配器加载与权重调节；批量生成与种子复现。
  - 显存优化：fp16/bf16、CPU offload、xformers、attention slicing。
- **外部 Key / 依赖**：无需外部 Key（模型从 HuggingFace 拉取）。依赖 `diffusers>=0.30.0`、`transformers>=4.41.0`、`accelerate>=0.31.0`、`torch>=2.0.0`，可选 `xformers`。
- **自带资源**：`references/advanced-usage.md`、`references/troubleshooting.md`。

#### `tensorrt-llm` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：NVIDIA 的推理极致优化方案，编译成 TensorRT 引擎后吞吐和延迟都拉满（指导型）。
- **什么时候用**：只在 NVIDIA 卡（A100/H100/GB200）上部署；要最高吞吐（Llama 3 上 24000+ tok/s）；要 FP8/INT4/FP4 量化 + in-flight batching + 多卡多机。
- **能干什么**：
  - `LLM(model=...)` + `SamplingParams` 的直接推理，或 `trtllm-serve` 起服务（`--tp_size`、`--max_batch_size`、`--max_num_tokens`）。
  - 量化路径与 in-flight batching 配置。
  - 多 GPU / 多节点扩展方案。
  - 和 vLLM、llama.cpp 的选型边界（要简单 Python API 选 vLLM，要 CPU/Apple Silicon 选 llama.cpp）。
- **外部 Key / 依赖**：无需外部 Key。环境很挑：**CUDA 13.0.0 + TensorRT 10.13.2 + Python 3.10–3.12**，`pip install tensorrt_llm==1.2.0rc3` 或直接用 `nvidia/tensorrt_llm:latest` 镜像。
- **自带资源**：`references/serving.md`、`references/optimization.md`、`references/multi-gpu.md`。

#### `visiomaster` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：把图片里的流程图/架构图/论文模块图重画成**可编辑 Visio 形状**（可执行型，16 个脚本，`image → scene.json → 校验 → Visio COM 渲染 → .vsdx/.svg/.png`）。
- **什么时候用**：用户要"把这张图重画成可编辑的 Visio"；要交付 `.vsdx` 而不是截图或 PPT 贴图；图的主体是方框、箭头、标签、容器。
- **能干什么**：
  - 用 `scene.json` 描述节点/连线/锚点/路由/语义，`scene_validate.py` 校验后再交给 Visio COM 渲染出 vsdx + svg + png。
  - 大图先做区域规划（可见区 `group_container` / 逻辑区 `audit_region`，每区 12–18 个节点）再拼整页，避免一次画崩。
  - 重画优先：主体结构必须重绘成可编辑元素，只有不值得重画的缩略图/纹理才允许留位图；不许整图贴上去充数。
  - 评审闭环：源图先 `stage_source_image.py` 落盘并留 SHA-256，`make_review_assets.py` 出对比图，再跑 review gate 与修复计划。
- **外部 Key / 依赖**：无需外部 Key。**必须是 Windows + 本机装 Microsoft Visio 桌面版 + Python 的 `pywin32`**。Python 依赖 `pywin32>=306`、`Pillow>=10.0.0`。
- **自带资源**：`scripts/`（16 个，含 scene_validate、scene_audit、make_review_assets、review_checklist_gate、enumerate_visio_masters、font_* 等）、`references/`（scene-schema、visio-component-map、visio-export-flow、review-contract、renderer-effective-fields 等）、`templates/`（10+ 个 scene.json 范例 + style_profiles.json + visio_components.json）、`docs/`、`agents/openai.yaml`、`README.md`、`RELEASE_NOTES.md`、`requirements.txt`。

#### `whisper` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：OpenAI 的通用语音识别模型，99 种语言，能转写、翻译成英文、识别语种（指导型）。
- **什么时候用**：语音转文字；播客/视频转写；会议纪要及时转写；嘈杂音频转写；多语言音频处理。
- **能干什么**：
  - 六种规格 tiny/base/small/medium/large/turbo 的参数、速度、显存对照表（turbo 809M、约 8× 速、6GB 显存，推荐首选）。
  - 命令行用法：`whisper audio.mp3 --model turbo`，输出 txt/srt/vtt/json，`--language`、`--task translate`。
  - 批量处理、实时流式（换 `faster-whisper`，快约 4×）、GPU 加速。
  - 已知局限（幻觉、长音频截断、时间戳精度）与应对建议。
- **外部 Key / 依赖**：无需外部 Key（模型首次运行自动下载）。依赖 `openai-whisper`、`transformers`、`torch`；**必须装 ffmpeg**（macOS `brew install ffmpeg` / Ubuntu `apt install ffmpeg` / Windows `choco install ffmpeg`）；Python 3.8–3.11。
- **自带资源**：`references/languages.md`（99 种语言支持情况）。

#### `windows-deployment-cli-locations` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：这台 Windows 工作站上各部署/数据库 CLI 装在哪、怎么调用的硬编码事实清单（查表型 · 事实型，不是流程教学）。
- **什么时候用**：任务要用 supabase / netlify / neon / vercel / wrangler / railway / psql / pg_dump / docker 之一；命令不在 PATH 里；要先确认工具是否已装、避免重复安装一份。
- **能干什么**：
  - 逐条列出命令、launcher 绝对路径、包目录、已核验版本、是否在 PATH（核验日期 2026-07-31）：Supabase 2.110.0、Netlify 27.0.1、Neon 2.38.2、Vercel 58.1.0、Wrangler 4.115.0、Railway 5.30.1、PostgreSQL 18.4。
  - PostgreSQL 目录布局（安装根、bin、data、日志）与"不要擅自动数据库集群"的红线。
  - 调用纪律：先 `Get-Command -All` 探，再显式用 launcher 绝对路径，最后在关键操作前 `Test-Path` + `--version` 双检；优先用固定 launcher 而不是 `npx` / 重装。
  - PATH 排查：进程环境变量可能比注册表里的旧，三个来源都要比。
- **外部 Key / 依赖**：无需外部 Key。**强绑定一台具体机器**（路径全是 `E:\...`、用户名 `C:\Users\18052\...`），换机器必须整表重写。
- **自带资源**：`agents/openai.yaml`。

#### `windows-ios-cicd` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：手上没有 Mac 也能开发和验证 iOS/watchOS 应用——代码在 Windows 写，编译/测试/模拟器截图全丢给 GitHub Actions 的 macOS runner（指导型 · 深度踩坑文档）。
- **什么时候用**：在 Windows 上写 iOS/watchOS App；要配 iOS CI/CD；要看模拟器截图确认 UI；要配 XcodeGen 多平台工程；要处理免费开发者账号分发。
- **能干什么**：
  - 工程标准：`project.yml` 是唯一事实源、`.xcodeproj` 不入库、纯逻辑抽成 SPM 本地包（规避 xcodegen 多平台 target 坑）。
  - CI 标准：`ios.yml`（每次 push 跑 build+test）+ `simulator-shots.yml`（手动触发跑 XCUITest 截图并 commit 回仓库，Windows 端 pull 就能看）。
  - 网络与分发：SSH 推 GitHub 替代 HTTPS、用 `curl.exe` + `gh auth token` 取二进制产物；分发现实是免费账号无 TestFlight、7 天签名过期（走 Mac 直连重签或 AltStore），$99/年才解锁 TestFlight。
  - 20+ 条真实踩坑与修法（xcodegen 多平台依赖、watchOS WidgetKit 枚举、PowerShell 编码损坏、倒置区间崩溃、`simctl` 启动参数、`App.init` 写 SwiftData 崩溃等）。
- **外部 Key / 依赖**：无需 API Key。但需要 **GitHub 账号 + Actions 额度**（public 仓库免费无限；private 每月 2000 分钟、macOS runner 按 10 倍计即约 200 分钟）+ **SSH key**；`gh` CLI 用于取 token。
- **自带资源**：无附属文件（全部 20+ 条踩坑、工程模板、CI 配置、检查清单都在 `SKILL.md` 里）。

#### `winui-app` <sub>Jensen-Yao/agents-skills</sub>

- **一句话**：用 C# + Windows App SDK 从零搭 WinUI 3 桌面应用——环境准备、脚手架、控件导航、主题无障碍、部署全流程（指导型 + 可执行的 winget 配置）。
- **什么时候用**：要新建 WinUI 3 App；要给机器装 WinUI 开发环境；要评审/重构/规划/排障已有 WinUI 工程；要做 XAML、控件、窗口、主题、响应式、性能、打包部署相关的事。
- **能干什么**：
  - 一条 `winget configure -f config.yaml` 打开开发者模式、装 VS Community 2026 与 WinUI/Windows App SDK 工作负载；随后 `dotnet new winui -o <name>` 起项目并 `dotnet build` 验证。
  - 强制先定打包模型：Store 式流程默认 packaged，CLI 反复构建/直接跑 exe 默认 unpackaged。
  - 硬性验收：必须真的跑起来看到窗口（有标题、可交互），不能只看进程退出码；做完默认把验证过的实例留给用户。XAML 编译器报错（`MSB3073`、`XamlCompiler.exe`）先退回标准脚手架，别自创恢复结构。
  - 16 份按主题切分的参考资料，按任务只读相关的那几份。
- **外部 Key / 依赖**：无需外部 Key。需 Windows 10.0.17763+、winget、.NET SDK、Visual Studio（由 `config.yaml` 装）。资料源：Microsoft Learn / WinUI Gallery / WindowsAppSDK-Samples。
- **自带资源**：`config.yaml`（WinGet DSC 配置）、`references/`（16 份，含 `_sections.md` 索引、环境审计、模板优先恢复、控件布局、外壳导航、样式主题、无障碍、性能、构建验证、测试检查清单等）、`assets/winui.png`、`agents/openai.yaml`、`LICENSE.txt`。


---

### 这一大堆怎么挑（给你的实用建议）

167 个技能不可能全装——装多了反而会让技能选择变模糊。按需求挑：

| 你的场景 | 建议先看这些 |
|---|---|
| 跑/训大模型 | `vllm` `sglang` `llama-cpp` `deepspeed` `accelerate` `megatron-core` |
| 微调 | `peft` `trl-fine-tuning` `unsloth` `axolotl` `llama-factory` `simpo` |
| 量化压缩 | `awq` `gptq` `gguf` `bitsandbytes` `hqq` `model-pruning` |
| 科研写论文 | `sci-extract` `sci-ppt` `sci-review` `ccf-paper-writer` `scholar-ppt-cn` |
| 中文知网检索 | `cnki-search` `cnki-researcher` 等 CNKI 家族（需浏览器登录态） |
| 看完就删，别装 | 标注"仓库符号链接/空壳"或正文极短的少数条目 |

> ⚠️ 注意：`sci-search` / `sci-zotero` 这类学术类是真要 Key 的（Scopus / WoS / Zotero），其余大量 ML 类只要本地 GPU + pip 包。


---

## 4. mattpocock/skills — 真实工程师的工作流技能（38 个）

> 仓库定位：Matt Pocock（TypeScript 圈知名讲师）自己每天在用的工程技能包，口号是 "Skills For Real Engineers — not vibe coding"。
> 全部是纯 Markdown 指令，不带代码运行时；MIT 许可。安装按 agent 选一种（**别同时装两种，否则每个技能都会出现两份**）：Claude Code / Codex / GitHub Copilot 走各自的 plugin 市场（可自动更新），Gemini CLI 手动 `gemini skills install`，其它 agent 用 `npx skills@latest add mattpocock/skills -a <agent>`。装完先跑一次 `/setup-matt-pocock-skills` 做仓库初始化。
>
> **数量按仓库实际目录核对**（⚠️ 与任务书写的 19 / 8 不一致，以仓库为准）：`engineering/` **20** 个、`in-progress/` **7** 个、`misc/` **4** 个、`productivity/` **7** 个，合计 **38** 个。三处独立来源互相印证：`ls -d skills/*/*/` 就是 38 个目录；`engineering/README.md` 列 11 个 user-invoked + 9 个 model-invoked = 20，`productivity/README.md` 列 5 + 2 = 7；`.claude-plugin/plugin.json` 的 skills 数组也是 27 个（20 工程 + 7 生产力，in-progress 与 misc 不进插件）。JSON 里 `skills/` 前缀共 39 条 = 本仓 38 条 + vercel-labs 的 `find-skills` 1 条，无重复名。任务书的 "engineering 19 / productivity 8" 与这四处都对不上，应是笔误。
>
> **一个关键区分**（原文反复强调）：技能分 *user-invoked*（只能你手打触发，`disable-model-invocation: true`，职责是"编排流程"）和 *model-invoked*（你和模型都能触发，承载"可复用的纪律"）。user-invoked 可以调用 model-invoked，反过来不行。下面每个条目在"一句话"末尾标了是哪一类，因为这决定了你"能不能指望 AI 自己想起来用它"。

---

### 一、engineering/（20 个，日常写代码用）

#### `ask-matt` <sub>mattpocock/skills</sub>
- **一句话**：技能选择器——你不知道该用哪个 skill 时问它，它给你整张路线图。（user-invoked）
- **什么时候用**：刚装完不知道从哪下手；一个任务卡住想确认"该不该跳过某一步"；想搞清楚各技能之间怎么衔接。
- **能干什么**：
  - 画出主流程 idea → ship：`grill-with-docs` →（需要跑得起来才答得清就插 `prototype`，两头用 `handoff` 过渡）→ `to-spec` → `to-tickets` → 每票 `implement` 或整体 `implement-spec` → `code-review` → `pr` → `retro` 收尾。
  - 给三条"上匝道"：issue 堆积走 `triage`、东西坏了走 `diagnosing-bugs`、巨大而看不清的活走 `wayfinder`；并解释 phase boundary 该选哪个（继续 / `/clear` / `/handoff` / 开子 agent / `/compact`），说明为什么"继续"是最该先排除的（重读一手资料的代价）。
  - 明确警告：转述某个技能之前必须先读它自己的 SKILL.md，摘要只用于定位。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：`PHASE-BOUNDARIES.md`（五选项决策树全文）。

#### `code-review` <sub>mattpocock/skills</sub>
- **一句话**：对着一个固定点做"双轴"审查——规范轴 + 需求轴，两个子 agent 并行跑、结果并列不合并。（model-invoked）
- **什么时候用**：想审一个分支 / PR / 半成品改动；说"review since <某提交>"。
- **能干什么**：
  - 固定点由你给（SHA、分支、tag、`main`、`HEAD~5`），先 `git rev-parse` 验证 + 确认 diff 非空再往下走，坏 ref 和空 diff 在这一步就该失败，而不是在两个并行子 agent 里炸。
  - **Standards 轴**：搜出仓库里所有"代码该怎么写"的文档（`CODING_STANDARDS.md`、`CONTRIBUTING.md` 必进列表），再叠加固定的 Fowler 坏味道基线（12 条，从 Mysterious Name 到 Refused Bequest）；仓库自己的规范永远压过基线，坏味道一律算"判断题"而非硬违规，工具已能查的一律跳过。
  - **Spec 轴**：按顺序找原始需求（commit 里的 `#123` → 你给的路径 → `docs/`/`specs/`/`.scratch/` 里的 spec），查"该做的漏了没 / 多做了没 / 做了但做错了"；找不到 spec 就跳过这轴并说明。
  - 两个子 agent 提示词都限 400 词；报告按 `## Standards` / `## Spec` 分栏，**禁止**跨轴排名（理由：一轴可能盖住另一轴）。
- **外部 Key / 依赖**：无需外部 Key；需要 `git`；用 GitHub 追踪器抓 issue 时需要 `gh` 已登录；需要宿主支持并行子 agent。
- **自带资源**：无额外文档（只有 `agents/` 里的展示名声明）。

#### `codebase-design` <sub>mattpocock/skills</sub>
- **一句话**：设计"深模块"的共用词汇表——大量行为藏在小小的接口后面。（model-invoked）
- **什么时候用**：要设计或改造某个模块的接口；想找"哪里可以加深"；决定 seam 放哪；想让代码更好测、更好被 AI 导航。
- **能干什么**：
  - 强制统一用词：Module / Interface / Implementation / Adapter / Depth / Seam / Leverage / Locality，并明确禁用 component、service、API、boundary 这些近义词（理由是"用词一致本身就是全部意义"）。
  - 给判定标准：接口是"调用方必须知道的一切"（含不变量、顺序约束、错误模式、配置、性能特征），不只是类型签名；模块"深"= 单位接口撬动的行为多。
  - 给两个可操作工具：**deletion test**（删掉它是把复杂度集中了，还是只是挪了个地方）和"接口即测试面"。
  - 提供 "design it twice" 的并行流程：3 个以上子 agent 各按不同约束（最小接口 / 最大杠杆 / …）产出**根本不同**的接口方案，因为"你的第一个想法多半不是最好的"（Ousterhout）。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：`DEEPENING.md`（按依赖分四类——进程内 / 本地可替代 / 自有远程（端口与适配器）/ 真外部（mock）——分别给加深与测试策略）、`DESIGN-IT-TWICE.md`。

#### `diagnosing-bugs` <sub>mattpocock/skills</sub>
- **一句话**：硬骨头的 bug 和性能回退，用六阶段诊断闭环，不许凭感觉猜。（model-invoked）
- **什么时候用**：你说 "diagnose"/"debug this"，或报"坏了 / 抛错 / 测试挂 / 变慢"。
- **能干什么**：
  - **Phase 1 建反馈回路**（原文说"这就是这个技能的全部"）：按优先级给 7 种造法——失败测试 → curl/HTTP 脚本 → CLI 加固定输入对比快照 → 无头浏览器（Playwright/Puppeteer）→ 回放抓下来的真实请求 → 最小化的一次性 harness → 属性/fuzz 循环；要求回路"对这一条 bug 会红"，并单独讲了非确定性 bug 和"实在建不出来"怎么办。
  - 后续闭环：**Phase 2 复现 + 最小化** → **Phase 3 假设** → **Phase 4 插桩** → **Phase 5 修 + 补回归测试** → **Phase 6 清理**；阶段可以跳，但必须给出明确理由。
  - 开头有硬性 **Redact** 规则：一切 secret 写成 `<REDACTED>`，回路尽量从环境变量取值，抓来的产物只引用有信号的那几行；脱敏后不够诊断就明说并问你。
- **外部 Key / 依赖**：无需外部 Key（但需要真能跑起代码的环境；脱敏规则针对的就是输出里的 token/凭据）。
- **自带资源**：`scripts/hitl-loop.template.sh`（人机同跑的复现脚本模板，含 `step`/`capture` 两个助手函数）。

#### `domain-modeling` <sub>mattpocock/skills</sub>
- **一句话**：主动把项目的领域模型磨锋利，而不是只读一读词汇表。（model-invoked）
- **什么时候用**：讨论代码里的术语；写或改 `GLOSSARY.md`；记录或修改 ADR。
- **能干什么**：
  - 术语和现有 glossary 冲突时当场叫停："你的 glossary 把 cancellation 定义成 X，你现在说的像是 Y，到底是哪个？"
  - 把含糊/一词多义的词收敛成唯一的规范词（"你说的 account 是指 Customer 还是 User？"）。
  - 主动编边界场景压测模型，并在**当下**就把结论写进 `GLOSSARY.md` 和 `docs/adr/`（文件懒创建，有第一个词/第一条决策才建）。
  - 多上下文仓库：根目录有 `GLOSSARY-MAP.md` 时按图找到每个子 context 自己的 `GLOSSARY.md` 与 ADR 目录。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：`ADR-FORMAT.md`、`GLOSSARY-FORMAT.md`（含 `GLOSSARY-MAP.md` 的目录结构示例）。

#### `grill-with-docs` <sub>mattpocock/skills</sub>
- **一句话**：= `grilling` + `domain-modeling`，一边被追问一边把词汇表和 ADR 落成文件。（user-invoked）
- **什么时候用**：你在一个**有仓库的工作目录**里、想在被追问的同时留下纸面痕迹（没仓库就用 `grill-me`）。
- **能干什么**：
  - 整个正文只有一句："连续调用 Skill 工具两次，先 grilling 再 domain-modeling"——它是个组合入口，不重复实现逻辑。
  - 因此：追问磨出来的一致用词会当场写进 `GLOSSARY.md`，难改的决策写成 ADR；同一个问题它比 `grill-me` 严格更好，因为它多留一份纸面痕迹。
- **外部 Key / 依赖**：无需外部 Key；**前提是 `grilling` 和 `domain-modeling` 也装了**。
- **自带资源**：无。

#### `implement` <sub>mattpocock/skills</sub>
- **一句话**：按 spec 或 ticket 把活写出来，收尾前必定过一遍 code review。（user-invoked）
- **什么时候用**：已经有 spec/ticket，就差人（或 agent）照着写。
- **能干什么**：
  - 你给的如果是 ticket 编号，先去追踪器拉下来并**先报出标题**再动手；指代不清就问你。
  - 在**事先商定好的 seam** 上调 `tdd`，一个红绿切片一个切片地建。
  - 过程中频繁跑类型检查、频繁跑单个测试文件，最后跑一次全量。
  - 干完调用 `code-review` 复审，然后提交到当前分支。
- **外部 Key / 依赖**：无需外部 Key；需要仓库已配置追踪器（否则提示你跑 `/setup-matt-pocock-skills`）；需要 `tdd`、`code-review` 已安装。
- **自带资源**：无。

#### `implement-spec` <sub>mattpocock/skills</sub>
- **一句话**：一整个 spec 一次落地——把 tickets 当任务图，多个实施子 agent 并行，全部汇到一条集成分支。（user-invoked）
- **什么时候用**：spec 已经拆成 tickets，你想"编排这次构建"而不是自己一票一票推。
- **能干什么**：
  - 把 tickets 读成**任务图**而非步骤清单，因此永远存在一个"就绪前沿"可以同时抓；每个 ticket 派一个实施子 agent，**各自在自己的 worktree + 分支**上干，干前确认基于集成分支、干完把集成分支最新提交合进来（保证每次合并都是快进）。
  - 子 agent 之间刻意少通信，用**上下文指针**（spec、ticket、调研笔记、历史 commit）代替复制粘贴；可选先派"勘探子 agent"把代码和外部文档摸清，把笔记写到仓库外一个所有子 agent 都能读的目录。
  - 追踪器靠 PR 关活、或你主动要 PR 时，才在第一次合并后开 draft PR（空分支开不了 PR）；收尾跑一次 `code-review`。
- **外部 Key / 依赖**：无需外部 Key；需要 `git worktree`、能开后台子 agent、追踪器已配置（否则提示跑 setup）；需要 `tdd`、`code-review` 已安装。
- **自带资源**：无。

#### `improve-codebase-architecture` <sub>mattpocock/skills</sub>
- **一句话**：巡一遍代码库找"加深机会"，出一份能点的 HTML 报告，你挑一个再一起 grill 到底。（user-invoked）
- **什么时候用**：手上没活、想给代码库做保养；或感觉"AI 越来越改不动这个仓库了"。
- **能干什么**：
  - 先**圈范围再扫**（YAGNI）：你指了方向就照办，否则翻 `git log` 找热点文件，最近改得多的优先；再派子 agent 逛代码，记"理解一个概念要在多少个碎模块之间来回跳"这类摩擦，对可疑的浅模块做 deletion test。
  - 在系统临时目录（`$TMPDIR` / `/tmp` / `%TEMP%`）生成**自包含 HTML**（Tailwind + Mermaid 走 CDN），每个候选一张卡：涉及文件 / 问题 / 方案 / 收益 / before-after 图 / 推荐强度徽章（`Strong`、`Worth exploring`、`Speculative`），末尾给 Top recommendation，生成后自动帮你打开。
  - 你选一个后进 grilling；中途术语变了就更新 `GLOSSARY.md`，你用"站得住的理由"否掉某个候选时它会问要不要记成 ADR 以免下次又提。
  - 与既有 ADR 冲突的候选不会一律列出，只在摩擦真的够大时标红提示；且**不在这一步给接口方案**（那是下一步的事）。
- **外部 Key / 依赖**：无需外部 Key；需要浏览器 + 联网（Tailwind/Mermaid CDN）；需要能开子 agent；词汇来自 `codebase-design`。
- **自带资源**：`HTML-REPORT.md`（完整 HTML 骨架、图表模式与样式指南）。

#### `pr` <sub>mattpocock/skills</sub>
- **一句话**：PR 正文该长什么样——一张最小可视化 + before/after 证据 + 合并风险判断。（model-invoked）
- **什么时候用**：写 PR body 的时候。
- **能干什么**：
  - **Summary**：挑"能说清重点的最小视图"——逻辑用伪代码、调用链用调用树、UI 用组件树、重构用浅文件树、交互/数据流用 Mermaid、纯讲"变了什么"就用 diff。
  - **Evidence**：Before / After 成对给（截图、输出、失败的测试 vs 通过的测试）。
  - **Merge Danger**：这扇门是**单向门还是双向门**，加一个词的爆炸半径，必要时补后果说明。
  - 硬要求：不写前言、散文尽量短、用 `GLOSSARY.md` 里的领域词。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：`CREDITS.md`（说明 Summary 的视觉菜单改自 Dex Horthy 的 `show-me`，是"抄内容不建依赖"）。

#### `prototype` <sub>mattpocock/skills</sub>
- **一句话**：写个一次性的小东西，把某个设计问题问出答案——问题决定形态。（model-invoked）
- **什么时候用**：想确认"这个状态模型/业务逻辑对不对"（纸上想不明白），或者"这个界面到底该长什么样"。
- **能干什么**：
  - **逻辑分支**（`LOGIC.md`）：单个自包含 HTML，按钮随便点、页签引导走查，能把状态机推过各种难想的用例；因为是单文件，可以直接丢给设计师/产品/领域专家自己玩，页面用他们的语言而不是代码的语言。
  - **UI 分支**（`UI.md`）：在**同一个路由**上用 `?variant=` 切换几套**差别巨大**的 UI 变体，底部浮动栏切；优先挂在真实页面里（有真 header、真数据、真密度才看得出好坏），实在没有才用独立路由。
  - 六条通用铁律：从第一天就标明"这是原型"、一行命令能跑起来、默认不持久化、跳过抛光（不写测试不做抽象）、每步都把完整状态显示出来、结束把结论折进真代码并把原型本身留成 primary source（提交到 `prototype/<name>` 分支，从实现 issue 指过去）。
  - 万一同一个问题两个分支都说得通且联系不上你，按周边代码猜（后端模块→逻辑，页面组件→UI），并在原型顶部写明这个假设。
- **外部 Key / 依赖**：无需外部 Key；需要能开浏览器 / 跑前端工程。
- **自带资源**：`LOGIC.md`、`UI.md`。

#### `research` <sub>mattpocock/skills</sub>
- **一句话**：把"读文档"这活派给后台 agent，你继续干别的，最后收获一份带出处的 Markdown。（model-invoked）
- **什么时候用**：想让人调研一个主题、抓官方文档/API 事实，或单纯想把阅读体力活外包。
- **能干什么**：
  - 只认**一手来源**（官方文档、源码、规范、一方 API），不认二手解读，每条结论都要追回到"拥有这个事实的那份材料"。
  - 把发现写进**单个** Markdown，每条主张都带来源引用。
  - 存放位置跟着仓库既有习惯走；没有习惯就放个合理的地方并告诉你放哪了。
- **外部 Key / 依赖**：无需外部 Key；需要联网，且宿主支持后台子 agent。
- **自带资源**：无。

#### `retro` <sub>mattpocock/skills</sub>
- **一句话**：一次编码会话的复盘——但改的是 **agent 的工作环境**，不是代码。（user-invoked）
- **什么时候用**：一次构建结束后（尤其走得很难看时），或想"下次别再踩同一个坑"。
- **能干什么**：
  - 先按 `writing-for-agents` 的写法规范来写，再回去读这次会话的一手记录（日志在你机器上找；你不指定就默认当前会话）。
  - 从**七类**找改进项：**Navigation**（找文件太费劲 → 加导航指针）、**Automated checks**（这个错本可被 lint/类型/测试/文件系统检查抓住；先读仓库自己的 `lint`/`check` 脚本和 CI——"检查存在却没接上或已悄悄坏掉"本身就是发现）、**Coding standards**（该给 reviewer agent 加/删/澄清哪条规则）、**Global AGENTS.md**（哪些 steering 指令该挪进 standards 或自动化检查）、**Tool economy**（哪次工具调用太烧 token）、**No-ops**（steering 文件里根本不改变 agent 行为的指令）、**Information access**（tee dev server 日志、给第三方服务只读权限）。
  - 关键分类动作：**机械型**违规（固定语法、禁用 API、import 形状、文件位置）一律转成确定性检查（自定义 lint 规则、pre-commit hook、CI job），默认"建检查"胜过"写规则"；`CODING_STANDARDS.md` 只留给真正的**判断题**。一个连防护栏都没有（既无 pre-commit 也无 CI 跑 lint/typecheck/test）的仓库，本身就是一条发现。
  - 输出按严重程度排序。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：无（内部加载 `writing-for-agents`）。

#### `setup-matt-pocock-skills` <sub>mattpocock/skills</sub>
- **一句话**：每个仓库跑一次的初始化——把其它工程技能都假设存在的那些配置搭好。（user-invoked）
- **什么时候用**：第一次用这套工程技能之前（原文：run once before first use）。
- **能干什么**：
  - **A. Issue tracker**：问你要 GitHub（默认）/ GitLab / 本地 markdown / 其它；先自己读一遍现场（`git remote -v`、`CLAUDE.md`/`AGENTS.md` 里是否已有 `## Agent skills` 段、有无旧版 `CONTEXT.md`/`CONTEXT-MAP.md`（会建议 `git mv` 改名成 `GLOSSARY`）、有无 `.scratch/`、有无 monorepo 信号）。
  - **B. Triage 标签 + C. 领域文档**：只在 `triage` 已安装时问**一个**问题——"要保留默认五个标签吗（推荐要）"，默认即 `needs-triage`/`needs-info`/`ready-for-agent`/`ready-for-human`/`wontfix` 五个字符串；领域文档默认**单 context**（根 `GLOSSARY.md` + `docs/adr/`，几乎不问），只有真发现 monorepo 信号才提多 context。
  - 写之前把草稿给你改；写的时候优先编辑已存在的 `CLAUDE.md`，没有再编辑 `AGENTS.md`，两个都没有就**问你**要建哪个（绝不擅自新建）；已有 `## Agent skills` 段就原地更新不重复追加。
  - 最后落 `docs/agents/issue-tracker.md`、`docs/agents/domain.md`、（装了 triage 才有的）`docs/agents/triage-labels.md`，并把 GitHub/GitLab 上缺的标签真的创建出来。
- **外部 Key / 依赖**：无需 API Key；用 GitHub/GitLab 时需要 `gh` / `glab` 已登录。
- **自带资源**：`issue-tracker-github.md`、`issue-tracker-gitlab.md`、`issue-tracker-local.md`、`triage-labels.md`、`domain.md`（都是可当种子的模板）。

#### `tdd` <sub>mattpocock/skills</sub>
- **一句话**：红 → 绿循环的参考手册，重点不是"写测试"而是"写出值得留的测试"。（model-invoked）
- **什么时候用**：想测试先行地建功能或修 bug；听到"red-green-refactor"；想要集成测试。
- **能干什么**：
  - 定义好测试：通过**公共接口**验证行为，读起来像一份规格说明（"用户能用有效购物车结账"），重构后仍然成立。
  - **seam 纪律**：只在**事先商定**的 seam 上测；写任何测试之前先把"要测哪些 seam"写下来跟你确认，每条 seam 附一句"能抓到什么 / 漏掉什么"。
  - 明确反模式与"什么时候不要 mock"：只在系统边界上 mock（外部 API、数据库（优先测试库）、时间/随机、文件系统）。
  - 界面形状本身可疑时（模块够不够深、seam 该在哪），转去调用 `codebase-design`。
- **外部 Key / 依赖**：无需外部 Key；需要项目里有可跑的测试。
- **自带资源**：`tests.md`（好测试 vs 坏测试对照例）、`mocking.md`（mock 指南）。

#### `to-spec` <sub>mattpocock/skills</sub>
- **一句话**：把当前对话**直接**合成一份 spec 并发到追踪器——不访谈，只归纳。（user-invoked）
- **什么时候用**：前面已经 grill 得很透了，想把聊出来的东西固化成可执行的 spec。
- **能干什么**：
  - 先探一遍仓库现状；写的时候用项目 glossary 的词、尊重相关 ADR。
  - 先草拟**测试 seam**（优先用既有 seam、取尽可能高的、越少越好，理想是 1 个）并跟你确认。
  - 按固定模板产出：Problem Statement / Solution / **很长的**编号 user stories / Implementation Decisions（不含具体文件路径和代码片段）/ Testing Decisions / Out of Scope / Further Notes。
  - 发布到追踪器并直接打 `ready-for-agent` 标签（不需要再走 triage）。
- **外部 Key / 依赖**：无需 API Key；需要追踪器已配置（否则提示你跑 `/setup-matt-pocock-skills`）。
- **自带资源**：无（模板内嵌在 SKILL.md 里）。

#### `to-tickets` <sub>mattpocock/skills</sub>
- **一句话**：把计划/spec/对话拆成"曳光弹"式垂直切片票，每张票声明它被谁阻塞。（user-invoked）
- **什么时候用**：spec 有了，要拆成能一票一票（或并行）干的小块。
- **能干什么**：
  - 切片规则：每片纵切**所有层**（schema/API/UI/测试），不是一个横层；做完可独立演示或验证；大小控制在一个全新上下文窗口内；该预重构的先做（"先把改动变容易，再做容易的改动"）。
  - **宽重构是垂直切片的例外**：一次机械改动炸遍全仓、没有一片能绿，就走 expand–contract（先并存新旧 → 按爆炸半径分批迁调用点，每批一票且被 expand 阻塞 → 最后删旧形态，被所有迁移票阻塞）；连分批都保不住绿时，让它们共用集成分支，全部阻塞一张"整合并验证"票，只在那一张上保证绿。
  - 发之前先把你拉进"测验"：粒度对不对、阻塞边对不对、要不要合并或再拆，改到你点头。
  - 落地两种形态：本地 → `.scratch/<feature-slug>/issues/<NN>-<slug>.md` 一票一文件，按依赖顺序从 `01` 编号（阻塞者在前）；真追踪器 → 一票一 issue、按依赖顺序发（这样阻塞边能引真实 id），有原生阻塞关系就用原生的，源是既有 issue 就做成子 issue，发完打 `ready-for-agent`。
- **外部 Key / 依赖**：无需 API Key；本地模式只需文件系统；真追踪器模式需要 `gh`/`glab` 等已登录。
- **自带资源**：无（本地票模板、issue 模板都内嵌）。

#### `triage` <sub>mattpocock/skills</sub>
- **一句话**：把 issue（以及外部 PR）在"分诊状态机"里推着走，最后产出 agent 能直接开工的 brief。（user-invoked）
- **什么时候用**：bug 报告和需求单堆积、需要有人过一遍；"帮我看看哪些要我现在处理"、"把 #42 移到 ready-for-agent"。
- **能干什么**：
  - 两个**类别**角色（`bug` / `enhancement`）+ 五个**状态**角色（`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`）；每个 issue 恰好一个类别 + 一个状态，冲突就标出来问你。先给你三桶（未打标 / needs-triage / needs-info 且报告人此后有新动静），最旧优先，带计数和一句话摘要。
  - 对一个具体 issue：先**查重**（按领域概念搜既存实现，找到就是"已实现"的 wontfix）+ 查 `.out-of-scope/` 历史拒绝 → 给建议等你定 → **验证主张**（bug 按报告步骤复现；PR 真检出跑一遍）→ 需要就 grilling 两连且边聊边更新 glossary/ADR → 落结果。
  - 落结果分五种：`ready-for-agent` 写 agent brief；`ready-for-human` 同样结构但说明为什么不能外包；`needs-info` 写分诊笔记（已确立什么 vs 还缺什么，问题必须具体可答）；`wontfix` 分三因由处理（已实现 → 指出在哪且**不写** `.out-of-scope/`；拒 bug → 说明后关；拒需求 → 写 `.out-of-scope/` 再关）。
  - 硬规则：分诊期间发到追踪器的每条评论/issue 都必须以 `> *This was generated by AI during triage.*` 开头。
- **外部 Key / 依赖**：无需 API Key；需要追踪器 + `gh`/`glab` 已登录；标签字符串映射由 `setup-matt-pocock-skills` 提供。
- **自带资源**：`AGENT-BRIEF.md`（怎么写耐用的 agent brief：描述行为、不写文件路径和行号）、`OUT-OF-SCOPE.md`（`.out-of-scope/` 知识库怎么用）。

#### `wayfinder` <sub>mattpocock/skills</sub>
- **一句话**：一个太大、太雾的活（超过一个 agent 会话能装下的），先在追踪器上画成"决策地图"，一张票一张票把路探清。（user-invoked）
- **什么时候用**：绿地项目或巨型功能，从"现在"到"目的地"的路还看不见；**只该用在这种场合**（原文说它最费脑、最慢，别拿它做边界清楚的功能）。
- **能干什么**：
  - 第一件事是**命名目的地**（要交给别人的 spec？要锁死的决策？要就地做的迁移？），目的地决定范围；地图 = 追踪器上一个带 `wayfinder:map` 标签的 issue，正文分 Destination / Notes / Decisions so far / **Not yet specified**（雾）/ Out of scope，票是它的子 issue。
  - 票分四型：`research`（AFK，派子 agent 跑 `research`）、`prototype`（HITL，调 `prototype`）、`grilling`（HITL，默认型）、`task`（必须先做的杂事）；阻塞边用追踪器**原生依赖**（这样"前沿"在 UI 上肉眼可见），开工前先把票 assign 给自己 = 认领。
  - 默认**只产出决策、不产出交付物**；一次会话最多解决一张票（research 票除外）；解决后写 resolution 评论、关票、把一行指针回填到 Decisions so far，并把新看清的雾 graduation 成新票。
  - 会说清"雾"和"出界"的区别：雾是"还说不清但在范围内"，出界是"已经明确不要"，出界的票直接关掉并在 Out of scope 留一行；地图清了就**交接、不施工**，并回主流程走 `to-spec`。
- **外部 Key / 依赖**：无需 API Key；需要追踪器支持子 issue 和原生阻塞关系（GitHub 用 `gh` 2.94+ 的原生字段，老版本走 API），否则退化到正文约定。
- **自带资源**：无。

#### `wizard` <sub>mattpocock/skills</sub>
- **一句话**：生成一个交互式 bash 向导，牵着你走完"只有人能做"的手工步骤（开面板、点按钮、抄密钥）。（model-invoked）
- **什么时候用**：要给云服务做初始配置、配 CI secret、走一个不熟的三方面板、跑一次性迁移或切换——凡是"AI 干不了、但每次都要重新解释一遍"的流程。
- **能干什么**：
  - 先**读仓库**再问（`.env`/`.env.example`/`README`/`docker-compose*`/框架配置/`.github/workflows/*` 里每个 `secrets.*`、`vars.*` 都是向导要产出的值），列出阶段清单和每步产出，让你增删改。
  - 用现成的 `template.sh`：阶段进度条、确认闸门、跨平台开链接（含 WSL）、隐藏输入 secret、幂等写 `.env`（0600）、`gh secret`/`gh variable` 写入、结尾汇总；`STAGES` 标记以上那段库代码在每个向导里完全一样，**禁止手改**，你只写各阶段。
  - 默认**用完即弃**：放到 scratch 或 `scripts/`，活干完删掉；只有你想把它变成仓库里可重复的配置路径时才提交。
- **外部 Key / 依赖**：无需 API Key；需要 `bash`；可选 `gh`（只有它存在且已登录才会帮你写 GitHub secret，否则记为 SKIPPED 让你手动设）。
- **自带资源**：`template.sh`（完整向导库 + 阶段骨架）。

---

### 二、in-progress/（7 个 —— 实验性 / 未完成，别当稳定件用）

> 分类 README 原文：**Beta**。"公开是有意的：试用并告诉我哪里坏了。" 它们**不进 Claude Code 插件、不给文档页、随时可能改或消失**。要单独安装：
> `npx skills@latest add mattpocock/skills --skill=<name>`

#### `chief-of-staff` <sub>mattpocock/skills</sub>
- **一句话**（实验性/未完成）：在一个**很长的**会话里当"幕僚长"，靠调度子 agent 和定时任务追一个长期目标。（user-invoked）
- **什么时候用**：目标要跑很久、需要跨会话积累"部族知识"、需要有人做长期战略判断。
- **能干什么**：
  - 所有活都派给子 agent（含后台 agent），保护自己的上下文窗口，同时保持和你对话；双轨思考——战术（这次任务怎么完成）+ 战略（怎么改环境让**下一次**更容易）。
  - 记忆存成 **wiki**：`index.md` 列所有页 + 一句话摘要，其它页一页一件事，互相链接；三种维护动作 ingest / query / lint（查矛盾、孤儿页、缺链、被新资料推翻的结论）。
  - 铁律「写历史，不写现在」：每条主张带日期（"2026-10-13，Matt 决定…"），涉及代码的必须带 commit（"截至 `repo@abc1234`，…"）；旧主张被推翻就**追加**新条目并标注旧的已作废，不改写旧的。
- **外部 Key / 依赖**：无需外部 Key；实际使用需要宿主支持**子 agent + 定时任务 + 持久化文件**。
- **自带资源**：无。

#### `claude-handoff` <sub>mattpocock/skills</sub>
- **一句话**（实验性/未完成）：把当前对话压成交接摘要，然后**立刻**起一个后台 agent 接着干。（user-invoked）
- **什么时候用**：你想换个窗口/让它自己跑，而不只是留个文档给下一个人。
- **能干什么**：
  - 摘要写到操作系统临时目录，再用 `claude --bg --name "<描述性名字>" -- "$(cat <摘要文件>)"` 起后台 agent；用文件传参是为了防止 shell 把摘要里的反引号或 `$` 展开。
  - 必须给 `-n`/`--name` 一个说得清的名字（会显示在任务列表、会话选择器、终端标题里），用 `claude agents` 管理。
  - 摘要里要有 "suggested skills" 段指名下一个 agent 该调哪些 skill；已有 spec/ADR/issue/commit 的内容不重复，只给路径或 URL。
- **外部 Key / 依赖**：**强依赖 Claude Code**（`claude --bg` 与 `claude agents`），别的 harness 用不了；摘要会变成新 agent 的提示词，所以必须先脱敏（API key、密码、可识别个人身份的信息）。
- **自带资源**：无。

#### `loop-me` <sub>mattpocock/skills</sub>
- **一句话**（实验性/未完成）：用 grilling 把"你生活里反复出现的 loop"磨成可交给 agent 执行的 workflow 规格。（user-invoked）
- **什么时候用**：想把每周/每天重复的事委托出去，但还说不清到底要委托什么。
- **能干什么**：
  - 用 **loop 视角**看你的生活（职业、一周、早晨、某个重复动作），找出值得规格化、甚至你没注意到的重复模式。
  - 输出就是 `workflows/*.md`，一个 workflow 一份，是唯一事实源；`NOTES.md` 记你的世界、你用的工具、你处理的渠道和你的叫法（空就先访谈）。
  - 给了一套只在需要时才拿的词汇：**Trigger**（事件或排程触发，事件通常更省）、**Checkpoint**（人确认/决策的点，可以没有）、**Push right**（先做完最大量的活，让人只被问一次且问得晚）、**Brief**（给人看的是决策级摘要，不是原始产出）。
  - 明确"什么都不强制"：一个 workflow 不需要有 AI、不需要 checkpoint、不需要排程，除非 grilling 显示需要；完成判据很硬——**一个实施 agent 不用再问任何问题就能把它建出来**。
- **外部 Key / 依赖**：无需外部 Key（需要 `grilling` 已安装）。
- **自带资源**：无。

#### `setup-ts-deep-modules` <sub>mattpocock/skills</sub>
- **一句话**（实验性/未完成）：给 TypeScript 仓库装 dependency-cruiser，用规则把每个包逼成"深模块"——只有包根文件是对外入口。（user-invoked）
- **什么时候用**：TS/JS monorepo 里深 import 到处飞、包边界形同虚设。
- **能干什么**：
  - 装四条 `error` 级规则：① 包外代码只能 import 该包的**入口**（包根文件），不能碰任何子目录；② 包内文件互相自由 import；③ `<pkg>/tests/` 只能 import 各包入口和自己的 fixtures，**连自己包的内部都不行**；④ 不许有循环依赖。刻意**不要 barrel**——公开面是"所有根文件"，所以一个包可以暴露好几个小入口。
  - 自动探测包管理器（pnpm/yarn/bun/npm）和包根（有 `src/` 用 `src/packages`，否则 `packages`）；已有 `.dependency-cruiser.*` 就**合并**而不是覆盖；加 `lint:boundaries` 并并入仓库已有那条总检查命令，**不动** `tsconfig`、不加路径别名。
  - 造一个可复制的 `example/` 包当模板；**最后一步是"证明规则真的会咬人"**：干净时通过 → 故意写一条 deep import 必须失败 → 撤掉后再次通过，三步都看到才算完。
  - 在 packages 目录写 `README.md` 讲清约定，并从 `CLAUDE.md`/`AGENTS.md` 加一行上下文指针，让 agent 能发现这条边界。
- **外部 Key / 依赖**：无需外部 Key；需要 Node + 包管理器，并会把 `dependency-cruiser` 装成 devDependency。
- **自带资源**：`dependency-cruiser.config.cjs`（四条规则的现成配置，改一个 `PACKAGES_ROOT` 就能用）。

#### `writing-beats` <sub>mattpocock/skills</sub>
- **一句话**（实验性/未完成）：写作的"exploit"阶段——素材已定，按"节拍"一段一段往前走，每次都让你选下一步。（user-invoked）
- **什么时候用**：素材是一堆原始笔记，你想把它写成一篇有推进感的文章，但不想先定死大纲。
- **能干什么**：
  - 先和你定**前置知识**（读者进门时已经懂什么，即"已 grounding"的概念）；任何一拍想用一个还没 grounding 的概念就是走错。
  - 每次给 2–3 个候选**起始拍**（不同入口），各自标明会 grounding 什么新概念；你选一个，才写进文件，然后停下来。
  - 写完一拍就重读磁盘上的文章，再给 2–3 个候选**下一拍**，循环到自然收尾；每拍可以是一句话也可以是几段，并会预演"这个选择会打开哪些后续拍"。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：无。

#### `writing-fragments` <sub>mattpocock/skills</sub>
- **一句话**（实验性/未完成）：写作的"explore"阶段——只挖碎片，不建结构。（user-invoked）
- **什么时候用**：你还不知道要写什么，只想先把脑子里的东西倒出来。
- **能干什么**：
  - 用 grilling 式追问逼你产**碎片**，从你说的第一句（含最初的提示）就开始记，边聊边追加到同一个 markdown。
  - "碎片"的标准很宽：一句想用但没处放的狠话、一句带一行动因的论断、一个小故事/代码片段/场景/类比、半截想法、引语与对话、按感觉凑在一起的观察清单、抱怨/自白/笑点，以及最有价值的**领头词**（一个能给整篇命名的压缩隐喻，像"曳光弹""战争迷雾"）。
  - 硬格式：开头只放一个工作标题 H1，没有元数据、目录、日期；碎片之间用 `---` 分隔，正文里不加标题不加标签。
  - 写入纪律：每次写前先重读磁盘（你可能自己改过/删过），**只追加不覆盖**；你说"删掉最后一条""这条改狠一点""这两条合了"都是一等指令。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：无。

#### `writing-shape` <sub>mattpocock/skills</sub>
- **一句话**（实验性/未完成）：写作的"exploit"阶段——把素材文件塑造成文章，一段一段来并辩论每段的形式。（user-invoked）
- **什么时候用**：素材（碎片/散乱散文/访谈记录）已经有了，要变成一篇成稿。
- **能干什么**：
  - 先把素材**读透**（且把它当只读，绝不改原文），再和你定读者前提。
  - 给 2–3 个**不同论点/角度的开头**，逼你选一个或拼一个混合体——选中的开头决定了后面整篇要做什么。
  - 之后每次问"给定这个开头，读者接下来需要听什么"，从素材里取料；每段都要辩论**形式**（段落/列表/表格/callout/引语/代码块），每个选择都得说得出理由。
  - 边定边立刻写进文件（不批量攒），循环到你喊停；同一套 **grounding** 准则：概念必须先被铺垫，下一段才能依赖它。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：无。

---

### 三、misc/（4 个 —— 冻结、无人维护）

> 分类 README 原文："留着但很少用的工具，不在插件里推广。" `SCOPE.md` 里进一步写明 **misc/ 是冻结且不维护的，所有 issue 都已关闭**。
> 换句话说：能用，但别指望修。

#### `git-guardrails-claude-code` <sub>mattpocock/skills</sub>
- **一句话**：给 Claude Code 装一个 PreToolUse hook，在执行前拦掉危险 git 命令。（model-invoked）
- **什么时候用**：让 agent 放手写代码，但绝不希望它自己 `push` 或 `reset --hard`。
- **能干什么**：
  - 拦这些：`git push`（含 `--force`）、`git reset --hard`、`git clean -f` / `-fd`、`git branch -D`、`git checkout .` / `git restore .`；被拦时 Claude 会看到"你没有权限使用这些命令"。
  - 问你是装**本项目**（`.claude/settings.json` + `.claude/hooks/`）还是**全局**（`~/.claude/`），拷脚本、`chmod +x`、把 hook 合并进既有的 `hooks.PreToolUse` 数组（不覆盖其它设置）。
  - 问你要不要增删黑名单正则；最后用 `echo '{"tool_input":{"command":"git push origin main"}}' | <脚本>` 自测，应当退出码 2 并打印 BLOCKED。
- **外部 Key / 依赖**：无需外部 Key；脚本依赖 `jq`（取 `.tool_input.command`）；只能在有 Claude Code hooks 的宿主上用。
- **自带资源**：`scripts/block-dangerous-git.sh`。

#### `migrate-to-shoehorn` <sub>mattpocock/skills</sub>
- **一句话**：把测试文件里的 `as` 类型断言换成 `@total-typescript/shoehorn` 的类型安全写法。（model-invoked）
- **什么时候用**：测试里到处 `as Request`、`as unknown as Foo` 硬转，改一个类型就要全改一遍。
- **能干什么**：
  - 三种映射：`as Type` → `fromPartial()`；`as unknown as Type`（故意给错数据）→ `fromAny()`；想先要完整对象、以后再放松 → `fromExact()`。
  - 给完整迁移清单：装包 → `grep -r " as [A-Z]" --include="*.test.ts" --include="*.spec.ts"` 找目标 → 逐个替换 → 补 import → 跑类型检查。
  - 硬规则：**只用于测试代码，生产代码永远不用**。
- **外部 Key / 依赖**：需要 `npm i @total-typescript/shoehorn`（唯一外部依赖，无需 Key）。
- **自带资源**：无（前后对照示例内嵌）。

#### `scaffold-exercises` <sub>mattpocock/skills</sub>
- **一句话**：批量造练习目录骨架，造出来能过他自己教程仓库的 linter。（model-invoked）
- **什么时候用**：你在维护 Matt 那套课程/练习仓库，要按规范加一批练习题。
- **能干什么**：
  - 目录命名：section 是 `XX-section-name/`，练习是 `XX.YY-exercise-name/`，全小写连字符；每个练习至少要有 `problem/`、`solution/`、`explainer/` 三者之一，打桩时默认建 `explainer/`。
  - 每个子目录都要有**非空**的 `readme.md` 且无坏链；有代码时还要 `main.ts`（>1 行），纯 readme 的桩是允许的。
  - 流程：解析计划 → `mkdir -p` → 写桩 readme → 跑 `pnpm ai-hero-cli internal lint` → 修到过 → 提交；还附了 linter 的完整规则清单（不许 `.gitkeep`、不许 `speaker-notes.md`、readme 里不许 `pnpm run exercise` 等）。
- **外部 Key / 依赖**：无需 Key；依赖 `pnpm ai-hero-cli internal lint`（Matt 教程仓库内部的 CLI，不是通用工具）。
- **自带资源**：无。

#### `setup-pre-commit` <sub>mattpocock/skills</sub>
- **一句话**：在当前仓库装 Husky pre-commit：Prettier 格式化 + 类型检查 + 测试。（model-invoked）
- **什么时候用**：JS/TS 仓库还没有提交门禁，想让每次 commit 自动过一遍格式/类型/测试。
- **能干什么**：
  - 自动识别包管理器（看 `package-lock.json`/`pnpm-lock.yaml`/`yarn.lock`/`bun.lockb`，认不出默认 npm）。
  - 装 `husky lint-staged prettier` 为 devDependencies → `npx husky init` → 写 `.husky/pre-commit`（`npx lint-staged` → `npm run typecheck` → `npm run test`；仓库没有这两个脚本就省掉并告诉你）。
  - 写 `.lintstagedrc`（`prettier --ignore-unknown --write`，图片之类 Prettier 读不懂的文件自动跳过）；只在缺失时创建 `.prettierrc` 默认配置；给 5 项自检清单，最后**用这个新 hook 提交一次自己**当冒烟测试。
- **外部 Key / 依赖**：无需 Key；需要 Node 项目，会装 `husky`、`lint-staged`、`prettier` 三个 npm 包。
- **自带资源**：无。

---

### 四、productivity/（7 个，不限语言的通用工作流）

#### `grill-me` <sub>mattpocock/skills</sub>
- **一句话**：`grilling` 的无状态入口——只要被追问，不留任何文件。（user-invoked）
- **什么时候用**：你**不在**一个工作目录里（在磨一个计划、一个设计、一篇稿子），不需要 `GLOSSARY.md` 之类的落盘产物。
- **能干什么**：
  - 正文只有一句："调用 Skill 工具，名为 grilling"——它是个薄壳入口。
  - 因为不落盘，适合一次性、不想污染仓库的思考。
- **外部 Key / 依赖**：无需外部 Key（需要 `grilling` 已安装）。
- **自带资源**：无。

#### `grilling` <sub>mattpocock/skills</sub>
- **一句话**：把人往死里追问的原语——把决策看成一棵设计树，一轮只问"前沿"。（model-invoked）
- **什么时候用**：想在一个计划/决策/点子上达成真正的共识（`grill-me`、`grill-with-docs`、`triage`、`wayfinder`、`improve-codebase-architecture` 内部都在用它）。
- **能干什么**：
  - 定义"前沿"：前置条件都已确定、**现在就能问**、不用瞎猜你答案的那些问题。一轮把整个前沿问完，每问编号 + 附上它的推荐答案，然后**停下等你回答**，再算下一轮；措辞要让"是"等于接受它的推荐。
  - 依赖本轮另一个未决问题的，属于**后面**的轮次，不能混进来。
  - 分工很明确：**事实是它的活**（需要环境信息就派子 agent 去查，绝不来问你能自己查的东西，且不阻塞其余问题），**决策是你的**——摆到你面前等你定。
  - 结束条件：前沿空了 = 设计树每个分支都走过、没有默认假设了；而且**在你确认达成共识之前不许动手**。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：无。

#### `handoff` <sub>mattpocock/skills</sub>
- **一句话**：把当前对话压成一份交接文档，让另一个 agent 能接着干。（user-invoked）
- **什么时候用**：要换 harness / 换目录 / 交给同事 / 上下文快满了，需要"可携带"的交接。
- **能干什么**：
  - 写到操作系统临时目录（`$TMPDIR`，没有就 `/tmp`，Windows 用 `%TEMP%`），**故意不写进当前工作区**。
  - 必须含 "suggested skills" 段，点名下个 agent 该调哪些 skill。
  - 不复述已有产物（spec、计划、ADR、issue、commit、diff），只给路径或 URL；你带了参数的话，把参数当作"下一次会话要干什么"来裁剪内容。
  - 必须脱敏：API key、密码、可识别个人身份的信息一律不放。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：无。

#### `teach` <sub>mattpocock/skills</sub>
- **一句话**：跨多次会话教你一个东西，把当前目录当成一个有状态的"教学工作区"。（user-invoked）
- **什么时候用**：你想真的学会（不是查一下），愿意分几次课慢慢来。
- **能干什么**：
  - 工作区文件各司其职：`MISSION.md`（**为什么**学，一切教学挂在它上面）、`RESOURCES.md`（可信一手资源）、`./lessons/0001-xxx.html`（一课一个自包含 HTML，编号递增）、`./reference/*.html`（压出来的速查卡，适合打印）、`./learning-records/0001-xxx.md`（教学版 ADR，用来算"最近发展区"）、`./assets/`（跨课复用组件，**默认复用**而不是内联）、`NOTES.md`（你的偏好与随手记）。
  - 方法论上分得很清：**fluency 强度**（当场想得起来）会给你虚假的掌握感，真目标是 **storage strength**（长期留存），所以刻意用回忆练习、间隔、交错来制造"合意困难"；拿知识时**难度是敌人**，练技能时**难度是工具**。
  - 每课要短、要美（"Tufte 那种"）、要给你一个能站上去的具体收获，并推荐一个最高质量的一手来源；选择题每个选项字数一样长、正确项位置打散。
  - 追问"智慧"时默认先自己答，再把你导向一个**社区**去真实世界练；并明确警告**不要相信自己的参数化知识**，知识必须来自 `RESOURCES.md`。
- **外部 Key / 依赖**：无需外部 Key；需要联网找资源、需要浏览器打开课程文件。
- **自带资源**：`MISSION-FORMAT.md`、`RESOURCES-FORMAT.md`、`GLOSSARY-FORMAT.md`、`LEARNING-RECORD-FORMAT.md`。

#### `to-questionnaire` <sub>mattpocock/skills</sub>
- **一句话**：把一个"你一个人答不了"的决策，做成一份 Markdown 问卷，寄给那个知道答案的人。（user-invoked）
- **什么时候用**：卡住你的东西既不在你脑子里、也不在代码里，而在别人的脑子里。
- **能干什么**：
  - 核心手法是"**访谈寄送，不访谈主题**"：只问你一定答得上的两件事——**寄给谁**（角色、专长、和你的关系，决定语气和要带多少背景）和**要拿回什么**（具体要能决定/能做成的清单）；问卷里的问题则瞄准"对方知道的"和"你需要的"之间那道**缺口**。
  - 按 discovery questionnaire 模板产出：Purpose（为什么存在、压着什么决策）/ From-To-如何使用答案 / Context（一段话给不在你脑子里的人）/ How to answer（截止时间、大概耗时，并说明"部分回答和'我不知道'都有用"）/ 按主题分 `##` 段 / 结尾 "Anything else?"。
  - 格式规矩：一题只装一个想法、绝不复合；题下直接给作答空行；只在容易被误读时才加一行 "_Why this matters_"；**越重要越靠前**（异步可能只给一次机会）；写到当前目录 `to-questionnaire-<slug>.md` 并告诉你路径。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：无（模板内嵌）。

#### `wait-what` <sub>mattpocock/skills</sub>
- **一句话**：一句话纠偏——"等等，我没听懂，重讲一遍"。（user-invoked）
- **什么时候用**：任何对话里、甚至任何别的 skill 执行到一半，某条消息没落地的时候立刻发。
- **能干什么**：
  - 让 agent 重新讲一遍，并补上你缺的那部分上下文。
  - 强制用 **ASD-STE100 简化技术英语**（短句、一个词一个意思）。
  - 强制使用 `GLOSSARY.md` 里的通用语言；一个仓库有多个 context 时跟 `GLOSSARY-MAP.md` 找到对应那个。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：无。

#### `writing-for-agents` <sub>mattpocock/skills</sub>
- **一句话**：写"给 agent 看的文档"的通用写法参考——skill、`AGENTS.md`/`CLAUDE.md`、任何被指针指向的文档。（model-invoked）
- **什么时候用**：要新建/修改一个 skill，要改 `AGENTS.md`/`CLAUDE.md`，或要写一份 agent 会去读的说明。
- **能干什么**：
  - **上下文指针（context pointer）**：真正决定 agent 何时去读的，是**指针的措辞**而不是目标本身。一个必备材料配上措辞含糊的指针 = 一个"波动 bug"：先磨措辞，磨不动才把内容内联。
  - 指针干两件事：说清材料是什么 + 列出**该去读它的分支**；同一个分支用同义词写两遍要合并；常驻的每个字每一轮都在花钱，所以指针比正文更要狠剪（领头词前置、删掉正文已有的身份信息）。
  - 区分两种负载：**context load**（常驻材料吃掉的窗口和注意力）与 **cognitive load**（人要知道有哪些文档、何时用哪个——这不是要最小化的成本，而是人的主体性的价格，花在真正需要人判断的地方）。
  - 还讲了信息层级、步骤与完成判据、什么时候该拆文档、领头词、怎么剪枝。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：`SKILL-MECHANICS.md`（专门讲"当文档是 skill 时"的差别：frontmatter、user-invoked vs model-invoked 怎么选、router 型 skill）。

---

### 小结（适合谁 / 怎么用 / 优先级）

- **适合谁**：真的在写工程项目、而不是让 AI 一把梭出 demo 的人。整套价值观是"小而可组合、可改、可读"，反对大而全的流程框架；单个 skill 都很短，就是为了让你 fork 改成自己的。
- **怎么用**：先装（plugin 或 `npx skills add`）→ 每个仓库跑一次 `/setup-matt-pocock-skills` → 日常从 `/grill-with-docs` 或 `/ask-matt` 进主流程；`in-progress/` 那 7 个单独装、当 beta 试；`misc/` 那 4 个按需拿，别指望维护。
- **优先级（我的建议）**：第一梯队 `grilling` / `grill-with-docs` / `tdd` / `diagnosing-bugs` / `code-review` / `to-spec` + `to-tickets`（原文也说 grilling 是它最受欢迎的技能、`grill-with-docs` 可能是整仓最酷的技巧）；第二梯队 `domain-modeling`、`codebase-design`、`improve-codebase-architecture`、`handoff`、`pr`；第三梯队是 tracker 相关（`triage`、`wayfinder`、`implement-spec`）和 `misc/`。
- **一个提醒**：这套东西的收益高度依赖"你愿意被追问、愿意先对齐再动手"。如果想跳过访谈直接出活，它反而会显得啰嗦。


---

## 5. xingtu1996/dsh-xingtu-skills — DSH 插件包（30 个）

> 数据来源：`skills/<name>/SKILL.md` 原文（已逐个读完）+ 仓库 `README.zh.md` / `README.md`
> 插件版本：`dsh-xingtu-skills@0.1.2`｜许可证 MIT｜装法：`dsh plugin add dsh-xingtu-skills`

### 这是什么

一句话：**一个把 30 个 SKILL.md 打成 DSH 标准插件的包**，装完技能的目录会自动出现，用到才加载（progressive disclosure）。它本身不是模型、不是网关，只是"技能内容 + 一个注册入口"（`lib/index.js` 里循环 `ctx.skills.register()`）。

30 个技能分四摞，脾气完全不同：

| 系列 | 数量 | 管什么 | 说明 |
|---|---:|---|---|
| **Caveman** | 14 | token 压缩 | 分两半：**本地 8 个**（模式本体 `/ 速查卡 / 账单 / commit / review / 压文件 / 子 agent 决策 / 只读探路`，装上就能用）+ **依赖 Caveman 产品 6 个**（`setup` `discover` `learn` `evidence-review` `manage` `optimize`，要网关/Cave API Key 或已登录的 `caveman` CLI） |
| **Ponytail** | 6 | 极简开发 / 技术债 | 一个"懒"模式 + 四个不同用途的配套（审 diff / 审全库 / 收债务 / 看战绩）+ 一张说明卡 |
| **Engineering practice** | 6 | 工程纪律 | 六个"什么时候停手"的流程约束，全是短 SKILL.md，没有脚本 |
| **Content & media** | 4 | 自媒体内容 | 行途技术公众号专用：去 AI 味、事实核查、对抗审查、数字核实 |

##### 先看这张表，别装错

| 你的需求 | 该用哪个 |
|---|---|
| 让 AI 回复短一点，省输出 token | `caveman`（模式，一直生效） |
| 看看这一场到底省了多少 token | `caveman-stats`（要 Claude Code 日志 + hook） |
| 把 CLAUDE.md 这类记忆文件压一遍 | `caveman-compress`（会改文件，先备份） |
| 写 commit message / 审 PR | `caveman-commit` / `caveman-review`（各管一件事） |
| 想量 LLM 成本、按任务拆账单 | `caveman-setup` → `caveman-discover` → `caveman-evidence-review` |
| 让 AI 少写代码、别过度设计 | `ponytail`（模式） |
| 审"这段 diff 有没有过度设计" | `ponytail-review`（只看 diff） |
| 审"整个仓库能砍掉什么" | `ponytail-audit`（看全树，排名输出） |
| 查"当初偷的懒还欠着吗" | `ponytail-debt`（收 `ponytail:` 注释成台账） |
| 想知道 Ponytail 到底有没有用 | `ponytail-gain`（官方基准分，**不是**你仓库的真实数字） |

> ⚠️ 两个系列都有"模式本体 + help 卡 + review/audit"的相似结构，**它们不是同一个东西的不同别名**，下面逐个写清区别。

---

### 一、Caveman · token 压缩（14 个）

#### `caveman` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：让 AI 像"聪明的原始人"那样说话——技术内容一个不少，客套话、冠词、模糊限定词全砍掉。
- **什么时候用**：觉得回复太啰嗦、太"AI 味"；长会话想延长上下文寿命；用户说"caveman 模式""说简短点""少点 token"。
- **能干什么**：
  - 六档强度：`lite`（只去废话）/ `full`（默认，去冠词、可用短语）/ `ultra`（极致压缩，一句一个事实）/ `wenyan-lite`、`wenyan-full`、`wenyan-ultra`（文言文三档，按**汉字数**算能减 80-90%，不是 token 数）。
  - 是**持续模式**：每个回复都生效，不会几轮后自己漂回啰嗦，只有说"stop caveman / normal mode"才退出。
  - 三条自我刹车：安全警告、不可逆操作确认、多步顺序容易读错时，**自动切回正常话**；讲完再切回去。
  - 反"装原始人"：不许为了像原始人而多写字、不许自创缩写（`cfg`/`impl` 这类在分词器里跟全词一样贵）、不许用箭头 `→`——不省 token 还更难读。
  - 沿用用户语言：用户写中文就中文压缩，只压风格不换语言；代码块、报错原文、API 名一律不动。
- **外部 Key / 依赖**：无需外部 Key。默认档位可用 `CAVEMAN_DEFAULT_MODE` 环境变量或 `~/.config/caveman/config.json` 改。
- **自带资源**：`README.md`（含六档对照与示例）。
- **注意**：SKILL.md 里写"实测省输出 token 65%"，但同仓 README 明确说"不发布总体降幅与质量等价声明"——**这个 65% 当宣传口径看，别当你要的账单数字**；要真数字用 `caveman-stats`。

#### `caveman-help` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：一张速查卡，把所有 caveman 档位、兄弟技能、怎么关掉、怎么改默认档全列出来。
- **什么时候用**：忘了 `/caveman` 后面能跟什么；想知道还有哪些 caveman 技能；要改默认档但忘了配置文件在哪。
- **能干什么**：
  - 一次输出全部档位表 + 技能表 + 关闭方式 + 环境变量/配置文件两种改默认法。
  - **一次性展示，不改状态**：不写标记文件、不持久化、不切换当前档位——这是它和 `caveman` 最根本的区别。
  - 附带 Claude Code 的插件自动更新流程（`/plugin` → Marketplaces → 开自动更新）。
- **外部 Key / 依赖**：无需外部 Key。纯静态卡片，一条命令都不跑。
- **自带资源**：`README.md`。

#### `caveman-stats` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：翻真实的会话日志，把这一场"省了多少 token"算给你看——不是 AI 估的，是磁盘上 JSONL 里数出来的。
- **什么时候用**：想知道 caveman 到底值不值；怀疑压缩反而更贵；要给老板/同事看凭证。
- **能干什么**：
  - 读 Claude Code 会话日志，报真实输入/输出 token、对比基线、节省量与百分比。
  - 额外给两行诚实账：`Est. rule overhead`（每轮注入 caveman 规则本身的输入成本，默认按 1250 token/轮，可用 `CAVEMAN_RULE_OVERHEAD_TOKENS` 覆盖）和 `Est. net`（节省减开销）。
  - **净值为负会直接说负**，并建议这个 workload 干脆把 caveman 关掉——不拿毛节省量掩盖。
- **外部 Key / 依赖**：无需 API Key，但**强依赖宿主环境**：需要 Claude Code 的会话日志 + `hooks/caveman-mode-tracker.js` / `hooks/caveman-stats.js`。这两个 hook **不在本插件包里**（本仓无 `hooks/` 目录），所以装了这个插件也不会自动有 `/caveman-stats`，得从上游 caveman 项目另装。
- **自带资源**：`README.md`（含示例输出，标明数字仅示意）。

#### `caveman-commit` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：写极简 commit message，Conventional Commits 格式，标题尽量 ≤50 字符，只在"为什么"不明显时才写正文。
- **什么时候用**：提交前；说"写个 commit""commit message"；暂存区有改动时自动触发。
- **能干什么**：
  - 强制 `type(scope): 祈使句摘要`，11 种 type（feat/fix/refactor/perf/docs/test/chore/build/ci/style/revert），祈使语气、无句号、硬上限 72 字符。
  - 正文只在必要时出现：非显然的"为什么"、破坏性变更、迁移说明、关联 issue。
  - 黑名单：不写"这个提交做了X"、不写"我/我们"、不写"按某某要求"、不写 AI 署名、不写 emoji。
  - 破坏性变更/安全修复/数据迁移/回滚提交**必须**带正文，不许压成标题完事。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：`README.md`。
- **注意**：只产出文本，**不替你 `git commit`、不 stage、不 amend**。

#### `caveman-review` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：把 PR 评论压成一行一条——位置、问题、怎么改，没有开场白。
- **什么时候用**：审 PR / 审 diff；说"review 这个 PR""代码审查"。
- **能干什么**：
  - 固定格式 `L<行号>: <问题>. <改法>.`，多文件时 `<file>:L<行>`。
  - 四级可选前缀：🔴 `bug:`（会出事）/ 🟡 `risk:`（能跑但脆）/ 🔵 `nit:`（风格，可以不理）/ ❓ `q:`（真提问，不是建议）。
  - 删掉"我注意到…""可能或许大概"这类软话；保留精确行号、反引号包住的符号名、可执行的改法。
  - 自动降级：CVE 级安全问题、架构分歧、新人 onboarding 场景，改成正常段落讲清楚，再回去压缩。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：`README.md`。
- **注意**：只写评论，**不改代码、不点 approve/request changes、不跑 linter**。

#### `caveman-compress` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：把 CLAUDE.md、todo、偏好这类**自然语言记忆文件**压成 caveman 体，直接覆盖原文件（会先备份），省的是每次会话都要重读的输入 token。
- **什么时候用**：CLAUDE.md 越写越长；每次开会话都要重新灌一堆上下文；`/caveman-compress <文件>`。
- **能干什么**：
  - 带一整套 Python 脚本流水线：先**零 token** 判文件类型（散文才压，代码/配置直接跳过），调模型压，再**零 token** 校验，错了只针对错处补，最多重试 2 次，还不行就原样不动并报错。
  - 保护得很死：代码块、行内代码、URL、文件路径、命令、技术名词、专有名词、日期版本号、环境变量**逐字不动**；Markdown 结构（标题层级、列表嵌套、表格、YAML frontmatter）全保留；frontmatter 是剥出来压完再原样贴回。
  - 安全兜底：`credentials.md`、`secrets.txt`、`.env`、`*.pem`、`id_rsa` 这类文件名**直接拒读**，全文不发出去。
  - 备份放到**仓库外的数据目录**（`$XDG_DATA_HOME/caveman-compress/backups/<父目录名>/`，Windows 在 `%LOCALAPPDATA%`），就是为了不让技能自动加载器把备份又当活文件读一遍。
- **外部 Key / 依赖**：**需要 Anthropic**——设了 `ANTHROPIC_API_KEY` 就走官方 Python SDK，没设就退回本机 `claude` CLI（用你已有的桌面登录）。另需 Python 3；`CAVEMAN_MODEL` 可换模型（默认 `claude-sonnet-4-5`）。
- **自带资源**：`scripts/`（`compress.py` 主编排、`cli.py` 入口、`detect.py` 类型判定、`validate.py` 校验、`benchmark.py`）、`SECURITY.md`（解释 Snyk 高风险评级从哪来，逐条说明它不干什么）、`README.md`（5 个样例文件实测平均压 46%）。

#### `cavecrew` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：一张"要不要把活派给子 agent"的决策表——三个 caveman 风格子 agent（找代码/改 1-2 个文件/审 diff），返回结果是压缩过的，主上下文省地方。
- **什么时候用**：长会话感觉上下文快满了；在"自己干"和"派子 agent"之间犹豫；说"delegate to subagent""用 cavecrew"。
- **能干什么**：
  - 三个预置角色分工：`cavecrew-investigator`（只读定位）、`cavecrew-builder`（≤2 文件外科手术式改）、`cavecrew-reviewer`（一行一条审 diff）。
  - 给了**可依赖的输出契约**：比如 investigator 固定回 `path:line — \`symbol\` — 短注`，builder 固定回 `path:行区间 — 改动≤10词` + `verified:`，超范围就回 `too-big.` / `needs-confirm.` 这种首词终止符——主线程可以直接程序化判读。
  - 明确"什么时候别用"：不知道文件在哪先别叫 builder；5 文件重构别走 investigator→builder 链（会被 `too-big.` 打回）；想要架构意见别找 reviewer，用原生 `Code Reviewer`。
  - 给了三种串联套路：定位→改→验、并行侦察（一次发 2-3 个 investigator）、已知位置直接单发。
- **外部 Key / 依赖**：无需外部 Key。**但依赖宿主支持子 agent 机制**（要能 spawn 子 agent），且在 DSH 里这三个预置子 agent 不一定存在，属于"给 Claude Code / Codex 类宿主写的"。
- **自带资源**：`README.md`（比 SKILL.md 更克制：明说"效果取决于任务/agent/派发次数"，不公布统一降幅比例）。

#### `caveman-explore` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：一个只读的"代码在哪"探路者，只回 `路径:行区间 + 一句理由`，它的读文件和搜索都不进你的主对话。
- **什么时候用**：冷启动不熟仓库；跨文件大范围找；直接搜没搜到；换句话说是"我还不知道该看哪个文件"的时候。
- **能干什么**：
  - 第一轮就**并行**铺开：Glob 猜路径 + Grep 找符号/字符串 + Read 看最像的几个文件，不一个个试。
  - 输出是**纯证据块**，一行一条 `path/to/file.ext:START-END  理由`，没有前言、没有总结、没有标题——因为它只优化"主 agent 的 token 预算"。
  - 硬约束：只报**真读过**的行区间，绝不凭空估行号，也不报超出文件末尾的范围；找不到就老实回 `no relevant locations found`。
  - 角色设定很窄：不改文件、不跑命令、不提方案。
- **外部 Key / 依赖**：无需外部 Key。声明了 `tools: Read, Glob, Grep`、`model: haiku`——**这是给支持"子 agent 带独立模型/工具白名单"的宿主的**；DSH 里这些字段不一定被解释。
- **自带资源**：`package.json`（私有包，只带 SKILL.md）、`tests/skill-file.test.mjs`（校验 SKILL.md 格式）。

#### `caveman-setup` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：把当前仓库的 LLM 请求接到 Caveman 网关上——**只做记录，不改行为**，从此每笔请求的成本、token、延迟都有账。
- **什么时候用**：想给 app 加 LLM 花费可观测性；用户贴了 Caveman 的 setup 提示词；说"set up caveman"。
- **能干什么**：
  - 先扫仓库找**每一个**真实 LLM 调用点：SDK import（openai / anthropic / ai + @ai-sdk / langchain / litellm / google-genai / crewai / pydantic_ai 等）、裸 HTTP 打 `api.openai.com` / `api.anthropic.com`、已有的 `*_BASE_URL` 环境变量；**找不到就停下来报"无可接线"**，不许硬造一个集成。
  - 按插件给的"官方配方"改 baseURL + 一个认证头，覆盖 OpenAI SDK(TS/Python)、Anthropic SDK、Vercel AI SDK、LangChain/LangGraph、LiteLLM、裸 HTTP 六种形态。
  - 密钥纪律：`CAVE_API_KEY` 只进 env 文件（并检查 `.gitignore`），绝不硬编码、绝不完整打印。
  - 最后**发一个真的小请求**验证（`max_tokens` ≤32），按 HTTP 状态如实报；失败有五种逐字模板（无可接线 / 网关不可达 / 401 / 404 路由不匹配 / provider 报错），**不许把失败报成成功**。
- **外部 Key / 依赖**：**需要 4 个值**：`GATEWAY`（网关地址）、`CAVE_API_KEY`（Cave 密钥）、`PROVIDER_KEYS`（`stored` 还是 `byok`）、`DASHBOARD`（控制台地址）。少一个就停下来问，不许猜地址、不许自己造 key。**会花真钱**：验证请求是一次真实的、会计费的 provider 调用。
- **自带资源**：无（纯 SKILL.md，但正文 1 万多字，含全部代码配方与失败模板）。

#### `caveman-discover` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：给仓库里每个 LLM 工作流贴标签，让 Caveman 的账单按"这活是干嘛的"分组（support-reply / nightly-digest），而不是全塞进一个 `unlabeled-workflow` 黑洞。
- **什么时候用**：`caveman-setup` 已经接好了网关，账单还是一坨；说"discover workflows"；想把花费按业务拆。
- **能干什么**：
  - 从**入口**清点而不是从 import 清点：HTTP/RPC handler、定时任务/队列消费者/GitHub Actions、CLI 脚本、烧真钱的评测/测试、框架里的每个 graph/crew/agent 各算一个。
  - 命名规矩讲得很死：小写 `[a-z0-9_-]`、1-96 字符，名字要描述"干什么活"而不是"用什么技术"；`openai-calls`、`main`、`SupportReply`、`johns-test-3` 都是反面例子。名字**基本等于永久**——改一次就把历史账单劈成两半。
  - 关键判断：一个 handler 里十个调用点 = 一个 workflow；一个共享 `llm.ts` 被三个任务用 = 三个 workflow（标签打在**调用方**，不打在共享 helper 上）。
  - 先给表格、等用户点头再改代码；重跑必须幂等；没接网关的调用点不贴标签，单独列进"未接线"。
  - 验证：跑通一条已打标的路径，说明"定时任务要等它下次跑才出现在面板上"——不假装已经实时可见。
- **外部 Key / 依赖**：无需额外 Key，但**前提是仓库已经过 `caveman-setup` 接进网关**（否则没有 `CAVE_API_KEY` 可带，也没有网关流量可打标）。
- **自带资源**：无。

#### `caveman-learn` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：`caveman learn` 只负责**测量**你的 token 花哪了；这个技能是"动手那一半"——把报告里的浪费点变成编辑，**每改一处都要你点头**。
- **什么时候用**：跑完 `caveman learn` 想看怎么改；CLAUDE.md 太肥想瘦身；每次会话都要重贴的上下文想挪走；说"降低 agent 的 token 成本"。
- **能干什么**：
  - 先跑 `caveman learn report --json`（`caveman.learn.v1`），读 Cave Score 四要素 + 排好序的 token 黑洞，逐个说明它属哪类、依据是什么。
  - 按类走不同流程：**reducible**（肥 CLAUDE.md、从没被触发过的 skill）→ `--dry-run` 出候选、给 before→after token 对比、你同意才用文件工具改、改完重测，**不降就回滚**；**recurring_context**（每会话重建的大段）→ 挪进 `cavemem`，只留一个便宜的指针。
  - 搬 context 这段防得很细：候选只给定位信息（`rel_path` + `jsonl_line` + `block_index` + `content_sha256`），**必须自己回本地重算 sha256 对上才动手**；存完必须验证 `caveman mem recall` 真能召回，召回不到就 `forget` + 还原源文件——"把上下文删了却没留召回路径"是它专门拦的头号失败。
  - **load_bearing**（真正承重的）一律不碰，只出现在报告里让分数诚实。
  - 铁律：逐条同意（不许"全部应用"）、每笔可回滚、数字只算 `inferred`、绝不挂货币单位。
- **外部 Key / 依赖**：无需 API Key，但**必须有已登录的 `caveman` CLI**（`caveman learn` / `caveman mem` 都是它的子命令）；`cavemem` 卸载功能依赖 `caveman mem forget`。
- **自带资源**：`README.md`、`CLAUDE.md`（给维护者看的实现说明）、`package.json`、`tests/skill-file.test.mjs`（断言净减门槛、不许变笨的守卫、逐条同意、可逆性都写进了 SKILL.md）。

#### `caveman-evidence-review` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：**只读**地把 Caveman 里的证据读给你听——钱花哪了、Cave Score 怎么样、哪些 workflow 要盯，不许动手。
- **什么时候用**：问"Caveman 查出来什么""LLM 花费都去哪了""为什么成本/质量变了""给我看个 trace 复盘"。
- **能干什么**：
  - 分四类账**绝不许混**：实测的 provider 完整标价成本、`inferred` 的每日优化空间、`verified` 的台账节省、证据本身的成本——不能相加、不能改标签。
  - 先立基线（overview / costs / score / workflows / verified_savings + 排好序的每日 headroom），再用 `caveman_trace_search` 拿**有边界的时间窗 + 闭区间过滤**去验证解释，最后只挑少量高信号 trace 看细节。
  - 保护隐私：默认**不拉** prompt / completion / 工具 / 产物正文，元数据、span、耗时、模型、token 数、状态就够。
  - 规矩：空结果只能说明"当前没信号"，不等于"零成本/零风险"；必须引用 trace id 和确切时间窗；不许从一条贵 trace 推因果，要给对照组或更早的窗。
- **外部 Key / 依赖**：需要 Caveman Cloud 登录 + 已选项目。优先走 Caveman MCP 工具（`caveman_context` / `caveman_report` / `caveman_trace_search` / `caveman_trace_get`），退路是 CLI（`caveman cloud whoami|costs|score|plan|traces`）。登不上或没选项目就停，不许猜。
- **自带资源**：无。

#### `caveman-manage` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：看 Caveman 实验（eval 门控的那些）的生命周期和证据，然后**挡住**任何不安全的执行——当前能力是"只读 + 给建议"。
- **什么时候用**：问"这个实验能不能 approve / 该 start 还是 cancel / 回滚"；问"证据支持什么动作"。
- **能干什么**：
  - 先读状态和结果，再给**一个**有证据支撑的建议或直接拦下；缺字段就说 `evidence incomplete`，不许当"通过"。
  - 六条硬闸：仅仅是"看看/解释/建议"只授权读；结果 pending、护栏缺失、证据报 breach 的**一律不批**；实验提升**绝不**能转成 `verified_savings`；不许自己填组织 id；**即使用户批准也不执行变更**（`<action>:<id>` 这种字符串 agent 自己就能生成，不算人类意图的证据）；未知状态和服务端错误一律 fail closed 并报 `cave_snake_code`。
  - 用户在外部自己执行了命令之后，要**重新读一遍**服务器实际状态，不许从"他说执行了"推断成功。
- **外部 Key / 依赖**：需要 Caveman Cloud 登录 + 项目 + 实验 id；MCP（`caveman_context` / `caveman_experiment_get` 的 get/results/list）或 CLI（`caveman cloud experiments list|show|results`）。
- **自带资源**：无。

#### `caveman-optimize` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：把 Caveman 给的"只读观察"变成**你自己选**的一个优化候选 + 一组配对的 baseline/candidate 评测——它不改钱、不改实验生命周期。
- **什么时候用**：手上有 Caveman 的 optimization 报告；想评估某个候选改法；说"看看这条观察""评估这个改动"。
- **能干什么**：
  - 只读 `caveman opportunities list` 里的 `report_only_observations`，只认四个 id（`context-window-profile` / `tool-catalog-profile` / `tool-output-size-profile` / `exploration-load-profile`），这三个退役 id（`context-window-bloat` / `tool-catalog-utilization` / `verbose-tool-output`）**永不再启用**。
  - 这些 profile 的"机会金额"**恒为零且没有执行路径**——不许排序、不许编美元数字、不许把聚合形状说成某个具体调用点的问题。
  - 流程：原样呈现观察（不排名）→ 要操作员**明确选一个** → 自己回仓库找能产生这种形状的机制（要引 `file:line`）→ 提一个最小候选 + **配对评测设计**（同一批固定输入、同一套成本口径、同一质量检查、写清混杂因素）→ 批准后才改 → 跑配对评测 + 仓库自己的检查 → 质量退化或结果不确定就**只回滚这一处**。
  - 报告只写"观察"，不写"节省"；本地配对结果只支持"这个候选在这批 fixture 上"的结论，不等于生产节省。
- **外部 Key / 依赖**：需要已登录的 `caveman` CLI。CLI 不可用、认证失败、没有 `report_only_observations` → 立刻停并报明确阻塞点，**不许退回裸网关 Cave Plan 或项目 API key**（那些面没有这个契约）。
- **自带资源**：无。

---

### 二、Ponytail · 极简开发 / 技术债（6 个）

> 六个技能的关系：`ponytail` 是**干活的姿势**，`ponytail-review` / `ponytail-audit` 是**检查别人有没有多写**（一个看 diff、一个看全库），`ponytail-debt` 是**回收自己当初欠下的**，`ponytail-gain` 是**看这套方法的历史战绩**，`ponytail-help` 是**说明书**。

#### `ponytail` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：一个"懒"的高级工程师模式——最省事但真能解决问题的写法，解释只留三行。
- **什么时候用**：任何写/加/改/重构/bug 修复/选库/设计代码的场合；被过度设计、样板代码、无谓依赖烦到；说"ponytail""懒一点""最简方案""YAGNI""别过度设计"。
- **能干什么**：
  - 一把**七级梯子**，从上往下停在第一个能用的档：这功能真的需要存在吗（YAGNI）→ 本仓库里已经有人写过吗（复用优先）→ 标准库能做吗 → 平台原生能力够吗（`<input type="date">` 而不是日期选择库）→ 已装的依赖能解决吗 → 能一行吗 → 最后才写"最小可用代码"。
  - 三档强度：`lite`（照做，但顺口提一句更懒的路子）/ `full`（默认，强制执行梯子）/ `ultra`（YAGNI 极端派，先质疑需求再写）。
  - 修 bug 必须先找**根因**：改之前 grep 一遍所有调用方——在共享函数加一个判空比在每个调用点加更小的 diff，只补工单点到的那条路会让兄弟调用方继续坏着。
  - 留痕机制：故意走捷径、有已知天花板的（全局锁、O(n²)、朴素启发式），要写 `# ponytail: <天花板>, <什么时候升级>` 注释——这样 `ponytail-debt` 才收得到。
  - 不许懒的地方写得很清楚：信任边界的输入校验、防丢数据的错误处理、安全措施、无障碍基线、用户明确要的东西；**理解问题本身永远不许懒**（先读完整再偷懒）；非平凡逻辑要留**一个**能跑的检查（一个 `assert` 自查或一个小 `test_*.py`，不要框架不要夹具）。
  - 输出纪律：代码在前，最多三行说明"跳过了什么、什么时候加"；如果解释比代码长就删解释。
- **外部 Key / 依赖**：无需外部 Key。默认档位可用 `PONYTAIL_DEFAULT_MODE` 或 `~/.config/ponytail/config.json` 改（优先级：环境变量 > 配置文件 > `full`）。
- **自带资源**：无（纯 SKILL.md）。

#### `ponytail-review` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：只盯"过度设计"的代码审查——**只看这次 diff**，一行一条：位置、砍什么、换成什么。
- **什么时候用**：审一个 PR / 一段 diff；问"这段是不是过度设计了""能删点什么""简化一下"。
- **能干什么**：
  - 五种标签：`delete:`（死代码、没人用的灵活性，替换成"没有"）、`stdlib:`（手写的标准库已有，要点名函数）、`native:`（依赖在做平台已做的事，要点名特性）、`yagni:`（一个实现的接口、没人设的配置、一个调用方的层）、`shrink:`（同逻辑更少行，要给出更短写法）。
  - 结尾只留一个指标：`net: -<N> lines possible.`；真没得砍就直接说 `Lean already. Ship.`
  - 边界很硬：**正确性 bug、安全漏洞、性能明确不在范围内**，转给常规 review；不实施修改，只列清单；**单个冒烟测试/assert 自查是 ponytail 的最低配，不算臃肿，永远不许判它该删**。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：无。

#### `ponytail-audit` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：`ponytail-review` 的**全仓库版**——扫整棵树而不是一个 diff，按"能砍多少"从大到小排。
- **什么时候用**：问"审一下这个代码库""整个仓库有什么能删的""找找臃肿"；不是审某个 PR，是想做一次体检。
- **能干什么**：
  - 沿用 review 的同一套五种标签，保证两边口径一致，不会一个技能说 `yagni` 另一个说 `delete`。
  - 专门的搜寻清单：标准库/平台已经有的依赖、单实现接口、单产品工厂、只转发不干事的包装、只导出一个东西的文件、死开关和死配置、手写标准库。
  - 输出一行一条并按大小排名：`<tag> <砍什么>. <换成什么>. [path]`，结尾 `net: -<N> lines, -<M> deps possible.`；没什么可砍就说 `Lean already. Ship.`
  - **只出报告不改代码**，一次性；同样把正确性/安全/性能排除在外。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：无。
- **和 `ponytail-review` 的区别**：review 看 **diff**（这次改动有没有多写），audit 看 **全树**（这个仓库整体有多少历史包袱）。同一个标签体系，不同的观察范围。

#### `ponytail-debt` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：把全仓库所有 `ponytail:` 注释收成一本**债务台账**，免得"以后再改"悄悄变成"永远不改"。
- **什么时候用**：问"当初偷的懒还欠着哪些""ponytail 留下了什么坑""列一下那些捷径"；周期性体检时。
- **能干什么**：
  - 扫 `grep -rnE '(#|//) ?ponytail:'`（跳过 `node_modules`/`.git`/构建产物），每个命中一行台账。
  - 每行固定三要素：`<file>:<line>, <简化了什么>. ceiling: <写明的上限>. upgrade: <什么时候回来改>`——直接从注释里抠，不自己编。
  - 风险标记：没写升级触发条件的打 `no-trigger` 标签——**这些正是会静静烂掉的**；结尾给 `<N> markers, <M> with no trigger.`
  - 可选：`git blame -L<line>,<line>` 给每行加个负责人。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：无。
- **注意**：只读只报；想落盘成 `PONYTAIL-DEBT.md` 要先问用户。

#### `ponytail-gain` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：一块"战绩看板"——用官方基准的中位数告诉你 ponytail 少写多少行、少花多少钱、快多少倍。
- **什么时候用**：想知道这套方法到底有没有用、值不值得开；说"ponytail gain""ponytail 省了什么""看战绩"。
- **能干什么**：
  - 用 ASCII 条画三行：代码行数降到 6-20%（降 80-94%）、成本降到 23-53%（降 47-77%）、速度 3-6 倍。
  - **诚实边界写死了**：这些是**发布基准的中位数**（5 个日常任务 × 3 个模型 Haiku/Sonnet/Opus），**不是你这个仓库的数字**；绝对不许打印"你在这里省了 X 行/token"——因为"没写的那版"根本没写出来，没有真实基线可减。
  - 想看真实的仓库级数字，它会把你导向 `ponytail-debt`（可数的台账），而不是编一个。
- **外部 Key / 依赖**：无需外部 Key。数据来自上游 `benchmarks/`，**本插件包里不含 `benchmarks/` 目录**，所以是"引用的官方数字"，不能重跑。
- **自带资源**：无。
- **注意**：一块一次性展示卡，不改模式、不改文件。

#### `ponytail-help` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：Ponytail 全家的速查卡——三档强度、六个技能各自干什么、怎么关、怎么改默认档、怎么更新。
- **什么时候用**：忘了 `/ponytail` 能跟什么；分不清 audit 和 review；想改默认档或开自动更新。
- **能干什么**：
  - 三档强度表 + 六个技能一览表（每个技能一行说清它干嘛、什么触发）。
  - 说明不同宿主的调用形式：Codex 用 `@ponytail` / `@ponytail-review` / `@ponytail-help`，Claude Code 和 OpenCode 用斜杠命令（OpenCode 六个都是斜杠命令）。
  - 关闭/恢复、改默认档（`PONYTAIL_DEFAULT_MODE` 环境变量 > `~/.config/ponytail/config.json` > `full`）、插件自动更新步骤。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：无。
- **和 `ponytail` 的区别**：help 是**一次性打印**，不切换模式、不写标记、不持久化；`ponytail` 是**持续生效的模式本体**。

---

### 三、Engineering practice · 工程纪律（6 个）

> 这一摞都是**很短的流程约束**（每个 SKILL.md 正文只有 460–880 字符），没有脚本，作用是给 AI 划"什么时候该停手"。每个都带一份 `agents/openai.yaml`（给 Codex 类宿主的展示名/默认提示词）。

#### `investigate-first` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：**先查清原因再改代码**——原因不明的故障、偶发问题、性能回退，都先拿证据。
- **什么时候用**：不知道为啥坏的、时好时坏的、性能突然变差的；任何"先别急着改"的排查场景。
- **能干什么**：
  - 把**观察到的症状**和**推断的原因**分开写，不许混。
  - 顺着输入、状态迁移、归属边界、失败输出四条线追。
  - 假设按"证据强度 + 验证成本低"排序，优先证伪便宜的。
  - 在有一个可信机制能解释全部证据之前**不许编辑**；证据够了就停，不许无限探索。
  - 只报"原因 + 证据"，任务没授权实现就**不给修复方案**。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：`agents/openai.yaml`。

#### `lean-build` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：做新功能时**只做那条又窄又完整的路**——不许顺手加模式、加配置、加扩展点。
- **什么时候用**：新行为、产品切片、集成类需求，也就是"最容易顺手做多"的场合。
- **能干什么**：
  - 先从需求和现有仓库推出**可观察的验收条件**和**明确的非目标**。
  - 追入口点到各层，把该改的层都改到——**不许为了少改文件把活硬塞进一个文件或一个局部补丁**（这是它和"瞎简化"的分界线）。
  - 复用现成的接缝；只有当就地打补丁会重复逻辑、削弱归属或掩盖根因时才重构。
  - 模式、provider、配置、可扩展性、打磨——验收不需要就都不做；要加新接口/依赖/服务/配置/迁移，必须说明生命周期理由和实际取舍。
  - 始终保持可运行，跑通路径、跑聚焦验证，**验收过了就停**，只报实质性的省略和触发条件。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：`agents/openai.yaml`。

#### `migration` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：做**能回滚、兼容期安全**的迁移——schema、数据、API、协议、配置、依赖都算。
- **什么时候用**：要动数据库结构、搬数据、改接口、换协议、升级依赖，尤其"新旧版本会同时在跑"的时候。
- **能干什么**：
  - 动手前先摸清：谁在读、谁在写、数据长什么样、兼容窗口多长、归属是谁。
  - 前向路径和**回滚路径**都要先定义好。
  - 保数据：破坏性步骤必须**显式列出、单独授权**，不许夹在中间顺手做。
  - 混版本共存要安全；顺序按 expand → migrate → verify → contract。
  - 重试要幂等，部分失败要**可见**。
  - 每个要求过渡阶段都验旧路和新路；**做完要求的阶段就停**，不许自己往下做破坏性收尾。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：`agents/openai.yaml`。

#### `safe-refactor` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：**只改结构、不改行为**，而且改之前先把"验证"架好。
- **什么时候用**：抽方法、合并重复、搬归属、清理代码，也就是重构类任务。
- **能干什么**：
  - 先划清"行为不变"的边界，并在动手**之前**把验证手段立起来（同一条证明，改前改后各跑一次）。
  - 功能改动必须留在重构之外——不许"顺手修一下"。
  - 一次只搬一个归属边界；公共接口、失败行为、顺序、兼容性默认保持不变，除非明确划进范围。
  - 中间状态必须仍可构建、可测试（不许出现"重构到一半编译不过"的长分支）。
  - 没有正确性需要，就不加依赖、不加配置。
  - 行为对上了、结构也到位了就停。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：`agents/openai.yaml`。

#### `surgical-patch` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：修 bug 只改**该负责的那一层**，别顺手清理、改名、抽抽象。
- **什么时候用**：修 bug、小行为变更，尤其需要"回归证明 + 周边行为不受影响"的时候。
- **能干什么**：
  - 能经济地复现就先复现，不能就先抓最强的现有证据。
  - 从症状追到真正负责的机制，然后**改最窄的那一层**。
  - 保留无关行为，也保留用户自己的改动（别把别人的活覆盖掉）。
  - 修复范围之外不做清理、不改名、不抽抽象。
  - 只补和这个任务相关的回归证明，跑聚焦验证 + 最近的受影响门禁；修好了、回归过了就停。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：`agents/openai.yaml`。

#### `verify-and-stop` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：**只做验证、不扩范围**——把验收条件翻译成最小够用的证明集，跑完立刻停。
- **什么时候用**：只要验证不要改的活；完工检查；跑某个门禁；最后一步"证明给我看"。
- **能干什么**：
  - 把验收条件换成**最小充分**的证明集，别一上来跑全量。
  - 仓库状态没变的话，可以复用仍然有效的结果。
  - 先跑聚焦检查，再往更宽的门禁扩。
  - 结论分四态、不许含糊：pass / fail / unavailable / blocked。
  - 验证请求里没带修复，就**不许改产品代码**；条件过了之后也不许再加打磨、清理、无关测试。
  - 只报命令、结果和未解决风险。
- **外部 Key / 依赖**：无需外部 Key。
- **自带资源**：`agents/openai.yaml`。
- **和 `investigate-first` 的区别**：investigate-first 解决"**为什么坏**"（原因未知），verify-and-stop 解决"**到底过没过**"（原因已知、要证据）。

---

### 四、Content & media · 自媒体内容（4 个）

> 这一摞是**行途技术公众号产线专用**，中文正文，绑了该号的内部规范编号（PUB-xxx / BCI B-xxx）。离开这个语境用，价值会打折。

#### `adversarial-review` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：四个角色从对立视角**同时**审一篇文章/封面/标题/方案，再用**脚本**（不是第五个 LLM）交叉验证，只留高置信度的发现。
- **什么时候用**：公众号封面/标题/正文/数据/方案定稿前；说"对抗审查""多视角审查""审文章""审封面""数据核查"。
- **能干什么**：
  - 四角色分工明确：**怀疑论者**（论证站不站得住、数字有没有来源、漏了什么反例）、**优化者**（还能怎么更好、更省 token 的写法）、**审计员**（封面数字=正文数字吗、来源真存在吗、口径一致吗）、**过滤器**（只做减法，拦掉低质量发现，目标拦 30-50%）。
  - 两轮：Round 1 三到四角色并行独立出发现（每角色上限 7 条）；Round 2 用脚本去重 + 分级（≥2 个角色独立报同一件事 = ✅ 交叉验证，单个角色 = 🔹 单独），再由过滤器筛。
  - 三档深度：轻量（单封面/标题，3 角色，~10K token）/ 标准（文章正文/数据，4 角色，~25K）/ 重量（系列、付费方案，4 角色 + 领域专家，~50K）。
  - 定位规则治"AI 说的行号对不上"：文章按章节/字段定位，封面按数字 + 文案字段定位，不写"第 3 行"。
  - 结果默认写回被审文件的"审查记录"章节，不另建报告。
- **外部 Key / 依赖**：无需外部 Key。但**流程里点名的 `check_cover_consistency.py`（封面/正文数字一致性校验）不在本插件包内**，属于行途产线脚本，装了这个插件也跑不了，得自备；数据库核实部分依赖 `ai-usage-report` 的 cc-switch 库。
- **自带资源**：无（纯 SKILL.md，正文 5 千多字，含四角色定义、两轮流程、报告模板、七项自检清单）。

#### `ai-usage-report` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：**核实文章里的每个 token/成本数字**，外加把内部 AI 平台的用量拉成可汇报的报表。
- **什么时候用**：文章里出现"缓存命中率 97.7%""缓存读 36.8 倍""30 天省 8880 万"这类数字要被查;要做内部 AI 平台用量汇报、模型对比、模型切换观察、TTFT/卡顿排查。
- **能干什么**：
  - 第一块（核实）：直连本机 `~/.cc-switch/cc-switch.db`（SQLite，约 17MB，**60 天滚动窗口**）跑 SQL，算全周期缓存命中率（`cache_read/(input+cache_read)`）、缓存读倍率、按模型汇总成本/请求/token、某日某模型明细。
  - 核实流程五步：定位原文数字 + 声称口径 → 查本地实测 → **口径对比**（同口径标"✅ 已核实"，不同口径必须说清差异来源，不许混用）→ 脱敏检查（公开发布只留量级，金额不公开）→ 写回文章标注。
  - 第一块的铁律：超过 60 天窗口的历史数字要用别处的数据补；平台显示的命中率口径可能不同，先确认口径；"省下的"（rtk gain）和"实际花的"（cc-switch）是两个维度不许互比。
  - 第二块（汇报）：`scripts/ai_usage_pull.sh` 一键拉全维度（summary/users/models/projects/趋势），`ai_usage_stats.sh` 拉单接口，`ai_usage_db.sh` 直查 users/logs 表；再算次均费用、每请求 token、token 工时密度、帕累托集中度、效率分层，输出两套 HTML（阶段对比版 1200px + 同事速览版 780px）。
- **外部 Key / 依赖**：**要两样**：① 本机 cc-switch 数据库（`~/.cc-switch/cc-switch.db`）+ `sqlite3` 命令；② 内部 AI 平台的认证（`AI_PLATFORM_ADMIN_TOKEN` + `AI_PLATFORM_ADMIN_USER`，或 `AI_PLATFORM_TOKEN`+`AI_PLATFORM_USER`，或 `AI_PLATFORM_COOKIE`，存在 `settings.local.json` 的 env 里）。**脚本里的 `BASE` 是占位域名 `your-内部 AI 平台.example.com`，必须自己改**，否则第二块跑不通。
- **自带资源**：`scripts/ai_usage_pull.sh`（一键拉取）、`scripts/ai_usage_stats.sh`（API 封装）、`scripts/ai_usage_db.sh`（库直查）、`scripts/gz_roster.example.txt`（人员映射**脱敏样例**，真实花名册是公司内部数据，禁止提交到公开仓库）。

#### `de-ai-flavor` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：把"赋能抓手闭环链路"改成**人话**，技术准确性和关键数字一个不丢。
- **什么时候用**：公众号文章/封面/标题/摘要/小红书/微信消息读起来"太 AI""黑话太多""不像人写的"；说"去 AI 味""润色""改自然点"。
- **能干什么**：
  - 五条专属方法（比通用替换表更根本）：① 抽象 → 具体动作 + 工具名（"提升研发效率" → "20 次 grep 变 3 次图查询"）② 留真实代价（保留"贵、慢、翻车"，不粉饰）③ 给可查证数字（官方口径 / 实测口径分开标）④ 抽象对立 → 时间差（"以前全量 grep 2 分钟，现在 3 次图查询"）⑤ 愿景 → 可兑现承诺（读者能直接抄的步骤）。
  - 一张 15 行的**黑话替换表**：赋能/助力→帮；抓手→切入点；闭环→跑通；链路→流程；颗粒度→细一点；对齐→确认；沉淀→记下来；降本增效→直接给数字；打造→做；落地→用起来；多维度/全方位/值得一提/综上所述→**删掉**。
  - 六条句式精简（去壳留核、去状语堆叠、去名词化动词、一句一事实、数字说话、删了会漏信息吗）。
  - **保留清单**（不能动）：技术术语/产品名、数字/时间/人名、项目名/命令、客户原话，以及**"思想定调锚点"金句**（如"不是模型变傻，是输出没人压"）——这算品牌印记，不算 AI 味。
  - 分场景给力度：封面标题最强（≤13 字 + 核心词前置）、封面副标强、正文中、摘要/转发语最强、文末引导中。
- **外部 Key / 依赖**：无需外部 Key。数字存疑时它会把你转给 `ai-usage-report` 核实（那个要 key）。
- **自带资源**：无（纯 SKILL.md）。

#### `fact-check-gate` <sub>xingtu1996/dsh-xingtu-skills</sub>

- **一句话**：成稿前的**事实核查门禁**——把说法拆成"事实 / 推论 / 价值判断"，联网核事实、再查推理链，告诉你"能信到什么程度"。
- **什么时候用**：文章/观点/数据/方案定稿前；说"事实核查""这个说法靠谱吗""数字对不对""防编造""这机制是真的吗"。
- **能干什么**：
  - 五步流程：**拆层**（可验证事实 / 从事实推出的结论 / 价值判断）→ **联网核事实**并打五档标记 → **查推理链** → 出结论 → 落地处置。
  - 五档标记：① 已证实（保留 + 标来源日期）② 基本成立需收窄（限定条件后保留）③ 存在争议（并列呈现，不选边）④ 证据不足（明写"暂未核实"，绝不硬写）⑤ 明显错误（删或改，标注错在哪）。
  - 推理链四个必查：这些事实推不推得出这个结论、藏了什么没验证的假设、有没有把相关当因果、有没有漏掉其他解释。
  - 行途特化规则：**第三方产品机制必须回一手源**（GitHub API / 官方 README / 官方文档），二手视频转述不算机制依据，找不到官方说明就不写机制（治"凭印象硬编别人家架构"）；数字的官方口径和实测口径分开标；批量成稿要**每篇独立核查**，不许"一篇查过=整批可信"。
- **外部 Key / 依赖**：无需 API Key，但**必须能联网**（Step 2 要求联网核查来源、样本、时间、完整上下文）；无网就退化成"全部标④证据不足"，失去意义。
- **自带资源**：无（纯 SKILL.md）。

---

### 附：几句实话（装之前该知道的）

1. **两个系列都靠"模式"生效，但都不是 DSH 原生机制**。`caveman` / `ponytail` 的"持续生效"是靠 SKILL.md 正文里写死"每个回复都照做"来约束模型，不是宿主强制的开关；换模型/长会话后可能漂移，SKILL.md 里也承认这一点。
2. **好几个技能是"给 Claude Code / Codex 写的"**：`caveman-stats`（要 hooks）、`caveman-explore`（`tools:`/`model:` 字段）、`cavecrew`（要子 agent 预置）、Ponytail 的 `@` 调用形式。在 DSH 里能加载，但功能不一定齐。
3. **依赖 Caveman 产品的那 6 件（setup / discover / learn / evidence-review / manage / optimize）是一条链**，共同前提是"Caveman 账号 + 网关地址 + Cave API Key 或已登录的 `caveman` CLI"。只看 SKILL.md 会觉得都能跑，实际没有账号一步都动不了——`caveman-setup` 甚至会把"少一个参数就停下来问"写成硬规则。
4. **内容四件绑定行途技术产线**：`check_cover_consistency.py`、`~/.cc-switch/cc-switch.db`、内部 AI 平台域名、PUB/BCI 规范编号——都是该号内部资产，外部用户拿到的是"方法论"而不是"能直接跑的工具"。
5. **没有重型资产**：30 个目录里 14 个**只有一个 SKILL.md**（纯提示词），其余 16 个多出来的文件也基本是一张 README 或一份 `agents/openai.yaml`；真正带可执行代码的只有两个——`caveman-compress`（`scripts/` 下 7 个 Python 文件，其中 5 个是实际逻辑）和 `ai-usage-report`（3 个 shell 脚本 + 1 份脱敏样例）。


---

## 6. bruc3van/awesome-dsh-plugin — 插件索引，不是技能库（0 个）

> 核实时间：2026-10-28｜核实方式：直接读仓库 `/tmp/repos/bruc3van_awesome-dsh-plugin/` 的 `README.md`、`CATALOG.md`、`catalog/*.md`（13 册全查）、`data/featured.json`、`data/stats.json`、`data/packages.json`、`data/market-v2.json`、`data/dsh-market-index.json`、`data/review/README.md`
> 快照日期：**2026-10-27 UTC**｜最近人工复核合并：**2026-10-28**

---

### 〇、一句话结论（先说最重要的）

**这个仓库不是技能库，一个 SKILL.md 都没有。**

实测：`find . -iname 'SKILL.md' | wc -l` → **0**。

它是 **DSH 插件的索引 / 订阅源**——把 GitHub `dsh-plugin` Topic 下的插件仓库收进来、核实、分类、做成可订阅的 JSON，再配一套人读的 Markdown 目录。技能（SKILL.md）是"提示词/流程"，插件（plugin）是"装进 DSH 的代码"；这个仓库只管**后者**，而且是**目录式管理**，不是内容本体。

所以：**如果你要的是"技能清单"，它满足不了**——详见第五节，那里给了替代路线。

| 你可能以为 | 实际是 |
|---|---|
| 一个技能集合，装了就有 30 个技能 | ❌ 一个索引仓库，装不了，也没有技能 |
| 一个 `npx` 就能装的东西 | ❌ 主体是数据 + Markdown；终端安装是逐条装插件 |
| 收 2 万多个"官方认证插件" | ❌ 是"带 Topic + 有简介 + 人工核实过存在性"的**索引**，明确声明不是安全/兼容性背书 |
| 一个插件市场（能浏览能点安装） | ⭕ 它**提供**市场数据；市场界面由下游插件/桌面端实现 |

---

### 一、它到底是什么

##### 1.1 准确定性

一个 **awesome-list 形态的 DSH 插件索引 + 机器可读订阅源**，由个人维护者（bruc3van）维护，MIT 许可。

它同时是三种东西：

1. **人读目录**：`README.md`（418 行首页，含场景化精选推荐）+ `CATALOG.md`（类目索引）+ `catalog/*.md`（13 册完整名单）+ `TOP200.md`（Star 榜）+ `SHOWCASE.md`（作者自荐）+ `MARKET.md`（下游市场文件的可读镜像）。
2. **机器可读数据**：`data/repositories.json`（23,581 个 Topic 仓库的原始快照，19.9 MB）、`data/approved.json`（20,671 条核实通过记录）、`data/curated.json`（剔除/排除裁决）、`data/packages.json`（1,073 条**安装目标映射**）、`data/market.json` / `market-v2.json`（各 1,000 条下游市场 feed）、`data/dsh-market-index.json`（官方 format-1 订阅索引，1,000 条）。
3. **订阅源**：一个固定的 HTTPS JSON 地址，DSH 官方「发现」页可以直接订阅。

##### 1.2 数据从哪来（管道长什么样）

```
GitHub topic:dsh-plugin（23,581 个仓库）
        ↓ 每日抓取 scripts/update.mjs（只抓原始快照 + 待审队列）
data/repositories.json + data/review/pending.md
        ↓ 人工/AI 核实（先看"Star 异常增长"告警节）
data/approved.json（通过，20,671 条） + data/curated.json（剔除 1,409 / 不进榜 771 / 不进市场 735）
        ↓ scripts/merge.mjs（只在审核合并时跑，不自动）
CATALOG.md + catalog/*.md（13 册，合计 20,667 条） + TOP200.md + README 数据区
        ↓ scripts/market.mjs / market-v2.mjs / host-market 生成器
data/market.json / market-v2.json（同 1,000 个仓库）+ dsh-market-index.json（另 1,000 个插件包）
        ↓ 发布
bruc3van.com/dsh-plugins/index.json（订阅）+ npm 包 awesome-dsh-plugin-feed（国内镜像通道）
```

几个关键事实，能看出它的边界：

- **快照 23,581 个仓库 → 目录 20,667 条**：中间剔了 908 个"蹭 Topic 的非插件仓库"（平台本体、其他 Agent 工具、同名目录站）+ 编辑部拉黑的仓库。
- **只有 1,073 个仓库有"安装目标映射"**（`data/packages.json`），即能给出确切 npm 包名或 Git 地址。订阅索引只收其中**能转换成安装目标**的：合格 1,024 个插件，取前 1,000（砍掉 24 个，入选线是 5 颗星）。
- **两个"1,000"不是同一批**（这点特别容易搞错，我实测过）：
  - `data/market.json` / `market-v2.json`：**1,000 个仓库**，按类目**均衡发牌**——10 个类目各 **100** 条，Star 从 7,003 到 **2**（因为均衡发牌，低星也进得来）。
  - `data/dsh-market-index.json`（订阅用）：**1,000 个插件包**，来自 **779 个仓库**（有 12 个仓库贡献多包，其中一个仓库挂了 9 个包），按**仓库 Star 降序**选，类目分布很不均（agents-workflows 290、ui-experience 213、web-browser 128……multi-agents 只有 28），Star 下限 5。
  - 两者仓库交集只有 **460 个**：索引独有 319 个仓库，市场独有 540 个。
- **用户可见页面不会自动更新**：脚本只抓数据，`CATALOG.md` / `catalog/*.md` / `TOP200.md` 只在人工核实合并后才重新生成——所以"新插件不会第二天就出现在目录里"是设计如此，不是 bug。

##### 1.3 它明确声明自己不是什么（README 原文口径）

- 不是人工推荐：`CATALOG.md` 原话"本页是索引，不代表人工推荐、兼容性验证或安全背书"。
- 不做安全审查：README 使用与安全节说"本列表仅做发现与整理，不代表 DSH 官方认可，收录也不构成安全或兼容性背书"。
- 不是 DSH 官方项目：官方上游是 `deepseek-ai/deepseek-harness`，这个仓库是社区索引。
- 首页精选推荐"不按星数排名"，但"绝大多数推荐来自 Star Top 200"，少数是几十 Star 但无可替代的项目。

---

### 二、13 个类目与各自条目数（照抄 CATALOG.md 表格）

`CATALOG.md` 的「分册 / Volumes」表共 **13 行**，合计 **20,667** 条（我逐册数了表格行，与表头数字完全一致，没有对不上的）。

| # | 类目（中 / 英） | 条目数 | 分册文件 |
|---:|---|---:|---|
| 1 | 生态与资源 / Ecosystem & Resources | **558** | `catalog/ecosystem-resources.md` |
| 2 | 界面与体验（A–L）/ UI & Experience (A–L) | **2,228** | `catalog/ui-experience.md` |
| 3 | 界面与体验（M–Z）/ UI & Experience (M–Z) | **2,117** | `catalog/ui-experience-m-z.md` |
| 4 | 设计、媒体与视觉 / Design, Media & Vision | **1,010** | `catalog/media-vision.md` |
| 5 | 网页与浏览器 / Web & Browser | **3,041** | `catalog/web-browser.md` |
| 6 | 集成与分享 / Integrations & Sharing | **883** | `catalog/integrations-sharing.md` |
| 7 | 知识与研究 / Knowledge & Research | **888** | `catalog/knowledge-research.md` |
| 8 | 开发者工具 / Developer Tools | **944** | `catalog/developer-tools.md` |
| 9 | 多 Agent 编排与协作 / Multi-Agent Orchestration & Teams | **390** | `catalog/multi-agents.md` |
| 10 | Agent、自动化与工作流（A–F）/ Agents, Automation & Workflows (A–F) | **1,824** | `catalog/agents-workflows.md` |
| 11 | Agent、自动化与工作流（G–M）/ Agents, Automation & Workflows (G–M) | **1,868** | `catalog/agents-workflows-g-m.md` |
| 12 | Agent、自动化与工作流（N–Z）/ Agents, Automation & Workflows (N–Z) | **3,742** | `catalog/agents-workflows-n-z.md` |
| 13 | 实用工具与其他 / Utilities & Other | **1,174** | `catalog/utilities.md` |
| | **合计** | **20,667** | 13 册 |

##### 为什么是 13 册而不是 10 个类目

- **语义上只有 10 个类目**：界面与体验、Agent/自动化/工作流 各被拆成 2 册和 3 册，所以 10 + 3 = 13 册。
- **拆分原因**：单页 Markdown 曾经到 2.14 MB，**超过 GitHub 1 MB 的渲染上限**，只能分册。分册按 owner 名首字母切（`ui-experience` A–L / M–Z；`agents-workflows` A–F / G–M / N–Z），**类目粒度不变**——市场发牌和 README 思维导图仍按 10 个类目统计。
- 把三册合并后，真实类目大小是：**Agent、自动化与工作流 7,434**（最大）、**界面与体验 4,345**、网页与浏览器 3,041、实用工具 1,174、设计媒体视觉 1,010、开发者工具 944、知识与研究 888、集成与分享 883、生态与资源 558、多 Agent 编排 390。

##### 全仓统计（`data/stats.json`）

| 指标 | 值 |
|---|---:|
| 快照日期 | 2026-10-27 |
| 收录仓库 | **20,667** |
| 主要语言 | 39 种 |
| 声明许可证 | 18,857 |
| 未归档且未禁用 | 20,424 |
| 分册数 | 13 |
| 累计剔除（蹭 Topic + 拉黑） | 908 |
| 不进榜单（保留在目录） | 771 |
| 不进下游市场 | 735 |

---

### 三、怎么用

##### 3.1 订阅到 DSH 官方「发现」页（推荐，只读浏览 + 点装）

**版本要求：DSH V0.2.1-rc.2 及以上。**

订阅地址（两个地址返回同一份 JSON，无重定向）：

```
https://bruc3van.com/dsh-plugins/index.json
```

旧地址仍兼容：`https://bruc3van.com/markets/dsh/index.json`

操作五步（README 原流程）：

1. **确认版本**：把 DSH 升到 V0.2.1-rc.2 或更高。
2. **开启自定义市场**：左侧「插件」→「实验性插件」旁的「更多」→ 打开「自定义插件市场」。开启后插件页会出现「已安装 / 发现」两个标签页。
3. **打开索引源配置**：进「发现」→ 首次点「添加索引源」；已有市场时，展开左上角市场名下拉菜单 →「管理索引源…」。
4. **添加订阅**：在「索引 URL」粘上 `index.json` 地址 → 「添加」→ 确认出现在列表 → 关闭弹窗（配置存在当前 profile 里）。
5. **浏览安装**：市场下拉菜单选「**Bruce 的精选插件**」→ 需要时点「刷新」。可按分类（含"编辑推荐"）、关键词、npm/Git 来源、近期更新、Star 标签筛选，看清版本要求再装。

常见卡点（README 专门写了）：
- 没有「发现」页 → 版本不够，或「自定义插件市场」没开。
- 开了功能不等于加好了订阅源 → 仍要执行第 3–4 步。
- **只订阅官方发现页不需要装 `safer-dsh-market`**。那个是另一条路（独立市场界面、多选安装、安装后 AI 审查），和「自定义插件市场」开关是两个不同入口。

订阅索引的实际内容规模：**1,000 个插件**（来自 **779 个仓库**——12 个仓库贡献了多个包；合格插件 1,024 个，按 Star 降序取前 1,000，砍掉 24 个，入选线 5 星），11 个分类槽位（10 个真实类目 + 一个「编辑推荐」），52 个标签（来源 npm/Git、Star 档位 50+/100+/500+/1k+/5k+/10k+、更新时间档「近 7 天 / 近 30 天」、用途标签）。分类分布很不均匀：agents-workflows 290、ui-experience 213、web-browser 128、utilities 67、media-vision 61、developer-tools 53、integrations-sharing 53、knowledge-research 46、ecosystem-resources 43、multi-agents 28、featured 18。索引按官方 `@deepseek-ai/dsh-plugin-market-index@0.2.1-alpha.5` 校验通过才发布。

##### 3.2 终端等价操作（不订阅，逐个装）

订阅页点"安装"背后执行的就是这条命令。仓库里 `data/packages.json` 给每个可映射仓库存了现成的 `command` 字段，语法是：

```bash
npx @deepseek-ai/dsh plugin --profile <web|desktop> add <install>
```

`<install>` 的三种形态（都能直接粘进官方「添加插件」输入框）：

| 形态 | 例子 |
|---|---|
| npm 包名（可带版本） | `dsh plugin --profile web add dsh-ui-auth@0.7.1` |
| Git 仓库地址 | `dsh plugin --profile web add github:02Muller25/dsh-api-balance` |
| 本地路径 | `npx -p @deepseek-ai/dsh dsh plugin --profile web add <path>` |

注意 `install` 字段和 `command` 字段的区别：`install` 是**粘进 GUI 输入框**的准确串（`github:` 语法要转成 `https://github.com/owner/repo`），`command` 是**终端完整命令**（保留 `github:` 前缀）。两者不可混用。

查某个插件的准确安装名，三个地方任选：

```bash
# 1. 仓库自己的映射表（1,073 条，最准）
jq -r '.entries["owner/repo"].packages[0].command' data/packages.json

# 2. 市场 feed v2（1,000 条，带 packages 块）
jq -r '.entries[] | select(.full_name=="owner/repo") | .packages.targets[0].command' data/market-v2.json

# 3. 订阅索引（已格式化，含 requirements）
jq -r '.plugins[] | select(.name=="包名") | .versions[0]' data/dsh-market-index.json
```

##### 3.3 其他数据消费方式

| 用途 | 文件 / 地址 | 规模 |
|---|---|---|
| 程序化拿插件元数据（星数/许可证/活跃度） | `data/repositories.json` | 23,581 条原始快照，19.9 MB |
| 下游市场吃的小文件 | `data/market.json`（v1）/ `data/market-v2.json`（v2 超集，带安装命令） | 各 1,000 条，≤1 MB |
| 国内直连（GitHub raw 不通时） | `https://registry.npmmirror.com/awesome-dsh-plugin-feed/latest/files/data/market-v2.json`（302，需跟随） | npm 包 `awesome-dsh-plugin-feed` |
| 生成/排除报告（可追溯每个决定） | `https://bruc3van.com/markets/dsh/audit.json` + 仓库内 `data/dsh-market-index.report.json` | 逐条诊断码 |
| 人读排行 | `TOP200.md`（Star Top 200）、`MARKET.md`（市场文件镜像） | — |

> ⚠️ `audit.json` 是"生成与排除报告"，**不是安全审计认证**——README 明确说"报告不是 DSH 官方审计认证，不代表安全审查或实机兼容验收"。

---

### 四、`featured.json` 里的编辑精选

##### 4.1 数量

**20 个**（`data/featured.json` → `entries` 数组长度 = 20；`title_zh: "编辑精选"`，`updated_at: "2026-10-01"`）。

补充三个实测细节，说明"精选名单"和"订阅里能点到"不是一回事：

- 这 20 条在 `data/market-v2.json` 的 `featured` 字段里**完整同步 20 条**（顺序一致，`featured_count: 20`）。
- 但订阅索引 `data/dsh-market-index.json` 的「featured」分类里只有 **18 条**。缺的两条**各有具体原因**，不是随机漏掉：
  - `dream-num/dsh-univer-office`（真 Office）→ 打包体积超限：report 里 `package_size_limit`，"unpacked tarball exceeds 67108864 bytes"（>64 MiB）。
  - `huiliyi37/dsh-tianshu-tui`（终端 TUI）→ **没有 `dsh plugin add` 命令**，README 改成一键 `curl` 引导脚本（`scripts/install-tui.sh`），`packages.json` 里 `install_mode: manual`、`packages: []`，转换不出安装目标。
- **20 条全部有安装映射**（我逐条核对了 `data/packages.json`，键是小写存储的，容易看漏）。例外是 `bruc3van/bruce-md2word`：它有映射、挂在 `featured` 里，但**不在 1,000 条 entries 里**（它是 featured-only 条目）。其余 19 条都在 entries 中且带 `packages` 块。

> 一句话：**精选举 20 个，订阅里能一键装的 18 个**（2 个体积/安装方式不合规），另 1 个只是精选位展示。

##### 4.2 举 10 个例子（按它自己写的中文简介）

| # | 插件 | 它解决什么 |
|---:|---|---|
| 1 | `omdsh-dev/DSH-better-sidebar` | **开放的侧边栏底座**：支持第三方扩展注册页面，内置文件预览与编辑、终端、侧边对话、Git 和子代理页面。把侧边栏变成工作台。 |
| 2 | `bowenliang123/dsh-context` | **上下文可视化**：仪表盘/浏览器/侧边栏/命令，看上下文统计、组成、明细和变化过程——想知道"我的 token 被什么吃了"就装这个。 |
| 3 | `xmanrui/dsh-im` | **IM 接入**：扫码或机器人凭据，把飞书、微信、钉钉、企业微信、QQ、Slack、Telegram、Discord、WhatsApp 接进 DSH。 |
| 4 | `shaobeichen/dsh-pocket` | **手机遥控**：电脑上跑 DSH Web，手机扫码同屏访问，支持局域网、公网和实时同屏。 |
| 5 | `liustack/modsearch` | **免 Key 联网搜索**：给缺原生联网能力的模型补网页与 X 搜索，支持抓取、引用、结构化 JSON 结果，**无需注册或 API 密钥**。 |
| 6 | `NanmiCoder/dsh-agent-teams` | **多智能体团队协作**：当前会话当队长，创建可续聊的子 Agent、把目标拆成带依赖的任务、用直达消息协调。 |
| 7 | `omdsh-dev/dsh-genui` | **回复内交互界面**：在助手回复里直接渲染布局、图表、表单、测验、Mermaid、三维场景，并把用户操作回传给模型。 |
| 8 | `lire1131/dsh-undo-savepoint` | **装坏了能撤回**：撤销配置和插件代码变更、创建保护敏感信息的快照、一键进安全模式；DSH 起不来时还有离线 CLI/GUI 恢复。 |
| 9 | `bruc3van/bruce-md2word` | **Markdown 转 Word**：中文排版、Mermaid 转图片、LaTeX 转可编辑的 Word 原生公式。**这是维护者自己的项目**（README 里如实标注"本列表作者开发"）。 |
| 10 | `dream-num/dsh-univer-office` | **真 Office 环境**：Univer 官方厂商出品，表格/文档/幻灯片/画布/关系表进对话，数据联动、版本管理、多智能体隔离工作区。 |

（给了 10 个；共同特点：**都是"解决一个具体问题"的工具型插件**，没有皮肤/桌宠那类娱乐项——那是首页精选推荐区才有的内容，不在 featured.json 里。）

##### 4.3 精选的局限

- `updated_at` 是 **2026-10-01**，比全仓快照（10-27）旧了近一个月——**这份名单不是每天跟着数据刷的**，是人工维护的。
- README 自己说精选"优先选择社区验证的高星项目""绝大多数来自 Star Top 200"，但"不按星数排名"。
- README 首页的「精选推荐」是**另一个东西**：按场景分组（桌面与终端 / 界面与工作台 / 让模型看得见搜得到 / 记忆与无人值守 / …），规模比 20 大得多，还带截图。`featured.json` 只是其中被单独结构化出来的 20 条。

---

### 五、能不能满足"我要技能清单"

##### 5.1 直接回答：**不能，它解决的是另一个问题**

| 你的需求 | 它的能力 | 结论 |
|---|---|---|
| 给我 30/100 个 SKILL.md 技能 | 0 个 SKILL.md；收的是插件仓库 | ❌ 完全不对口 |
| 告诉我哪些技能能装 | 收"插件"，其中**有一小部分是技能类插件**（能注册 skill 的插件，如 `dsh-skill-studio`、`dsh-find-plugins`、`tabbit-browser` 自带技能） | ⚠️ 只能间接找到 |
| 帮我装插件、看插件热度 | 强项：20,667 条目录 + 1,000 个可装插件（订阅索引）+ Star 榜 + 类目 | ✅ 很好用 |
| 告诉我插件安不安全 | 明确不做 | ❌ 别指望 |

**根本区别**：技能 = 一段可复用的提示词/流程（`SKILL.md`，DSH 里由 skills 服务按需加载）；插件 = 装进 DSH 的代码包（可以顺带注册技能，也可以只改 UI）。这个仓库索引的是后者。

实测佐证：全仓 `SKILL.md` 数量 0；13 册目录里提到 "skill" 这个词的条目**总共 1,253 行**（粗筛，含 "skilled/skillful" 之类噪音，去重后更少），其中多数是"插件里附带一个技能"或"技能管理器"这类，**没有一条是纯粹的技能内容仓库**。而真正想要"技能清单"应该去找技能仓库本身（比如同样带 `SKILL.md` 的那些），这个索引帮不上。

##### 5.2 那"我要技能清单"该怎么用这个仓库

三条现实路线，按推荐度排：

**路线 A（最推荐）：别用它找技能，去找技能源仓库**
技能类内容通常在专门的 skills 仓库里（一堆 `skills/<name>/SKILL.md`），而不是插件索引。这个仓库唯一的相关价值是「**生态与资源**」分册（558 条）——里面收的是插件市场、注册表、开发模板、脚手架、教程手册，**技能管理类插件基本都在这一册**。我实测捞出来的几个（都是"装完之后你在 DSH 里就能浏览/启停技能"的工具）：

| 插件 | 干什么 | 在哪一册 |
|---|---|---|
| `cheshireez/dsh-skill-hub` | Web GUI 技能中枢：浏览/搜索本地技能目录、启用/禁用、看正文、诊断、新建技能（基于官方 `ctx.skills` 注册表） | 生态与资源 |
| `aa2246740/dsh-skillhub` | 管理已存在 Agent home / DSH home 里的用户技能 | 生态与资源 |
| `2026heshao/dsh-armory` | 一体化看板：MCP + 技能 + 专家 + 三大市场直连（含 SkillHub 19 万+ 技能） | 生态与资源 |
| `Imzl-zl/dsh-toolbox-ui` | 工具箱面板：MCP 管理 + 技能管理（作用域/覆盖、启停、模型自动调用、skillhub.cn 市场） | 生态与资源 |
| `Nagi-ovo/dsh-find-plugins` | 一个 **Skill**：让 DSH 自己搜索、安装、验证 GitHub 插件 | 生态与资源 |
| `PopCapEA/dsh-skill-studio` | 统一浏览/编辑/启停 Codex、DSH、`.agents` 各处的 SKILL.md，会话里 `$skill-name` 注入 | Agent 工作流（N–Z） |

> 注意最后一行：**技能工作室不全在「生态与资源」里**——`dsh-skill-studio` 有两个同名不同 owner 的仓库，分别落在「Agent、自动化与工作流（N–Z）」和「设计、媒体与视觉」两册。所以别只翻一册就下结论，用下面的 grep 全库捞更稳。

**路线 B：用它找"能注册技能"的插件**
```bash
# 在 13 册里捞提到 skill 的插件（粗筛，需人工过一遍）
grep -in "skill" catalog/*.md | grep -iv "skilled\|skillful" | head -50
```
命中的大致三类：① 技能管理器/工作室（浏览、编辑、启停 SKILL.md）② 自带技能的插件（如 `tabbit-browser` 自带 `tabbit-browser` 技能，装插件顺带得到技能）③ 技能商城/市场。**装 ① 和 ③ 才有"清单"体验**；②类只是"顺带"，给不了清单。

> 提醒：这条 grep 只能捞到**描述里写了 "skill" 的条目**——很多插件注册了技能但简介里没写这个词，会漏。想要完整清单，还是得回技能源仓库。

**路线 C：把它当"插件清单"用，别当"技能清单"**
如果其实你想要的是"DSH 能装哪些东西"，那它就是答案：订阅 `index.json`（1,000 个可装），或按 `CATALOG.md` 13 册翻（20,667 个）。这时候它非常好用——**问题只是"名字叫错了"，工具没错**。

##### 5.3 一句话给用户的话术

> 这个仓库是**插件索引**不是技能库（0 个 SKILL.md，20,667 条全是插件仓库）。你要技能清单，得去技能源仓库；但如果你想的是"DSH 生态里都有什么能装的"，那订阅它的 `index.json` 就对了，1,000 个能一键安装的插件（779 个仓库），按 10 个类目 + 编辑精选分类浏览。

---

### 六、核实过程中发现的几个"坑"（用之前该知道）

1. **别把三个 1,000 当成同一批**（最容易踩的坑）：`market.json` 与 `market-v2.json` 是同一批 1,000 个**仓库**（同序，v2 多带 `packages` 安装块）；`dsh-market-index.json` 是另一批 1,000 个**插件包**（来自 779 个仓库）。**两边仓库交集只有 460 个**——你在市场文件里看到的插件，有四成多不在订阅索引里，反之亦然。原因：市场文件按类目均衡发牌（10 类各 100，Star 低到 2 也能进），订阅索引按仓库 Star 降序取（Star 下限 5，类目分布极不均）。所以"订阅里没搜到"不代表"这个插件不存在"，要去 `CATALOG.md` 翻。
2. **20,667 ≠ 可安装数量**：只有 1,073 条有安装映射（其中 1,008 条给出可直接执行的命令，65 条是 `install_mode: manual` 只能手动按 README 装），订阅索引只发合格的前 1,000 个插件。**目录里约 95% 的条目点不了"一键安装"**（20,667 里只有 1,073 有映射，约 5.2%），得自己看 README。
3. **"核实"的含金量有限**：`approved.json` 的核实是"仓库公开、有 dsh-plugin Topic、有简介、确实是插件形态"；`packages.json` 的核实是"命令逐字取自上游 README"（`readme-verified`）或"README 声明源没上 npm，退回仓库地址"（`repo-fallback`，8 条）。**都不是实机兼容验收**，报告里反复强调这点。索引还会因为**打包体积超 64 MiB**（4 个）、**不是 DSH bundle**（10 个）、**包名重复**（99 个）等原因把插件刷掉。
4. **精选名单不跟数据刷新**：`featured.json` 的 `updated_at` 是 10-01，比快照旧 26 天；20 个精选里 2 个因为体积/安装方式进不了订阅索引（见 §4.1）。
5. **首页有大量手写内容，脚本不代写**：精选推荐、入门组合、最近加入、二级推荐表都是人工维护；自动生成的只有生态全景思维导图、热度榜 Top 50、自荐预览三个区块。想提改动得按 `CONTRIBUTING.md` 改 Markdown，不是提 PR 改数据。
6. **收录不需要提 PR，但核实要时间**：仓库带上 `dsh-plugin` Topic + 有简介，就会在每日刷新时进待审队列，人工核实后才进目录——**"我加了 Topic 怎么目录里没有"是正常的**。
7. **`TOP200.md` 和目录是两个池子**：771 个仓库"保留在目录但不参与榜单"（桌面客户端/壳、插件市场、星数来自通用产品而非 DSH 生态真实热度的）。所以"我在目录里但不在榜上"不代表被拉黑。
8. **许可**：仓库自身 MIT；收录的 20,667 条里 18,857 条声明了许可证，**1,810 条没声明**——用之前自己看。

