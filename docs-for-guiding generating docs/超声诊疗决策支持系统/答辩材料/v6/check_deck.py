#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_deck.py — PPTX 版式硬校验（真实字体度量 + 渲染像素审计 + 术语可读性）。

背景：本会话模型不支持读图（read_image 返回 "does not declare image input"），
无法用眼睛看渲染结果。因此改用四把客观尺子替代目视：

  尺子 A｜真实字体度量断行（PIL + PingFang SC）
      用**实际字体的字宽**做贪心断行，算排版后真实高度，与文本框高度比对。
      能抓出「文字超出框高」「单行超宽被裁」。

  尺子 B｜渲染像素墨迹审计
      统计画布边缘的墨迹，区分「有意出血」与「意外裁切」；同时检测空白页。

  尺子 C｜文本形状渲染区必须有墨迹
      字体缺失/颜色写白/字号异常时该区域是空的，形状边界检查看不出问题。

  尺子 D｜missingFonts 诊断
      LibreOffice 会静默回退字体，像素审计抓不到；渲染清单的 missingFonts
      字段是「中文掉字」的唯一硬证据。

  尺子 E｜术语可读性
      缩写首次出现必须带白话解释（跨专业评委约束，来自 03 号调研）。

用法:
    python check_deck.py <deck.pptx> [--render-dir <dir>] [--manifest <json>]
退出码 0 = 全部通过，1 = 有问题。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageFont
from pptx import Presentation
from pptx.enum.text import PP_ALIGN

EMU_IN = 914400.0

# 中文字体（按优先级）。PingFang.ttc index: 3=SC Regular, 7=SC Medium
FONT_CANDIDATES = [
    ("/System/Library/AssetsV2/com_apple_MobileAsset_Font8/"
     "86ba2c91f017a3749571a82f2c6d890ac7ffb2fb.asset/AssetData/PingFang.ttc", 3),
    ("/System/Library/Fonts/STHeiti Medium.ttc", 1),
]


class Metrics:
    """按 (size_pt, bold) 缓存字体，供真实宽度测量。"""

    def __init__(self):
        self.path, self.idx = next((p, i) for p, i in FONT_CANDIDATES if Path(p).exists())
        self._cache: dict = {}

    def font(self, size_pt: float, bold: bool = False):
        key = (round(size_pt * 4), bold)
        if key not in self._cache:
            idx = self.idx + (4 if bold else 0)
            try:
                self._cache[key] = ImageFont.truetype(
                    self.path, max(8, int(size_pt * 4)), index=idx)
            except Exception:
                self._cache[key] = ImageFont.truetype(
                    self.path, max(8, int(size_pt * 4)), index=self.idx)
        return self._cache[key]

    def width_in(self, s: str, size_pt: float, bold: bool = False) -> float:
        """字符串在给定字号下的宽度（英寸）。用 4x 字号测，消除取整误差。"""
        f = self.font(size_pt, bold)
        return f.getlength(s) / 4.0 / 72.0

    def wrap_text(self, s: str, size_pt: float, bold: bool, box_w_in: float) -> list[str]:
        """贪心断行，返回每一行的文本（用于孤字检测）。空串返回 [""]。"""
        if not s:
            return [""]
        avail = max(box_w_in, 0.1)
        tokens = re.findall(r"[A-Za-z0-9%.,:\-+_/]+|\s+|[^\sA-Za-z0-9]", s)
        lines, cur, curw = [], "", 0.0
        for tk in tokens:
            w = self.width_in(tk, size_pt, bold)
            if curw + w > avail + 1e-6:
                if tk.isspace():
                    continue
                if w > avail:
                    # 超长串硬切
                    per = max(1, int(avail / max(w / max(len(tk), 1), 1e-6)))
                    for i in range(0, len(tk), per):
                        lines.append(cur + tk[i:i + per])
                        cur, curw = "", 0.0
                    continue
                lines.append(cur)
                cur, curw = tk, w
            else:
                cur += tk
                curw += w
        lines.append(cur)
        return lines

    def wrap_runs(self, runs, box_w_in: float) -> list[str]:
        """按 (text, size_pt, bold) 分段断行 —— 混合 bold/常规时必须用这个。

        为什么需要：P4 总问题条前半 bold、后半常规，若整体按非粗体估算，
        宽度会低估 ~6%，导致「算单行、渲染折行」的漏报。
        """
        avail = max(box_w_in, 0.1)
        lines, cur, curw = [], "", 0.0
        for text, size, bold in runs:
            for tk in re.findall(r"[A-Za-z0-9%.,:\-+_/]+|\s+|[^\sA-Za-z0-9]", text):
                w = self.width_in(tk, size, bold)
                if curw + w > avail + 1e-6:
                    if tk.isspace():
                        continue
                    lines.append(cur)
                    cur, curw = tk, w
                else:
                    cur += tk
                    curw += w
        lines.append(cur)
        return lines

    def wrap_lines(self, s: str, size_pt: float, bold: bool, box_w_in: float) -> int:
        """贪心断行（模拟 PowerPoint 的 CJK 逐字换行 + 西文按词）。"""
        if not s:
            return 1
        avail = max(box_w_in, 0.1)
        tokens = re.findall(r"[A-Za-z0-9%.,:\-+_/]+|\s+|[^\sA-Za-z0-9]", s)
        lines, cur = 1, 0.0
        for tk in tokens:
            w = self.width_in(tk, size_pt, bold)
            if cur + w > avail + 1e-6:
                if tk.isspace():
                    continue
                if w > avail:
                    n = max(1, int(avail / max(w / max(len(tk), 1), 1e-6)))
                    lines += int(len(tk) / n)
                    cur = 0.0
                    continue
                lines += 1
                cur = w
            else:
                cur += w
        return lines


# ---------------------------------------------------------------- 尺子 A


def check_bounds(prs) -> list[str]:
    """形状越出画布。"""
    W, H = prs.slide_width, prs.slide_height
    tol = EMU_IN * 0.02
    bad = []
    for pi, slide in enumerate(prs.slides, 1):
        for shp in slide.shapes:
            l, t = shp.left or 0, shp.top or 0
            r = l + (shp.width or 0)
            b = t + (shp.height or 0)
            if l < -tol or t < -tol or r > W + tol or b > H + tol:
                bad.append(
                    f"P{pi} 形状越界 ({l/EMU_IN:.2f},{t/EMU_IN:.2f})-"
                    f"({r/EMU_IN:.2f},{b/EMU_IN:.2f}) {shp.name}")
    return bad


def check_text_fit(prs, m: Metrics) -> list[str]:
    """真实断行后，文本框所需高度 vs 实际高度。"""
    issues = []
    for pi, slide in enumerate(prs.slides, 1):
        for shp in slide.shapes:
            if getattr(shp, "has_table", False) and shp.has_table:
                issues.extend(_check_table(pi, shp, m))
                continue
            if not shp.has_text_frame:
                continue
            if not shp.text_frame.text.strip():
                continue
            box_w = (shp.width or 0) / EMU_IN
            box_h = (shp.height or 0) / EMU_IN
            if box_w <= 0.05:
                continue
            tf = shp.text_frame
            wrap = tf.word_wrap is not False
            need_h = 0.0
            for p in tf.paragraphs:
                runs = [r for r in p.runs if r.text]
                if not runs:
                    need_h += 0.12
                    continue
                size = max((r.font.size.pt for r in runs if r.font.size), default=None)
                if size is None:
                    continue
                bold = any(r.font.bold for r in runs)
                txt = "".join(r.text for r in runs)
                if wrap:
                    n = m.wrap_lines(txt, size, bold, box_w - 0.08)
                else:
                    n = 1
                    wid = m.width_in(txt, size, bold)
                    if wid > box_w + 0.04:
                        issues.append(
                            f"P{pi} 不换行文本超宽 {wid:.2f}>{box_w:.2f}in ← {txt[:30]!r}")
                ls = p.line_spacing if isinstance(p.line_spacing, float) else 1.15
                need_h += n * size / 72.0 * 1.20 * ls
                need_h += ((p.space_before.pt if p.space_before else 0) +
                           (p.space_after.pt if p.space_after else 0)) / 72.0
            if box_h > 0.1 and need_h > box_h * 1.12:
                issues.append(
                    f"P{pi} 文本框高不足 需{need_h:.2f}in>框{box_h:.2f}in "
                    f"← {tf.text.strip()[:40]!r}")
    return issues


def _check_table(pi: int, shp, m: Metrics) -> list[str]:
    """表格：每格按列宽断行，行高不足则报。"""
    issues = []
    tbl = shp.table
    for r, row in enumerate(tbl.rows):
        row_h = row.height / EMU_IN
        worst, worst_txt = 0.0, ""
        for c, cell in enumerate(row.cells):
            col_w = tbl.columns[c].width / EMU_IN
            inner = col_w - 0.30
            need = 0.05
            for p in cell.text_frame.paragraphs:
                runs = [x for x in p.runs if x.text]
                if not runs:
                    continue
                size = max((x.font.size.pt for x in runs if x.font.size), default=18)
                bold = any(x.font.bold for x in runs)
                txt = "".join(x.text for x in runs)
                need += m.wrap_lines(txt, size, bold, inner) * size / 72.0 * 1.36
            if need > worst:
                worst, worst_txt = need, cell.text_frame.text.strip()
        if worst > row_h + 0.06:
            issues.append(
                f"P{pi} 表格第{r}行高不足 需{worst:.2f}in>设{row_h:.2f}in ← {worst_txt[:30]!r}")
    return issues


def check_overlap(prs) -> list[str]:
    """两个含较长文字的文本框互相压叠（忽略装饰形状）。"""
    bad = []
    for pi, slide in enumerate(prs.slides, 1):
        boxes = []
        for shp in slide.shapes:
            if not shp.has_text_frame:
                continue
            t = shp.text_frame.text.strip()
            if len(t) < 6:
                continue
            l, tp = (shp.left or 0) / EMU_IN, (shp.top or 0) / EMU_IN
            w, h = (shp.width or 0) / EMU_IN, (shp.height or 0) / EMU_IN
            boxes.append((l, tp, l + w, tp + h, t))
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                ox = min(a[2], b[2]) - max(a[0], b[0])
                oy = min(a[3], b[3]) - max(a[1], b[1])
                if ox > 0.30 and oy > 0.22:
                    bad.append(
                        f"P{pi} 文字框重叠 {ox:.2f}x{oy:.2f}in: "
                        f"{a[4][:18]!r} ↔ {b[4][:18]!r}")
    return bad


# ---------------------------------------------------------------- 尺子 B / C


def check_pixels(png: Path) -> list[str]:
    """渲染图墨迹审计：空白页 + 边缘裁切（区分有意出血）。"""
    im = Image.open(png).convert("L")
    W, H = im.size
    px = im.load()
    issues = []

    def ink_count(x0, y0, x1, y1, thr=200, step=2):
        n = 0
        for y in range(max(0, y0), min(H, y1), step):
            for x in range(max(0, x0), min(W, x1), step):
                if px[x, y] < thr:
                    n += 1
        return n

    total = ink_count(0, 0, W, H, step=3)
    if total == 0:
        return [f"{png.name} 整页空白（渲染失败）"]
    ratio = total / ((W // 3) * (H // 3))
    if ratio < 0.006:
        issues.append(f"{png.name} 墨迹过少 {ratio*100:.2f}%（可能漏渲染）")

    def edge_cov(axis: str, side: int) -> float:
        hit = tot = 0
        if axis == "col":
            xs = [0, 1, 2] if side == 0 else [W - 3, W - 2, W - 1]
            for x in xs:
                if 0 <= x < W:
                    for y in range(0, H, 2):
                        tot += 1
                        hit += px[x, y] < 200
        else:
            ys = [0, 1, 2] if side == 0 else [H - 3, H - 2, H - 1]
            for y in ys:
                if 0 <= y < H:
                    for x in range(0, W, 2):
                        tot += 1
                        hit += px[x, y] < 200
        return hit / max(tot, 1)

    mx, my = int(W * 0.012), int(H * 0.012)
    edges = [
        ("左", "col", 0, (0, 0, mx, H)),
        ("右", "col", 1, (W - mx, 0, W, H)),
        ("上", "row", 0, (0, 0, W, my)),
        ("下", "row", 1, (0, H - my, W, H)),
    ]
    for name, axis, side, (x0, y0, x1, y1) in edges:
        cov = edge_cov(axis, side)
        if cov > 0.85:
            continue                      # 有意出血的整条色带
        n = ink_count(x0, y0, x1, y1, step=1)
        if n <= 20:
            continue
        # 上/下边的墨迹若集中在很窄的竖带里 → 竖条出血，不是文字裁切
        if axis == "row":
            xs = [x for x in range(0, W, 3)
                  for yy in range(y0, min(y1, H), 2) if px[x, yy] < 200]
            if xs and (max(xs) - min(xs)) < W * 0.06:
                continue
        issues.append(
            f"{png.name} 疑似裁切：{name}边零星墨迹 {n}px（覆盖率 {cov*100:.0f}%）")
    return issues


def check_text_rendered(prs, render_dir: Path) -> list[str]:
    """每个文本形状对应的渲染区域内必须有墨迹（抓掉字/白字）。"""
    issues = []
    pngs = sorted(render_dir.glob("*.png"))
    if not pngs:
        return issues
    W_emu, H_emu = prs.slide_width, prs.slide_height
    for pi, slide in enumerate(prs.slides, 1):
        if pi > len(pngs):
            break
        im = Image.open(pngs[pi - 1]).convert("L")
        iw, ih = im.size
        px = im.load()
        for shp in slide.shapes:
            if not shp.has_text_frame:
                continue
            txt = shp.text_frame.text.strip()
            if len(txt) < 2:
                continue
            l = (shp.left or 0) / W_emu * iw
            t = (shp.top or 0) / H_emu * ih
            r = l + (shp.width or 0) / W_emu * iw
            b = t + (shp.height or 0) / H_emu * ih
            x0, y0 = max(0, int(l)), max(0, int(t))
            x1, y1 = min(iw, int(r)), min(ih, int(b))
            if x1 - x0 < 6 or y1 - y0 < 6:
                continue
            step = max(1, (x1 - x0) // 120)
            ink = 0
            for y in range(y0, y1, max(1, step)):
                for x in range(x0, x1, step):
                    if px[x, y] < 200:
                        ink += 1
            if ink == 0:
                issues.append(
                    f"P{pi} 文本未渲染（区域无墨迹，疑字体掉字/白字）← {txt[:34]!r}")
    return issues


# ---------------------------------------------------------------- 尺子 D


def check_missing_fonts(manifest: Path) -> list[str]:
    """渲染清单的 missingFonts —— 字体缺失的唯一硬证据。"""
    try:
        d = json.loads(manifest.read_text())
    except Exception as e:
        return [f"渲染清单读取失败: {e}"]
    mf = d.get("missingFonts") or []
    return [f"字体缺失（会掉字/回退）: {', '.join(map(str, mf))}"] if mf else []


# ---------------------------------------------------------------- 尺子 E

ABBR_GLOSS = {
    "RAG": "检索增强生成",
    "AUC": "曲线下面积",
    "TN3K": "甲状腺结节公开数据集",
    "TI-RADS": "甲状腺影像报告分级",
    "LI-RADS": "肝脏影像报告分级",
    "LVEF": "左室射血分数",
    "Self-RAG": "自检式检索",
    "CRAG": "纠正式检索",
    "DSC": "分割相似度",
    "DCA": "决策曲线分析",
    "NMPA": "国家药监局",
    "κ": "一致性系数",
    "κ 值": "一致性系数",
}


def check_jargon(prs) -> list[str]:
    """缩写首次出现必须带白话解释（跨专业评委硬约束）。"""
    pages_text = []
    for slide in prs.slides:
        buf = []
        for shp in slide.shapes:
            if shp.has_text_frame:
                buf.append(shp.text_frame.text)
            if getattr(shp, "has_table", False) and shp.has_table:
                for row in shp.table.rows:
                    for cell in row.cells:
                        buf.append(cell.text_frame.text)
        pages_text.append("\n".join(buf))

    issues, seen = [], set()
    for i, txt in enumerate(pages_text, 1):
        for abbr, gloss in ABBR_GLOSS.items():
            if abbr in seen or abbr not in txt:
                continue
            seen.add(abbr)
            if gloss not in txt:
                issues.append(
                    f"P{i} 术语 {abbr} 首次出现未给白话解释（应含「{gloss}」）")
    return issues


# ---------------------------------------------------------------- 尺子 F/G/H
# 这三条是「人眼发现、程序补上」的规则：
#   F 框外墨迹  —— P1 卡片文字溢出卡片底边（框内墨迹检查抓不到）
#   G 孤字       —— P4「医生不敢用」的"用"、"依据不可核查"的"查"单独成行
#   H 页脚保留区 —— P5 安全层压住页脚（几何不重叠但视觉上压在一起）


def check_overflow_ink(prs, render_dir: Path, *, pad_px: int = 6) -> list[str]:
    """尺子 F（受限版）：文字溢出**孤立**文本框。

    ⚠️ 局限说明（重要）：本册版面密集，文本框几乎都叠在某个底色块里，
    「框外」往往就是父容器的正常背景，墨迹本来就该出现在那里。
    因此本尺子只在文本形状**周围 pad_px 内没有任何其它形状**时生效
    —— 即只能抓「孤立文字溢出到画布空白处」这一类。

    真正抓 P1 那种「文字溢出卡片底边」的是**尺子 A（真实字体度量）**：
    它算的是文本所需高度 vs 文本框高度，P1 的 2.98in > 2.72in 就是这么发现的。
    F 只是补充，不是主力。
    """
    issues = []
    pngs = sorted(render_dir.glob("*.png"))
    if not pngs:
        return issues
    W_emu, H_emu = prs.slide_width, prs.slide_height

    for pi, slide in enumerate(prs.slides, 1):
        if pi > len(pngs):
            break
        im = Image.open(pngs[pi - 1]).convert("L")
        iw, ih = im.size
        px = im.load()
        sx, sy = iw / W_emu, ih / H_emu
        boxes = [((shp.left or 0) * sx, (shp.top or 0) * sy,
                  ((shp.left or 0) + (shp.width or 0)) * sx,
                  ((shp.top or 0) + (shp.height or 0)) * sy) for shp in slide.shapes]

        def is_isolated(me, l, t, r, b):
            for j, (l2, t2, r2, b2) in enumerate(boxes):
                if j == me:
                    continue
                # 与该框 pad 范围内相交的形状
                if not (r2 < l - pad_px or l2 > r + pad_px or
                        b2 < t - pad_px or t2 > b + pad_px):
                    return False
            return True

        def covered(x, y, me):
            for j, (l2, t2, r2, b2) in enumerate(boxes):
                if j == me:
                    continue
                if l2 + 1 <= x <= r2 - 1 and t2 + 1 <= y <= b2 - 1:
                    return True
            return False

        for idx, shp in enumerate(slide.shapes):
            if not shp.has_text_frame:
                continue
            txt = shp.text_frame.text.strip()
            if len(txt) < 6:
                continue
            l, t, r, b = boxes[idx]
            if not is_isolated(idx, l, t, r, b):
                continue
            for (x0, y0, x1, y1, side) in [
                    (l, b, r, b + pad_px, "下"), (l, t - pad_px, r, t, "上"),
                    (l - pad_px, t, l, b, "左"), (r, t, r + pad_px, b, "右")]:
                x0, y0 = max(0, int(x0)), max(0, int(y0))
                x1, y1 = min(iw, int(x1)), min(ih, int(y1))
                if x1 <= x0 or y1 <= y0:
                    continue
                leak = sum(1 for y in range(y0, y1) for x in range(x0, x1)
                           if px[x, y] < 200 and not covered(x, y, idx))
                if leak > 40:
                    issues.append(
                        f"P{pi} 孤立文本溢出到{side}侧（{leak}px）← {txt[:32]!r}")
                    break
    return issues


def check_orphan_char(prs, m: Metrics) -> list[str]:
    """尺子 G：孤字 / 末行过短。

    判定两条（任一命中即报）：
      1. 末行只剩 1 个 CJK 字
      2. 末行宽度 < 该段平均行宽 × 1.5（兜住「用」这类单独成行的短尾）

    ⚠️ 必须用 wrap_runs 分段测量：P4 总问题条前半 bold、后半常规，
    整体按非粗体会低估宽度 → 漏报「用」孤行（Lead 复验发现的实际案例）。
    """
    issues = []
    for pi, slide in enumerate(prs.slides, 1):
        for shp in slide.shapes:
            if not shp.has_text_frame:
                continue
            tf = shp.text_frame
            if tf.word_wrap is False:
                continue
            box_w = (shp.width or 0) / EMU_IN
            if box_w <= 0.4:
                continue
            for p in tf.paragraphs:
                runs = [r for r in p.runs if r.text]
                if not runs:
                    continue
                if p.alignment is not None and p.alignment != PP_ALIGN.LEFT:
                    continue
                segs = [(r.text, r.font.size.pt if r.font.size else None,
                         bool(r.font.bold)) for r in runs]
                if any(sz is None for _t, sz, _b in segs):
                    continue
                lines = m.wrap_runs(segs, box_w - 0.08)
                if len(lines) < 2:
                    continue
                last = lines[-1].strip()
                if not last:
                    continue
                max_sz = max(sz for _t, sz, _b in segs)
                bold_any = any(b for _t, _s, b in segs)
                txt = "".join(t for t, _s, _b in segs)
                lw = m.width_in(last, max_sz, bold_any)
                avg = sum(m.width_in(ln, max_sz, bold_any) for ln in lines) / len(lines)

                cjk_last = sum(1 for ch in last if ord(ch) > 0x2E80)
                # 规则 1：末行只有 1 个 CJK 字 —— 无条件报
                if cjk_last == 1 and len(last.strip()) <= 2:
                    issues.append(f"P{pi} 孤字成行：末行只有 {last!r} ← {txt[:34]!r}")
                    continue
                # 规则 2：末行远短于前一行，且前一行接近满行
                #         （排除「多段拼接」：那种情况前面各段也不满）
                if len(lines) >= 2:
                    prev = lines[-2].strip()
                    prev_w = m.width_in(prev, max_sz, bold_any)
                    near_full = prev_w > (box_w - 0.08) * 0.88
                    if near_full and lw < prev_w * 0.35:
                        issues.append(
                            f"P{pi} 末行过短（{lw:.2f}in vs 前行 {prev_w:.2f}in）"
                            f"← {txt[:34]!r}")
    return issues


def check_footer_zone(prs, *, reserve_in: float = 0.55) -> list[str]:
    """尺子 H：页面底部保留区只允许页脚形状存在。

    P5 的「安全层」底边 6.86in 压住了 6.90in 起的页脚文字 —— 两者几何上
    只差 0.04in 不构成「重叠」，但渲染后视觉上贴在一起。
    """
    issues = []
    H_in = prs.slide_height / EMU_IN
    zone_top = H_in - reserve_in            # 6.95
    for pi, slide in enumerate(prs.slides, 1):
        for shp in slide.shapes:
            top = (shp.top or 0) / EMU_IN
            bottom = top + (shp.height or 0) / EMU_IN
            # 页脚形状：整体落在保留区内且高度很小
            is_footer = top >= zone_top - 0.22 and (shp.height or 0) / EMU_IN <= 0.42
            if is_footer:
                continue
            if bottom > zone_top + 0.02 and top < zone_top:
                w_in = (shp.width or 0) / EMU_IN
                # 通栏/通高装饰色带（如封面左竖轨、底色块）不算侵入
                if w_in >= H_in * 0.95 or w_in <= 0.20:
                    continue
                txt = shp.text_frame.text.strip()[:28] if shp.has_text_frame else shp.name
                if not txt and (shp.height or 0) / EMU_IN <= 0.10:
                    continue
                issues.append(
                    f"P{pi} 形状侵入页脚保留区（bottom={bottom:.2f} > {zone_top:.2f}）← {txt!r}")
    return issues



# ---------------------------------------------------------------- 主流程


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pptx")
    ap.add_argument("--render-dir", help="已渲染 PNG 目录")
    ap.add_argument("--manifest", help="office-render 的 JSON 清单（查 missingFonts）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    path = Path(args.pptx)
    prs = Presentation(str(path))
    m = Metrics()

    issues = []
    issues += check_bounds(prs)
    issues += check_text_fit(prs, m)
    issues += check_overlap(prs)
    issues += check_jargon(prs)
    issues += check_orphan_char(prs, m)
    issues += check_footer_zone(prs)
    if args.manifest:
        issues += check_missing_fonts(Path(args.manifest))
    if args.render_dir:
        rd = Path(args.render_dir)
        for png in sorted(rd.glob("*.png")):
            issues += check_pixels(png)
        issues += check_text_rendered(prs, rd)
        issues += check_overflow_ink(prs, rd)

    result = {
        "file": str(path),
        "slides": len(prs.slides),
        "size_in": [prs.slide_width / EMU_IN, prs.slide_height / EMU_IN],
        "issues": issues,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"== {path.name} | {result['slides']} 页 | "
              f"{result['size_in'][0]:.2f}x{result['size_in'][1]:.2f} in ==")
        if issues:
            print(f"✗ {len(issues)} 个问题：")
            for it in issues:
                print("  -", it)
        else:
            print("✓ 边界 / 真实断行 / 重叠 / 术语 / 像素墨迹 / 字体 全部通过")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
