"""规则引擎等价性验证（二期 §11）。

用途：替换规则引擎前，把新引擎与既有参照实现在**同一批规则与同一批输入**上对拍，
只有命中规则集合逐条一致才允许切换。

规格要求「≥200 规则 × 1000 输入一致性验证」，本脚本默认就按这个规模跑：
- 规则集 = 既有默认禁忌规则 + 合成规则（补齐到 200 条，覆盖 global 与器官级）；
- 输入集 = 1000 组事实，覆盖四个分支（仅 patient_conditions / 仅 clinical_context /
  两者都有 / 两者都无 —— 最后一个是"缺数据兜底命中"的安全路径，必须被覆盖到）。

退出码：0 = 等价；1 = 存在不一致；2 = 规则或输入不足量（验证本身不成立）。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MIN_RULES = 200
DEFAULT_INPUTS = 1000

ORGANS = ["thyroid", "liver", "cardiac", "kidney", "breast"]
SEVERITIES = ["high", "medium", "low"]
EXTRA_CONDITIONS = ["diabetes", "renal_failure", "hypertension", "obesity"]


def build_rule_corpus() -> list[Any]:
    """既有默认规则 + 合成规则（补齐到 ≥MIN_RULES 条）。"""
    from app.knowledge.rule_engine import CONDITION_ALIASES, GLOBAL_SCOPE, Rule
    from safety_gateway.contraindication import ContraindicationEngine

    legacy = ContraindicationEngine()
    legacy.load_rules()

    rules: list[Rule] = []
    seen = set()

    def _add(rule: Rule) -> None:
        if rule.rule_id in seen:
            return
        seen.add(rule.rule_id)
        rules.append(rule)

    for index, raw in enumerate(legacy.get_global_rules()):
        _add(
            Rule(
                rule_id=f"legacy_global_{index}_{raw.id}",
                condition=raw.condition,
                description=raw.description,
                severity=raw.severity,
                organ=GLOBAL_SCOPE,
                enabled=raw.enabled,
            )
        )
    for organ in ORGANS:
        for index, raw in enumerate(legacy.get_rules_for_organ(organ)):
            _add(
                Rule(
                    rule_id=f"legacy_{organ}_{index}_{raw.id}",
                    condition=raw.condition,
                    description=raw.description,
                    severity=raw.severity,
                    organ=organ,
                    enabled=raw.enabled,
                )
            )

    conditions = list(CONDITION_ALIASES) + EXTRA_CONDITIONS
    synthetic_index = 0
    while len(rules) < MIN_RULES:
        condition = conditions[synthetic_index % len(conditions)]
        organ = ORGANS[(synthetic_index // len(conditions)) % len(ORGANS)]
        severity = SEVERITIES[synthetic_index % len(SEVERITIES)]
        # 每条条件都要覆盖到 global 与器官级两种作用域：作用域过滤是引擎差异的
        # 高发区（global 规则对任何器官都生效，器官规则只对本人）
        if synthetic_index % 7 == 0:
            organ = GLOBAL_SCOPE
        _add(
            Rule(
                rule_id=f"synth_{synthetic_index:04d}",
                condition=condition,
                description=f"合成规则 {synthetic_index}（{condition}/{organ}）",
                severity=severity,
                organ=organ,
                enabled=synthetic_index % 17 != 0,  # 少量禁用规则：禁用分支也要对拍
            )
        )
        synthetic_index += 1

    return rules


def build_inputs(count: int, seed: int = 20260924) -> list[dict[str, Any]]:
    """确定性生成输入事实（固定种子，失败可复现）。"""
    from app.knowledge.rule_engine import CONDITION_ALIASES

    rng = random.Random(seed)
    conditions = list(CONDITION_ALIASES)
    aliases = [alias for values in CONDITION_ALIASES.values() for alias in values]
    unrelated = ["healthy", "routine_check", "followup"]

    inputs: list[dict[str, Any]] = []
    for index in range(count):
        shape = index % 4
        facts: dict[str, Any] = {}

        if shape in (0, 2):
            pool = aliases if index % 3 else conditions
            facts["patient_conditions"] = [
                rng.choice(pool) for _ in range(rng.randint(1, 3))
            ]
        if shape in (1, 2):
            pool = conditions if index % 3 else unrelated
            facts["clinical_context"] = {
                "conditions": [rng.choice(pool) for _ in range(rng.randint(0, 2))]
            }
        if shape == 3:
            # 故意不提供任何条件字段：走"缺数据兜底命中"的安全分支
            facts["note"] = "no condition data"

        inputs.append(facts)
    return inputs


def run(rules_count_min: int, inputs_count: int, seed: int) -> int:
    from app.knowledge.rule_engine import (
        ContraindicationRuleEngineAdapter,
        SimpleRuleEngine,
        verify_equivalence,
    )
    from safety_gateway.contraindication import ContraindicationEngine

    rules = build_rule_corpus()
    inputs = build_inputs(inputs_count, seed=seed)
    organs = [None, *ORGANS]

    if len(rules) < rules_count_min:
        print(f"[ERR ] 规则数 {len(rules)} < 要求 {rules_count_min}，验证不成立")
        return 2
    if len(inputs) < inputs_count:
        print(f"[ERR ] 输入数 {len(inputs)} < 要求 {inputs_count}，验证不成立")
        return 2

    report = verify_equivalence(
        SimpleRuleEngine(),
        ContraindicationRuleEngineAdapter(ContraindicationEngine()),
        rules,
        inputs,
        organs=organs,
    )

    print("=" * 78)
    print("规则引擎等价性验证")
    print("=" * 78)
    print(f"  规则数      : {len(rules)}（含 synthetic）")
    print(f"  输入数      : {report.total_inputs}")
    print(f"  命中输入的  : {report.matched_inputs}（命中数为 0 的对拍不能算通过）")
    print(f"  不一致条数  : {len(report.mismatches)}")
    print(f"  结论        : {'等价' if report.equivalent else '不一致'}")
    if report.mismatches:
        for item in report.mismatches[:5]:
            print(
                f"    - #{item['input_index']} organ={item['organ']} "
                f"expected={item['expected']} actual={item['actual']}"
            )
    print("-" * 78)

    if report.matched_inputs == 0:
        # 全不命中时两引擎"结果一致"毫无意义：等价性验证必须真的走到匹配分支
        print("[ERR ] 没有任何输入命中规则，等价性验证无效")
        return 2
    return 0 if report.equivalent else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="规则引擎等价性验证")
    parser.add_argument("--rules-min", type=int, default=MIN_RULES)
    parser.add_argument("--inputs", type=int, default=DEFAULT_INPUTS)
    parser.add_argument("--seed", type=int, default=20260924)
    parser.add_argument("--json", dest="json_path", help="把结果写入 JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    exit_code = run(args.rules_min, args.inputs, args.seed)
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(
                {
                    "exit_code": exit_code,
                    "rules_min": args.rules_min,
                    "inputs": args.inputs,
                    "seed": args.seed,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
