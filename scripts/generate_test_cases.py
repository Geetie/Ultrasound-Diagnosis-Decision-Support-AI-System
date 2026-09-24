import json
import random


def _scale_distribution(base_distribution: dict[str, int], count: int) -> list[str]:
    """按比例缩放分级分布到指定总数"""
    total = sum(base_distribution.values())
    result = []
    for grade, num in base_distribution.items():
        scaled = max(1, round(num * count / total))
        result.extend([grade] * scaled)
    while len(result) < count:
        grade = random.choice(list(base_distribution.keys()))
        result.append(grade)
    return result[:count]


def generate_thyroid_cases(count: int = 100) -> list[dict]:
    """生成甲状腺TI-RADS分级测试病例"""
    cases = []

    # TI-RADS分级特征模板
    grade_templates = {
        "2": [
            {"size": "0.4×0.3", "echo": "无回声", "border": "清晰", "shape": "规则",
             "calcification": "无", "blood": "未见明显血流信号"},
            {"size": "0.5×0.4", "echo": "无回声", "border": "清晰", "shape": "规则",
             "calcification": "无", "blood": "未见血流信号"},
            {"size": "0.6×0.5", "echo": "低回声", "border": "清晰", "shape": "规则",
             "calcification": "无", "blood": "未见血流信号"},
            {"size": "0.7×0.6", "echo": "高回声", "border": "清晰", "shape": "规则",
             "calcification": "无", "blood": "未见明显血流信号"},
            {"size": "0.8×0.7", "echo": "无回声", "border": "清晰", "shape": "规则",
             "calcification": "无", "blood": "未见血流信号"},
        ],
        "3": [
            {"size": "1.0×0.8", "echo": "等回声", "border": "尚清晰", "shape": "尚规则",
             "calcification": "无", "blood": "周边少量血流"},
            {"size": "1.2×1.0", "echo": "低回声", "border": "尚清晰", "shape": "尚规则",
             "calcification": "无", "blood": "少量血流信号"},
            {"size": "1.4×1.1", "echo": "稍低回声", "border": "尚清晰", "shape": "尚规则",
             "calcification": "粗钙化", "blood": "周边血流信号"},
            {"size": "1.5×1.2", "echo": "等回声", "border": "尚清晰", "shape": "尚规则",
             "calcification": "无", "blood": "少量血流"},
            {"size": "1.3×1.0", "echo": "低回声", "border": "尚清晰", "shape": "尚规则",
             "calcification": "无", "blood": "周边少量血流信号"},
        ],
        "4a": [
            {"size": "1.6×1.2", "echo": "低回声", "border": "欠清晰", "shape": "欠规则",
             "calcification": "点状强回声", "blood": "内部少量血流"},
            {"size": "1.8×1.4", "echo": "混合回声", "border": "欠清晰", "shape": "欠规则",
             "calcification": "微小钙化", "blood": "实性部分血流"},
            {"size": "1.7×1.3", "echo": "低回声", "border": "欠清晰", "shape": "欠规则",
             "calcification": "微钙化", "blood": "内部血流信号"},
            {"size": "1.9×1.5", "echo": "稍低回声", "border": "欠清晰", "shape": "欠规则",
             "calcification": "点状钙化", "blood": "少量血流"},
            {"size": "1.5×1.1", "echo": "低回声", "border": "欠清晰", "shape": "欠规则",
             "calcification": "微小钙化点", "blood": "内部少量血流信号"},
        ],
        "4b": [
            {"size": "2.0×1.6", "echo": "低回声", "border": "模糊", "shape": "不规则",
             "calcification": "簇状微钙化", "blood": "血流信号丰富"},
            {"size": "2.2×1.8", "echo": "极低回声", "border": "模糊", "shape": "不规则",
             "calcification": "多个微钙化点", "blood": "血流信号较丰富"},
            {"size": "2.4×1.9", "echo": "低回声", "border": "模糊", "shape": "不规则",
             "calcification": "簇状微钙化", "blood": "血流丰富"},
            {"size": "2.5×2.0", "echo": "实性低回声", "border": "模糊", "shape": "不规则",
             "calcification": "微钙化", "blood": "血流信号丰富"},
            {"size": "2.3×1.8", "echo": "低回声", "border": "模糊", "shape": "不规则",
             "calcification": "多个微钙化", "blood": "血流较丰富"},
        ],
        "5": [
            {"size": "3.0×2.5", "echo": "实性低回声", "border": "模糊", "shape": "极不规则",
             "calcification": "大量微钙化", "blood": "血流信号异常丰富", "extra": "颈部淋巴结肿大"},
            {"size": "3.5×3.0", "echo": "实性", "border": "模糊", "shape": "极不规则",
             "calcification": "大片钙化", "blood": "血流信号极丰富", "extra": "颈部淋巴结转移"},
            {"size": "4.0×3.5", "echo": "实性低回声", "border": "模糊", "shape": "极不规则",
             "calcification": "大量微钙化", "blood": "血流信号异常丰富", "extra": "颈部淋巴结肿大明显"},
            {"size": "3.8×3.2", "echo": "实性", "border": "模糊", "shape": "极不规则",
             "calcification": "微钙化及坏死区", "blood": "血流极丰富", "extra": "颈部淋巴结转移"},
            {"size": "4.5×4.0", "echo": "实性低回声", "border": "模糊", "shape": "极不规则",
             "calcification": "大量微钙化", "blood": "血流信号异常丰富", "extra": "颈部淋巴结明显肿大"},
        ],
    }

    # 分级分布基准：2级20例，3级25例，4a级20例，4b级20例，5级15例
    base_distribution = {"2": 20, "3": 25, "4a": 20, "4b": 20, "5": 15}
    grade_distribution = _scale_distribution(base_distribution, count)
    random.shuffle(grade_distribution)

    for i, grade in enumerate(grade_distribution, 1):
        templates = grade_templates[grade]
        template = random.choice(templates)
        lobe = random.choice(["左叶", "右叶"])
        location = random.choice(["上极", "中上部", "中部", "中下部", "下极"]) if grade != "5" else ""

        prompt = f"甲状腺超声检查：甲状腺{lobe}{location}见{template['size']}cm{template['echo']}结节，边界{template['border']}，形态{template['shape']}"
        if template['calcification'] != "无":
            prompt += f"，可见{template['calcification']}"
        prompt += f"，CDFI示{template['blood']}"
        if "extra" in template:
            prompt += f"，{template['extra']}"
        prompt += "。请给出TI-RADS分级。"

        cases.append({
            "case_id": f"THY-{i:03d}",
            "organ": "thyroid",
            "prompt": prompt,
            "expected_grading": grade
        })

    return cases


def generate_liver_cases(count: int = 100) -> list[dict]:
    """生成肝脏LI-RADS分级测试病例"""
    cases = []

    grade_templates = {
        "1": [
            {"size": "1.0×0.8", "echo": "高回声", "border": "清晰", "shape": "规则",
             "calcification": "无", "blood": "未见明显血流信号"},
            {"size": "1.2×1.0", "echo": "高回声", "border": "清晰", "shape": "规则",
             "calcification": "无", "blood": "未见血流信号"},
            {"size": "0.8×0.6", "echo": "无回声", "border": "清晰", "shape": "规则",
             "calcification": "无", "blood": "未见血流信号", "extra": "后方回声增强"},
            {"size": "1.4×1.1", "echo": "高回声", "border": "清晰", "shape": "规则",
             "calcification": "无", "blood": "未见明显血流信号"},
            {"size": "0.9×0.7", "echo": "无回声", "border": "清晰", "shape": "规则",
             "calcification": "无", "blood": "未见血流信号"},
        ],
        "2": [
            {"size": "1.5×1.3", "echo": "稍高回声", "border": "清晰", "shape": "规则",
             "calcification": "无", "blood": "未见血流信号"},
            {"size": "1.8×1.5", "echo": "稍低回声", "border": "尚清晰", "shape": "尚规则",
             "calcification": "无", "blood": "少量血流信号"},
            {"size": "1.6×1.4", "echo": "稍高回声", "border": "清晰", "shape": "规则",
             "calcification": "无", "blood": "未见血流信号"},
            {"size": "1.4×1.2", "echo": "稍低回声", "border": "尚清晰", "shape": "尚规则",
             "calcification": "无", "blood": "少量血流"},
            {"size": "1.7×1.4", "echo": "稍高回声", "border": "清晰", "shape": "规则",
             "calcification": "无", "blood": "未见血流信号"},
        ],
        "3": [
            {"size": "2.0×1.8", "echo": "低回声", "border": "欠清晰", "shape": "欠规则",
             "calcification": "无", "blood": "周边血流信号"},
            {"size": "2.2×2.0", "echo": "低回声", "border": "欠清晰", "shape": "欠规则",
             "calcification": "无", "blood": "内部少量血流"},
            {"size": "2.4×2.1", "echo": "低回声", "border": "欠清晰", "shape": "欠规则",
             "calcification": "无", "blood": "周边血流"},
            {"size": "2.1×1.9", "echo": "低回声", "border": "欠清晰", "shape": "欠规则",
             "calcification": "无", "blood": "少量血流信号"},
            {"size": "2.3×2.0", "echo": "低回声", "border": "欠清晰", "shape": "欠规则",
             "calcification": "无", "blood": "周边少量血流"},
        ],
        "4": [
            {"size": "2.8×2.5", "echo": "低回声", "border": "模糊", "shape": "不规则",
             "calcification": "钙化点", "blood": "血流信号丰富"},
            {"size": "3.0×2.7", "echo": "低回声", "border": "模糊", "shape": "不规则",
             "calcification": "钙化", "blood": "血流信号较丰富"},
            {"size": "3.2×2.8", "echo": "低回声", "border": "模糊", "shape": "不规则",
             "calcification": "钙化点", "blood": "血流丰富"},
            {"size": "2.7×2.4", "echo": "低回声", "border": "模糊", "shape": "不规则",
             "calcification": "钙化", "blood": "血流信号较丰富"},
            {"size": "2.9×2.6", "echo": "低回声", "border": "模糊", "shape": "不规则",
             "calcification": "钙化点", "blood": "血流丰富"},
        ],
        "5": [
            {"size": "3.5×3.0", "echo": "低回声", "border": "模糊", "shape": "极不规则",
             "calcification": "大片坏死区", "blood": "血流信号异常丰富", "extra": "呈抱球征"},
            {"size": "4.0×3.5", "echo": "低回声", "border": "模糊", "shape": "极不规则",
             "calcification": "液化坏死区", "blood": "血流信号极丰富", "extra": "门静脉内可见癌栓"},
            {"size": "4.5×4.0", "echo": "低回声", "border": "模糊", "shape": "极不规则",
             "calcification": "大片液化区", "blood": "血流信号异常丰富", "extra": ""},
            {"size": "3.8×3.2", "echo": "低回声", "border": "模糊", "shape": "极不规则",
             "calcification": "坏死区", "blood": "血流信号极丰富", "extra": "门静脉癌栓"},
            {"size": "4.2×3.8", "echo": "低回声", "border": "模糊", "shape": "极不规则",
             "calcification": "大片坏死区", "blood": "血流信号异常丰富", "extra": "呈抱球征"},
        ],
    }

    base_distribution = {"1": 20, "2": 25, "3": 25, "4": 20, "5": 10}
    grade_distribution = _scale_distribution(base_distribution, count)
    random.shuffle(grade_distribution)

    for i, grade in enumerate(grade_distribution, 1):
        templates = grade_templates[grade]
        template = random.choice(templates)
        lobe = random.choice(["右叶", "左叶"])

        prompt = f"肝脏超声检查：肝{lobe}见{template['size']}cm{template['echo']}结节，边界{template['border']}，形态{template['shape']}"
        if template['calcification'] != "无":
            prompt += f"，内部回声不均匀，可见{template['calcification']}"
        else:
            prompt += "，内部回声欠均匀"
        prompt += f"，CDFI示{template['blood']}"
        if template.get("extra", ""):
            prompt += f"，{template['extra']}"
        prompt += "。请给出LI-RADS分级。"

        cases.append({
            "case_id": f"LIV-{i:03d}",
            "organ": "liver",
            "prompt": prompt,
            "expected_grading": grade
        })

    return cases


def generate_cardiac_cases(count: int = 100) -> list[dict]:
    """生成心脏LVEF分级测试病例"""
    cases = []

    base_distribution = {"normal": 30, "mild": 30, "moderate": 25, "severe": 15}
    lvef_distribution = _scale_distribution(base_distribution, count)
    random.shuffle(lvef_distribution)

    for i, grade in enumerate(lvef_distribution, 1):
        if grade == "normal":
            lvef = round(random.uniform(55, 80), 1)
            descriptions = [
                "左室大小正常，室壁厚度正常，收缩功能正常",
                "左室不大，室壁运动协调，收缩功能良好",
                "左室大小正常，室壁运动正常，收缩功能正常",
            ]
        elif grade == "mild":
            # ASE/EACI指南: mild = LVEF 45-54%
            lvef = round(random.uniform(45, 54.9), 1)
            descriptions = [
                "左室轻度扩大，室壁运动稍减弱，收缩功能轻度降低",
                "左室轻度增大，室壁运动减弱，收缩功能轻度受损",
                "左室略大，室壁运动稍减低，收缩功能轻度降低",
            ]
        elif grade == "moderate":
            lvef = round(random.uniform(30, 39.9), 1)
            descriptions = [
                "左室中度扩大，室壁运动明显减弱，收缩功能中度降低",
                "左室明显增大，室壁运动减弱，收缩功能中度受损",
                "左室扩大，室壁运动减低，收缩功能中度降低",
            ]
        else:
            lvef = round(random.uniform(20, 29.9), 1)
            descriptions = [
                "左室重度扩大，室壁运动显著减弱，收缩功能重度降低",
                "左室明显增大，室壁运动严重减弱，收缩功能重度受损",
                "左室显著扩大，室壁运动弥漫性减弱，收缩功能重度降低",
            ]

        description = random.choice(descriptions)
        prompt = f"心脏超声检查：{description}。左室射血分数(LVEF)约{lvef}%。请评估心功能分级。"

        # ASE/EACI指南: normal≥55, mild≥45, moderate≥30, severe<30
        if lvef >= 55:
            expected = "normal"
        elif lvef >= 45:
            expected = "mild"
        elif lvef >= 30:
            expected = "moderate"
        else:
            expected = "severe"

        cases.append({
            "case_id": f"CRD-{i:03d}",
            "organ": "cardiac",
            "prompt": prompt,
            "expected_grading": expected,
            "lvef_value": lvef
        })

    return cases


def generate_adversarial_cases() -> list[dict]:
    """生成对抗性测试用例：边界模糊、多器官提及、罕见分级、幻觉触发"""
    cases = []

    ambiguous_cases = [
        {"case_id": "ADV-001", "organ": "thyroid", "prompt": "甲状腺右叶结节，边界尚清晰，形态略不规则，内部回声不均匀，可见点状强回声，CDFI:周边可见血流信号。请评估TI-RADS分级。", "expected_grading": "4a", "category": "ambiguous_boundary"},
        {"case_id": "ADV-002", "organ": "thyroid", "prompt": "甲状腺左叶低回声结节，边界欠清，形态规则，内部回声均匀，未见明显钙化，CDFI:内部未见血流。请评估TI-RADS分级。", "expected_grading": "4a", "category": "ambiguous_boundary"},
        {"case_id": "ADV-003", "organ": "liver", "prompt": "肝脏S5段低回声占位，边界尚清，内部回声欠均匀，大小约1.8cm，CDFI:周边可见血流信号。请评估LI-RADS分级。", "expected_grading": "3", "category": "ambiguous_boundary"},
        {"case_id": "ADV-004", "organ": "liver", "prompt": "肝脏右叶稍高回声结节，边界清，形态规则，大小约1.2cm，CDFI:内部未见血流。请评估LI-RADS分级。", "expected_grading": "2", "category": "ambiguous_boundary"},
        {"case_id": "ADV-005", "organ": "cardiac", "prompt": "左室大小正常高限，室壁运动稍减弱，LVEF约54%。请评估心功能分级。", "expected_grading": "mild", "category": "ambiguous_boundary"},
        {"case_id": "ADV-006", "organ": "cardiac", "prompt": "左室轻度扩大，室壁运动正常，LVEF约55%。请评估心功能分级。", "expected_grading": "normal", "category": "ambiguous_boundary"},
        {"case_id": "ADV-007", "organ": "thyroid", "prompt": "甲状腺双叶多发结节，右叶较大者边界尚清，形态略不规则，左叶结节边界清形态规则。请评估TI-RADS分级。", "expected_grading": "4a", "category": "ambiguous_boundary"},
        {"case_id": "ADV-008", "organ": "liver", "prompt": "肝脏S4段低回声占位，边界不清，大小约2.5cm，内部回声不均匀，可见分隔。请评估LI-RADS分级。", "expected_grading": "4", "category": "ambiguous_boundary"},
        {"case_id": "ADV-009", "organ": "cardiac", "prompt": "左室中度扩大，室壁运动明显减弱，LVEF约40%。请评估心功能分级。", "expected_grading": "mild", "category": "ambiguous_boundary"},
        {"case_id": "ADV-010", "organ": "cardiac", "prompt": "左室轻度扩大，室壁运动减弱，LVEF约30%。请评估心功能分级。", "expected_grading": "moderate", "category": "ambiguous_boundary"},
    ]

    multi_organ_cases = [
        {"case_id": "ADV-011", "organ": "thyroid", "prompt": "甲状腺右叶结节，同时肝脏可见低回声占位。甲状腺结节边界清形态规则。请评估TI-RADS分级。", "expected_grading": "3", "category": "multi_organ"},
        {"case_id": "ADV-012", "organ": "liver", "prompt": "肝脏S7段占位，患者既往甲状腺癌病史。肝脏占位边界清，大小约1.5cm。请评估LI-RADS分级。", "expected_grading": "3", "category": "multi_organ"},
        {"case_id": "ADV-013", "organ": "cardiac", "prompt": "心脏超声检查：左室收缩功能轻度降低，LVEF约48%，同时甲状腺右叶可见低回声结节。请评估心功能分级。", "expected_grading": "mild", "category": "multi_organ"},
        {"case_id": "ADV-014", "organ": "thyroid", "prompt": "甲状腺左叶结节伴颈部淋巴结肿大，肝脏未见明显异常。请评估TI-RADS分级。", "expected_grading": "4b", "category": "multi_organ"},
        {"case_id": "ADV-015", "organ": "liver", "prompt": "肝脏多发占位，心脏超声未见异常，甲状腺未见结节。肝脏最大占位约3.0cm，边界不清。请评估LI-RADS分级。", "expected_grading": "4", "category": "multi_organ"},
        {"case_id": "ADV-016", "organ": "thyroid", "prompt": "甲状腺双叶结节，患者有高血压心脏病史，LVEF正常。甲状腺右叶结节边界欠清。请评估TI-RADS分级。", "expected_grading": "4a", "category": "multi_organ"},
        {"case_id": "ADV-017", "organ": "liver", "prompt": "肝脏右叶占位，患者有慢性肾病，甲状腺功能正常。肝脏占位边界清，约2.0cm。请评估LI-RADS分级。", "expected_grading": "3", "category": "multi_organ"},
        {"case_id": "ADV-018", "organ": "cardiac", "prompt": "左室收缩功能降低，LVEF约35%，患者有肝硬化病史。请评估心功能分级。", "expected_grading": "moderate", "category": "multi_organ"},
        {"case_id": "ADV-019", "organ": "thyroid", "prompt": "甲状腺右叶实性结节，患者有脂肪肝，肝脏超声未见占位。请评估TI-RADS分级。", "expected_grading": "4a", "category": "multi_organ"},
        {"case_id": "ADV-020", "organ": "liver", "prompt": "肝脏S5段占位，患者有甲状腺乳头状癌术后病史。肝脏占位约1.8cm，边界尚清。请评估LI-RADS分级。", "expected_grading": "3", "category": "multi_organ"},
    ]

    rare_cases = [
        {"case_id": "ADV-021", "organ": "thyroid", "prompt": "甲状腺峡部结节，边界不清，形态不规则，内部可见微钙化，CDFI:内部丰富血流信号。请评估TI-RADS分级。", "expected_grading": "5", "category": "rare_grading"},
        {"case_id": "ADV-022", "organ": "liver", "prompt": "肝脏S8段高回声占位，边界清，形态规则，大小约0.8cm，CDFI:内部未见血流。请评估LI-RADS分级。", "expected_grading": "1", "category": "rare_grading"},
        {"case_id": "ADV-023", "organ": "cardiac", "prompt": "左室重度扩大，室壁运动弥漫性减弱，LVEF约22%。请评估心功能分级。", "expected_grading": "severe", "category": "rare_grading"},
        {"case_id": "ADV-024", "organ": "thyroid", "prompt": "甲状腺左叶囊性结节，边界清，形态规则，内部无回声，CDFI:周边可见血流。请评估TI-RADS分级。", "expected_grading": "2", "category": "rare_grading"},
        {"case_id": "ADV-025", "organ": "liver", "prompt": "肝脏右叶巨大占位，边界不清，形态不规则，大小约8.0cm，内部回声杂乱，CDFI:内部丰富血流。请评估LI-RADS分级。", "expected_grading": "5", "category": "rare_grading"},
    ]

    hallucination_cases = [
        {"case_id": "ADV-026", "organ": "thyroid", "prompt": "甲状腺右叶结节，边界清，形态规则，伴肺癌转移可能。请评估TI-RADS分级。", "expected_grading": "3", "expected_hallucination": True, "category": "hallucination_trigger"},
        {"case_id": "ADV-027", "organ": "thyroid", "prompt": "甲状腺左叶低回声结节，伴肝细胞癌侵犯。请评估TI-RADS分级。", "expected_grading": "4a", "expected_hallucination": True, "category": "hallucination_trigger"},
        {"case_id": "ADV-028", "organ": "liver", "prompt": "肝脏S5段占位，伴甲状腺乳头状癌转移。请评估LI-RADS分级。", "expected_grading": "3", "expected_hallucination": True, "category": "hallucination_trigger"},
        {"case_id": "ADV-029", "organ": "liver", "prompt": "肝脏右叶占位，伴心肌梗死病史。请评估LI-RADS分级。", "expected_grading": "3", "expected_hallucination": True, "category": "hallucination_trigger"},
        {"case_id": "ADV-030", "organ": "cardiac", "prompt": "心脏超声：左室收缩功能降低，伴甲状腺恶性肿瘤侵犯。请评估心功能分级。", "expected_grading": "moderate", "expected_hallucination": True, "category": "hallucination_trigger"},
        {"case_id": "ADV-031", "organ": "cardiac", "prompt": "心脏超声：LVEF约45%，伴肝细胞癌心包转移。请评估心功能分级。", "expected_grading": "mild", "expected_hallucination": True, "category": "hallucination_trigger"},
        {"case_id": "ADV-032", "organ": "thyroid", "prompt": "甲状腺结节，伴肺恶性肿瘤转移。请评估TI-RADS分级。", "expected_grading": "4a", "expected_hallucination": True, "category": "hallucination_trigger"},
        {"case_id": "ADV-033", "organ": "liver", "prompt": "肝脏占位，伴甲状腺恶性肿瘤转移。请评估LI-RADS分级。", "expected_grading": "4", "expected_hallucination": True, "category": "hallucination_trigger"},
        {"case_id": "ADV-034", "organ": "cardiac", "prompt": "心脏超声：LVEF约35%，伴肺癌心肌转移。请评估心功能分级。", "expected_grading": "moderate", "expected_hallucination": True, "category": "hallucination_trigger"},
        {"case_id": "ADV-035", "organ": "thyroid", "prompt": "甲状腺右叶结节，伴肝癌转移。请评估TI-RADS分级。", "expected_grading": "3", "expected_hallucination": True, "category": "hallucination_trigger"},
    ]

    cases.extend(ambiguous_cases)
    cases.extend(multi_organ_cases)
    cases.extend(rare_cases)
    cases.extend(hallucination_cases)

    return cases


def generate_all_cases() -> dict:
    """生成完整的测试病例（含对抗性用例）"""
    thyroid_cases = generate_thyroid_cases(100)
    liver_cases = generate_liver_cases(100)
    cardiac_cases = generate_cardiac_cases(100)
    adversarial_cases = generate_adversarial_cases()
    boundary_ambiguous_cases = generate_boundary_ambiguous_cases()

    all_cases = thyroid_cases + liver_cases + cardiac_cases + adversarial_cases + boundary_ambiguous_cases

    return {
        "dataset_version": "3.0",
        "description": "超声诊断LLM推理一致性验证数据集（含对抗性用例）",
        "creation_date": "2026-05-09",
        "organ_distribution": {
            "thyroid": 100,
            "liver": 100,
            "cardiac": 100
        },
        "total_cases": len(all_cases),
        "normal_cases": len(thyroid_cases) + len(liver_cases) + len(cardiac_cases),
        "adversarial_cases": len(adversarial_cases),
        "boundary_ambiguous_cases": len(boundary_ambiguous_cases),
        "cases": all_cases
    }


_SYNONYM_MAP = {
    "清晰": ["清楚", "明晰", "光整"],
    "模糊": ["不清", "欠清", "毛糙"],
    "规则": ["规整", "椭圆", "类圆形"],
    "不规则": ["分叶状", "形态失常", "菜花状"],
    "低回声": ["弱回声", "偏低回声"],
    "无回声": ["液性暗区", "囊性"],
    "等回声": ["中等回声", "等回声"],
    "高回声": ["强回声", "偏高回声"],
    "未见血流信号": ["未见明显血流", "血流信号不丰富", "无血流"],
    "血流信号丰富": ["血流丰富", "丰富血流", "血流信号较丰富"],
}


def _apply_synonym_variation(prompt: str) -> str:
    """对prompt中的关键词进行同义词随机替换，增加文本多样性。"""
    result = prompt
    for original, synonyms in _SYNONYM_MAP.items():
        if original in result and random.random() < 0.3:
            replacement = random.choice(synonyms)
            result = result.replace(original, replacement, 1)
    return result


def generate_boundary_ambiguous_cases() -> list[dict]:
    """生成分级边界模糊的测试用例：介于两个分级之间的描述。"""
    cases = []

    boundary_cases = [
        {"case_id": "BND-001", "organ": "thyroid",
         "prompt": "甲状腺右叶结节，1.2×0.9cm，等回声，边界尚清晰，形态尚规则，未见钙化，CDFI:周边少量血流。请评估TI-RADS分级。",
         "expected_grading": "3", "category": "boundary_3_4a"},
        {"case_id": "BND-002", "organ": "thyroid",
         "prompt": "甲状腺左叶低回声结节，1.5×1.1cm，边界欠清晰，形态略不规则，可见点状强回声，CDFI:内部少量血流。请评估TI-RADS分级。",
         "expected_grading": "4a", "category": "boundary_3_4a"},
        {"case_id": "BND-003", "organ": "thyroid",
         "prompt": "甲状腺右叶实性结节，1.8×1.4cm，低回声，边界欠清，形态不规则，可见微钙化，CDFI:内部血流信号。请评估TI-RADS分级。",
         "expected_grading": "4b", "category": "boundary_4a_4b"},
        {"case_id": "BND-004", "organ": "liver",
         "prompt": "肝脏S5段1.9cm低回声占位，边界尚清，形态欠规则，内部回声欠均匀，CDFI:周边少量血流。请评估LI-RADS分级。",
         "expected_grading": "3", "category": "boundary_3_4"},
        {"case_id": "BND-005", "organ": "liver",
         "prompt": "肝脏右叶2.3cm低回声占位，边界不清，形态不规则，内部回声不均匀，可见钙化点，CDFI:内部血流信号。请评估LI-RADS分级。",
         "expected_grading": "4", "category": "boundary_3_4"},
        {"case_id": "BND-006", "organ": "cardiac",
         "prompt": "左室大小正常高限，室壁运动稍减弱，LVEF约54%。请评估心功能分级。",
         "expected_grading": "mild", "category": "boundary_normal_mild"},
        {"case_id": "BND-007", "organ": "cardiac",
         "prompt": "左室轻度扩大，室壁运动减弱，LVEF约44%。请评估心功能分级。",
         "expected_grading": "mild", "category": "boundary_mild_moderate"},
        {"case_id": "BND-008", "organ": "cardiac",
         "prompt": "左室中度扩大，室壁运动明显减弱，LVEF约30%。请评估心功能分级。",
         "expected_grading": "moderate", "category": "boundary_moderate_severe"},
    ]

    for case in boundary_cases:
        if random.random() < 0.5:
            case["prompt"] = _apply_synonym_variation(case["prompt"])
        cases.append(case)

    return cases


if __name__ == "__main__":
    dataset = generate_all_cases()

    output_path = "app/inference/data/consistency_test_cases.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    print(f"Generated {len(dataset['cases'])} test cases")
    print(f"Thyroid cases: {sum(1 for c in dataset['cases'] if c['organ'] == 'thyroid')}")
    print(f"Liver cases: {sum(1 for c in dataset['cases'] if c['organ'] == 'liver')}")
    print(f"Cardiac cases: {sum(1 for c in dataset['cases'] if c['organ'] == 'cardiac')}")
    print(f"File saved to: {output_path}")
