"""构造双引擎一致性评测的分层用例集（≥700 例：常规≥200 + 边界≥400 + 高风险≥100）。

用法（在真机 ~/uai/src 下）：
    python scripts/server_build_eval_cases.py --output app/inference/data/consistency_test_cases.json

分层口径（对齐 07-数据标注与模型评估.md 的样本量要求）：
- 常规：复用 scripts/generate_test_cases.py 的模板（3 器官 × 80）
- 边界：每个器官的相邻分级对，特征取两档交界处的混合描述（category=boundary_*），
  一致性口径只看两引擎是否落在同一侧，expected_grading 取较低档
- 高风险：矛盾特征/危急值类提示（is_high_risk=True），兼幻觉触发

注意（对齐 grading_definitions.py 的抽取口径）：
- thyroid prompt 用 "请给出TI-RADS分级。"，分级取值域 {2,3,4a,4b,5}
- liver 用 "请给出LI-RADS分级。"，取值域 {1,2,3,4,5}
- cardiac 用 "请评估心功能分级。"，取值域 {normal,mild,moderate,severe}，prompt 带 LVEF 数值
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

from generate_test_cases import (  # noqa: E402
    generate_cardiac_cases,
    generate_liver_cases,
    generate_thyroid_cases,
)

rng = random.Random(20260929)

# ---------------------------------------------------------------------------
# 边界用例（特征取相邻分级的交界描述）
# ---------------------------------------------------------------------------
_THY_FEATURES = {
    "2": ["等回声结节", "边界清晰", "形态规则", "无钙化", "纵横比小于一"],
    "3": ["低回声结节", "边界清晰", "形态规则", "无钙化", "纵横比小于一"],
    "4a": ["低回声结节", "边界欠清晰", "形态欠规则", "无钙化", "纵横比小于一"],
    "4b": ["低回声结节", "边界模糊", "形态欠规则", "微钙化", "纵横比大于一"],
    "5": ["极低回声结节", "边界模糊", "形态不规则", "微钙化", "纵横比大于一"],
}
_THY_PAIRS = [("2", "3"), ("3", "4a"), ("4a", "4b"), ("4b", "5")]
_THY_SIZES = ["0.6×0.5cm", "0.9×0.8cm", "1.2×1.0cm", "1.6×1.4cm", "2.1×1.8cm"]

_LIVER_FEATURES = {
    "1": ["无病灶", "肝实质回声均匀"],
    "2": ["直径<1cm 的均匀高回声结节", "边界清晰"],
    "3": ["直径 2cm 的稍低回声结节", "边界尚清", "周边无声晕"],
    "4": ["直径 3cm 的低回声结节", "边界欠清", "周边可见低回声晕"],
    "5": ["直径 5cm 的不均匀回声肿块", "边界不清", "侵及周围血管"],
}
_LIVER_PAIRS = [("1", "2"), ("2", "3"), ("3", "4"), ("4", "5")]
_LIVER_EXTRA = ["动脉期未见明显强化", "动脉期可见结节状强化", "门脉期强化减退", "可见粗大流入血流信号"]


def _thyroid_boundary_cases(per_pair: int) -> list[dict]:
    cases: list[dict] = []
    idx = 1
    for lower, upper in _THY_PAIRS:
        for _ in range(per_pair):
            low_feats = rng.sample(_THY_FEATURES[lower], 3)
            high_feats = rng.sample(_THY_FEATURES[upper], 2)
            feats = list(dict.fromkeys(low_feats + high_feats))
            size = rng.choice(_THY_SIZES)
            lobe = rng.choice(["左叶", "右叶"])
            position = rng.choice(["上极", "中份", "下极"])
            prompt = (
                f"甲状腺超声检查：甲状腺{lobe}{position}见{size}{feats[0]}，"
                f"{'，'.join(feats[1:])}，CDFI示内部血流信号{rng.choice(['不明显', '可见', '较丰富'])}。"
                f"请给出TI-RADS分级。"
            )
            cases.append(
                {
                    "case_id": f"BND-THY-{lower.replace('a', 'A').replace('b', 'B')}{upper}{idx:03d}",
                    "organ": "thyroid",
                    "prompt": prompt,
                    "expected_grading": lower,
                    "category": f"boundary_{lower}_{upper}",
                }
            )
            idx += 1
    return cases


def _liver_boundary_cases(per_pair: int) -> list[dict]:
    cases: list[dict] = []
    idx = 1
    for lower, upper in _LIVER_PAIRS:
        for _ in range(per_pair):
            low_feats = rng.sample(_LIVER_FEATURES[lower], min(2, len(_LIVER_FEATURES[lower])))
            high_feats = rng.sample(_LIVER_FEATURES[upper], 2)
            feats = list(dict.fromkeys(low_feats + high_feats))
            extra = rng.choice(_LIVER_EXTRA)
            prompt = (
                f"肝脏超声检查：肝{rng.choice(['左叶', '右叶', '尾状叶'])}可见"
                f"{'，'.join(feats)}，{extra}。请给出LI-RADS分级。"
            )
            cases.append(
                {
                    "case_id": f"BND-LIV-{lower}{upper}{idx:03d}",
                    "organ": "liver",
                    "prompt": prompt,
                    "expected_grading": lower,
                    "category": f"boundary_{lower}_{upper}",
                }
            )
            idx += 1
    return cases


def _cardiac_boundary_cases(per_pair: int) -> list[dict]:
    # LVEF 阈值（ASE 2015 / verification_config）：≥55 normal，≥45 mild，≥30 moderate，<30 severe
    pairs = [("normal", "mild", 55.0), ("mild", "moderate", 45.0), ("moderate", "severe", 30.0)]
    cases: list[dict] = []
    idx = 1
    for lower, upper, threshold in pairs:
        for _ in range(per_pair):
            # LVEF 在阈值附近 ±1.5 内抖动，制造边界模糊
            lvef = round(rng.uniform(threshold - 1.5, threshold + 1.5), 1)
            if lvef >= 55:
                desc = "左室壁运动未见明显异常，收缩功能正常"
            elif lvef >= 45:
                desc = "左室壁运动稍减弱，收缩功能轻度降低"
            elif lvef >= 30:
                desc = "左室壁运动普遍减弱，收缩功能中度降低"
            else:
                desc = "左室壁运动明显减弱，收缩功能重度降低"
            prompt = (
                f"心脏超声检查：{desc}。左室射血分数(LVEF)约{lvef}%。请评估心功能分级。"
            )
            cases.append(
                {
                    "case_id": f"BND-CRD-{lower[:3].upper()}{upper[:3].upper()}{idx:03d}",
                    "organ": "cardiac",
                    "prompt": prompt,
                    "expected_grading": lower,
                    "lvef_value": lvef,
                    "category": f"boundary_{lower}_{upper}",
                }
            )
            idx += 1
    return cases


# ---------------------------------------------------------------------------
# 高风险用例（矛盾特征 / 危急值，is_high_risk=True）
# ---------------------------------------------------------------------------
_HR_TEMPLATES = [
    "甲状腺超声检查：{lobe}见{size}低回声结节，纵横比大于一，内见微钙化，{trap}。请给出TI-RADS分级。",
    "甲状腺超声检查：{lobe}见{size}结节，报告描述{trap}，结节边界模糊伴形态不规则。请给出TI-RADS分级。",
    "肝脏超声检查：肝{lobe}见{size}肿块，{trap}，周边可见低回声晕。请给出LI-RADS分级。",
    "肝脏超声检查：{trap}；肝内胆管未见明显扩张。请给出LI-RADS分级。",
    "心脏超声检查：{trap}；左室射血分数(LVEF)约{lvef}%。请评估心功能分级。",
    "心脏超声检查：大量心包积液（液暗区宽约{eff}mm），{trap}。请评估心功能分级并给出处理建议。",
]
_TRAPS = [
    "报告前文称结节内未见钙化但后文描述可见微钙化",
    "报告描述病灶边界清晰但测量值提示边界不规则",
    "检查描述为低回声而结论部分写为高回声",
    "报告称血流信号不明显但频谱多普勒提示高速动脉血流",
    "前后两次测量值差异明显（首次2.1cm，复测1.3cm）",
    "报告引用了未在本例进行的增强检查结论",
]


def _high_risk_cases(count: int) -> list[dict]:
    cases: list[dict] = []
    for i in range(count):
        tpl = _HR_TEMPLATES[i % len(_HR_TEMPLATES)]
        trap = rng.choice(_TRAPS)
        if "TI-RADS" in tpl:
            prompt = tpl.format(
                lobe=rng.choice(["左叶", "右叶"]),
                size=rng.choice(_THY_SIZES),
                trap=trap,
            )
            organ, expected = "thyroid", rng.choice(["4a", "4b"])
        elif "LI-RADS" in tpl:
            prompt = tpl.format(
                lobe=rng.choice(["左叶", "右叶", "尾状叶"]),
                size=rng.choice(["2.5cm", "3.8cm", "6.2cm"]),
                trap=trap,
            )
            organ, expected = "liver", rng.choice(["3", "4"])
        else:
            prompt = tpl.format(
                trap=trap,
                lvef=rng.choice([28.0, 33.5, 44.0, 52.0, 58.0]),
                eff=rng.choice([8.0, 15.0, 25.0]),
            )
            organ, expected = "cardiac", rng.choice(["mild", "moderate", "severe"])
        cases.append(
            {
                "case_id": f"HR-{organ[:3].upper()}-{i + 1:03d}",
                "organ": organ,
                "prompt": prompt,
                "expected_grading": expected,
                "is_high_risk": True,
                "category": "high_risk_contradiction",
            }
        )
    return cases


def build_dataset(
    regular_per_organ: int = 80,
    boundary_per_pair: int = 40,
    high_risk_count: int = 100,
) -> dict:
    regular = (
        generate_thyroid_cases(regular_per_organ)
        + generate_liver_cases(regular_per_organ)
        + generate_cardiac_cases(regular_per_organ)
    )
    boundary = (
        _thyroid_boundary_cases(boundary_per_pair)
        + _liver_boundary_cases(boundary_per_pair)
        + _cardiac_boundary_cases(boundary_per_pair)
    )
    high_risk = _high_risk_cases(high_risk_count)
    cases = regular + boundary + high_risk

    organ_distribution: dict[str, int] = {}
    for c in cases:
        organ_distribution[c["organ"]] = organ_distribution.get(c["organ"], 0) + 1

    return {
        "dataset_version": "4.0-stratified",
        "stratification": {
            "regular": len(regular),
            "boundary": len(boundary),
            "high_risk": len(high_risk),
        },
        "organ_distribution": organ_distribution,
        "total_cases": len(cases),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="构造分层一致性评测用例集")
    parser.add_argument(
        "--output",
        default="app/inference/data/consistency_test_cases.json",
    )
    parser.add_argument("--regular-per-organ", type=int, default=80)
    parser.add_argument("--boundary-per-pair", type=int, default=40)
    parser.add_argument("--high-risk-count", type=int, default=100)
    args = parser.parse_args()

    dataset = build_dataset(args.regular_per_organ, args.boundary_per_pair, args.high_risk_count)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"total={dataset['total_cases']}  stratification={dataset['stratification']}")
    print(f"organ_distribution={dataset['organ_distribution']}")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
