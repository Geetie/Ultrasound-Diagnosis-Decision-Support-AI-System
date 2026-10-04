# 超声诊疗决策支持系统 · 医生侧 UI 还原任务单

> 给实现 Agent 的任务单。**数据在 `design_data_for_agents/`，规则在 `../17-医生侧展示契约.md`**。
> 本任务单只管「怎么做」，不管「为什么」——「为什么」在 `../14-前端设计评审结论.md`。

---

## 任务总览

把画布「超声诊疗决策支持系统-医生侧工作区」的 **15 屏**按数据还原为前端页面。

**但先看清一件事**：15 屏里**只有 3 个是真页面**（工作列表 / 设置 / 新建检查会话），另外 **11 屏是同一个「检查工作区」的状态与主题变体**。所以真实工作量 ≈ **1 个工作区组件 + 3 个页面**，不是 15 个页面。

`_overview.json` 里每屏都标了 `kind` 与 `diff_from`（该屏基于哪一屏、只差什么）。

---

## 一、技术栈要求

- **框架**：React 18 + Vite（沿用仓库 `frontend/`）
- **UI 库**：Ant Design 5 —— 但见下方「颜色禁令」
- **样式**：Tailwind（现有代码已在用，如 `components/layout/Sidebar.tsx`）
- **状态**：Zustand（仓库已用，`src/stores/`）
- **影像**：cornerstone.js（已有 `components/dicom/DicomViewer`）

---

## 二、颜色体系（唯一来源：`../tokens/tokens.css`）

**不要去 design_data.json 里抄 0~1 浮点色值**。所有颜色都已在 token 里映射好：

| 用途 | token | dark | light |
|---|---|---|---|
| 页面背景 | `--color-bg` | `#1B1E23` | `#F7F8FA` |
| 卡片 / 面板 | `--color-surface` | `#24282F` | `#FFFFFF` |
| 条 / 侧栏 | `--color-surface-alt` | `#17191E` | `#EEF0F3` |
| 影像区底 | `--color-image-bg` | `#15171B` | `#15171B`（不随主题） |
| 正文 | `--color-text-primary` | `#E6E8EB` | `#1F2937` |
| 次要文字 | `--color-text-secondary` | `#A8AEB6` | `#5A6673` |
| **语义·看一眼** | `--color-attention` | `#8C99A6` | `#5A6673` |
| **语义·停** | `--color-semantic-stop` | `#9B2423` | `#9B2423` |
| 描边 | `--color-border` | `#3A3F47` | `#D8DDE3` |
| 主按钮底 / 字 | `--color-inverse` / `--color-on-inverse` | `#E6E8EB` / `#1B1E23` | `#1F2937` / `#FFFFFF` |

### ⛔ 颜色禁令（违反即返工）

1. **不得新增第三个语义色**。全稿语义只有两级：**「停」= `semantic-stop`** / **「看一眼」= `attention`** / **正常 = 不渲染任何颜色**。
2. **`antd` 的 `success`(绿) / `warning`(橙) 禁用**。它们会引入本设计已明确移除的颜色。`Alert` / `Tag` / `Badge` 一律用自定义样式，不要用 `type="warning"`。
3. **主题按设备级一次设置**：根节点加 `data-theme="light"` 即整站换肤。**不要按页面分别设默认主题**。

---

## 三、检查工作区骨架（唯一硬骨架，不得改动）

```
┌ 常驻条 40 ────────────────────────┐
├ 影像区 600（恒定，永不缩放）───────┤
├ 结论区 260（有确认动作区时 → 200）──┤
├ 确认动作区 60（仅有待确认项时渲染）─┤
└───────────────────────────────────┘
合计 = 900（1440 × 900）
```

`design_data.json` 里每个节点的 `region_heights` / `height_budget` 都按此给出。**影像区 600 在任何状态下都不变**，这是全稿最容易做错的一条。

---

## 四、设计数据使用方法

1. 读 `_overview.json` 确认本页 `node_id`、`kind`、`reference`、`diff_from`。
2. 读 `NN_页面名/design_data.json` —— 完整元素树（`data.nodes`，与 ardot 原始结构同型）。
   - 每个节点带 `token_hint`：告诉你怎么映射到 token（例如 `{"background":"color-bg","height":"size-bar-h"}`）。
   - 带 `cond`：该节点的**渲染条件**。
   - 带 `why`：这条设计为什么这么做。
   - 带 `annotation_note`：**该节点是评阅用标注，不要实现**。
   - 带 `missing_by_rule`：本应存在但按规则**故意不渲染**的元素。
3. 读 `NN_页面名/page_info.json` —— 目标文件建议、缺项清单、不要实现的清单。
4. 打开 `../参考图/` 对应 PNG 做视觉比对。

---

## 五、⛔ 最容易做错的一件事：「按规则不渲染」≠「设计漏画」

实现者看到"少了东西"就想补上，一补就把整套设计规则破坏了。以下是**故意的缺失**：

| 看起来"缺了" | 真相 |
|---|---|
| 常驻条里没有同意状态、连接状态 | **正确**。只在异常时渲染，正常态渲染＝违反「默认沉默」 |
| 结论卡里没有部位指示条 | **正确**。部位数 < 2 时不渲染（对照 05 / 14 屏） |
| 大部分屏都没有确认动作区 | **正确**。只有存在待确认项时才出现（对照 10 屏） |
| 07 报告面板顶部没有报告状态行 | **正确**。草稿态不渲染（对照 12 屏已签发态） |
| 全稿找不到绿色 | **正确**。"正常"不是绿色，是**什么都不显示** |
| 结论卡里没有置信度 / 一致性 / Self-RAG 明细 | **正确**。二级折叠，且后端字段尚未接通（见 `../16-设计依据代码核定报告.md` §N-01） |
| 有 4 个「区域编号」胶囊 | **那是给评阅人看的标注**，`annotation_note` 已标，**不要实现** |

**验收时必须能在 DevTools 里证明"该元素在 DOM 里不存在"** —— 不是 `display:none`，是不渲染。

---

## 六、四条硬规则（违反即返工）

1. **默认沉默** —— 不满足条件就不渲染（不是隐藏）。
2. **影像尺寸恒定** —— 影像区永远 600。
3. **语义色两级** —— 只有 `semantic-stop`（停，挡签发，配一次性声光）与 `attention`（看一眼，静态、不发声、不闪烁）。
4. **主题按设备级一次设置**；`--color-image-bg` 不参与主题切换。

---

## 七、验收要求（不是"看着像就算")

单一来源证据不足以判定还原达标。至少交叉比对三段：

1. **声明值**：tokens + `design_data.json` 的尺寸/间距。
2. **设计侧像素**：`../参考图/` 对应 PNG（2x）。
3. **代码侧渲染**：本地起服务截图，或读 computed style / `getBoundingClientRect()`。

**专项检查**：
- 量影像区 `getBoundingClientRect().height`，**必须 = 600**。
- 读 computed `background-color` / `color`，与 token **逐位对齐**（不要目测）。
- 条件渲染 → 查 DOM 是否存在（见第五节）。
- 文本 → 对照参考图检查换行与截断。

---

## 八、数据导出进度：**15 / 15 完成** ✅

**节点数据没有服务端导出接口**，每屏的完整元素树需逐屏灌入。现已**全部完成**：

| 屏 | 元素 | 类型分布 |
|---|---|---|
| `01_工作列表` | **41** | 17 FRAME + 24 TEXT |
| `02_扫查态` | **25** | 12 FRAME + 13 TEXT |
| `03_等待分析中` | **17** | 8 FRAME + 9 TEXT |
| `04_判读态结论` | **27** | 11 FRAME + 13 TEXT + 2 ELLIPSE + 1 VECTOR |
| `05_多部位态` | **20** | 9 FRAME + 11 TEXT |
| `06_停档禁忌命中` | **28** | 13 FRAME + 12 TEXT + 2 VECTOR + 1 ELLIPSE |
| `07_报告侧面板` | **39** | 16 FRAME + 20 TEXT + 2 ELLIPSE + 1 VECTOR |
| `08_设置` | **28** | 10 FRAME + 18 TEXT |
| `09_新建检查会话` | **29** | 13 FRAME + 16 TEXT |
| `10_确认动作区` | **33** | 14 FRAME + 16 TEXT + 2 ELLIPSE + 1 VECTOR |
| `11_AI不可用态` | **20** | 10 FRAME + 10 TEXT |
| `12_签发后` | **43** | 18 FRAME + 22 TEXT + 2 ELLIPSE + 1 VECTOR |
| `13_诊室遮蔽态` | **32** | 13 FRAME + 16 TEXT + 2 ELLIPSE + 1 VECTOR |
| `14_多部位标签排` | **39** | 17 FRAME + 19 TEXT + 2 ELLIPSE + 1 VECTOR |
| `15_工作列表浅色` | **41** | 17 FRAME + 24 TEXT |
| **合计** | **462** | **198 FRAME + 243 TEXT + 13 ELLIPSE + 8 VECTOR** |

每屏目录内含：`design_data.json`（元素级全量）· `page_info.json`（角色 / 路由建议 / 参考图 / 差异）· `_summary.json`（脚本算出的统计，含 `absolute_positioned` / `dashed_nodes` / `gradient_nodes` / `shadow_nodes` / `hidden_nodes`）。

### 属性集与编码约定（读数据前必看）

- **属性集 26 项，含对齐与增长**：`primaryAxisAlignItems` / `counterAxisAlignItems` / `layoutGrow` / `lineHeight` / `letterSpacing` / `effects` / `dashPattern` / `opacity` 都在。
  ⚠️ 早期版本漏过对齐属性，导致「页头」只剩 `gap:0`、看不出是 `SPACE_BETWEEN` —— 现已补全。
- **矢量图标带内部结构**：`图标-信息` = `ELLIPSE + VECTOR + ELLIPSE`；`图标-警示` = `VECTOR ×2 + ELLIPSE`。**不是位图**，导出 SVG 见 `../assets/svg/`。
- **省略 = 默认值**：每个 `design_data.json` 顶部的 `_encoding.defaults` 列了被省略的属性及其中性值。
  凡在节点上**出现**的属性都是非默认值，**必须实现**；被省略的按默认值处理。
- **颜色**：SOLID 已转 hex（原 0~1 浮点 → `round(v*255)`）；渐变保留原浮点与 `gradientTransform`。
- **`visible: false`**（仅 11_AI不可用态 用了 2 处）：应理解为**条件不满足即不渲染**，不是 CSS 隐藏。
- 特殊键：`token`（映射到哪个 token）、`cond`（渲染条件）、`why`（为什么这么做）、
  `annotation_note`（**评阅用标注，不要实现**）、`missing_by_rule`（**故意不渲染**的元素）、
  `actions`（可点元素 → 去向）、`diff_from` / `diff_summary`（该屏基于哪屏、差什么）。

### 导出时在画布上发现并修正的 4 处缺陷（数据已按修正值给出）

1. `08_设置` 的侧栏高亮错停在「工作列表」→ 已改为「设置」
2. `12_签发后` 的报告面板标题仍是「报告草稿」、签发按钮仍可用 → 已改「已签发」+ 禁用态
3. `15_工作列表浅色` 的侧栏脚注仍写「设备级深色主题」→ 已改「浅色」
4. `01_工作列表` 的页头对齐属性此前未导出 → 已补全

> `09_新建检查会话` 的侧栏高亮停在「工作列表」是**有意为之**（它属于工作列表流程的子页，导航反映"所在板块"）—— 已在 `design_data.json` 的 `_nav_highlight_decision` 说明。

排期建议：**先做 01 / 02 打通「数据 → 代码 → 三处证据验收」整条链**，再按 `diff_from` 关系批量补状态变体（变体之间只差少数节点，成本远低于新页面）。

---

## 九、已知限制

1. **影像区不是真实 DICOM 图像**。本环境 AI 图像生成不可用，画布里是深色渐变 + 成像视口家具。**叠加标注与真实组织纹理的对比度从未验证** —— 接上真实超声图后必须重新验。
2. SVG 图标**颜色已烘焙**（导出时写死 `fill="#FFF"`）。要跟主题变色，改成 `currentColor` 再用 CSS `color` 控制。真实只有 **2 个图标**（info / warning 三角），`../assets/svg/` 里其余是同一图标在不同屏的实例 —— 用**同一个组件**。
3. 浅色主题只画了 `15_工作列表浅色` 一屏做验证样本；token 已给全，其余屏换 `data-theme` 即得。
