#!/usr/bin/env python3
"""p78 — 仓库内 harness 结构自检（只读；给人和 Agent 同一把尺子）。

背景（A.106）：`AGENTS.md` 与 SKILL `uai-harness-maintenance` 一直要求收尾跑
`node ~/.codebuddy/skills/harness-creator/scripts/validate-harness.mjs`，但该脚本
属**用户级**目录：换一台机器/换一个 Agent 就不存在（本机实测 `No such file`）。
于是"整饬 harness"这件事没有可执行的验收口，审计方只能读文档相信结论。

本脚本把可机器判定的部分落到仓库里，任何人（含审计方）在任意机器上跑
`python scripts/audit/p78_harness_selfcheck.py` 即可复现同一份判定。

判定分两级：
- ``FAIL``：违反 `AGENTS.md` 的硬性不变量（状态文件缺失/不可解析/关键节缺失/
  16 号附录编号乱序），退出码 1。
- ``WARN``：需要人拍板的偏离（例如 `in_progress` 多于 1 个），只报告不阻断。

用法::

    python scripts/audit/p78_harness_selfcheck.py [--root .] [--json]

退出码：0 = 无 FAIL（可有 WARN）；1 = 存在 FAIL；2 = 用法/环境错误。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROGRESS_FILE = "docs-for-guiding generating docs/超声诊疗决策支持系统/16-开发进展汇报-20260928.md"

REQUIRED_FILES = ("AGENTS.md", "progress.md", "feature_list.json", "session-handoff.md", "init.sh")

# AGENTS.md「State Artifacts」要求的进度四要素（"当前目标"允许写作 Current Objective）
PROGRESS_MARKERS = ("Last Updated", "Blockers", "Recommended Next Step")

# session-handoff.md 六项必填（见该文件头部说明）
HANDOFF_MARKERS = (
    "Last Updated",
    "Current Objective",
    "Recommended Next Step",
    "Blockers",
    "Files",
    "Next Session",
)

AGENTS_MARKERS = ("权威次序", "Definition of Done", "Verification Commands", "硬规则")

# AGENTS.md 里用反引号写的仓库相对路径（只查"看着像文件"的，跳过 ~ 与 http）
_BACKTICK_PATH = re.compile(r"`([A-Za-z0-9_\-./]+\.(?:md|json|py|sh|mjs|ts|tsx|yml|yaml|toml|lock))`")


@dataclass
class Result:
    name: str
    ok: bool
    detail: str
    level: str = "FAIL"  # FAIL / WARN


def _read(root: Path, name: str) -> str | None:
    p = root / name
    if not p.is_file():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


def check_files(root: Path) -> list[Result]:
    out: list[Result] = []
    for name in REQUIRED_FILES:
        p = root / name
        if not p.is_file():
            out.append(Result(f"file:{name}", False, "缺失"))
        elif p.stat().st_size == 0:
            out.append(Result(f"file:{name}", False, "存在但为空"))
        else:
            out.append(Result(f"file:{name}", True, f"{p.stat().st_size} bytes"))
    return out


def check_feature_list(root: Path) -> list[Result]:
    raw = _read(root, "feature_list.json")
    if raw is None:
        return [Result("feature_list", False, "文件缺失")]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:  # silent-ok: 原因原样进 FAIL 结果（不吞异常信息）
        return [Result("feature_list", False, f"JSON 不可解析：{exc}")]
    feats = data.get("features") if isinstance(data, dict) else None
    if not isinstance(feats, list) or not feats:
        return [Result("feature_list", False, "features 为空或不是列表")]
    bad = [f for f in feats if not {"id", "name", "status"} <= set(f)]
    out = [
        Result("feature_list", not bad, f"{len(feats)} 条；缺 id/name/status：{[b.get('id') for b in bad][:5]}")
    ]
    in_prog = [f["id"] for f in feats if f.get("status") == "in_progress"]
    out.append(
        Result(
            "feature_list:one_in_progress",
            len(in_prog) <= 1,
            f"in_progress={len(in_prog)} {in_prog}（AGENTS「同一时间只推进一个」要求 ≤1）",
            level="WARN",
        )
    )
    return out


def check_markers(root: Path, name: str, markers: tuple[str, ...], label: str) -> Result:
    text = _read(root, name)
    if text is None:
        return Result(label, False, f"{name} 缺失")
    missing = [m for m in markers if m not in text]
    return Result(label, not missing, "缺：" + "、".join(missing) if missing else f"{name} 六/四要素齐备")


def check_appendix_monotonic(root: Path) -> Result:
    text = _read(root, PROGRESS_FILE)
    if text is None:
        return Result("appendix:A.N", False, f"{PROGRESS_FILE} 缺失")
    # 条目有三种写法：`### A.96 …`、`#### A.97 …`、`## 附录 A.52 — …`。
    # 子节 `##### A.96.1` 的数字后是 "."，不匹配；同号不同级只算"补记"，不算重号。
    heads = re.findall(r"^(#{1,6}) (?:附录 )?A\.(\d+)(?=\s|—|$)", text, flags=re.M)
    if not heads:
        return Result("appendix:A.N", False, "16 号附录 A 未找到任何条目")
    pairs = [(len(h), int(n)) for h, n in heads]
    nums = [n for _, n in pairs]
    sub = {int(a) for a in re.findall(r"^#{1,6} (?:附录 )?A\.(\d+)\.\d+", text, flags=re.M)}
    present = set(nums) | sub
    dup = sorted({n for (lvl, n) in pairs if pairs.count((lvl, n)) > 1})
    ordered = nums == sorted(nums)
    gaps = [n for n in range(min(present), max(present) + 1) if n not in present]
    ok = ordered and not dup and not gaps
    detail = f"条目 {len(present)} 个（A.{min(present)}~A.{max(present)}）"
    if dup:
        detail += f"；同号同级重复 {dup}（历史重号，只能追加更正）"
    if gaps:
        detail += f"；缺号 {gaps}（需确认是漏记还是被引用未成篇）"
    return Result("appendix:A.N", ok, detail, level="WARN")


def check_no_pptx_tracked(root: Path) -> Result:
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.pptx"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # silent-ok: 无 git / 超时 → 该检查不适用
        return Result("git:no_pptx", True, f"跳过（{type(exc).__name__}）", level="WARN")
    if out.returncode != 0:
        return Result("git:no_pptx", True, "跳过（非 git 仓库）", level="WARN")
    files = [f for f in out.stdout.split("\x00") if f.strip()]
    return Result("git:no_pptx", not files, f"入库 pptx {len(files)} 个（硬规则 #16 要求 0）")


def _tracked_paths(root: Path) -> set[str] | None:
    """git 跟踪的文件集合（POSIX 相对路径）；非 git 环境返回 None。"""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):  # silent-ok: 无 git → 由调用方退化
        return None
    if out.returncode != 0:
        return None
    return {p for p in out.stdout.split("\x00") if p}


def check_agents_links(root: Path) -> Result:
    """AGENTS.md 反引号里的仓库相对路径必须可解析。

    纯文件名（如 `settings.py`）允许写在任意层级——按 git 跟踪清单做后缀匹配，
    避免"根目录没有就判死链"的假红。
    """
    text = _read(root, "AGENTS.md")
    if text is None:
        return Result("agents:links", False, "AGENTS.md 缺失")
    paths = sorted({m for m in _BACKTICK_PATH.findall(text) if not m.startswith(("http", "~", "/"))})
    tracked = _tracked_paths(root)
    missing: list[str] = []
    for p in paths:
        if (root / p).exists():
            continue
        if tracked is not None:
            if "/" in p:
                if p not in tracked:
                    missing.append(p)
            elif not any(t.endswith("/" + p) for t in tracked):
                missing.append(p)
        else:
            missing.append(p)
    return Result(
        "agents:links",
        not missing,
        "死链：" + "、".join(missing[:6]) if missing else f"{len(paths)} 条引用均可解析",
    )


def run(root: Path) -> list[Result]:
    results: list[Result] = []
    results += check_files(root)
    results += check_feature_list(root)
    results.append(check_markers(root, "progress.md", PROGRESS_MARKERS, "progress:markers"))
    results.append(check_markers(root, "session-handoff.md", HANDOFF_MARKERS, "handoff:markers"))
    results.append(check_markers(root, "AGENTS.md", AGENTS_MARKERS, "agents:markers"))
    results.append(check_appendix_monotonic(root))
    results.append(check_no_pptx_tracked(root))
    results.append(check_agents_links(root))
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="仓库内 harness 结构自检（只读）")
    ap.add_argument("--root", default=".", help="仓库根（默认当前目录）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    if not (root / "AGENTS.md").is_file():
        print(f"FAIL: {root} 不像本仓库根（缺 AGENTS.md）", file=sys.stderr)
        return 2

    results = run(root)
    fails = [r for r in results if not r.ok and r.level == "FAIL"]
    warns = [r for r in results if not r.ok and r.level == "WARN"]

    if args.json:
        print(
            json.dumps(
                {
                    "root": str(root),
                    "fail": len(fails),
                    "warn": len(warns),
                    "checks": [r.__dict__ for r in results],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for r in results:
            mark = "ok  " if r.ok else ("WARN" if r.level == "WARN" else "FAIL")
            print(f"  [{mark}] {r.name}: {r.detail}")
        print()
        print(f"summary: {len(results) - len(fails) - len(warns)} passed, {len(warns)} warn, {len(fails)} fail")
        if fails:
            print("HARNESS SELFCHECK: FAIL —— 先修 FAIL 再用这份状态对外汇报")
        elif warns:
            print("HARNESS SELFCHECK: PASS（含 WARN，需人拍板，不阻断）")
        else:
            print("HARNESS SELFCHECK: PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
