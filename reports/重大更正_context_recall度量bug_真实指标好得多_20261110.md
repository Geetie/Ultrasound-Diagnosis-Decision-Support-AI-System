# 重大更正：`context_recall` 有度量 bug，真实指标好得多

> 日期：2026-11-10｜用户要求「继续改进检索，去看去读内部的数据结构和内容」

## 一、我要先认错

上一轮我在台账和汇报里写了：

> **`grading_standard` 只有 0.245（占 26% 问题量）—— 这是 `hit@k` 完全看不到的真实短板**

**这是错的。0.245 是我度量代码的 bug，不是检索差。**

## 二、bug 是什么

`p123_context_recall._key_units()` 对**表格行**返回空列表：

```
输入：原文提到：| | 1 | 2~10 | 4A 低度可疑恶性 |
输出：[]
```

原因：
- `2~10` 是**区间无单位**，不匹配 `\d+...(mm|cm|%)`
- `4A` 单独出现，不匹配完整分级码正则 `(?:C-TIRADS|...)\s*(?:[1-6]\s*[A-C]?\s*类)`

**空单元 → `_sentence_covered` 直接 return False → recall 必然为 0。**

## 三、影响面（实测）

| 数据集 | 修正前 | **修正后** | 变化 |
|---|---|---|---|
| L3（201 条） | 0.5386 | **0.8105** | **+27.2pt** |
| B2（484 条） | 0.3763 | **0.6568** | **+28.1pt** |

| 数据集 | 修正前 precision | **修正后** |
|---|---|---|
| L3 | 0.3104 | **0.5323** |
| B2 | 0.2506 | **0.5917** |

## 四、修法

补两类关键单元：

```python
# 1. 区间与裸数字（表格行里最有辨识度的部分）
for m in re.finditer(r"-?\d+(?:[~～-]\d+)?", sentence):
    units.append(m.group(0).replace(" ", ""))
# 2. 独立分级码
for m in re.finditer(r"(?<![A-Za-z0-9])([1-6]\s*[A-Ca-c]|TR\s?[1-5]|LR-?[1-5M])(?![A-Za-z0-9])", sentence):
    units.append(m.group(1).replace(" ", "").lower())
```

## 五、修正后的真实短板（完全变了）

### `grading_standard` 从"最差"变成正常

| query_type | 修正前 | **修正后** |
|---|---|---|
| **grading_standard** | **0.245** ❌ | **0.765** ✅ |
| technical_parameter | — | **0.577** ← **现在最差** |
| procedure_indication | — | 0.658 |
| comparison | — | 0.678 |
| boundary_case | — | 0.698 |
| followup_strategy | 0.885 | 0.718 |

**真正的短板是 `technical_parameter`（0.577）** —— 技术参数类
（机械指数、造影条件、帧频、切面选择）。

### 按格子（L3，修正后）

| 格子 | recall |
|---|---|
| multi/specific/attending | **0.979** |
| multi/abstract/attending | 0.962 |
| multi/specific/qa | 0.917 |
| single/abstract/qa | 0.909 |
| **multi/abstract/resident** | **0.615** ← 最低 |
| **multi/abstract/clinician** | **0.654** |

### 按格子（B2，修正后）

| 格子 | recall |
|---|---|
| single/specific/medium | **0.894** |
| multi/specific/medium | 0.768 |
| multi/abstract/short | 0.722 |
| single/abstract/medium | 0.665 |
| single/abstract/short | **0.571** ← 最低 |

## 六、这次错误的性质

**我看到的模式和我之前记录过的一模一样**：

> 「看到 A 和 B 不一样，就归因到最显眼的变量，而不是先确认实验条件是否成立」

这次是：**看到 recall 低，就归因到「检索差」，而不是先确认「度量本身对不对」。**

而且我把它写进了台账和对外汇报，当成一个「有价值的发现」。

## 七、已做的补救

1. `p123` 修正关键单元提取
2. 新增 5 个测试守护（`TestTableRowCoverage`），钉住三个 case：
   - `2~10` 区间必须抽为单元
   - `4A` 独立码必须抽为单元
   - `-1` 负数必须抽为单元
3. 修正后的数字重新跑了两份数据集

## 八、下一步（按真实指标）

**真实短板现在是**：
1. **`technical_parameter` 0.577**（110 条，占 23%）—— 技术参数类
2. **`multi × abstract × resident` 0.615** —— 住院医师问抽象多跳问题
3. **`single × abstract × short` 0.571**（B2 的 197 条）—— 短问题问抽象概念

**这些才该是检索改进的目标。**
