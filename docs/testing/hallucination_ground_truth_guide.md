# 幻觉检测Ground Truth标注指南

## 1. 字段定义

### `expected_hallucination`

| 值 | 含义 | 使用场景 |
|---|---|---|
| `True` | 该文本确实包含幻觉内容 | 禁忌词出现且无否定修饰、分级矛盾、数值异常 |
| `False` | 该文本不包含幻觉内容 | 禁忌词被否定修饰、分级一致、数值正常 |
| `None` | 无法确定是否为幻觉 | 边界模糊场景，需二次标注 |

## 2. 标注原则

### 2.1 基于检测策略的预期结果

标注应基于系统5种检测策略的预期输出：

1. **禁忌词检测 (forbidden_terms)**: 检查器官禁忌词是否在否定上下文中出现
2. **矛盾分级检测 (contradictory_grading)**: 检查同一器官是否出现矛盾分级
3. **医学常识检测 (medical_common_sense)**: 检查是否违反基本医学常识
4. **数值有效性检测 (numerical_validity)**: 检查数值是否在合理范围内
5. **上下文感知检测 (context_aware)**: 检查上下文是否支持所述结论

### 2.2 否定修饰处理

以下否定词出现在禁忌词前方100字符窗口内时，该禁忌词不应标记为幻觉：

- 强否定：排除、未见、不考虑、无、未发现、否认、不支持、不可能
- 弱否定：未提示、不伴、未查及、未探及、未见明显
- 特殊：不除外（标记为info而非hallucination）

### 2.3 分级矛盾判定

以下情况判定为矛盾分级（幻觉）：
- 同一器官出现两个不同分级（如TI-RADS 3类和TI-RADS 4类）
- 分级与描述矛盾（如描述"恶性可能"但分级为2类）

以下情况不判定为矛盾：
- 分级后附加修正说明（如"TI-RADS 3类，建议随访"）
- 不同器官的不同分级（正常）

## 3. 标注示例

### 3.1 禁忌词检测

**正例（expected_hallucination=True）**:
```
甲状腺结节，伴骨转移，TI-RADS 4a类
```
- "骨转移"为thyroid禁忌词，无否定修饰 → 幻觉

**反例（expected_hallucination=False）**:
```
甲状腺结节，未见骨转移，TI-RADS 3类
```
- "骨转移"被"未见"否定 → 非幻觉

### 3.2 矛盾分级检测

**正例（expected_hallucination=True）**:
```
甲状腺右叶结节，TI-RADS 3类，TI-RADS 5类
```
- 同一器官出现3类和5类 → 幻觉

**反例（expected_hallucination=False）**:
```
甲状腺右叶结节TI-RADS 3类，左叶结节TI-RADS 4a类
```
- 不同叶的不同分级 → 非幻觉

### 3.3 医学常识检测

**正例（expected_hallucination=True）**:
```
甲状腺结节，建议子宫切除
```
- 甲状腺结节与子宫切除无医学关联 → 幻觉

**反例（expected_hallucination=False）**:
```
甲状腺乳头状癌，建议甲状腺全切
```
- 甲状腺癌与甲状腺全切有医学关联 → 非幻觉

### 3.4 数值有效性检测

**正例（expected_hallucination=True）**:
```
左室射血分数LVEF 120%
```
- LVEF > 100% 不可能 → 幻觉

**反例（expected_hallucination=False）**:
```
左室射血分数LVEF 55%
```
- LVEF 55% 正常范围 → 非幻觉

### 3.5 上下文感知检测

**正例（expected_hallucination=True）**:
```
甲状腺未见异常。建议：骨扫描检查。
```
- 甲状腺未见异常却建议骨扫描，上下文不支持 → 幻觉

**反例（expected_hallucination=False）**:
```
甲状腺结节伴钙化，TI-RADS 4a类。建议：FNA活检。
```
- 4a类建议FNA活检，上下文支持 → 非幻觉

## 4. 质量控制

### 4.1 双标注要求

- 每个测试用例须由≥2名标注员独立标注
- 一致率（Cohen's Kappa）须 ≥ 0.85
- 不一致样本由第三名标注员仲裁

### 4.2 标注员资质

- 具备超声诊断基础知识
- 完成标注培训（≥2小时）
- 通过标注资格测试（准确率≥90%）

### 4.3 标注格式

```json
{
  "case_id": "thyroid_001",
  "organ": "thyroid",
  "text": "甲状腺结节，伴骨转移，TI-RADS 4a类",
  "expected_hallucination": true,
  "expected_strategies": ["forbidden_terms"],
  "annotator_id": "A001",
  "confidence": 1.0,
  "notes": "骨转移为thyroid禁忌词，无否定修饰"
}
```
