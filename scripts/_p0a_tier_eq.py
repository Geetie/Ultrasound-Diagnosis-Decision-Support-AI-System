"""0A-Tech-P0 业务结论等价率分析：从 phase0a_retest_v2.json 的 780 例明细，
按各器官"临床处置分层"（3 档）计算两引擎的跨栈业务等价率——供口径决策用。

分层（可辩护的三档口径，源分级体系见 grading_definitions.py）：
- thyroid: 随访 {0,1,2,3} / 穿刺评估 {4a,4b} / 积极处置 {4c,5}
- liver:   随访 {0,1} / 评估 {2,3} / 积极处置 {4,5,lr-tr}
- cardiac: 随访 {normal,mild} / 药物评估 {moderate} / 积极干预 {severe}
"""
import json
from collections import Counter

TIERS = {
    "thyroid": ({"0", "1", "2", "3"}, {"4a", "4b"}, {"4c", "5"}),
    "liver": ({"0", "1"}, {"2", "3"}, {"4", "5", "lr-tr"}),
    "cardiac": ({"normal", "mild"}, {"moderate"}, {"severe"}),
}


def tier(organ: str, grade: str | None) -> int | None:
    if not grade:
        return None
    for i, s in enumerate(TIERS.get(organ, (frozenset(),) * 3)):
        if grade.strip().lower() in s:
            return i
    return None


r = json.load(open("/mnt/ee16/home/student03/uai/src/reports/phase0a_retest_v2.json"))
rows = r["case_rows"]
n = len(rows)
same_tier = 0
adjacent = 0
two_tier_off = 0
none_b = 0
by_organ = Counter()
by_organ_total = Counter()
grade_diff_only = 0

for row in rows:
    organ = row["organ"]
    ta = tier(organ, row["grading_a"])
    tb = tier(organ, row["grading_b"])
    if tb is None:
        none_b += 1
        continue
    if ta is None:
        continue
    by_organ_total[organ] += 1
    d = abs(ta - tb)
    if d == 0:
        same_tier += 1
        by_organ[organ] += 1
    elif d == 1:
        adjacent += 1
    else:
        two_tier_off += 1
    if not row["consistent"]:
        grade_diff_only += 1

valid = (by_organ_total and sum(by_organ_total.values())) or 1
print(f"total={n} valid={sum(by_organ_total.values())} (B 侧无分级 {none_b})")
print(f"同处置层: {same_tier} = {same_tier / valid:.2%}")
print(f"相邻层(±1): {adjacent} = {adjacent / valid:.2%}")
print(f"跨两层: {two_tier_off} = {two_tier_off / valid:.2%}")
for organ in by_organ_total:
    print(f"  {organ}: {by_organ[organ]}/{by_organ_total[organ]} = {by_organ[organ] / by_organ_total[organ]:.2%}")
print(f"分级不一致但业务等价的救回: {grade_diff_only} 例不一致中 {grade_diff_only - (len(rows) - same_tier)} ...")
