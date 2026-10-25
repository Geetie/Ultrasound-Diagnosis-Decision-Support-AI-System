"""A.95：评测样本瓶颈的**根因链**守卫。

## 为什么要守这个

`p73` 判出"157 样本 < 300，门控无法判定" —— 但那只是**症状**。
真机取证发现根因不是"懒得攒"，而是：

```
判读总数 656
  └─ 有正文 405
      └─ **有影像 0**  ← 断在这里
```

采集器要求「有影像」（去重键 + L2 判官上下文），所以只能采到 30 条。
**放宽这个条件会得到不可去重、不可判分的样本，比没有更糟。**

更完整的图景：另有 **277 个 session 有影像但没有判读** —— 两个方向的数据都断着。

## 本守卫钉什么

1. `build_story` 必须**算出 gap**（有正文 − 有正文且有影像）；
2. 当 gap > 0 时，根因描述必须点明"缺影像"而不是含糊的"样本不足"；
3. 必须写明**为什么不放宽采集条件**（否则下一轮会有人直接放宽）；
4. 必须关联到其它三处同源发现（否则会被当成孤立问题）；
5. 必须给可执行下一步（不是"待攒"）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.track_quality]

_REPO = Path(__file__).resolve().parents[3]
_P74 = _REPO / "scripts" / "audit" / "p74_eval_sample_bottleneck.py"


def _load():
    spec = importlib.util.spec_from_file_location("p74_mod", _P74)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["p74_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestStoryComputesGap:
    def test_gap_reported_when_no_images(self):
        """真实形态：405 有正文、0 有影像 → 根因必须点明缺影像。"""
        p74 = _load()
        story = p74.build_story(
            {
                "total_results": 656,
                "has_content": 405,
                "has_content_and_image": 0,
                "no_image": 656,
                "sessions_with_images_but_no_diagnosis": 277,
            }
        )
        assert "405" in story["root_cause"]
        assert "没有任何影像" in story["root_cause"], story["root_cause"]
        assert "0" in story["blockers"][0] or "0 < 300" in story["blockers"][0]

    def test_no_blocker_when_enough_samples(self):
        """反证：给 400 条有影像 → 无 blocker（判据不是恒告警）。"""
        p74 = _load()
        story = p74.build_story(
            {
                "total_results": 500,
                "has_content": 400,
                "has_content_and_image": 400,
                "no_image": 100,
                "sessions_with_images_but_no_diagnosis": 0,
            }
        )
        assert story["blockers"] == []


class TestStoryExplainsWhyNotRelax:
    def test_says_relaxing_is_worse(self):
        """必须写明"放宽采集条件比没有更糟"，否则下一轮会有人直接放宽。"""
        p74 = _load()
        story = p74.build_story(
            {
                "total_results": 656,
                "has_content": 405,
                "has_content_and_image": 0,
                "no_image": 656,
                "sessions_with_images_but_no_diagnosis": 277,
            }
        )
        why = story["why_not_a_collector_bug"]
        assert "比没有更糟" in why or "更糟" in why, why
        assert "去重" in why, "没解释去重口径为什么需要影像"


class TestStoryLinksToSameRoot:
    def test_links_other_findings(self):
        """必须关联到 A.94/Q2、Q3、Q33 —— 否则会被当孤立问题。"""
        p74 = _load()
        story = p74.build_story(
            {
                "total_results": 1,
                "has_content": 1,
                "has_content_and_image": 0,
                "no_image": 1,
                "sessions_with_images_but_no_diagnosis": 0,
            }
        )
        joined = " ".join(story["same_root_as_other_findings"])
        for kw in ("影像腿", "no_image", "样本"):
            assert kw in joined, f"没关联到 {kw}"
        assert "影像-文本配对是断的" in joined, "没有给出统一结论"

    def test_gives_executable_steps(self):
        p74 = _load()
        story = p74.build_story(
            {
                "total_results": 1,
                "has_content": 1,
                "has_content_and_image": 0,
                "no_image": 1,
                "sessions_with_images_but_no_diagnosis": 0,
            }
        )
        steps = story["executable_next_steps"]
        assert len(steps) >= 3
        joined = " ".join(steps)
        assert "带影像" in joined or "带上影像" in joined, "缺少「让判读带影像」这条关键下一步"
        assert "277" in joined or "反向补判读" in joined, "没提反向补判读"
