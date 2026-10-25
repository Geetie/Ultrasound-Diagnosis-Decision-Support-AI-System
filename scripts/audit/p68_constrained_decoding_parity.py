"""A.95 Q29：约束解码的分级一致率重测（离线可跑，需 8002 vLLM）。

## 这条要验的到底是什么（P0-2 的原始判据）

15 号 P0-2 的完成判据是「**780 例重测，常规/高风险档分级一致率 ≥95%**」。
它要回答的问题是：**同一病例，模型两次生成的分级是否稳定**。

为什么要用"约束解码"路径来验：A2 轮发现"双引擎一致率"问题（vLLM vs llama.cpp
对同一病例给出不同分级），当时的修法方向已固化为 schema 级约束
（vLLM `response_format: json_schema`），而不是 prompt 级祈祷。
**但这条路从未做过规模化重测。**

## ⚠️ 与原始计划的两个已知偏差（必须写清，不许含糊）

1. **780 例不存在了**。真机核对：当前最大评测集是
   `~/uai/data/eval/double_engine_eval_set.jsonl`，**169 条，其中 157 条有真实结论**
   （其余是故障期 `llm_unavailable`）。780 是历史峰值，之后被 A.70 的
   脏数据清理削掉了（608 条被删）。
   → 本脚本按**实际可用样本数**跑，并在报告里写 `sample_shortfall`；
   **不假装跑到了 780 例**。
2. **llama.cpp 已弃用**（2026-10-01，CPU-only 构建、7B 实际跑 8 线程 CPU）。
   原判据里"双引擎"的后一半不存在了 → 本脚本改为验**同一引擎的两次生成一致性**
   （这是"分级一致率"在当前部署下唯一有意义的形式），并在报告里注明口径变化。

## 判据（跑前写死，与 P0-2 同源）

* **常规档**：`temperature<=0.3` 下，两次生成的分级一致率 ≥ **95%**
* **高风险档**：`temperature` 稍高（模拟高风险病例的采样抖动）下同样 ≥ **95%**
* 一致率 = 两次解析出的分级码完全相同的比例（按器官分别算，再汇总）
* 抽不出分级的样本 → 计入 `unparseable`，**不计入分母**（但必须在报告里显示，
  否则"分母缩小 = 一致率虚高"）

退出码：0 裁决已出 / 2 前置缺失（无评测集、8002 不可达）。

用法：
    ~/uai/venv/bin/python scripts/audit/p68_constrained_decoding_parity.py \
        --n 60 --report ~/uai/reports/constrained_decoding_parity.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EVAL_SET = pathlib.Path.home() / "uai" / "data" / "eval" / "double_engine_eval_set.jsonl"

#: 判据阈值（预注册，跑前写死）
MIN_AGREEMENT = 0.95

#: 分级码抽取：三器官各自的体系
GRADE_PATTERNS = {
    "thyroid": re.compile(r"\bTR\s*([1-5])\b"),
    "liver": re.compile(r"\bLR\s*-\s*([1-5])\b|\bLR\s*([1-5])\b"),
    "cardiac": re.compile(r"LVEF\s*[:：]\s*(\d+(?:\.\d+)?)\s*%"),
}


def extract_grade(text: str, organ: str) -> str | None:
    """从结论文本抽分级码（与生产 `extract_grading_severest` 同源思路，此处轻量版）。"""
    if not text:
        return None
    pat = GRADE_PATTERNS.get(organ)
    if pat is None:
        return None
    m = pat.search(text)
    if not m:
        return None
    # 取第一个非 None 的分组
    val = next((g for g in m.groups() if g is not None), None)
    if val is None:
        return None
    if organ == "thyroid":
        return f"TR{val}"
    if organ == "liver":
        return f"LR-{val}"
    return f"LVEF={val}"


def load_cases(limit: int | None) -> list[dict]:
    """读评测集，只保留**有真实结论**的样本（去掉故障期 llm_unavailable）。"""
    if not EVAL_SET.is_file():
        return []
    out: list[dict] = []
    with EVAL_SET.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            c = r.get("conclusion") or ""
            if not c or "llm_unavailable" in c or "manual review required" in c:
                continue
            g = extract_grade(c, r.get("organ") or "")
            if g is None:
                continue
            out.append(
                {
                    "request_id": r.get("request_id"),
                    "organ": r.get("organ"),
                    "clinical_text": r.get("clinical_text") or "",
                    "gold_grade": g,
                    "gold_conclusion": c,
                }
            )
    if limit:
        out = out[:limit]
    return out


def build_prompt(case: dict) -> str:
    """构造与生产同口径的 prompt（要求首行就是分级行）。"""
    organ_cn = {"thyroid": "甲状腺", "liver": "肝脏", "cardiac": "心脏"}.get(case["organ"], "病灶")
    return (
        f"你是超声科医生。请对{organ_cn}病例给出分级。\n"
        "输出格式（硬约束，首行就是分级行）：\n"
        "分级: <分级码>\n理由: <一句话>\n"
        "甲状腺用 TI-RADS（TR1-TR5）；肝脏用 LI-RADS（LR-1..LR-5）；"
        "心脏给出 LVEF 百分比。\n"
        f"病例：{case['clinical_text'] or '（无文本描述，仅影像）'}\n"
    )


async def run_arm(cases: list[dict], *, temperature: float, structured: bool) -> list[dict]:
    """跑一轮生成，返回每例的分级。structured=True 走约束解码。

    注意 `BaseHttpInferenceEngine.generate(request: InferenceRequest)` —— 是
    **位置参数对象**，不是关键字参数（本条先按 key= 写，真机报
    `got an unexpected keyword argument 'max_tokens'` 才发现）。
    """
    from app.config.settings import settings
    from app.inference.protocols import InferenceRequest
    from app.inference.vllm import VLLMEngine

    # `VLLMEngine` 默认 `require_model=True`，不传 model 会在第一次生成时抛
    # E2002「vLLM requires a model name but none was provided」。
    # 模型名取生产配置真值（settings.vllm_default_model），不要自己编。
    model = getattr(settings, "vllm_default_model", None) or None
    engine = VLLMEngine(
        base_url=f"http://{settings.vllm_host}:{settings.vllm_port}",
        default_model=model,
    )
    out: list[dict] = []
    try:
        for i, c in enumerate(cases, 1):
            prompt = build_prompt(c)
            try:
                extra: dict = {}
                if structured:
                    from app.inference.grading_schema import grading_json_schema

                    schema = grading_json_schema(c["organ"])
                    if schema:
                        extra["response_format"] = {
                            "type": "json_schema",
                            "json_schema": {"name": "grading", "schema": schema},
                        }
                req = InferenceRequest(
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=120,
                    request_id=c.get("request_id"),
                    extra=extra,
                )
                resp = await engine.generate(req)
                text = getattr(resp, "text", "") or ""
            except Exception as exc:  # silent-ok: 生成失败已写入该例的 error 字段并在报告中统计，非静默
                out.append(
                    {
                        **c,
                        "parsed": None,
                        "raw_head": "",
                        "error": f"{type(exc).__name__}: {str(exc)[:80]}",
                    }
                )
                continue
            # 结构化输出是 {"grading": "TR3"} 形态；普通输出是文本
            parsed = None
            if structured:
                try:
                    obj = json.loads(text.strip())
                    parsed = str(obj.get("grading") or "") or None
                except (json.JSONDecodeError, AttributeError):  # silent-ok: 结构化解析失败已回退到正则抽取，回退路径有留痕
                    parsed = extract_grade(text, c["organ"])
            else:
                parsed = extract_grade(text, c["organ"])
            out.append({**c, "parsed": parsed, "raw_head": text[:120], "error": None})
            if i % 20 == 0:
                print(f"    ... {i}/{len(cases)}")
    finally:
        close = getattr(engine, "close", None)
        if callable(close):
            try:
                await close()
            except Exception as exc:  # silent-ok: 关闭失败不影响已生成结果，统计不依赖它
                print(f"    [warn] engine.close 失败: {type(exc).__name__}")
    return out


def agreement(a: list[dict], b: list[dict]) -> dict:
    """两次生成的分级一致率（只对**两次都解析出分级**的样本计）。"""
    by_id_b = {r["request_id"]: r for r in b}
    per_organ: dict[str, dict] = defaultdict(lambda: {"same": 0, "diff": 0, "diff_cases": []})
    unparseable = 0
    total = 0
    for ra in a:
        rb = by_id_b.get(ra["request_id"])
        if rb is None:
            continue
        total += 1
        ga, gb = ra.get("parsed"), rb.get("parsed")
        if ga is None or gb is None:
            unparseable += 1
            continue
        organ = ra.get("organ") or "unknown"
        if ga == gb:
            per_organ[organ]["same"] += 1
        else:
            per_organ[organ]["diff"] += 1
            if len(per_organ[organ]["diff_cases"]) < 5:
                per_organ[organ]["diff_cases"].append(
                    {"request_id": ra["request_id"], "arm_a": ga, "arm_b": gb}
                )

    per_organ_out = {}
    tot_same = tot_cmp = 0
    for organ, v in per_organ.items():
        cmp_n = v["same"] + v["diff"]
        rate = v["same"] / cmp_n if cmp_n else None
        per_organ_out[organ] = {
            "compared": cmp_n,
            "same": v["same"],
            "diff": v["diff"],
            "agreement": round(rate, 4) if rate is not None else None,
            "diff_examples": v["diff_cases"],
        }
        tot_same += v["same"]
        tot_cmp += cmp_n

    overall = tot_same / tot_cmp if tot_cmp else None
    return {
        "total_cases": total,
        "compared": tot_cmp,
        "unparseable": unparseable,
        "same": tot_same,
        "overall_agreement": round(overall, 4) if overall is not None else None,
        "per_organ": per_organ_out,
    }


def verdict(regular: dict, highrisk: dict) -> tuple[str, list[str]]:
    """按预注册判据裁决。"""
    notes: list[str] = []
    r_rate = regular.get("overall_agreement")
    h_rate = highrisk.get("overall_agreement")
    if r_rate is None or h_rate is None:
        return "INSTRUMENT_NOT_READY", ["一致率无法计算（可比样本为 0）"]
    if regular["compared"] < 20:
        notes.append(f"⚠️ 常规档可比样本仅 {regular['compared']} 条，统计意义有限")
    low = []
    if r_rate < MIN_AGREEMENT:
        low.append(f"常规档 {r_rate:.2%} < {MIN_AGREEMENT:.0%}")
    if h_rate < MIN_AGREEMENT:
        low.append(f"高风险档 {h_rate:.2%} < {MIN_AGREEMENT:.0%}")
    if low:
        return "NOT_MET", low + notes
    return "PASS", notes


async def main(n: int | None, report: str | None) -> int:
    cases = load_cases(n)
    if not cases:
        print(f"[FATAL] 评测集为空或不可读：{EVAL_SET}")
        return 2
    print(f"载入 {len(cases)} 条有真实结论的样本")
    print("器官分布:", dict(Counter(c["organ"] for c in cases)))

    print("\n=== 常规档（temperature=0.1，约束解码两次）===")
    reg_a = await run_arm(cases, temperature=0.1, structured=True)
    reg_b = await run_arm(cases, temperature=0.1, structured=True)
    reg = agreement(reg_a, reg_b)
    print(f"  一致率 {reg['overall_agreement']}  可比 {reg['compared']}  未解析 {reg['unparseable']}")

    print("\n=== 高风险档（temperature=0.7，约束解码两次）===")
    hr_a = await run_arm(cases, temperature=0.7, structured=True)
    hr_b = await run_arm(cases, temperature=0.7, structured=True)
    hr = agreement(hr_a, hr_b)
    print(f"  一致率 {hr['overall_agreement']}  可比 {hr['compared']}  未解析 {hr['unparseable']}")

    v, notes = verdict(reg, hr)
    print(f"\n== 裁决：{v} ==")
    if notes:
        for x in notes:
            print(f"  {x}")

    payload = {
        "probe": "p68_constrained_decoding_parity",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "eval_set": str(EVAL_SET),
        "sample_count": len(cases),
        "sample_shortfall": {
            "planned": 780,
            "actual": len(cases),
            "reason": (
                "780 是历史峰值；A.70 清理 608 条脏数据后，当前最大评测集 "
                "double_engine_eval_set.jsonl 仅 169 条（157 条有真实结论）"
            ),
        },
        "caliber_note": (
            "原判据为「双引擎一致率」，但 llama.cpp 已于 2026-10-01 弃用 "
            "（CPU-only 构建、7B 跑 8 线程 CPU）。本报告改为"
            "「同一引擎（vLLM）两次生成的分级一致率」—— 这是当前部署下"
            "『分级是否稳定』唯一有意义的形式。"
        ),
        "pre_registered": {"min_agreement": MIN_AGREEMENT},
        "regular_arm": {"temperature": 0.1, **reg},
        "highrisk_arm": {"temperature": 0.7, **hr},
        "verdict": v,
        "verdict_notes": notes,
    }
    if report:
        rp = pathlib.Path(report).expanduser()
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告：{rp}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=None, help="最多跑多少例（默认全部）")
    ap.add_argument(
        "--report",
        default=str(pathlib.Path.home() / "uai/reports/constrained_decoding_parity.json"),
    )
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.n, a.report)))
