# 设计数据包 · 交接契约

> **用途**：让其他 Agent / 工程师**仅凭本包数据 + 参考图**就能还原画布里的 15 屏，不需要去猜数值。
> **来源**：Ardot 画布「超声诊疗决策支持系统-医生侧工作区」`https://ardot.tencent.com/file/732101049255682`（2026-10-02 导出）
> **目标栈**：React 18 + Ant Design 5 + Tailwind（与仓库现有 `frontend/src` 一致）

---

## 1. 三步读法

1. 读 [`screens.json`](./screens.json) —— 15 屏 + 2 块板的结构化索引。每屏带 `nodeId`、`kind`、`theme`、参考图路径、**`proves`（这屏证明了哪条设计规则）**、`heightBudget`（高度账）、`docs`（对应设计文档章节）。
2. 读 [`tokens/tokens.css`](./tokens/tokens.css) 或 [`tokens/tokens.json`](./tokens/tokens.json) —— 语义 token，含 dark / light 两套模式。**不要自己另起一套颜色。**
3. 打开 [`参考图/`](./参考图) 对应 PNG 做视觉比对。参考图是 **2x**，1440×900 逻辑像素。

**关键前置**：动手前先读设计文档的**两个约定**——[17-医生侧展示契约](../17-医生侧展示契约.md)（显示什么）与 [14-前端设计评审结论](../14-前端设计评审结论.md) §0.1（三条准绳）。**本包只给数据，不给判断依据**；脱离那两条会做错。

---

## 2. 包结构

```
设计数据包/
├── README.md            ← 本文（交接契约）
├── screens.json         ← 机读屏索引 + 高度账 + 条件渲染证据 + 已知限制
├── tokens/
│   ├── tokens.css       ← CSS 变量（:root = dark，[data-theme="light"] = light）
│   └── tokens.json      ← W3C DTCG 格式，双模式写在 $extensions.modes
├── assets/svg/          ← 7 个 SVG（**实际只有 2 个真实图标**，其余是同图标在不同屏的实例）
└── 参考图/               ← 15 张 PNG（2x），仅供视觉比对
```

---

## 3. 映射规则（Ardot → React / antd / Tailwind）

### 3.1 布局

| Ardot | 实现 |
|---|---|
| `layoutMode: HORIZONTAL` | `flex flex-row` |
| `layoutMode: VERTICAL` | `flex flex-col` |
| `layoutMode: NONE` | 父级 `relative`，子级全部绝对定位 |
| 子节点 `layoutPositioning: ABSOLUTE` | 在 flex 父级里也要绝对定位：父 `relative` + 子 `absolute`（**不要**指望 x/y 在 flex 里生效） |
| `itemSpacing` | `gap-*`，只用 `--space-*` 阶梯（4/8/12/16/24/32/48） |
| `padding*` | `p-*` / `px-*` / `py-*`；**容器务必 `box-sizing: border-box`**（否则 `w-full` + padding 会撑出横向滚动条） |
| `primaryAxisAlignItems: CENTER / SPACE_BETWEEN` | `justify-center` / `justify-between` |
| `counterAxisAlignItems: CENTER` | `items-center` |
| `layoutGrow: 1` | `flex-1` |
| `width: "fill_container"` | `w-full` 或 `flex-1` |
| `height: "hug_contents"` | `h-auto` |
| `clipsContent: true` | `overflow-hidden` |

### 3.2 颜色与填充

| Ardot | 实现 |
|---|---|
| `fills` SOLID | `bg-[var(--color-surface)]` 之类；**不要写字面色值** |
| `strokes` + `strokeWeight` | `border border-[var(--color-border)]` |
| `dashPattern`（虚线，如「未校验」标注框） | `border-dashed`；1.5px 细描边用 `border-[1.5px]` |
| `cornerRadius` | `rounded-tag`(4) / `rounded-card`(8) / `rounded-panel`(12) |
| `effects: DROP_SHADOW` | `shadow-*`；**全稿只有报告侧面板一处用了投影**，不要给每个卡片加阴影 |
| 渐变（影像区占位） | `background: linear-gradient(...)`；`gradientTransform [[0,1,0],[-1,0,1]]` = `to bottom` |

### 3.3 文本

| Ardot | 实现 |
|---|---|
| `fontName.family = "Noto Sans SC"` | `font-cjk`（中文正文） |
| `fontName.family = "Inter"` | `font-numeric`（**只用于纯数字**：测量值、时间、深度刻度） |
| `fontSize` | 用 `--font-size-*` 阶梯（32/20/18/14/13/12/11） |
| `fontWeight` | `"400"`→`font-normal`、`"500"`→`font-medium`、`"600"`→`font-semibold`、`"700"`→`font-bold` |
| `lineHeight: 22` 等 | 行内指定 `leading-[22px]`；正文段落用 24 |
| 每个 text 节点的 `fill` | 必须显式给：`text-[var(--color-text-primary)]` / `-secondary` / `-attention` |
| `textAlignHorizontal: CENTER` | `text-center` |

**注意**：设计原文里的中文标点（`·`、`—`、`（）`、`「」`）**是内容的一部分，不要替换成英文标点**；`18.4 × 12.1 mm` 里的 `×` 同理。

### 3.4 Ant Design 5 接线注意（容易踩）

1. **不要用 antd 的默认主题色**（`@primary-color` 蓝）与 `success`(绿) / `warning`(橙) 语义色 —— 本项目的语义色只有两级：**「停」= `semantic-stop` 红 / 「看一眼」= `attention` 中性青灰 / 正常 = 无颜色**。antd 的 `Alert type="warning"` 会带橙色，**直接冲突**，必须用自定义样式。
2. `Button` 的主操作 = `bg-[var(--color-inverse)] text-[var(--color-on-inverse)]`（深色主题下是浅底深字，浅色主题下自动翻成深底白字）。
3. `Tag` / `Badge` 的状态色一律走 `attention`，**不要**用 antd 的 `status` 预设。
4. 主题切换：给根节点加 `data-theme="light"` 即可，**不要**按页面分别切主题（这是硬规则，见下）。

---

## 4. 工作区骨干尺寸（唯一硬骨架）

```
┌ 常驻条 40 ─────────────────────────────┐
├ 影像区 600（恒定，永不缩放）────────────┤
├ 结论区 260（确认动作区出现时收至 200）──┤
├ 确认动作区 60（仅有待确认项时渲染）─────┤
└────────────────────────────────────────┘
合计 = 900（1440 × 900）
```

`screens.json` 每屏的 `heightBudget` 都按此给出。**影像区 600 在任何状态下都不变** —— 这是全稿最容易被做错的一条。

---

## 5. 必须遵守的规则，以及**哪些"缺失"是正确的**

实现时最容易犯的错，是**把"按规则不渲染"当成"设计漏画"给补上**。以下都是**故意的缺失**：

| 看起来"缺了" | 真相 |
|---|---|
| S02 常驻条里没有同意状态、连接状态 | **正确**。它们只在异常时渲染。正常态渲染 = 违反 P1「默认沉默」 |
| S04 结论卡里没有「部位指示条」 | **正确**。部位数 < 2 时不渲染（对照 S05 / S14） |
| 所有屏都没有「确认动作区」 | **大部分正确**。只有存在待确认项时才出现（对照 S10） |
| S07 报告面板顶部没有「报告状态」行 | **正确**。草稿态不渲染（对照 S12 已签发态） |
| 全稿找不到"绿色" | **正确**。"正常"不是绿色，是**什么都不显示**。屏幕上"有颜色"就等于"有事" |
| S04 没有置信度、一致性、Self-RAG 明细 | **正确**。它们是二级折叠内容，且当前后端字段还没接通（见 `screens.json` 的 `knownLimitations` 与 [16-报告](../16-设计依据代码核定报告.md) §N-01） |

**四条硬规则**（违反即返工）：
1. **默认沉默** —— 不满足条件**不渲染**，不是 `display:none` 藏起来。验收时要能证明"该元素在 DOM 里不存在"。
2. **影像尺寸恒定** —— 影像区永远 600，任何面板/状态都不许挤压它。
3. **语义色两级** —— 只有 `semantic-stop`（停，挡签发，并伴随一次性声光）与 `attention`（看一眼，静态、不发声、不闪烁）。**不得新增第三个语义色**。
4. **主题按设备级一次设置** —— 不是按页面分默认；`image-bg` 固定深色，**不参与主题切换**。

---

## 6. 验收要求（不是"看着像就算"）

单一来源的证据**不足以判定还原达标**。至少交叉比对三段：

1. **声明值**：读 `tokens.css` + 本屏 `heightBudget`，确认尺寸/颜色/间距与设计一致。
2. **设计侧像素**：`参考图/` 对应那张 PNG（2x）。
3. **代码侧渲染**：本地起服务截图，或用浏览器读 computed style。

**每屏具体怎么比**：
- 尺寸类 → 量 `getBoundingClientRect()`，重点量**影像区高度 = 600**。
- 颜色类 → 读 computed `background-color` / `color`，与 token 逐位对齐（不要目测）。
- 条件渲染 → 在 DevTools 里查 DOM：**S02 不该有同意/连接节点；S04 不该有部位标签排**。
- 文本类 → 对照 `参考图` 检查换行位置与是否截断。

---

## 7. 已知限制（实现前必须知道）

1. **影像区不是真实 DICOM 图像**。本环境 AI 图像生成不可用，当前是深色渐变 + 成像视口家具（参数角标 / 时间 / 深度刻度 / 冻结态测量框与测量值 / 标注框）。**叠加标注与真实组织纹理的对比度从未验证** —— 接上真实超声图后必须重新验一次。
2. **SVG 图标颜色已烘焙**（导出时写死了 `fill="#FFF"` 等）。要跟主题变色，请把 `fill` / `stroke` 改为 `currentColor`，再用 CSS `color` 控制。真实只有 **2 个图标**（info / warning 三角），`assets/svg/` 里其余是同一图标在不同屏的实例 —— 实现时用**同一个组件**，不要做 7 份。
3. **浅色主题只画了 S15 一屏**作为验证样本。token 已给全（`[data-theme="light"]`），其余屏换主题即得，但**未出图**。
4. 同意撤回、连接中断等异常态**还没画**；对应规则见 17-文档 §1 的「条件」列。

---

## 8. 不要做什么

- **不要把 `参考图/*.png` 当成资产内嵌进代码**。它们是给人做视觉比对的，不是切图。图标走 `assets/svg/`，其余全部用代码实现。
- **不要把 B1「读图指南板」、B2「页面与跳转总览板」当成页面实现**。它们是文档性质的板，`screens.json` 里 `kind: "board"` 已标注。
- **不要新增 token**。缺什么先看 `tokens.css` 有没有；确实没有的，先改设计文档再改代码（单一真相源）。
- **不要"顺手"加装饰**：不加渐变、不加圆角阴影、不加图标点缀。本设计的准绳是「约束优先于装饰」。
