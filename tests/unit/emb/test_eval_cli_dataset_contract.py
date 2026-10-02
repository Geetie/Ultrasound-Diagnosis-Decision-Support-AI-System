"""评估/微调 CLI 的数据集契约守卫。

## 为什么有这条测试

`BUSIAdapter` 原来是"路径不存在 → 静默造 6 条合成样本"，两个 CLI 都直接吃这个行为：
**指向空目录也能"评估成功"并写出 `eval_report.json`**。适配器侧已改为 fail-fast
（见 `test_busi_adapter_contract.py`），这条测试钉住 CLI 侧的两件事：

1. 开关**默认关闭**（`--allow-synthetic` 必须显式给），且确实被透传到适配器；
2. 数据集侧失败时**返回非 0 退出码 4**（区别于 2=模型加载失败、3=评估失败），
   并打印明确原因 —— 让 CI/流水线能区分"数据没到位"和"模型有问题"。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def eval_cli():
    return _load_script("evaluate_ultrasound_clip")


@pytest.fixture(scope="module")
def finetune_cli():
    return _load_script("finetune_ultrasound_clip")


def _parse(module, monkeypatch, argv: list[str]):
    monkeypatch.setattr(sys, "argv", ["prog", *argv])
    return module.parse_args()


class TestFlagDefaultsToOff:
    def test_eval_allow_synthetic_defaults_false(self, eval_cli, monkeypatch):
        args = _parse(
            eval_cli, monkeypatch, ["--model-path", "m", "--test-dataset-path", "d"]
        )

        assert args.allow_synthetic is False, (
            "默认必须关闭：否则指向空目录也能产出'看起来正常'的评估报告"
        )

    def test_eval_allow_synthetic_can_be_enabled(self, eval_cli, monkeypatch):
        args = _parse(
            eval_cli,
            monkeypatch,
            ["--model-path", "m", "--test-dataset-path", "d", "--allow-synthetic"],
        )

        assert args.allow_synthetic is True

    def test_finetune_allow_synthetic_defaults_false(
        self, finetune_cli, monkeypatch, tmp_path
    ):
        # 微调 CLI 的 --output-dir 是必填项（不给会 SystemExit:2，第一版就栽在这）
        args = _parse(
            finetune_cli,
            monkeypatch,
            ["--dataset-path", "d", "--output-dir", str(tmp_path / "out")],
        )

        assert args.allow_synthetic is False


class TestSourceWiresFlagIntoAdapter:
    """源码断言：CLI 的开关必须真的传到适配器，而不是解析了却不用。"""

    @pytest.mark.parametrize("script", ["evaluate_ultrasound_clip", "finetune_ultrasound_clip"])
    def test_allow_synthetic_is_forwarded(self, script: str):
        code = (SCRIPTS / f"{script}.py").read_text(encoding="utf-8")

        assert "allow_synthetic=args.allow_synthetic" in code
        # 不得存在仍按旧行为构造适配器的地方（那会让 fail-fast 被绕过）
        assert "BUSIAdapter(dataset_path=args" not in code


class TestMissingDatasetFailsWithDistinctExitCode:
    def test_eval_returns_4_on_missing_dataset(self, eval_cli, monkeypatch, tmp_path, capsys):
        missing = tmp_path / "no-such-dataset"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "prog",
                "--model-path",
                "m",
                "--test-dataset-path",
                str(missing),
            ],
        )

        rc = eval_cli.main()

        assert rc == 4, "数据集缺失必须是独立退出码，供流水线区分原因"
        out = capsys.readouterr().out
        assert "[ERROR]" in out
        assert "合成样本" in out, "错误信息必须点明风险，而不是只说找不到路径"

    def test_allow_synthetic_gets_past_dataset_stage(
        self, eval_cli, monkeypatch, tmp_path, capsys
    ):
        """显式 opt-in 后应越过数据集阶段（随后因无权重而返回 2）。"""
        # 拦掉真实权重解析：不挂网络（否则非本地路径会被当作 HF repo id 去解析）
        def _fail_load(self, *args, **kwargs):
            raise RuntimeError("no weights")

        monkeypatch.setattr(eval_cli.UltrasoundCLIPModel, "load", _fail_load)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "prog",
                "--model-path",
                str(tmp_path / "no-weights"),
                "--test-dataset-path",
                str(tmp_path / "no-such-dataset"),
                "--allow-synthetic",
                "--device",
                "cpu",
                "--max-samples",
                "3",
            ],
        )

        rc = eval_cli.main()

        assert rc != 4, "开了开关就不该停在数据集阶段"
        assert "测试样本数: 3" in capsys.readouterr().out
