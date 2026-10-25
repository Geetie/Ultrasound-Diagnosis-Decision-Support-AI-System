"""A.91 修 `patient_name` 规则：把"任意 2-4 汉字"收紧为"姓氏白名单 + 谓词排除"。

## 为什么必须改（真机可复现的因果链）

1. `desensitization_rules.yaml` 的 `patient_name` 原 pattern：
   `((?:患者…|病人)\\s*)([\\u4e00-\\u9fa5]{2,4})`
   —— 第二段是裸的"任意 2-4 个汉字"，紧跟"患者："的句子都会被当姓名。

2. 真机现场（L2 融合层脱敏，`app/fusion/desensitization.py` 复用标准化引擎）：
   ```
   输入：… 证据: 无 患者： 请结合图像与病史进行人工判读。
   输出：… 证据: 无 患者： PATIENT_001像与病史进行人工判读。
   ```
   **脱敏引擎自己制造了 `PATIENT_001`**，并写进最终结论。

3. 后果链：
   * 结论正文被污染（`患者PATIENT_001:` / `PATIENT_061医生`）；
   * 安全网关把它误判成"患者姓名"→ 整条结论降级抑制（A.89.4 只修了网关侧规则）；
   * A.90 手工清洗历史 47 条 → 归零；但**再跑一条新判读立刻又出现**（A.91 实测）。

即：只洗历史、只改网关，都不算修完 —— **生产这个标记的地方必须堵住**。

## 修法（与 A.89.2 的 `sg_patient_name` 同族）

* **姓氏白名单**：真姓名几乎都以常见姓氏开头；不在白名单的一律不当姓名；
* **谓词排除**：请/需/可/继续/建议/目前… 这些开头一定是句子不是姓名。

白名单比"排除表"更安全：宁可漏脱敏一个生僻姓（还有 `patient_name_simple`
与 `patient_name_with_title` 两条规则兜底），也不要把普通句子写成
`PATIENT_00N` 污染结论 —— 后者是**制造假数据**，比漏脱敏更严重
（漏脱敏由 L3/SG 兜底；假数据会直接进报告）。

## 关键实现约束

`app/security/desensitization.py::_make_name_replacer` 对 `patient_name`
**硬依赖 `match.group(2)`**（姓名必须是第 2 个捕获组）。所以姓氏约束必须用
**lookahead `(?=[...])`** 表达（零宽，不占组），不能用 `(?:...)` 包住姓名。
本脚本因此断言"编译后恰好 2 个捕获组"。

用法：
    python scripts/ops/fix_patient_name_rule_a91.py            # 只检查
    python scripts/ops/fix_patient_name_rule_a91.py --apply    # 写回 YAML
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
YAML_PATH = _ROOT / "desensitization_rules.yaml"

#: 常见姓氏（单姓 400+）。真姓名几乎都以这些字开头。
SURNAMES = (
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄"
    "和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁"
    "杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍"
    "虞万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚"
    "程嵇邢滑裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓"
    "牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙"
    "叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴胥能苍双"
    "闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍卻璩桑桂濮牛寿通边扈燕冀郏浦尚农"
    "温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘"
    "匡国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空"
    "曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"
)

#: 谓词/副词开头 —— 这些一定不是姓名，而是句子
NEGATIVE_WORDS = (
    "请", "需", "可", "应", "将", "已", "未", "无", "有", "如", "若", "建", "目",
    "暂", "近", "反", "复", "再", "继续", "建议", "目前", "近期", "已经", "尚未",
    "可以", "需要", "应该", "表现", "主诉", "否认", "既往", "本次", "此次",
    "反复", "偶有", "自觉",
    # A.91 真机补：**名词性标签**也必须排除。
    # 真机证据（L2 探针，2026-10-25）：
    #   入：… 患者解释: 请结合图像与病史进行人工判读。
    #   出：… 患者PATIENT_001: 请结合图像与病史进行人工判读。
    # 即 `患者` + `解释` 被当成姓名，把「患者解释:」这个**字段标签**吃成了
    # 「患者PATIENT_001:」。—— 这是本缺陷最后一个漏网形态，也是最常见的
    # 那个（`患者解释` 是 prompt 里的固定字段名，几乎每条结论都有）。
    "解释", "沟通", "说明", "情况", "信息", "病史", "症状", "主诉",
    "家属", "本人", "自述", "描述", "反馈", "疑问", "顾虑", "需求",
)

NEW_PATTERN = (
    # A.91：前缀分支简化。原 pattern 的交替表里 `患者` 排在 `病人` 之前，
    # 对 "病人：陈志强" **从来不匹配**（实测 old pattern 返回 None）——
    # 即"病人："这个写法一直没被脱敏过。这里用 `(?:患者姓名|患者|病人)` +
    # 可选冒号，覆盖全部写法，顺带修掉这个既有漏网。
    r"((?:(?:患者姓名|患者|病人)\s*[：:]?\s*)"
    r")"
    # 谓词排除（零宽负向断言）
    r"(?!(?:" + "|".join(NEGATIVE_WORDS) + r"))"
    # 姓氏白名单（零宽正向断言，**不占捕获组** —— replacer 需要 group(2) 是姓名）
    r"(?=[" + SURNAMES + r"])"
    r"([\u4e00-\u9fa5]{2,4})"
)

#: `patient_name_with_title` 的**同族缺陷**（A.91 一并修）。
#:
#: 原 pattern：`([\u4e00-\u9fa5]{2,4})(女士|先生|同志|医生)`
#: —— 同样是"任意 2-4 汉字"，真机后果（可复现）：
#:
#:   入：… 如有疑问，请及时咨询主治医生。
#:   出：… 如有疑问，请及时PATIENT_001医生。
#:
#: 「咨询主治」被当成患者姓名。这与 A.89.2 修的 `sg_patient_name`
#: （「请咨询主治医生」→ 患者姓名）是**完全同一个误报模式**，
#: 只是发生在融合层脱敏引擎而不是安全网关。
#:
#: 教训：**同一个误报模式会在多处规则里各写一份** —— 修完 SG 侧必须
#: 同步检查脱敏引擎侧（以及任何"任意 2-4 汉字 + 上下文"形态的规则）。
NEW_PATTERN_WITH_TITLE = (
    # 姓氏白名单（零宽，不占组；group(1) 仍是姓名，replacer 依赖它）
    r"(?=[" + SURNAMES + r"])"
    r"([\u4e00-\u9fa5]{2,4})"
    r"(女士|先生|同志|医生)"
)


def build_pattern() -> str:
    return NEW_PATTERN


def verify(pattern: str) -> tuple[bool, list[str]]:
    """编译 + 捕获组数量 + 行为断言。返回 (ok, messages)。"""
    msgs: list[str] = []
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        return False, [f"编译失败: {exc}"]

    if compiled.groups != 2:
        return False, [
            f"捕获组数 = {compiled.groups}，必须恰好 2（replacer 依赖 group(2)=姓名）"
        ]
    msgs.append("编译通过，捕获组 = 2")

    # 应当脱敏（真姓名）
    should_mask = [
        "患者：张建国，男，45岁",
        "患者：李秀英 女 62岁",
        "患者：王小明，建议复查",
        "患者：刘伟 男",
        "病人：陈志强",
    ]
    # 不应脱敏（句子/谓词）
    should_not = [
        "… 证据: 无 患者： 请结合图像与病史进行人工判读。",
        "患者：请提供更多信息",
        "患者：目前情况稳定",
        "患者：建议定期复查",
        "患者：继续观察即可",
        # A.91 真机形态：字段标签「患者解释:」绝不能被吃成「患者PATIENT_00N:」
        "… 患者解释: 请结合图像与病史进行人工判读。",
        "患者解释：结节多数为良性。",
        "患者沟通: 需定期复查。",
        "患者说明：已告知风险。",
    ]
    ok = True
    for text in should_mask:
        if not compiled.search(text):
            ok = False
            msgs.append(f"漏脱敏（应命中）: {text}")
    for text in should_not:
        if compiled.search(text):
            ok = False
            msgs.append(f"误报（不应命中）: {text}")
    if ok:
        msgs.append(f"行为断言通过：{len(should_mask)} 例应命中 / {len(should_not)} 例不应命中")
    return ok, msgs


def verify_with_title(pattern: str) -> tuple[bool, list[str]]:
    """`patient_name_with_title` 的校验：group(1)=姓名、group(2)=称谓。"""
    msgs: list[str] = []
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        return False, [f"编译失败: {exc}"]
    if compiled.groups != 2:
        return False, [f"捕获组 = {compiled.groups}，必须恰好 2（group(1)=姓名, group(2)=称谓）"]

    # 真姓名 + 称谓 → 应命中
    should_mask = [
        "张建国医生",
        "李秀英女士",
        "王小明先生",
    ]
    # 谓词/动词短语 + 称谓 → **不**应命中（真机缺陷原句）
    should_not = [
        "请及时咨询主治医生",
        "由值班医生处理",
        "请外科医生会诊",
        "建议专科医生复查",
    ]
    ok = True
    for t in should_mask:
        if not compiled.search(t):
            ok = False
            msgs.append(f"漏脱敏（应命中）: {t}")
    for t in should_not:
        if compiled.search(t):
            m = compiled.search(t)
            ok = False
            msgs.append(f"误报（不应命中）: {t} -> 抓到 {m.group(1)!r}")
    if ok:
        msgs.append(
            f"称谓规则通过：{len(should_mask)} 例应命中 / {len(should_not)} 例不应命中"
        )
    return ok, msgs


def main(apply: bool) -> int:
    pattern = build_pattern()
    ok, msgs = verify(pattern)
    print("=== patient_name ===")
    for m in msgs:
        print("  " + m)
    if not ok:
        print("[FATAL] patient_name 新 pattern 校验未通过，不写文件")
        return 2

    ok2, msgs2 = verify_with_title(NEW_PATTERN_WITH_TITLE)
    print("\n=== patient_name_with_title ===")
    for m in msgs2:
        print("  " + m)
    if not ok2:
        print("[FATAL] patient_name_with_title 新 pattern 校验未通过，不写文件")
        return 2

    data = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    rules = data.get("rules", [])
    by_name = {r.get("name"): r for r in rules}
    for nm in ("patient_name", "patient_name_with_title"):
        if nm not in by_name:
            print(f"[FATAL] YAML 里缺 {nm} 规则")
            return 2

    targets = {
        "patient_name": (
            pattern,
            "患者姓名脱敏（带前缀；A.91 加姓氏白名单与谓词排除，避免把句子当姓名）",
        ),
        "patient_name_with_title": (
            NEW_PATTERN_WITH_TITLE,
            "患者姓名脱敏（带称谓；A.91 加姓氏白名单，避免「咨询主治医生」被当姓名）",
        ),
    }

    changed = []
    for nm, (new_pat, new_desc) in targets.items():
        rule = by_name[nm]
        if rule.get("pattern") != new_pat:
            print(f"\n[{nm}]\n  旧长度 = {len(rule.get('pattern') or '')}\n  新长度 = {len(new_pat)}")
            rule["pattern"] = new_pat
            rule["description"] = new_desc
            changed.append(nm)
        else:
            print(f"\n[{nm}] 已是目标 pattern")

    if not changed:
        print("\n无需修改")
        return 0

    if not apply:
        print("\n[dry-run] 未写文件。加 --apply 写回。")
        return 0

    YAML_PATH.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    recheck = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    rb = {r.get("name"): r for r in recheck["rules"]}
    for nm, (new_pat, _) in targets.items():
        assert rb[nm]["pattern"] == new_pat, f"写回后 {nm} 校验失败"
    print(f"\n[APPLIED] 已写回 {YAML_PATH}（改 {', '.join(changed)}），重新解析校验通过")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真的写回 YAML（默认只检查）")
    a = ap.parse_args()
    sys.exit(main(a.apply))
