# Phase0 DATA轨道未达成目标实现计划

## 一、问题分析

### 1.1 未达成目标概述

| 目标 | 要求 | 现状 | 差距 | 严重程度 |
|:---|:---|:---|:---|:---|
| 术语映射覆盖率 | ≥95% | 75.3% | -19.7% | 🔴 高 |
| 心脏用例有效性 | 100%含预期属性 | 0% | -100% | 🔴 高 |

### 1.2 根因分析

**问题1：术语映射覆盖率不足**
- 词汇表覆盖范围有限
- 部分复合医学术语未收录（如"边界尚清"、"回声减低"等）
- 位置描述词汇不完整

**问题2：心脏用例有效性为0**
- 心脏诊断属性体系尚未完善
- 心脏超声报告结构与甲状腺/肝脏差异较大
- 缺乏标准化的心脏属性词汇映射

---

## 二、实现计划

### 2.1 方案一：词汇表扩充（短期 - 1-2周）

**目标**：将术语映射覆盖率提升至≥95%

**修改文件**：`app/knowledge/vocabulary.py`

**实施步骤**：

| 步骤 | 任务 | 预期产出 |
|:---|:---|:---|
| 1 | 扩充边界描述术语 | 添加"边界尚清"、"边界模糊"等 |
| 2 | 扩充回声描述术语 | 添加"回声减低"、"回声稍低"、"回声增强"等 |
| 3 | 扩充位置描述术语 | 添加"近被膜"、"深部"等位置描述 |
| 4 | 扩充形态描述术语 | 添加"类圆形"、"不规则形"等变体 |
| 5 | 扩充血流描述术语 | 添加"血流信号不丰富"、"少许血流"等 |

**具体修改内容**：

#### 2.1.1 边界描述扩充
```python
# 在 _init_boundary 方法中添加
terms = [
    ("clear", ["边界清", "边界清晰", "境界清", "境界清晰", "边界清楚", "边界尚清"], []),
    ("ill-defined", ["边界不清", "境界不清", "边界欠清", "边界模糊"], []),
    ("irregular", ["边界不规则"], []),
    ("lobulated", ["分叶状", "边界分叶"], []),
]
```

#### 2.1.2 回声描述扩充
```python
# 在 _init_echogenicity 方法中添加
terms = [
    ("hyperechoic", ["高回声", "强回声", "回声增强", "高回声区", "回声稍强"], []),
    ("hypoechoic", ["低回声", "回声减低", "低回声区", "回声稍低"], []),
    ("isoechoic", ["等回声", "等回声区"], []),
    ("mixed", ["混合回声", "不均质回声", "回声不均", "混合回声区"], []),
    ("anechoic", ["无回声", "液性暗区", "无回声区"], []),
]
```

#### 2.1.3 位置描述扩充
```python
# 在 _init_anatomical_locations 方法中添加
location_terms = [
    # 甲状腺位置扩展
    ("right_lobe_upper", ["右叶上极", "右叶上部", "右叶顶端"], [], Organ.THYROID),
    ("right_lobe_middle", ["右叶中极", "右叶中部", "右叶中段"], [], Organ.THYROID),
    ("right_lobe_lower", ["右叶下极", "右叶下部", "右叶底部"], [], Organ.THYROID),
    ("left_lobe_upper", ["左叶上极", "左叶上部", "左叶顶端"], [], Organ.THYROID),
    ("left_lobe_middle", ["左叶中极", "左叶中部", "左叶中段"], [], Organ.THYROID),
    ("left_lobe_lower", ["左叶下极", "左叶下部", "左叶底部"], [], Organ.THYROID),
    ("isthmus", ["峡部", "甲状腺峡部"], [], Organ.THYROID),
    # 肝脏位置扩展
    ("liver_right_anterior", ["肝右前叶", "右前叶"], [], Organ.LIVER),
    ("liver_right_posterior", ["肝右后叶", "右后叶"], [], Organ.LIVER),
    ("liver_left_lateral", ["肝左外叶", "左外叶"], [], Organ.LIVER),
    ("liver_left_medial", ["肝左内叶", "左内叶"], [], Organ.LIVER),
]
```

#### 2.1.4 形态描述扩充
```python
# 在 _init_morphology 方法中添加
terms = [
    ("round", ["圆形", "类圆形", "近圆形"], []),
    ("oval", ["椭圆形", "卵圆型", "形态规则", "规则", "类椭圆形"], []),
    ("lobulated", ["分叶状", "呈分叶状"], []),
    ("irregular", ["不规则形", "形态不规则", "不规则", "形态欠规则"], []),
]
```

#### 2.1.5 血流描述扩充
```python
# 在 _init_vascularity 方法中添加
terms = [
    ("absent", ["无血流", "未见明显血流", "未见血流", "血流不丰富", "血流信号不丰富", "未见血流信号"], []),
    ("peripheral", ["周边血流", "边缘血流", "环状血流", "环绕血流"], []),
    ("internal", ["内部血流", "中央血流", "内部见血流", "中心血流"], []),
    ("mixed", ["混合血流", "周边及内部血流", "血流丰富", "丰富血流", "血流信号丰富", "少许血流", "少量血流"], []),
]
```

---

### 2.2 方案二：心脏诊断属性体系完善（中期 - 2-3周）

**目标**：建立完整的心脏超声诊断属性词汇表，激活216例心脏用例

**修改文件**：
- `app/knowledge/vocabulary.py` - 添加心脏专用属性枚举和词汇
- `app/knowledge/baseline_validation.py` - 更新心脏用例生成，添加expected_attributes

**实施步骤**：

| 步骤 | 任务 | 预期产出 |
|:---|:---|:---|
| 1 | 添加心脏属性枚举类型 | 创建心脏专用属性分类（心腔结构、瓣膜、室壁、心包等） |
| 2 | 建立心脏属性词汇映射 | 中文→英文映射表 |
| 3 | 更新心脏验证用例 | 为216例心脏用例添加expected_attributes |

**具体修改内容**：

#### 2.2.1 心脏属性枚举定义（vocabulary.py）
```python
class HeartStructure(str, Enum):
    """心脏结构枚举."""
    LEFT_ATRIUM = "left_atrium"
    LEFT_VENTRICLE = "left_ventricle"
    RIGHT_ATRIUM = "right_atrium"
    RIGHT_VENTRICLE = "right_ventricle"
    MITRAL_VALVE = "mitral_valve"
    TRICUSPID_VALVE = "tricuspid_valve"
    AORTIC_VALVE = "aortic_valve"
    PULMONARY_VALVE = "pulmonary_valve"
    INTERVENTRICULAR_SEPTUM = "interventricular_septum"
    LEFT_VENTRICLE_WALL = "left_ventricle_wall"
    PERICARDIUM = "pericardium"
    AORTA = "aorta"

class HeartCondition(str, Enum):
    """心脏状况枚举."""
    NORMAL = "normal"
    ENLARGED = "enlarged"
    THICKENED = "thickened"
    MILD_REGURGITATION = "mild_regurgitation"
    MODERATE_REGURGITATION = "moderate_regurgitation"
    SEVERE_REGURGITATION = "severe_regurgitation"
    STENOSIS = "stenosis"
    HYPOKINESIS = "hypokinesis"
    NO_ABNORMALITY = "no_abnormality"
    REDUCED_SYSTOLIC_FUNCTION = "reduced_systolic_function"
    REDUCED_DIASTOLIC_FUNCTION = "reduced_diastolic_function"
    SMALL_EFFUSION = "small_effusion"
    NO_EFFUSION = "no_effusion"

class HeartFunctionGrade(str, Enum):
    """心功能分级枚举."""
    EF_NORMAL = "ef_normal"  # EF > 55%
    EF_MILD_REDUCED = "ef_mild_reduced"  # 40-55%
    EF_MODERATE_REDUCED = "ef_moderate_reduced"  # 30-40%
    EF_SEVERE_REDUCED = "ef_severe_reduced"  # < 30%
```

#### 2.2.2 心脏词汇初始化（vocabulary.py）
```python
def _init_heart_structures(self):
    """初始化心脏结构术语."""
    terms = [
        ("left_atrium", ["左房", "左心房"], [], Organ.HEART),
        ("left_ventricle", ["左室", "左心室"], [], Organ.HEART),
        ("right_atrium", ["右房", "右心房"], [], Organ.HEART),
        ("right_ventricle", ["右室", "右心室"], [], Organ.HEART),
        ("mitral_valve", ["二尖瓣", "二尖瓣瓣口"], [], Organ.HEART),
        ("tricuspid_valve", ["三尖瓣", "三尖瓣瓣口"], [], Organ.HEART),
        ("aortic_valve", ["主动脉瓣"], [], Organ.HEART),
        ("pulmonary_valve", ["肺动脉瓣"], [], Organ.HEART),
        ("interventricular_septum", ["室间隔"], [], Organ.HEART),
        ("left_ventricle_wall", ["左室壁", "左室后壁", "左室侧壁"], [], Organ.HEART),
        ("pericardium", ["心包", "心包腔"], [], Organ.HEART),
        ("aorta", ["主动脉", "升主动脉", "降主动脉"], [], Organ.HEART),
    ]
    for standard, synonyms, related, organ in terms:
        self._add_mapping(standard, synonyms, related, organ=organ, category="heart_structure")

def _init_heart_conditions(self):
    """初始化心脏状况术语."""
    terms = [
        ("normal", ["未见明显异常", "未见异常"], [], Organ.HEART),
        ("enlarged", ["增大", "扩大"], [], Organ.HEART),
        ("thickened", ["增厚", "增厚明显"], [], Organ.HEART),
        ("mild_regurgitation", ["轻度反流", "少量反流"], [], Organ.HEART),
        ("moderate_regurgitation", ["中度反流", "中量反流"], [], Organ.HEART),
        ("severe_regurgitation", ["重度反流", "大量反流"], [], Organ.HEART),
        ("stenosis", ["狭窄", "瓣口狭窄"], [], Organ.HEART),
        ("hypokinesis", ["运动减低", "运动减弱"], [], Organ.HEART),
        ("no_abnormality", ["结构未见明显异常", "未见器质性病变"], [], Organ.HEART),
        ("reduced_systolic_function", ["收缩功能减低", "收缩功能下降"], [], Organ.HEART),
        ("reduced_diastolic_function", ["舒张功能减低", "舒张功能下降"], [], Organ.HEART),
        ("small_effusion", ["少量积液", "微量积液"], [], Organ.HEART),
        ("no_effusion", ["未见积液", "心包腔未见积液"], [], Organ.HEART),
    ]
    for standard, synonyms, related, organ in terms:
        self._add_mapping(standard, synonyms, related, organ=organ, category="heart_condition")

def _init_heart_function(self):
    """初始化心功能分级术语."""
    terms = [
        ("ef_normal", ["EF正常", "左室射血分数正常", "EF>55%"], [], Organ.HEART),
        ("ef_mild_reduced", ["EF轻度减低", "EF40-55%"], [], Organ.HEART),
        ("ef_moderate_reduced", ["EF中度减低", "EF30-40%"], [], Organ.HEART),
        ("ef_severe_reduced", ["EF重度减低", "EF<30%"], [], Organ.HEART),
    ]
    for standard, synonyms, related, organ in terms:
        self._add_mapping(standard, synonyms, related, organ=organ, category="heart_function")
```

#### 2.2.3 更新心脏用例生成（baseline_validation.py）
```python
def _generate_heart_cases(vocab: UDAFVocabulary) -> list[ValidationCase]:
    """Generate heart validation cases with expected_attributes."""
    cases: list[ValidationCase] = []
    idx = 0

    # 正常心脏检查结果
    normal_findings = [
        ("心内结构未见明显异常", {"heart_condition": "no_abnormality"}),
        ("左室壁运动未见明显异常", {"heart_condition": "normal"}),
        ("各瓣膜回声及活动未见异常", {"heart_condition": "normal"}),
        ("心包腔未见积液", {"heart_condition": "no_effusion"}),
        ("左室收缩功能正常", {"heart_function": "ef_normal"}),
        ("左室舒张功能正常", {"heart_condition": "normal"}),
    ]
    
    for finding_text, expected_attrs in normal_findings:
        idx += 1
        cases.append(ValidationCase(
            case_id=f"heart_{idx:04d}",
            organ=Organ.HEART,
            input_text=finding_text + "。",
            expected_attributes=expected_attrs,
            source="generated",
        ))

    # 心脏结构与状况组合
    structures = [
        ("左房", "left_atrium"),
        ("左室", "left_ventricle"),
        ("右房", "right_atrium"),
        ("右室", "right_ventricle"),
        ("二尖瓣", "mitral_valve"),
        ("三尖瓣", "tricuspid_valve"),
        ("主动脉瓣", "aortic_valve"),
        ("室间隔", "interventricular_septum"),
        ("左室壁", "left_ventricle_wall"),
        ("心包", "pericardium"),
    ]
    
    conditions = [
        ("增大", "enlarged"),
        ("增厚", "thickened"),
        ("轻度反流", "mild_regurgitation"),
        ("中度反流", "moderate_regurgitation"),
        ("重度反流", "severe_regurgitation"),
        ("狭窄", "stenosis"),
        ("运动减低", "hypokinesis"),
        ("未见明显异常", "no_abnormality"),
    ]
    
    for struct_name, struct_code in structures:
        for cond_name, cond_code in conditions:
            idx += 1
            text = f"{struct_name}{cond_name}。"
            expected_attrs = {
                "heart_structure": struct_code,
                "heart_condition": cond_code,
            }
            cases.append(ValidationCase(
                case_id=f"heart_{idx:04d}",
                organ=Organ.HEART,
                input_text=text,
                expected_attributes=expected_attrs,
                source="generated",
            ))

    # 功能评估用例
    function_cases = [
        ("左室收缩功能减低", {"heart_condition": "reduced_systolic_function"}),
        ("左室舒张功能减低", {"heart_condition": "reduced_diastolic_function"}),
        ("左室EF值正常", {"heart_function": "ef_normal"}),
        ("左室EF轻度减低", {"heart_function": "ef_mild_reduced"}),
        ("心包少量积液", {"heart_condition": "small_effusion"}),
    ]
    
    for text, expected_attrs in function_cases:
        idx += 1
        cases.append(ValidationCase(
            case_id=f"heart_{idx:04d}",
            organ=Organ.HEART,
            input_text=text + "。",
            expected_attributes=expected_attrs,
            source="generated",
        ))

    # 组合用例
    combinations = [
        ("左房增大，二尖瓣轻度反流", {"heart_structure": "left_atrium", "heart_condition": "enlarged", "heart_condition": "mild_regurgitation"}),
        ("左室增大，左室收缩功能减低", {"heart_structure": "left_ventricle", "heart_condition": "enlarged", "heart_condition": "reduced_systolic_function"}),
        ("右房增大，三尖瓣轻度反流", {"heart_structure": "right_atrium", "heart_condition": "enlarged", "heart_condition": "mild_regurgitation"}),
        ("左室壁增厚，左室舒张功能减低", {"heart_structure": "left_ventricle_wall", "heart_condition": "thickened", "heart_condition": "reduced_diastolic_function"}),
        ("心包少量积液，心内结构未见明显异常", {"heart_condition": "small_effusion", "heart_condition": "no_abnormality"}),
    ]
    
    for text, expected_attrs in combinations:
        idx += 1
        cases.append(ValidationCase(
            case_id=f"heart_{idx:04d}",
            organ=Organ.HEART,
            input_text=text + "。",
            expected_attributes=expected_attrs,
            source="generated",
        ))

    return cases
```

---

### 2.3 方案三：UDT解析器扩展支持心脏属性（中期）

**目标**：更新UDTParser以支持心脏属性提取

**修改文件**：`app/knowledge/udt_parser.py`

**实施步骤**：

| 步骤 | 任务 | 预期产出 |
|:---|:---|:---|
| 1 | 添加心脏属性类别 | 在_ATTRIBUTE_CATEGORIES中添加心脏相关类别 |
| 2 | 扩展器官检测 | 增强心脏相关术语识别 |

**具体修改内容**：
```python
# 在_ATTRIBUTE_CATEGORIES中添加心脏类别
_ATTRIBUTE_CATEGORIES = [
    "echogenicity", "boundary", "morphology", "vascularity", "calcification",
    "heart_structure", "heart_condition", "heart_function",  # 添加心脏属性
]
```

---

## 三、验证与测试计划

### 3.1 单元测试扩展

**修改文件**：`tests/unit/test_knowledge.py`

**新增测试用例**：
- 验证新增术语的映射正确性
- 验证心脏属性解析功能
- 验证覆盖率计算准确性

### 3.2 验证指标

| 指标 | 目标值 | 验证方法 |
|:---|:---|:---|
| 术语映射覆盖率 | ≥95% | 运行baseline_validation.py |
| 心脏用例有效率 | 100% | 运行analyze_cases.py |
| 甲状腺准确率 | ≥85% | 运行baseline_validation.py |
| 肝脏准确率 | ≥85% | 运行baseline_validation.py |
| 心脏准确率 | ≥80% | 运行baseline_validation.py |

---

## 四、风险评估与应对

| 风险 | 严重程度 | 应对措施 |
|:---|:---|:---|
| 术语映射冲突 | 中 | 建立术语审核流程，确保术语唯一性 |
| 心脏属性体系不完整 | 高 | 请心血管领域专家审核属性体系 |
| 解析器性能下降 | 低 | 使用Aho-Corasick算法优化多模式匹配（已有TODO项） |
| 测试用例不足 | 中 | 扩展验证用例至≥300例/器官 |

---

## 五、资源需求

| 角色 | 职责 | 投入时间 |
|:---|:---|:---|
| 医疗术语专家 | 术语审核、词汇扩充 | 2-3天 |
| 心血管领域专家 | 心脏属性体系设计 | 3-5天 |
| 数据工程师 | 词汇表更新、用例维护 | 5-7天 |
| 测试工程师 | 验证测试、报告生成 | 2-3天 |

---

## 六、预期效果

| 指标 | 改善前 | 改善后 |
|:---|:---|:---|
| 术语映射覆盖率 | 75.3% | ≥95% |
| 心脏用例有效率 | 0% | 100% |
| 心脏解析准确率 | 0% | ≥80% |
| 整体准确率 | 83.3% | ≥85% |

---

## 七、里程碑计划

| 阶段 | 时间 | 交付物 |
|:---|:---|:---|
| Phase 1 | 第1周 | 词汇表扩充完成 |
| Phase 2 | 第2周 | 心脏属性体系完成 |
| Phase 3 | 第3周 | 解析器扩展完成 |
| Phase 4 | 第4周 | 验证测试完成 |