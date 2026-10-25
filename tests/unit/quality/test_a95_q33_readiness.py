"""A.95 Q33-①/②：**未做的项必须留下可执行的就绪度判据**。

## 为什么这条守卫值得存在

Q33「P1 升级包」4 子项，本轮只完成 ③Cornerstone3D（早已完成）+ ④vLLM 版本固定。
① Qwen3 基座升级、② RAGFlow DeepDoc 接入 **本轮没做**。

"没做"有两种写法：
* 含糊带过（"后续推进"）→ 下一轮仍然不知道卡在哪、要什么条件；
* 留下**机器可读的就绪度检查表** → 下一轮照着清单补条件即可。

本守卫钉的是后者：脚本必须真的**算出阻塞项**，而不是只打印一句"暂不做"。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.track_quality]

_REPO = Path(__file__).resolve().parents[3]
_P73 = _REPO / "scripts" / "audit" / "p73_qwen3_upgrade_readiness.py"
_P72 = _REPO / "scripts" / "audit" / "p72_doc_parse_baseline.py"


def _load_p73():
    spec = importlib.util.spec_from_file_location("p73_mod", _P73)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["p73_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestAssessBlocksWhenSampleTooSmall:
    def test_small_sample_not_ready(self):
        """反证腿：157 条（当前真实值）必须判 not ready。"""
        p73 = _load_p73()
        res = p73.assess(157, 45.4)
        assert res["ready"] is False
        assert any("评测样本不足" in b for b in res["blockers"]), res["blockers"]

    def test_enough_sample_and_vram_removes_those_blockers(self):
        """反证：给 500 条 + 显存够 → 那两条 blocker 消失（说明判据不是恒 False）。

        注意不断言 `ready is True`：真实环境还可能有第三条 blocker
        （`.env` 不存在 → 无回滚路径，本机就是这样）。只验"这两条被清掉"。
        """
        p73 = _load_p73()
        res = p73.assess(500, 20.0)
        joined = " ".join(res["blockers"])
        assert "评测样本不足" not in joined, joined
        assert "显存余量" not in joined and "显存不可探测" not in joined, joined

    def test_vram_unknown_is_blocker_not_fake_number(self):
        """显存探不到时必须计为 blocker，**不许编一个数字**。"""
        p73 = _load_p73()
        res = p73.assess(500, None)
        assert res["ready"] is False
        assert res["checks"]["vram"]["used"] is None, "探不到却编了数字"
        assert any("显存" in b for b in res["blockers"])


class TestAcceptanceCriteriaPresent:
    def test_four_quantitative_criteria(self):
        p73 = _load_p73()
        assert len(p73.ACCEPTANCE) >= 4
        joined = " ".join(p73.ACCEPTANCE)
        for kw in ("幻觉增量", "准确率保留", "结构化输出", "分级一致率"):
            assert kw in joined, f"验收清单缺 {kw}"

    def test_min_sample_is_300(self):
        p73 = _load_p73()
        assert p73.MIN_EVAL_SAMPLES == 300


class TestNoFalseClaims:
    @pytest.mark.parametrize("path", [_P72, _P73])
    def test_no_upgrade_done_claims(self, path):
        src = path.read_text(encoding="utf-8")
        for bad in ("已完成升级", "已接入 DeepDoc", "Qwen3 已上线", "已完成替换"):
            assert bad not in src, f"{path.name} 出现假声明: {bad}"

    def test_p73_states_decision_explicitly(self):
        src = _P73.read_text(encoding="utf-8")
        assert "DO_NOT_UPGRADE_THIS_ROUND" in src, "没有明确的决策标记"

    def test_p73_explains_not_technical_infeasibility(self):
        src = _P73.read_text(encoding="utf-8")
        assert "验证条件不具备" in src or "不是技术不可行" in src
