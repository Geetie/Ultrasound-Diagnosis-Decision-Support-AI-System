"""T-G1-DOC-01：可追溯性矩阵生成器测试。

要点：
1. 需求编号必须从规格文档的**表格**里抽出来（不是全文乱扫，否则会把正文里
   顺口提到的编号也当成需求条目）；
2. 锚点扫描必须区分代码目录与测试目录，状态判定据此得出；
3. ``--strict`` 用于 CI 门禁，缺测试锚点时必须返回非 0。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate_traceability.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("generate_traceability", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_traceability"] = module
    spec.loader.exec_module(module)
    return module


gt = _load_module()


SPEC_SAMPLE = """# 开发计划

| 任务ID | 任务名称 | 优先级 | 依赖 | 轨道 | 交付物 | 验收标准 |
|---|---|---|---|---|---|---|
| T-G1-DATA-02 | 模型版本兼容性验证 | P0 | T-G0-INF-01 | DATA | 校验器 | 启动时自动校验 |
| T-G1-DOC-01 | 可追溯性矩阵 | P0 | 无 | DOC | 生成脚本 | 每条需求有测试 |
| T-G9-XXX-99 | 无锚点需求 | P3 | 无 | XXX | — | — |

正文里顺口提到的 T-G1-DATA-02 不应重复计入。
"""


def _prepare_repo(tmp_path, monkeypatch, spec_text=SPEC_SAMPLE, code_files=None, test_files=None):
    """搭一个临时仓库，把生成器的路径常量指过去。"""
    spec_dir = tmp_path / "docs" / "计划"
    spec_dir.mkdir(parents=True)
    spec_path = spec_dir / "开发计划.md"
    spec_path.write_text(spec_text, encoding="utf-8")

    for name, content in (code_files or {}).items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    for name, content in (test_files or {}).items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    monkeypatch.setattr(gt, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gt, "SPEC_DOC", spec_path)
    monkeypatch.setattr(gt, "CODE_DIRS", ("app",))
    monkeypatch.setattr(gt, "TEST_DIRS", ("tests",))
    return spec_path


class TestExtractRequirements:
    def test_extracts_id_and_title_from_table(self, tmp_path, monkeypatch):
        spec_path = _prepare_repo(tmp_path, monkeypatch)

        requirements = gt.extract_requirements(spec_path)
        by_id = {item.req_id: item for item in requirements}

        assert set(by_id) == {"T-G1-DATA-02", "T-G1-DOC-01", "T-G9-XXX-99"}
        assert by_id["T-G1-DATA-02"].title == "模型版本兼容性验证"

    def test_body_mentions_do_not_create_requirements(self, tmp_path, monkeypatch):
        spec_path = _prepare_repo(tmp_path, monkeypatch)

        requirements = gt.extract_requirements(spec_path)

        # 正文那句提到 T-G1-DATA-02，但由于不在表格行里，不应产生第二条
        assert len(requirements) == 3

    def test_missing_spec_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            gt.extract_requirements(tmp_path / "nope.md")


class TestScanAnchors:
    def test_classifies_code_and_test_refs(self, tmp_path, monkeypatch):
        _prepare_repo(
            tmp_path,
            monkeypatch,
            code_files={"app/config/validator.py": "# T-G1-DATA-02\n"},
            test_files={"tests/unit/test_validator.py": "# T-G1-DATA-02\n"},
        )
        requirements = gt.extract_requirements(gt.SPEC_DOC)
        gt.scan_anchors(requirements)
        by_id = {item.req_id: item for item in requirements}

        assert by_id["T-G1-DATA-02"].code_refs == ["app/config/validator.py"]
        assert by_id["T-G1-DATA-02"].test_refs == ["tests/unit/test_validator.py"]
        assert by_id["T-G1-DATA-02"].status == "已追溯"

    def test_code_without_test_is_flagged(self, tmp_path, monkeypatch):
        _prepare_repo(
            tmp_path,
            monkeypatch,
            code_files={"app/config/validator.py": "# T-G1-DATA-02\n"},
        )
        requirements = gt.extract_requirements(gt.SPEC_DOC)
        gt.scan_anchors(requirements)
        by_id = {item.req_id: item for item in requirements}

        assert by_id["T-G1-DATA-02"].status == "有代码无测试"
        assert by_id["T-G1-DOC-01"].status == "无锚点"

    def test_same_file_counted_once(self, tmp_path, monkeypatch):
        _prepare_repo(
            tmp_path,
            monkeypatch,
            test_files={
                "tests/unit/test_validator.py": "# T-G1-DATA-02\n# T-G1-DATA-02 again\n"
            },
        )
        requirements = gt.extract_requirements(gt.SPEC_DOC)
        gt.scan_anchors(requirements)
        by_id = {item.req_id: item for item in requirements}

        assert by_id["T-G1-DATA-02"].test_refs == ["tests/unit/test_validator.py"]

    def test_pycache_is_skipped(self, tmp_path, monkeypatch):
        _prepare_repo(
            tmp_path,
            monkeypatch,
            test_files={"tests/__pycache__/stale.py": "# T-G1-DATA-02\n"},
        )
        requirements = gt.extract_requirements(gt.SPEC_DOC)
        gt.scan_anchors(requirements)
        by_id = {item.req_id: item for item in requirements}

        assert by_id["T-G1-DATA-02"].test_refs == []


class TestReport:
    def test_report_lists_summary_and_matrix(self, tmp_path, monkeypatch):
        _prepare_repo(
            tmp_path,
            monkeypatch,
            code_files={"app/config/validator.py": "# T-G1-DATA-02\n"},
            test_files={"tests/unit/test_validator.py": "# T-G1-DATA-02\n"},
        )
        requirements = gt.extract_requirements(gt.SPEC_DOC)
        gt.scan_anchors(requirements)
        report = gt.build_report(requirements)

        assert "可追溯性矩阵" in report
        assert "需求总数：3" in report
        assert "全追溯：1" in report
        assert "`app/config/validator.py`" in report
        assert "T-G9-XXX-99" in report

    def test_untested_section_present(self, tmp_path, monkeypatch):
        _prepare_repo(tmp_path, monkeypatch)
        requirements = gt.extract_requirements(gt.SPEC_DOC)
        gt.scan_anchors(requirements)
        report = gt.build_report(requirements)

        assert "缺少测试锚点的需求" in report
        assert "T-G1-DOC-01" in report

    def test_json_export_shape(self, tmp_path, monkeypatch):
        _prepare_repo(tmp_path, monkeypatch)
        requirements = gt.extract_requirements(gt.SPEC_DOC)
        gt.scan_anchors(requirements)
        payload = gt.build_json(requirements)

        assert payload["total"] == 3
        assert payload["fully_traced"] == 0
        assert len(payload["requirements"]) == 3


class TestCli:
    def test_stdout_mode_writes_nothing(self, tmp_path, monkeypatch, capsys):
        spec_path = _prepare_repo(tmp_path, monkeypatch)
        output = tmp_path / "out" / "matrix.md"

        exit_code = gt.main(["--spec", str(spec_path), "--output", str(output), "--stdout"])

        assert exit_code == 0
        assert not output.exists()
        assert "可追溯性矩阵" in capsys.readouterr().out

    def test_writes_report_file(self, tmp_path, monkeypatch):
        spec_path = _prepare_repo(tmp_path, monkeypatch)
        output = tmp_path / "out" / "matrix.md"

        exit_code = gt.main(["--spec", str(spec_path), "--output", str(output)])

        assert exit_code == 0
        assert "T-G1-DATA-02" in output.read_text(encoding="utf-8")

    def test_strict_returns_one_when_test_anchor_missing(self, tmp_path, monkeypatch):
        spec_path = _prepare_repo(tmp_path, monkeypatch)

        exit_code = gt.main(
            ["--spec", str(spec_path), "--output", str(tmp_path / "m.md"), "--strict"]
        )

        assert exit_code == 1

    def test_strict_passes_when_all_requirements_tested(self, tmp_path, monkeypatch):
        spec_path = _prepare_repo(
            tmp_path,
            monkeypatch,
            test_files={
                "tests/unit/test_all.py": "T-G1-DATA-02 T-G1-DOC-01 T-G9-XXX-99\n"
            },
        )

        exit_code = gt.main(
            ["--spec", str(spec_path), "--output", str(tmp_path / "m.md"), "--strict"]
        )

        assert exit_code == 0

    def test_json_output_path(self, tmp_path, monkeypatch):
        spec_path = _prepare_repo(tmp_path, monkeypatch)
        json_path = tmp_path / "out" / "matrix.json"

        gt.main(
            [
                "--spec",
                str(spec_path),
                "--output",
                str(tmp_path / "m.md"),
                "--json",
                str(json_path),
            ]
        )

        assert '"total": 3' in json_path.read_text(encoding="utf-8")


class TestRealRepository:
    """对真实仓库的冒烟检查：防住"脚本能跑但抽不到需求"这类静默失效。"""

    def test_real_spec_yields_requirements(self):
        monkeypatch_root = gt.REPO_ROOT
        assert monkeypatch_root == REPO_ROOT

        requirements = gt.extract_requirements()

        assert len(requirements) >= 100
        ids = {item.req_id for item in requirements}
        assert "T-G1-DATA-02" in ids
        assert "T-G1-DOC-01" in ids

    def test_real_scan_classifies_known_wiring(self):
        requirements = gt.extract_requirements()
        gt.scan_anchors(requirements)
        by_id = {item.req_id: item for item in requirements}

        # 本轮接线的两项必须有测试锚点
        assert any(ref.startswith("tests/") for ref in by_id["T-G1-DATA-02"].test_refs)
        assert "T-G1-DOC-01" in by_id
        assert by_id["T-G1-DOC-01"].code_refs, "生成器自身应被识别为代码锚点"
