#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""影像叠加对比度审计（鲁棒版，A.55）。

**为什么重写**：旧版把色值（`ATTENTION/PRIMARY/SECONDARY` 三个元组）与元素几何
（6 个像素坐标）**硬编码**在脚本里 —— 那是"穷举色板"式修法，在两种情况下会静默失效：
  1. token 改了（或主题切换），脚本仍拿旧色值算，照样打印"通过"；
  2. 布局/尺寸变了，坐标量的已经不是那个元素，甚至落在空白处（旧版对"无底纹样本"只打印一行就跳过）。
实测后果：旧版只看 `:root`（深色）一行的 `--color-attention=#8C99A6`（对底板 6.17 ✓），
而**浅色主题**下同一个语义 token 是 `#5A6673`（对同一底板只有 3.06），叠加标注几乎不可读
—— 穷举法永远看不见。

**本版原则**（对应硬性要求：鲁棒、科学、准确）：
  1. **色值来自单一事实源**：解析**生产构建产物** `app/static/ui/assets/*.css` 里的
     `--color-*` 声明，按作用域（`:root` / `[data-theme="light"]` …）还原**每个主题**的
     有效色。脚本内**零硬编码色值** —— token 一变，这里自动跟着变。
  2. **几何/实渲色来自真实 DOM**（`--sidecar`）：由浏览器探针 p25 输出每个叠加元素的
     `getBoundingClientRect()` + `getComputedStyle` 的 color/背景，脚本内**零硬编码坐标**。
  3. **缺输入即失败**（退出码 2）：找不到 CSS、缺底板或叠加 token、sidecar 字段不全、
     指定的截图不存在 —— 一律报错退出，**绝不静默通过、"无样本"不算通过**。
  4. **结果可门禁**：退出码 0=全达标 / 1=有低于阈值 / 2=输入或解析错误；`--json-out` 出机器可读 JSON。
  5. 判定按 **WCAG 2.1**：正文 4.5、大字（≥18.66px 粗体或 ≥24px）3.0；字号取自 sidecar，
     没有 sidecar 时按正文 4.5（保守）。

用法::

    python scripts/contrast.py                          # 解析生产 CSS，逐主题算叠加 token vs 影像底板
    python scripts/contrast.py --css <file.css>         # 指定 CSS（默认取 app/static/ui/assets 最新）
    python scripts/contrast.py --sidecar <json>         # 追加真实 DOM 元素（p25 探针产物）
    python scripts/contrast.py --shot <png> --sidecar <json>   # 追加像素级最坏情况（矩形取自 sidecar）
    python scripts/contrast.py --include-ui-tokens      # 附带列出 UI 色板 token 压在同一底板上的读数（对照用）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from typing import Any

# ---------------------------------------------------------------------------
# 契约：影像底板上**必须**用到的 token（缺任一 → 退出码 2）
#   --color-image-bg 是底板本身；其余是叠加专用色（见 design-tokens.css 注释）
# ---------------------------------------------------------------------------
PLATE_VAR = "--color-image-bg"
# 压在影像上、需要按 WCAG 正文门槛（4.5）计的文字色
OVERLAY_TEXT_VARS = (
    "--color-overlay-text",
    "--color-overlay-text-secondary",
    "--color-overlay-attention",
    "--color-overlay-stop",
)
# 结构性色（描边等）：必须显式声明，但**不按 4.5 门禁** —— 拿文字门槛卡装饰性描边是伪失败
# （WCAG 1.4.11 也只对"必要的非文本边界"要求 3.0，而这里的浮层自带不透明底提供边界）
OVERLAY_STRUCTURAL_VARS = ("--color-overlay-border",)
REQUIRED_VARS = (PLATE_VAR, *OVERLAY_TEXT_VARS, *OVERLAY_STRUCTURAL_VARS)
# 对照用（不算门禁）：UI 语义色板，按界面底色设计，压在影像上不保证可读
UI_TOKENS = (
    "--color-text-primary",
    "--color-text-secondary",
    "--color-attention",
    "--color-semantic-stop",
)

COLOR_DECL = re.compile(r"(--color-[a-z0-9-]+)\s*:\s*(#[0-9A-Fa-f]{3,8}|rgba?\([^)]*\)|[A-Za-z]+)")
RULE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.S)
THEME_SEL = re.compile(r"""data-theme\s*=\s*["']?([A-Za-z0-9_-]+)["']?""")


class InputError(RuntimeError):
    """输入/解析不合格 —— 必须让调用方以非 0 退出，禁止静默通过。"""


# ---------------------------------------------------------------- 色彩数学
def _srgb_to_lin(c: float) -> float:
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def parse_color(value: str) -> tuple[int, int, int, float]:
    """`#rgb` / `#rrggbb` / `#rrggbbaa` / `rgb()` / `rgba()` → (r, g, b, alpha)。"""
    v = value.strip().lower()
    if v.startswith("#"):
        h = v[1:]
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        if len(h) not in (6, 8):
            raise InputError(f"无法解析颜色: {value!r}")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        a = int(h[6:8], 16) / 255.0 if len(h) == 8 else 1.0
        return r, g, b, a
    m = re.fullmatch(r"rgba?\(([^)]*)\)", v)
    if m:
        parts = [p.strip() for p in m.group(1).split(",") if p.strip()]
        if len(parts) < 3:
            raise InputError(f"无法解析颜色: {value!r}")
        rgb = [round(float(p.rstrip("%"))) for p in parts[:3]]
        alpha = float(parts[3]) if len(parts) == 4 else 1.0
        return rgb[0], rgb[1], rgb[2], alpha
    raise InputError(f"无法解析颜色: {value!r}（只支持 #hex 与 rgb()/rgba()）")


def rel_luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (_srgb_to_lin(v / 255.0) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: tuple[int, int, int, float], bg: tuple[int, int, int, float]) -> float:
    """WCAG 对比度。半透明前景按 sRGB 与底板做 alpha 合成（不猜"名义色"）。"""
    if fg[3] < 1.0 or bg[3] < 1.0:
        af, ab = fg[3], bg[3]
        # 前景压到底板上：c = fg*af + bg*ab*(1-af)（底板自身再与不透明黑合成，保守取黑）
        comp = tuple(
            round(fg[i] * af + bg[i] * ab * (1 - af)) for i in range(3)
        )
        fg = (comp[0], comp[1], comp[2], 1.0)
        bg = (round(bg[0] * ab), round(bg[1] * ab), round(bg[2] * ab), 1.0)
    lf, lb = rel_luminance(fg[:3]), rel_luminance(bg[:3])
    hi, lo = max(lf, lb), min(lf, lb)
    return (hi + 0.05) / (lo + 0.05)


def verdict(ratio: float, font_px: float | None, bold: bool) -> tuple[str, float]:
    """→ (判定文案, 适用阈值)。字号来自真实 DOM；没有则按正文（保守）。"""
    large = font_px is not None and (font_px >= 24 or (font_px >= 18.66 and bold))
    need = 3.0 if large else 4.5
    if ratio >= 4.5:
        return "AA正文", need
    if ratio >= 3.0:
        return ("AA大字", need) if large else ("不足(仅达大字)", need)
    return "不足", need


# ---------------------------------------------------------------- CSS 解析
def parse_color_scopes(css_text: str) -> dict[str, dict[str, str]]:
    """→ {选择器: {变量: 原值}}（只收 --color-*）。选择器去空白。

    **必须先剥注释**：注释里常出现 `[data-theme="light"]`、`:root` 这类字样（本仓库的
    `design-tokens.css` 头部注释就有），不剥会被 `[^{}]+` 连带粘进"选择器"里 ——
    实测后果是 `:root` 被误判成"带主题的选择器"，基线色板解析为空、整个审计直接失效。
    CSS 注释不承载声明，剥离是安全且必要的。
    """
    stripped = re.sub(r"/\*.*?\*/", "", css_text, flags=re.S)
    scopes: dict[str, dict[str, str]] = {}
    for sel, body in RULE.findall(stripped):
        decls = dict(COLOR_DECL.findall(body))
        if not decls:
            continue
        # 取最后一段选择器（前导可能残留空白/换行），再去多余空白
        key = re.sub(r"\s+", " ", sel).strip()
        if "," in key:
            key = key  # 组合选择器原样保留，由 resolve_themes 决定归属
        scopes.setdefault(key, {}).update(decls)
    if not scopes:
        raise InputError("CSS 里没有解析到任何 --color-* 声明（产物是否为空/被压缩成非预期形态？）")
    return scopes


def resolve_themes(scopes: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """把作用域还原成"每个主题的有效色"。`:root` 是基线；带 data-theme 的选择器叠加在基线上。"""
    base: dict[str, str] = {}
    for sel, decls in scopes.items():
        if re.fullmatch(r":root|html|\*", sel) or "data-theme" not in sel:
            base.update(decls)
    themes: dict[str, dict[str, str]] = {"dark": dict(base)}
    for sel, decls in scopes.items():
        m = THEME_SEL.search(sel)
        if not m:
            continue
        name = m.group(1)
        eff = dict(base)
        eff.update(decls)
        themes[name] = eff
    if not base:
        raise InputError("CSS 里没有 :root 基线色板")
    return themes


def require_vars(theme: str, colors: dict[str, str]) -> None:
    missing = [v for v in REQUIRED_VARS if v not in colors]
    if missing:
        raise InputError(f"主题 `{theme}` 缺少必需 token: {missing}（叠加色必须显式声明，禁止静默回退）")


def pick_css(explicit: str | None, root: str) -> str:
    if explicit:
        if not os.path.isfile(explicit):
            raise InputError(f"--css 指定的文件不存在: {explicit}")
        return explicit
    pattern = os.path.join(root, "app", "static", "ui", "assets", "*.css")
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    if not files:
        raise InputError(f"没有找到生产 CSS 产物（{pattern}）—— 请先 `npm run build` 并部署产物")
    return files[0]


# ---------------------------------------------------------------- sidecar（真实 DOM）
def load_sidecar(path: str) -> list[dict[str, Any]]:
    if not os.path.isfile(path):
        raise InputError(f"--sidecar 指定的文件不存在: {path}")
    with open(path, encoding="utf-8") as fh:
        data = json.loads(fh.read())
    elements = data.get("elements")
    if not isinstance(elements, list) or not elements:
        raise InputError("sidecar 里没有 elements（探针应当输出真实 DOM 叠加元素；空列表不算通过）")
    for i, el in enumerate(elements):
        for key in ("name", "color", "rect", "font_size_px", "theme"):
            if key not in el:
                raise InputError(f"sidecar.elements[{i}] 缺字段 `{key}`（探针输出不完整，拒绝静默跳过）")
    return elements


def pixel_worst_case(shot: str, rect: dict[str, float], fg_hex: str, dpr: float) -> tuple[float, tuple[int, int, int]]:
    """在截图里、按 DOM 矩形采样底纹最坏像素（不硬编码坐标）。"""
    try:
        from PIL import Image
    except ImportError as e:  # pragma: no cover
        raise InputError(f"像素级比对需要 Pillow: {e}") from e
    if not os.path.isfile(shot):
        raise InputError(f"--shot 指定的截图不存在: {shot}")
    im = Image.open(shot).convert("RGB")
    px = im.load()
    W, H = im.size
    fg = parse_color(fg_hex)[:3]
    x0 = max(0, int(rect["x"] * dpr))
    y0 = max(0, int(rect["y"] * dpr))
    x1 = min(W, int((rect["x"] + rect["width"]) * dpr))
    y1 = min(H, int((rect["y"] + rect["height"]) * dpr))
    if x1 <= x0 or y1 <= y0:
        raise InputError(f"sidecar 矩形落在截图之外: {rect} (shot {W}x{H}, dpr={dpr})")
    samples = [
        px[x, y]
        for y in range(y0, y1)
        for x in range(x0, x1)
        if sum(abs(px[x, y][i] - fg[i]) for i in range(3)) > 90
    ]
    if not samples:
        raise InputError("矩形内没有底纹样本（元素是否根本没渲染？拒绝当作通过）")
    samples.sort(key=rel_luminance)
    worst = samples[int(len(samples) * 0.10)]  # 偏亮的一侧最吃亏
    return contrast_ratio(parse_color(fg_hex), (*worst, 1.0)), worst


# ---------------------------------------------------------------- 主流程
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="影像叠加对比度审计（单一事实源 + 全主题 + 可门禁）")
    ap.add_argument("--css", help="生产 CSS 路径（默认取 app/static/ui/assets 最新）")
    ap.add_argument("--root", default=".", help="仓库根（默认当前目录）")
    ap.add_argument("--sidecar", help="p25 探针输出的真实 DOM 元素 JSON")
    ap.add_argument("--shot", help="截图 PNG（配合 --sidecar 做像素级最坏情况）")
    ap.add_argument("--include-ui-tokens", action="store_true", help="附带列出 UI 色板 token 压在同一底板上的读数（对照）")
    ap.add_argument("--json-out", help="结果 JSON 输出路径")
    args = ap.parse_args(argv)

    result: dict[str, Any] = {"css": None, "themes": {}, "dom": [], "pixel": [], "failures": []}
    try:
        css_path = pick_css(args.css, args.root)
        result["css"] = css_path
        with open(css_path, encoding="utf-8") as fh:
            scopes = parse_color_scopes(fh.read())
        themes = resolve_themes(scopes)
        print(f"CSS（单一事实源）: {css_path}")
        print(f"主题: {', '.join(sorted(themes))}")

        for name in sorted(themes):
            colors = themes[name]
            require_vars(name, colors)
            plate = parse_color(colors[PLATE_VAR])
            print(f"\n===== 主题 {name}：叠加 token vs 底板 {colors[PLATE_VAR]} =====")
            print(f"{'token':34s} {'值':10s} {'对比度':>8s}  {'判定':12s} 阈值")
            print("-" * 78)
            rows = []
            for var in OVERLAY_TEXT_VARS:
                fg = parse_color(colors[var])
                r = contrast_ratio(fg, plate)
                v, need = verdict(r, None, False)
                rows.append({"token": var, "value": colors[var], "ratio": round(r, 2), "verdict": v, "need": need})
                print(f"{var:34s} {colors[var]:10s} {r:8.2f}  {v:12s} {need}")
                if r < need:
                    result["failures"].append(f"[{name}] {var} {colors[var]} 对底板 {colors[PLATE_VAR]} 仅 {r:.2f} < {need}")
            for var in OVERLAY_STRUCTURAL_VARS:
                r = contrast_ratio(parse_color(colors[var]), plate)
                rows.append({"token": var, "value": colors[var], "ratio": round(r, 2), "verdict": "结构性(不计入门禁)", "need": None})
                print(f"{var:34s} {colors[var]:10s} {r:8.2f}  {'结构性(不计门禁)':12s} (非文字)")
            if args.include_ui_tokens:
                print("  -- 对照：UI 色板 token 压在同一底板（仅供解释为什么必须用叠加 token）--")
                for var in UI_TOKENS:
                    if var in colors:
                        r = contrast_ratio(parse_color(colors[var]), plate)
                        v, _ = verdict(r, None, False)
                        print(f"     {var:32s} {colors[var]:10s} {r:8.2f}  {v}")
                        rows.append({"token": var, "value": colors[var], "ratio": round(r, 2), "verdict": v, "ui_reference": True})
            result["themes"][name] = {"plate": colors[PLATE_VAR], "rows": rows}

        # ---- 真实 DOM 元素（几何与实渲色都来自浏览器，不猜）----
        if args.sidecar:
            elements = load_sidecar(args.sidecar)
            print(f"\n===== 真实 DOM 叠加元素（{len(elements)} 个，来自 {args.sidecar}）=====")
            print(f"{'主题':6s} {'元素':28s} {'字号':>5s} {'前景':10s} {'底板':10s} {'对比度':>7s} {'判定':12s}")
            print("-" * 92)
            for el in elements:
                fg = parse_color(el["color"])
                bg_raw = el.get("background") or el.get("plate") or ""
                if not bg_raw or parse_color(bg_raw)[3] < 1.0:
                    # 底板半透明/未取到 → 用该主题声明里的影像底板（有据可依，不用猜）
                    theme_plate = result["themes"].get(el["theme"], {}).get("plate")
                    if not theme_plate:
                        raise InputError(f"元素 {el['name']} 的底板无法解析，且主题 {el['theme']} 无底板 token")
                    bg_raw = theme_plate
                bg = parse_color(bg_raw)
                r = contrast_ratio(fg, bg)
                v, need = verdict(r, float(el["font_size_px"]), bool(el.get("bold")))
                print(f"{el['theme']:6s} {el['name'][:28]:28s} {el['font_size_px']:5.0f} {el['color']:10s} {bg_raw:10s} {r:7.2f} {v:12s}")
                result["dom"].append({"theme": el["theme"], "name": el["name"], "ratio": round(r, 2), "verdict": v, "need": need, "color": el["color"], "background": bg_raw})
                if r < need:
                    result["failures"].append(f"[{el['theme']}] DOM 元素 {el['name']} {el['color']} on {bg_raw} = {r:.2f} < {need}")

        # ---- 像素级最坏情况（矩形来自 sidecar，字号/主题也来自 DOM）----
        if args.shot:
            if not args.sidecar:
                raise InputError("--shot 必须与 --sidecar 同时使用（没有 DOM 矩形就会退回硬编码坐标，禁止）")
            with open(args.sidecar, encoding="utf-8") as fh:
                dpr = float(json.loads(fh.read()).get("dpr", 1.0))
            print(f"\n===== 像素级最坏情况（截图 {args.shot}，dpr={dpr}）=====")
            for el in load_sidecar(args.sidecar):
                shot = args.shot
                if el.get("screenshot"):
                    shot = el["screenshot"] if os.path.isabs(el["screenshot"]) else os.path.join(os.path.dirname(args.sidecar), el["screenshot"])
                r, worst = pixel_worst_case(shot, el["rect"], el["color"], dpr)
                v, need = verdict(r, float(el["font_size_px"]), bool(el.get("bold")))
                print(f"  {el['theme']:6s} {el['name'][:28]:28s} 底纹最坏 {worst} → {r:6.2f}  {v}")
                result["pixel"].append({"theme": el["theme"], "name": el["name"], "ratio": round(r, 2), "verdict": v, "need": need, "worst_pixel": list(worst)})
                if r < need:
                    result["failures"].append(f"[{el['theme']}] 像素 {el['name']} 最坏底纹 {worst} = {r:.2f} < {need}")

    except InputError as e:
        print(f"\n[输入不合格] {e}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"\n[输入不合格] sidecar 不是合法 JSON: {e}", file=sys.stderr)
        return 2

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
        print(f"\nJSON 结果: {args.json_out}")

    print()
    if result["failures"]:
        print(f"结论：**不达标 {len(result['failures'])} 项**")
        for f in result["failures"]:
            print(f"  - {f}")
        return 1
    print("结论：全部达标（阈值：正文 4.5 / 大字 3.0；字号与几何来自真实 DOM 或保守默认）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
