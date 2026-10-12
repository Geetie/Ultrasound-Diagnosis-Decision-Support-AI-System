"""影像叠加对比度的**计算式**守卫（A.55）。

为什么必须有这组测试（用户指令：修法要鲁棒、科学、准确，不许穷举色板）：
旧做法是"硬编码一份深色色板 + 硬编码像素坐标"去量 —— token 或主题一变就静默失效
（实测漏掉了浅色主题下 `--color-text-primary=#1F2937` 对影像底板只有 **1.22**，
叠加标注几乎不可见，而脚本照样打印"通过"）。

本测试把不变量**算**出来并钉住：
  ① 叠加 token 必须在**每个主题**下都显式声明，且跨主题**同值**（影像底板不随主题切换）；
  ② 每个主题里，叠加 token 对影像底板的对比度必须 ≥ AA 正文 4.5（计算，不硬编码）；
  ③ UI 色板 token 在浅色主题下**确实**不达标 —— 反向证明"必须用叠加 token"，防回退；
  ④ 引用叠加色的样式/组件不得再引用 UI 主题色（防止悄悄改回去）；
  ⑤ **产物过期守卫**：`design-tokens.css` 里的每个 `--color-*` 都必须能在生产 CSS 产物里找到
     —— 改了 token 忘了 `npm run build` / 忘了部署产物，这里立刻红。
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TOKENS_CSS = ROOT / "frontend/src/styles/design-tokens.css"
WORKSPACE_CSS = ROOT / "frontend/src/styles/workspace.css"
SHIPPED_CSS_GLOB = "app/static/ui/assets/*.css"

pytestmark = [pytest.mark.track_api]


def _load_contrast_module():
    path = ROOT / "scripts/contrast.py"
    spec = importlib.util.spec_from_file_location("uai_contrast", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


contrast = _load_contrast_module()


def _shipped_css_text() -> str:
    files = sorted((ROOT / "app/static/ui/assets").glob("*.css"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        pytest.fail("没有生产 CSS 产物 —— 先 `cd frontend && npm run build` 并部署到 app/static/ui")
    return files[0].read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- ①② token 不变量
def test_source_tokens_declare_overlay_group_in_every_theme():
    themes = contrast.resolve_themes(contrast.parse_color_scopes(TOKENS_CSS.read_text(encoding="utf-8")))
    assert {"dark", "light"} <= set(themes), f"主题解析不全: {sorted(themes)}"
    for name, colors in themes.items():
        # 缺任何一个 → 抛 InputError（不是静默回退）
        contrast.require_vars(name, colors)


def test_overlay_tokens_are_theme_invariant():
    """叠加色跨主题同值：影像底板不随主题切换，叠加色也必须如此。"""
    themes = contrast.resolve_themes(contrast.parse_color_scopes(TOKENS_CSS.read_text(encoding="utf-8")))
    for var in contrast.OVERLAY_TEXT_VARS + contrast.OVERLAY_STRUCTURAL_VARS:
        values = {themes[t][var] for t in themes}
        assert len(values) == 1, f"{var} 在不同主题取值不同: {values}（叠加色必须同值）"


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("var", list(contrast.OVERLAY_TEXT_VARS))
def test_overlay_token_contrast_computed_meets_aa(theme: str, var: str):
    """核心断言：**算**出来的对比度 ≥ 4.5（不是看色板"长得像"）。"""
    themes = contrast.resolve_themes(contrast.parse_color_scopes(TOKENS_CSS.read_text(encoding="utf-8")))
    colors = themes[theme]
    ratio = contrast.contrast_ratio(
        contrast.parse_color(colors[var]), contrast.parse_color(colors[contrast.PLATE_VAR])
    )
    assert ratio >= 4.5, (
        f"主题 {theme} 的 {var}={colors[var]} 压在影像底板 {colors[contrast.PLATE_VAR]} 上只有 {ratio:.2f} < 4.5"
    )


def test_light_theme_ui_tokens_would_fail_on_plate():
    """反向证据（防回退）：浅色主题的 UI 色板 token 压在影像底板上**不达标**。"""
    themes = contrast.resolve_themes(contrast.parse_color_scopes(TOKENS_CSS.read_text(encoding="utf-8")))
    light = themes["light"]
    plate = contrast.parse_color(light[contrast.PLATE_VAR])
    bad = {
        var: contrast.contrast_ratio(contrast.parse_color(light[var]), plate)
        for var in ("--color-text-primary", "--color-text-secondary", "--color-attention")
        if var in light
    }
    assert bad["--color-text-primary"] < 4.5, f"预期浅色 text-primary 在影像底板上不达标，实测 {bad}"
    # 顺带钉住"这就是穷举色板看不见的那个洞"的量级
    assert bad["--color-text-primary"] < 2.0


# ---------------------------------------------------------------- ④ 引用侧不回退
def test_image_area_css_uses_overlay_tokens_only():
    body = WORKSPACE_CSS.read_text(encoding="utf-8")
    # 只取影像区那几条规则（.ws-image* / .ws-anno* / .ws-measure*）
    rules = {
        sel.strip(): decl
        for sel, decl in contrast.RULE.findall(body)
        if re.search(r"\.ws-(image|anno|measure)", sel)
    }
    assert rules, "没解析到影像区规则（选择器改名了？）"
    forbidden = []
    for sel, decl in rules.items():
        for var in re.findall(r"var\((--color-[a-z0-9-]+)\)", decl):
            if var in contrast.UI_TOKENS or var == "--color-border":
                forbidden.append(f"{sel} → {var}")
    assert not forbidden, f"影像区样式不应引用 UI 主题色（底板不随主题切换）: {forbidden}"
    assert "var(--color-overlay-attention)" in body
    assert "var(--color-overlay-text)" in body


def test_image_components_use_overlay_tokens_only():
    forbidden = []
    for name in (
        "frontend/src/components/workspace/WorkspaceImageRegion.tsx",
        "frontend/src/components/workspace/WorkspaceDicomLayer.tsx",
    ):
        src = (ROOT / name).read_text(encoding="utf-8")
        for var in re.findall(r"var\((--color-[a-z0-9-]+)\)", src):
            if var in contrast.UI_TOKENS or var == "--color-border":
                forbidden.append(f"{name} → {var}")
    assert not forbidden, f"影像区组件不应引用 UI 主题色: {forbidden}"


# ---------------------------------------------------------------- ⑤ 产物过期守卫
def test_shipped_css_is_not_stale():
    """改了 token 但没重建/没部署产物 → 这里红（避免"源码对了、线上还是旧的"）。"""
    source = contrast.parse_color_scopes(TOKENS_CSS.read_text(encoding="utf-8"))
    source_colors = {v: val for decls in source.values() for v, val in decls.items()}
    shipped = contrast.parse_color_scopes(_shipped_css_text())
    shipped_colors = {v: val for decls in shipped.values() for v, val in decls.items()}
    missing = sorted(v for v in source_colors if v not in shipped_colors)
    assert not missing, f"生产 CSS 里缺这些 token（产物过期，请重建并部署）: {missing}"
    for var in contrast.REQUIRED_VARS:
        assert shipped_colors[var] == source_colors[var], (
            f"{var} 产物值 {shipped_colors[var]} != 源码值 {source_colors[var]}（产物过期）"
        )


def test_shipped_css_has_both_themes_with_overlay_group():
    themes = contrast.resolve_themes(contrast.parse_color_scopes(_shipped_css_text()))
    assert {"dark", "light"} <= set(themes), f"产物里主题不全: {sorted(themes)}"
    for name, colors in themes.items():
        contrast.require_vars(name, colors)


# ---------------------------------------------------------------- 失败即报错（不静默）
def test_missing_overlay_token_raises_instead_of_silent_pass():
    themes = contrast.resolve_themes(contrast.parse_color_scopes(TOKENS_CSS.read_text(encoding="utf-8")))
    broken = dict(themes["light"])
    broken.pop("--color-overlay-attention")
    with pytest.raises(contrast.InputError):
        contrast.require_vars("light", broken)


def test_pick_css_raises_when_artifact_missing(tmp_path):
    with pytest.raises(contrast.InputError):
        contrast.pick_css(None, str(tmp_path))


def test_sidecar_validation_rejects_incomplete_and_empty(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"elements": [{"name": "x", "color": "#fff"}]}), encoding="utf-8")
    with pytest.raises(contrast.InputError):
        contrast.load_sidecar(str(bad))
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"elements": []}), encoding="utf-8")
    with pytest.raises(contrast.InputError):
        contrast.load_sidecar(str(empty))
    with pytest.raises(contrast.InputError):
        contrast.load_sidecar(str(tmp_path / "nope.json"))


# ---------------------------------------------------------------- 色彩数学自检
def test_wcag_math_known_values():
    assert abs(contrast.contrast_ratio(contrast.parse_color("#000000"), contrast.parse_color("#ffffff")) - 21.0) < 0.01
    # 设计基线：深色叠加 attention 对影像底板
    r = contrast.contrast_ratio(contrast.parse_color("#8C99A6"), contrast.parse_color("#15171B"))
    assert 6.0 < r < 6.4, r
    # 半透明前景要按 alpha 合成，不能拿名义色硬算
    solid = contrast.contrast_ratio(contrast.parse_color("#E6E8EB"), contrast.parse_color("#15171B"))
    faded = contrast.contrast_ratio(contrast.parse_color("rgba(230,232,235,0.5)"), contrast.parse_color("#15171B"))
    assert faded < solid

def test_structural_overlay_token_declared_but_not_gated():
    """描边类 token 必须显式声明；但**不**按 4.5 卡（拿文字门槛卡装饰描边是伪失败）。"""
    themes = contrast.resolve_themes(contrast.parse_color_scopes(TOKENS_CSS.read_text(encoding="utf-8")))
    for name, colors in themes.items():
        for var in contrast.OVERLAY_STRUCTURAL_VARS:
            assert var in colors, f"主题 {name} 缺结构性 token {var}"
    assert not set(contrast.OVERLAY_STRUCTURAL_VARS) & set(contrast.OVERLAY_TEXT_VARS)
