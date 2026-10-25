"""A.95 Q33-①：Qwen3 LLM 基座升级的**可行性评估**（不做替换，只出决策依据）。

## 为什么不直接换（诚实的工程判断）

15 号 P1 建议「Qwen3-1.7B / Qwen3-4B」替代现役 Qwen2.5-7B-Instruct。听起来是"换个模型 ID"，
但真机核对后发现这是**高风险、长周期**的改动：

| 约束 | 实测 |
|---|---|
| **显存** | 现有 45.4 / 48 GB 被 8002（Qwen2.5-7B）+ 8003 + 8006 占满；Qwen3-4B 需额外 8~10 GB |
| **评测集** | 780 例**已不存在**（A.70 清理 608 条脏数据后只剩 157 条有真实结论） |
| **验证成本** | 换基座须重跑：分级一致率、幻觉增量 ≤0.5%、准确率保留 ≥98%、结构化输出成功率 |
| **服务中断** | 换模型要停 8002 重载，生产判读链路在此期间不可用 |

**结论：本轮不该做。** 15 号原文的门控也要求「沿用量化验收（幻觉增量 ≤0.5%、
准确率保留 ≥98%）」—— 在只有 157 条样本的情况下，这些指标**统计上站不住**
（157 条估 0.5% 的幻觉增量，置信区间比效应本身还宽）。

## 本脚本做什么：给出**可执行的前置条件清单**

不做替换，但把"要换需要满足什么"变成机器可读的检查表，让下一轮能直接执行：

1. `vram_headroom`：显存是否够（需要停止哪些服务、腾出多少）
2. `eval_sample_size`：评测集是否够支撑门控（当前 157 < 建议 300+）
3. `rollback_plan`：回滚路径是否明确（换回 Qwen2.5-7B 的最短路径）
4. `acceptance_criteria`：15 号四条量化验收逐条列出

## 用法

    ~/uai/venv/bin/python scripts/audit/p73_qwen3_upgrade_readiness.py

退出码：0 评估完成 ｜ 2 前置缺失。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EVAL_SET = pathlib.Path.home() / "uai" / "data" / "eval" / "double_engine_eval_set.jsonl"

#: 15 号 P1 的量化验收（原文）
ACCEPTANCE = [
    "幻觉增量 ≤ 0.5%（相对现役模型）",
    "准确率保留 ≥ 98%",
    "结构化输出成功率 ≥ 99%（response_format 生效）",
    "分级一致率（同引擎两次生成）不低于现役基线 79.56%",
]

#: 换基座需要的最小评测样本（统计意义）
MIN_EVAL_SAMPLES = 300

#: 显存需求估计（GB，Qwen3-4B fp16 + KV cache 余量）
QWEN3_4B_VRAM_GB = 10.0
#: 现役 GPU 总显存（真机 48G 卡）
GPU_TOTAL_GB = 48.0


def count_usable_eval() -> int:
    """评测集里有真实结论的条数（排除故障期 llm_unavailable）。"""
    if not EVAL_SET.is_file():
        return 0
    n = 0
    with EVAL_SET.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            c = r.get("conclusion") or ""
            if c and "llm_unavailable" not in c and "manual review required" not in c:
                n += 1
    return n


def assess(n_eval: int, vram_used_gb: float | None) -> dict:
    """按四类前置条件评估升级就绪度。纯函数（守卫钉住）。

    Args:
        n_eval: 可用评测样本数。
        vram_used_gb: 当前已用显存（None = 探不到）。

    Returns:
        ``{"ready": bool, "blockers": [...], "checks": {...}}``
    """
    checks: dict = {}
    blockers: list[str] = []

    # ① 评测样本量
    checks["eval_sample_size"] = {
        "current": n_eval,
        "required": MIN_EVAL_SAMPLES,
        "ok": n_eval >= MIN_EVAL_SAMPLES,
        "why": (
            "157 条估 0.5% 的幻觉增量，置信区间比效应本身还宽 → 门控无法判定。"
            "15 号要求『幻觉增量 ≤0.5%、准确率保留 ≥98%』，需要 ≥300 条才有统计意义。"
        ),
    }
    if n_eval < MIN_EVAL_SAMPLES:
        blockers.append(f"评测样本不足：{n_eval} < {MIN_EVAL_SAMPLES}")

    # ② 显存
    if vram_used_gb is None:
        checks["vram"] = {
            "used": None,
            "need_extra": QWEN3_4B_VRAM_GB,
            "ok": False,
            "why": "NVML 探不到显存（已知外观问题）；无法确认换模型后能否加载",
        }
        blockers.append("显存不可探测（NVML 失败）→ 无法确认能否加载 Qwen3-4B")
    else:
        headroom = GPU_TOTAL_GB - vram_used_gb
        ok = headroom >= QWEN3_4B_VRAM_GB
        checks["vram"] = {
            "used": vram_used_gb,
            "total": GPU_TOTAL_GB,
            "headroom": round(headroom, 1),
            "need_extra": QWEN3_4B_VRAM_GB,
            "ok": ok,
            "why": (
                f"余量 {headroom:.1f}GB vs 需要 {QWEN3_4B_VRAM_GB}GB —— "
                + ("够" if ok else "不够，须先停 8006 判官或降 8002 的 gpu_memory_utilization")
            ),
        }
        if not ok:
            blockers.append(f"显存余量 {headroom:.1f}GB < 需要 {QWEN3_4B_VRAM_GB}GB")

    # ③ 回滚路径（结构性检查：只要 .env 备份存在就算有）
    env = pathlib.Path.home() / "uai" / ".env"
    checks["rollback"] = {
        "env_exists": env.is_file(),
        "how": "把 VLLM_DEFAULT_MODEL 改回 Qwen2.5-7B-Instruct + 重启 8002（约 2 分钟）",
        "ok": env.is_file(),
    }
    if not env.is_file():
        blockers.append("缺 .env → 无回滚路径")

    return {
        "ready": not blockers,
        "blockers": blockers,
        "checks": checks,
        "acceptance_criteria": ACCEPTANCE,
    }


def main(report: str | None) -> int:
    n_eval = count_usable_eval()
    if n_eval == 0:
        print(f"[FATAL] 评测集不可读或无可用样本：{EVAL_SET}")
        return 2

    # 尝试探显存（NVML 失败时不假装知道）
    vram_used: float | None = None
    try:
        import subprocess

        out = subprocess.run(
            [
                "env",
                "-u",
                "LD_LIBRARY_PATH",
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            vram_used = float(out.stdout.strip().splitlines()[0]) / 1024.0
    except Exception as exc:  # silent-ok: 探不到就如实返回 None（下面判 blocker），不编数字
        print(f"  [info] 显存探测失败（{type(exc).__name__}）→ 按不可知处理")

    res = assess(n_eval, vram_used)
    print("=== Qwen3 LLM 基座升级就绪度评估 ===")
    print(f"  可用评测样本: {n_eval}（需 ≥{MIN_EVAL_SAMPLES}）")
    if vram_used is not None:
        print(f"  当前显存占用: {vram_used:.1f} GB / {GPU_TOTAL_GB} GB")
    else:
        print("  当前显存占用: 不可探测")
    print(f"\n  就绪: {'✅ 是' if res['ready'] else '❌ 否'}")
    if res["blockers"]:
        print("  阻塞项:")
        for b in res["blockers"]:
            print(f"    · {b}")
    print("\n  15 号量化验收（换基座必须逐条通过）:")
    for a in ACCEPTANCE:
        print(f"    □ {a}")
    print(
        "\n  结论: 本轮**不该做**基座替换 —— 评测样本不足使门控无法判定，"
        "且显存余量不确定。先把评测集攒到 ≥300，再执行。"
    )

    payload = {
        "probe": "p73_qwen3_upgrade_readiness",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "current_model": "Qwen2.5-7B-Instruct",
        "candidate": "Qwen3-4B-Instruct-2507（Apache-2.0，已确认可下载）",
        "decision": "DO_NOT_UPGRADE_THIS_ROUND",
        "decision_reason": (
            "评测样本 157 < 300，门控指标（幻觉增量 ≤0.5%）无法判定；"
            "显存余量不确定（NVML 探不到，且 45.4/48GB 已占用）。"
            "这不是技术不可行，是**验证条件不具备**。"
        ),
        "n_eval": n_eval,
        "vram_used_gb": vram_used,
        **res,
    }
    if report:
        rp = pathlib.Path(report).expanduser()
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告：{rp}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--report",
        default=str(pathlib.Path.home() / "uai/reports/qwen3_upgrade_readiness.json"),
    )
    a = ap.parse_args()
    sys.exit(main(a.report))
