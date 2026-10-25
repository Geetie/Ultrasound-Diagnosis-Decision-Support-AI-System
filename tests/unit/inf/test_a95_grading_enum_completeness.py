"""A.95 守卫：约束解码的枚举**必须等于生产分级体系**（不许漏项）。

## 真机缺陷（Q29 重测暴露，2026-10-26）

P0-2 要求「780 例重测，分级一致率 ≥95%」。真机重测（157 例 × 4 轮）得到：
**常规档 75.18%、高风险档 54.01%** —— 远低于阈值。

查下去发现约束解码**本身是生效的**（输出全是合法枚举值），但**枚举集不全**：

| 器官 | 原枚举 | 生产常量 | 漏了什么 |
|---|---|---|---|
| thyroid | `2 / 3 / 4a / 4b / 5` | `TR1…TR5` | **缺 TR1** |
| liver | `1 / 2 / 3 / 4 / 5` | `LR-1…LR-5 / LR-M / LR-TIV` | **缺 LR-M / LR-TIV** |
| cardiac | `normal/mild/moderate/severe` | 同 | ✅ |

后果最严重的不是"少一个等级"，而是**漏掉的是最需要报出来的两类**：
* `LR-M` = 可能恶性但非 HCC 特异（要往恶性方向提示）
* `LR-TIV` = 癌栓（直接改变分期与处置）
两者被枚举排除 → 模型只能塞进 LR-4/LR-5，**属于临床语义降级**。

同时 TR1（正常）被排除 → 正常病例被迫报成 TR2，**假阳性风险**。

## 另一个坑：模块级导入会循环

`specialist/base.py` 与三个专科都 `from app.inference.grading_schema import ...`，
而枚举要从专科常量派生 → 模块级导入命中
`partially initialized module 'app.inference.grading_schema'` → **静默走 fallback
（等于没修）**。所以必须**调用时延迟解析**（`_production_levels` + `_LazyEnums`）。

## 本守卫钉什么

1. `thyroid` 枚举**必须含 `1`**（TR1）—— 反证：删掉即红；
2. `liver` 枚举**必须含 `M` / `TIV`** —— 反证：删掉即红；
3. `cardiac` 四档齐全；
4. 枚举能从**生产常量**派生成功（不是 fallback）—— 反证：制造导入失败即红；
5. 模块级 `import grading_schema` **不触发** fallback 告警（即无循环导入）。
"""
from __future__ import annotations

import logging

import pytest

from app.inference.grading_schema import GRADING_ENUMS, get_grading_enum

pytestmark = [pytest.mark.track_inf]


class TestEnumCompleteness:
    """枚举必须覆盖生产分级体系的全部取值。"""

    def test_thyroid_has_tr1(self):
        """反证腿：把枚举改回 `["2","3","4a","4b","5"]` 即红。

        真机后果：TR1（正常）病例被逼报成 TR2 → 假阳性。
        """
        enum = get_grading_enum("thyroid")
        assert enum is not None
        assert "1" in enum, (
            f"thyroid 枚举缺 TR1：{enum} —— "
            "正常病例会被迫报成 TR2（假阳性），且与 TI_RADS_LEVELS 不一致"
        )

    def test_liver_has_lr_m_and_tiv(self):
        """反证腿：删掉 M/TIV 即红。

        真机后果：LR-M（可能恶性非 HCC 特异）与 LR-TIV（癌栓）无法输出
        → 模型只能塞进 LR-4/LR-5 = **临床语义降级**。
        """
        enum = get_grading_enum("liver")
        assert enum is not None
        assert "M" in enum, f"liver 枚举缺 LR-M：{enum}"
        assert "TIV" in enum, f"liver 枚举缺 LR-TIV：{enum}"

    def test_cardiac_four_levels(self):
        enum = get_grading_enum("cardiac")
        assert enum == ["normal", "mild", "moderate", "severe"]

    def test_thyroid_covers_all_five_tr_levels(self):
        """TR1-TR5 都在（4a/4b 是 TR4 的细分，不替代 4）。"""
        enum = set(get_grading_enum("thyroid") or [])
        for lv in ("1", "2", "3", "5"):
            assert lv in enum, f"thyroid 枚举缺 {lv}"
        assert "4a" in enum or "4" in enum, "thyroid 枚举缺 TR4 形态"


class TestDerivedFromProduction:
    """枚举必须从生产常量派生，不许另抄一份。"""

    def test_matches_production_liver_levels(self):
        from app.agents.specialist.liver import LI_RADS_LEVELS

        enum = get_grading_enum("liver") or []
        expected = [x.removeprefix("LR-") for x in LI_RADS_LEVELS]
        assert enum == expected, (
            f"约束解码枚举 {enum} ≠ 生产 LI_RADS_LEVELS 派生 {expected}"
        )

    def test_no_fallback_warning_on_import(self, caplog):
        """模块级导入**不许**触发 fallback（循环导入的征兆）。

        反证腿：把 `_production_levels` 的导入改回模块级 → 冷启动时
        这里会看到 `grading_enum_fallback_to_snapshot`。
        """
        with caplog.at_level(logging.WARNING):
            # 重新解析一次（清缓存后强制走真实派生路径）
            import app.inference.grading_schema as gs

            gs._LEVELS_CACHE = None
            got = gs._production_levels()
        fallback_hits = [r for r in caplog.records if "grading_enum_fallback" in r.message]
        assert not fallback_hits, (
            "枚举走了 fallback（说明导入链路有问题，约束解码用的可能是快照而非生产常量）："
            + str([r.message[:120] for r in fallback_hits])
        )
        assert got["thyroid"] == ["1", "2", "3", "4a", "4b", "5"]


class TestLazyProxyUsable:
    """`GRADING_ENUMS` 代理必须支持常见访问方式（不继承 dict，避免递归）。"""

    def test_subscript(self):
        assert GRADING_ENUMS["cardiac"] == ["normal", "mild", "moderate", "severe"]

    def test_get(self):
        assert GRADING_ENUMS.get("nonexistent") is None

    def test_contains_and_len(self):
        assert "thyroid" in GRADING_ENUMS
        assert len(GRADING_ENUMS) == 3

    def test_items_iterable(self):
        keys = {k for k, _ in GRADING_ENUMS.items()}
        assert keys == {"thyroid", "liver", "cardiac"}

    def test_repr_does_not_recurse(self):
        """反证腿：改回 dict 子类且重写 __len__/__iter__ → 这里 RecursionError。"""
        r = repr(GRADING_ENUMS)
        assert "thyroid" in r
