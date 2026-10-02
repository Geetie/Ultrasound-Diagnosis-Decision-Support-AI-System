"""构建 UDT-ICD10 映射表（底表检索 + 医学关键词匹配）。"""
from __future__ import annotations

import json
from pathlib import Path

ICD_PATH = Path("data/medical_resources/icd10/icd10_clinical_v2.json")
OUT_PATH = Path("data/medical_resources/udt_icd_map.json")

UDT_KEYWORDS = {
    "thyroid_hyperthyroidism": ["甲状腺毒症", "甲状腺功能亢进"],
    "thyroid_toxic_diffuse_goiter": ["弥漫性毒性甲状腺肿", "毒性弥漫性甲状腺肿"],
    "thyroid_toxic_nodular_goiter": ["毒性结节性甲状腺肿", "毒性结节"],
    "thyroid_toxic_adenoma": ["毒性腺瘤"],
    "thyroid_hypothyroidism": ["甲状腺功能减退", "甲状腺功能低下"],
    "thyroid_primary_hypothyroidism": ["原发性甲状腺功能减退"],
    "thyroid_secondary_hypothyroidism": ["继发性甲状腺功能减退"],
    "thyroid_hashimoto": ["桥本", "慢性淋巴细胞性甲状腺炎"],
    "cardiac_arrhythmia": ["心律失常"],
    "cardiac_atrial_fibrillation": ["心房纤颤", "心房颤动", "房颤"],
    "cardiac_tachycardia": ["心动过速"],
    "cardiac_bradycardia": ["心动过缓"],
    "cardiac_ventricular_arrhythmia": ["室性心动过速", "室性心律失常"],
    "cardiac_pericardial_effusion": ["心包积液"],
    "cardiac_right_heart_failure": ["右心衰竭"],
    "cardiac_right_heart_failure_acute": ["急性右心衰竭"],
    "cardiac_right_heart_failure_chronic": ["慢性右心衰竭"],
    "cardiac_tricuspid_regurgitation": ["三尖瓣关闭不全", "三尖瓣反流"],
    "cardiac_tricuspid_regurgitation_mild": ["三尖瓣关闭不全"],
    "cardiac_tricuspid_regurgitation_moderate": ["三尖瓣关闭不全"],
    "cardiac_tricuspid_regurgitation_severe": ["三尖瓣关闭不全"],
    "cardiac_liver_congestion": ["心源性肝硬化", "淤血性肝硬化", "肝淤血"],
    "liver_cirrhosis": ["肝硬化"],
    "liver_alcoholic_cirrhosis": ["酒精性肝硬化"],
    "liver_biliary_cirrhosis": ["胆汁性肝硬化"],
    "liver_post_hepatitic_cirrhosis": ["肝炎后肝硬化"],
    "liver_congestion": ["肝淤血", "淤血肝"],
    "liver_congestion_mild": ["肝淤血", "淤血肝"],
    "liver_congestion_severe": ["肝淤血", "淤血性肝硬化"],
    "liver_autoimmune_hepatitis": ["自身免疫性肝炎"],
}
def main():
    icd = json.loads(ICD_PATH.read_text(encoding="utf-8"))
    print("ICD-10 base:", len(icd))
    mapping = {}
    exact = multi = missing = 0
    # 否定词/特指排除：防止子串误匹配（"毒性"误中"非毒性"）与
    # 特指亚型（地方性/先天性）抢走通用码
    NEGATIVE = {"thyroid_toxic_diffuse_goiter": ["非毒性"],
                "thyroid_toxic_nodular_goiter": ["非毒性"],
                "thyroid_toxic_adenoma": ["非毒性"]}
    SPECIFIC = ["地方性", "先天性", "呆小病", "新生儿"]
    for udt, keywords in UDT_KEYWORDS.items():
        candidates = []
        for kw in keywords:
            candidates = [e for e in icd if kw in e["name"]]
            if candidates:
                break
        neg = NEGATIVE.get(udt, [])
        if neg:
            candidates = [c for c in candidates
                          if not any(n in c["name"] for n in neg)]
        if len(candidates) > 1:
            generic = [c for c in candidates
                       if not any(s in c["name"] for s in SPECIFIC)]
            if generic:
                candidates = generic
            candidates.sort(key=lambda c: len(c["name"]))
        if not candidates:
            mapping[udt] = {"confidence": "missing", "candidates": []}
            missing += 1
        elif len(candidates) == 1:
            mapping[udt] = {"confidence": "exact", "icd10": candidates[0]["code"],
                            "name": candidates[0]["name"], "candidates": candidates}
            exact += 1
        else:
            mapping[udt] = {"confidence": "multi", "icd10": candidates[0]["code"],
                            "name": candidates[0]["name"], "candidates": candidates[:5]}
            multi += 1
    # 医学人工补录（底表无对应名称，按临床编码惯例手工指定）
    MANUAL = {
        "thyroid_toxic_adenoma": {
            "icd10": "E05.100", "name": "甲状腺毒症伴有毒性单个甲状腺结节",
            "confidence": "manual",
        },
        "cardiac_right_heart_failure_chronic": {
            "icd10": "I50.900", "name": "心力衰竭（底表无右心衰独立码，保守映射）",
            "confidence": "manual_pending_review",
        },
    }
    for udt, entry in MANUAL.items():
        mapping[udt] = entry
    report = {"total": len(UDT_KEYWORDS), "exact": exact, "multi": multi,
              "missing": missing, "mapping": mapping}
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"exact={exact} multi={multi} missing={missing}")


if __name__ == "__main__":
    main()
