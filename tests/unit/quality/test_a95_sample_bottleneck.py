"""A.95：评测样本瓶颈的**根因链**守卫（已按自纠后的正确结论更新）。

## 这条守卫经历过一次**结论反转**，值得记录

**第一版结论（错）**：查 `findings->'image_ids'`（JSONB 路径）→ 全库 0 条有影像
→ 断言"判读记录全都没有影像，影像-文本配对是断的"。

**真相**：`image_ids` 是 `diagnosis_results` 的**独立列**，类型 **`text[]`**，
**不在 `findings` JSONB 里**。正确查询是 `array_length(image_ids, 1) > 0`。
修正后真机：**448 条同时有正文+影像**。

**真实瓶颈**：不是"没影像"，是**患者集中度** —— 真机 584 张影像只来自 **11 个患者**
（单患者占 301 张），采集器按 `patient_id + sop_instance_uid` 去重后只能出 171 条独立样本。
**去重口径是对的**（同患者同影像重复入集会虚高指标），不能为了凑数放宽；
要达 300 需**更多患者**，不是更多判读。

这条印证 A.95 的一条教训：**别信断言，先确认字段位置与类型**。
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


def _facts(**over):
    base = {
        "total_results": 912,
        "has_content": 659,
        "has_content_and_image": 448,
        "no_image": 462,
        "sessions_with_images_but_no_diagnosis": 21,
        "n_images": 584,
        "n_patients_with_images": 11,
    }
    base.update(over)
    return base


class TestStoryComputesPatientConstraint:
    def test_reports_patient_count(self):
        """患者数必须出现在根因里 —— 它才是硬约束（584 张只来自 11 人）。"""
        p74 = _load()
        story = p74.build_story(_facts())
        assert "584" in story["root_cause"], story["root_cause"]
        assert "11" in story["root_cause"], story["root_cause"]
        assert "患者" in story["root_cause"]

    def test_blocker_when_below_300(self):
        p74 = _load()
        story = p74.build_story(_facts(has_content_and_image=171))
        assert story["blockers"], "171 < 300 应有 blocker"

    def test_no_blocker_when_at_or_above_300(self):
        """反证：给 448（真机现状）→ 无 blocker（判据不是恒告警）。"""
        p74 = _load()
        story = p74.build_story(_facts())
        assert story["blockers"] == [], story["blockers"]


class TestStoryExplainsWhyNotRelax:
    def test_says_relaxing_dedup_is_wrong(self):
        """必须写明"不能为了凑数放宽去重" —— 否则下轮有人直接放宽。"""
        p74 = _load()
        story = p74.build_story(_facts())
        why = story["why_not_a_collector_bug"]
        assert "放宽" in why or "不能为了凑数" in why, why
        assert "去重" in why, "没解释去重口径"
        assert "虚高" in why or "不可独立计分" in why, "没解释放宽的后果"


class TestStoryRecordsSelfCorrection:
    def test_records_field_mistake(self):
        """必须记录"第一版查错字段"这次自纠 —— 否则旧结论会被当事实。"""
        p74 = _load()
        story = p74.build_story(_facts())
        joined = " ".join(story["same_root_as_other_findings"])
        assert "自纠" in joined or "推翻" in joined, joined
        assert "findings->'image_ids'" in joined or "image_ids 列" in joined, (
            "没写清错在哪个字段"
        )

    def test_keeps_still_valid_findings(self):
        """仍然成立的发现（影像腿不产特征）要保留。"""
        p74 = _load()
        story = p74.build_story(_facts())
        joined = " ".join(story["same_root_as_other_findings"])
        assert "影像腿" in joined

    def test_gives_executable_steps(self):
        p74 = _load()
        story = p74.build_story(_facts())
        steps = story["executable_next_steps"]
        assert len(steps) >= 3
        joined = " ".join(steps)
        assert "带影像" in joined or "带上影像" in joined
        assert "反向补判读" in joined or "277" in joined or "584" in joined
