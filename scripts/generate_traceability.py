"""T-G1-DOC-01：IEC 62304 可追溯性矩阵生成器。

用途
----
从需求规格（``docs-for-guiding generating docs/开发计划/开发计划.md`` 的任务表）
抽取全部需求编号 ``T-G*``，再在代码与测试里逐个查锚点，产出
「需求 -> 代码 -> 测试」矩阵，供审计（IEC 62304 §5.1.1 可追溯性）取用。

为什么用"锚点引用"而不是"人工登记表"
----------------------------------
人工登记的可追溯性矩阵会腐化：需求改了、测试删了，矩阵还写着"已覆盖"。这里
每次重新扫描真实仓库，矩阵与代码状态必然一致；未被引用的需求会显式为 ❌，
从而暴露"这条需求其实没人测"。

约定
----
需求编号以注释/docstring 形式落在代码或测试中即可被识别，例如：测试文件头写
一行 ``T-G1-DATA-02 模型版本兼容性强制校验``，代码里写一行
``T-GR-INT-01: 灰度发布路由``。

用法::

    python scripts/generate_traceability.py                     # 生成并写文件
    python scripts/generate_traceability.py --stdout            # 只看结果
    python scripts/generate_traceability.py --strict            # 有无测试覆盖的需求则退出码 1
    python scripts/generate_traceability.py --json out.json     # 同时导出机器可读结果
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_DOC = REPO_ROOT / "docs-for-guiding generating docs" / "开发计划" / "开发计划.md"
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "合规" / "可追溯性矩阵.md"

# 需求编号：T-G<阶段/轨道>-<领域>-<序号>，如 T-G1-DATA-02 / T-GA-INT-01 / T-GR-INT-01
REQ_ID_PATTERN = re.compile(r"T-G[A-Z0-9]+(?:-[A-Z0-9]+)+")

# 代码锚点扫描目录（相对仓库根）
CODE_DIRS = ("app", "edge", "safety_gateway", "alembic", "scripts")
TEST_DIRS = ("tests",)

SKIP_DIR_NAMES = {"__pycache__", "node_modules", ".git", ".venv", "venv", "dist", "build"}
SCAN_SUFFIXES = {".py", ".md", ".sql", ".yml", ".yaml", ".ts", ".tsx", ".json"}


@dataclass
class Requirement:
    req_id: str
    title: str
    code_refs: list[str] = field(default_factory=list)
    test_refs: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.test_refs and self.code_refs:
            return "已追溯"
        if self.test_refs:
            return "仅测试引用"
        if self.code_refs:
            return "有代码无测试"
        return "无锚点"

    @property
    def status_icon(self) -> str:
        # 用 ASCII 标记而非 emoji：Windows 控制台默认 GBK，emoji 会导致
        # --stdout 直接 UnicodeEncodeError。
        return {
            "已追溯": "[OK]",
            "仅测试引用": "[!]",
            "有代码无测试": "[!]",
            "无锚点": "[X]",
        }[self.status]


def _iter_files(dirs: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for name in dirs:
        base = REPO_ROOT / name
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in SCAN_SUFFIXES:
                continue
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            files.append(path)
    return files


def extract_requirements(spec_path: Path = SPEC_DOC) -> list[Requirement]:
    """从任务表抽取需求编号与标题（标题取编号所在行的第一段中文描述）。"""
    if not spec_path.is_file():
        raise FileNotFoundError(f"规格文档不存在: {spec_path}")

    requirements: dict[str, Requirement] = {}
    for line in spec_path.read_text(encoding="utf-8").splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        for index, cell in enumerate(cells):
            match = REQ_ID_PATTERN.fullmatch(cell)
            if not match:
                continue
            title = ""
            for later in cells[index + 1:]:
                if later and not REQ_ID_PATTERN.fullmatch(later):
                    title = later
                    break
            requirements.setdefault(
                match.group(0), Requirement(req_id=match.group(0), title=title)
            )
            break

    return sorted(requirements.values(), key=lambda item: item.req_id)


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def scan_anchors(requirements: list[Requirement]) -> None:
    """就地填充 code_refs / test_refs（同一文件只记一次，避免引用爆炸）。"""
    by_id = {item.req_id: item for item in requirements}
    if not by_id:
        return

    targets: list[tuple[tuple[str, ...], str]] = [(CODE_DIRS, "code_refs"), (TEST_DIRS, "test_refs")]
    for dirs, attr in targets:
        for path in _iter_files(dirs):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                logger.warning("scan_anchors: 未预期异常，按原逻辑降级", exc_info=True)
                continue
            matched_ids = {match.group(0) for match in REQ_ID_PATTERN.finditer(text)}
            if not matched_ids:
                continue
            rel = _relative(path)
            for req_id in matched_ids:
                requirement = by_id.get(req_id)
                if requirement is None:
                    continue
                refs: list[str] = getattr(requirement, attr)
                if rel not in refs:
                    refs.append(rel)

    for requirement in by_id.values():
        requirement.code_refs.sort()
        requirement.test_refs.sort()


def build_report(requirements: list[Requirement]) -> str:
    covered = sum(1 for item in requirements if item.status == "已追溯")
    total = len(requirements)
    untested = [item for item in requirements if not item.test_refs]
    unanchored = [item for item in requirements if not item.code_refs and not item.test_refs]

    lines: list[str] = []
    lines.append("# 可追溯性矩阵（IEC 62304 §5.1.1）")
    lines.append("")
    lines.append("> 本文件由 `scripts/generate_traceability.py` 自动生成，请勿手工编辑。")
    lines.append("> 需求编号来源：`docs-for-guiding generating docs/开发计划/开发计划.md`；")
    lines.append("> 锚点来源：真实仓库中带需求编号的代码/测试文件。")
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append(f"- 需求总数：{total}")
    lines.append(f"- 需求→代码→测试 全追溯：{covered}（{(covered / total * 100) if total else 0:.1f}%）")
    lines.append(f"- 缺少测试锚点：{len(untested)}")
    lines.append(f"- 完全无锚点：{len(unanchored)}")
    lines.append("")

    lines.append("## 矩阵")
    lines.append("")
    lines.append("| 需求编号 | 需求名称 | 代码锚点 | 测试锚点 | 状态 |")
    lines.append("|---|---|---|---|---|")
    for item in requirements:
        code = "<br>".join(f"`{ref}`" for ref in item.code_refs) or "—"
        tests = "<br>".join(f"`{ref}`" for ref in item.test_refs) or "—"
        title = item.title.replace("|", "\\|")
        lines.append(f"| {item.req_id} | {title} | {code} | {tests} | {item.status_icon} {item.status} |")
    lines.append("")

    if untested:
        lines.append("## 缺少测试锚点的需求")
        lines.append("")
        for item in untested:
            hint = "（也无代码锚点）" if not item.code_refs else ""
            lines.append(f"- {item.req_id} {item.title}{hint}")
        lines.append("")

    return "\n".join(lines)


def build_json(requirements: list[Requirement]) -> dict:
    return {
        "generated_by": "scripts/generate_traceability.py",
        "total": len(requirements),
        "fully_traced": sum(1 for item in requirements if item.status == "已追溯"),
        "requirements": [
            {
                "req_id": item.req_id,
                "title": item.title,
                "status": item.status,
                "code_refs": item.code_refs,
                "test_refs": item.test_refs,
            }
            for item in requirements
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 IEC 62304 可追溯性矩阵")
    parser.add_argument("--spec", default=str(SPEC_DOC), help="需求规格文档路径")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Markdown 输出路径")
    parser.add_argument("--json", dest="json_path", default=None, help="可选：JSON 输出路径")
    parser.add_argument("--stdout", action="store_true", help="只打印结果，不写文件")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="存在缺少测试锚点的需求时以退出码 1 结束（可用于 CI 门禁）",
    )
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    requirements = extract_requirements(Path(args.spec))
    scan_anchors(requirements)
    report = build_report(requirements)

    if args.stdout:
        print(report)
    else:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        print(f"[traceability] 已写入 {output_path}（需求 {len(requirements)} 条）")

    if args.json_path:
        json_path = Path(args.json_path)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(build_json(requirements), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[traceability] 已写入 {json_path}")

    untested = [item for item in requirements if not item.test_refs]
    if untested:
        print(f"[traceability] 缺少测试锚点 {len(untested)} 条：")
        for item in untested[:15]:
            print(f"  - {item.req_id} {item.title}")
        if len(untested) > 15:
            print(f"  ... 其余 {len(untested) - 15} 条见报告")
        if args.strict:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
