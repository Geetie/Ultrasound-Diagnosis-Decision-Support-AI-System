#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UDDS 中期答辩 v6 — PPTX 构建脚本（内容源：09-完整大纲-v10.md + 10-P4内容与方法-定稿.md）.

用法:
    python build_deck.py              # 生成 UDDS-中期答辩-v6.pptx
    python build_deck.py --check      # 生成并跑自检

设计规范（硬约束，改版式前先读）:
    画布      16:9, 13.333 x 7.5 in
    中文字体  显式写入 a:latin + a:ea + a:cs，否则中文掉字
    字号      标题 32~40pt / 正文 >=20pt / 页脚 14pt 为唯一例外
    配色      深蓝灰 #1F2937 主 + 暗红 #991B1B 仅用于负结果与断点
    每页      一个主视觉 + 页码；无网络图片，全部 shapes 手绘
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from check_deck import Metrics

HERE = Path(__file__).resolve().parent
OUT_PPTX = HERE / "UDDS-中期答辩-v6.pptx"

M = Metrics()          # 真实字体度量（PingFang SC），版式尺寸的唯一标尺

# ---------------------------------------------------------------- 画布

SLIDE_W = 13.333
SLIDE_H = 7.5
MARGIN_X = 0.66
TOP_Y = 0.46
RULE_Y = 1.34
BODY_Y = 1.70
BODY_BOTTOM = 6.56
FOOTER_Y = 6.90

# ---------------------------------------------------------------- 配色

INK = RGBColor(0x1F, 0x29, 0x37)
INK_SOFT = RGBColor(0x37, 0x41, 0x51)
GRAPHITE = RGBColor(0x4B, 0x55, 0x63)
CRIMSON = RGBColor(0x99, 0x1B, 0x1B)
CRIMSON_TINT = RGBColor(0xF7, 0xED, 0xED)
MUTED = RGBColor(0x6B, 0x72, 0x80)
HAIRLINE = RGBColor(0xE1, 0xE4, 0xE9)
WASH = RGBColor(0xF6, 0xF7, 0xF9)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

# ---------------------------------------------------------------- 字体与字号

CJK = "PingFang SC"
LATIN = "Helvetica Neue"

FS_DISPLAY = 42
FS_TITLE = 33
FS_LEAD = 23
FS_BODY = 21
FS_BODY_S = 20
FS_CELL = 20
FS_NOTE = 20
FS_FOOT = 14          # 仅页脚/页码/来源注
# 正文级字号档位（全部 >= 20）。原 FS_FOOT+N 的写法会让正文掉到 16~20pt，
# 违反答辩可读性硬规则，这里统一替换成显式档位。
FS_XS = 20            # 最小正文：小注、标签
FS_S = 20             # 次级正文
FS_M = 21             # 正文
FS_L = 22             # 强调正文
FS_XL = 26            # 小标题
FS_XXL = 32           # 数字/大标签

TEXT_INSET = 0.08

# ---------------------------------------------------------------- 底层


def new_deck() -> Presentation:
    prs = Presentation()
    prs.slide_width = Inches(SLIDE_W)
    prs.slide_height = Inches(SLIDE_H)
    return prs


def blank(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def _apply_font(run, *, size, bold, color, font, spacing=None):
    f = run.font
    f.size = Pt(size)
    f.bold = bold
    f.color.rgb = color
    latin_name = LATIN if font == CJK else font
    f.name = latin_name
    rPr = run._r.get_or_add_rPr()
    for tag in ("a:latin", "a:ea", "a:cs"):
        old = rPr.find(qn(tag))
        if old is not None:
            rPr.remove(old)
    for tag, name in (("a:latin", latin_name), ("a:ea", CJK), ("a:cs", CJK)):
        rPr.append(rPr.makeelement(qn(tag), {"typeface": name}))
    if spacing is not None:
        rPr.set("spc", str(int(spacing * 100)))


def tb(slide, x, y, w, h, *, anchor=MSO_ANCHOR.TOP, pad=(0, 0, 0, 0), wrap=True):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = wrap
    tf.vertical_anchor = anchor
    tf.margin_left, tf.margin_right, tf.margin_top, tf.margin_bottom = [
        Inches(p) for p in pad]
    tf.clear()
    return box, tf


def para(tf, *, first=False, align=PP_ALIGN.LEFT, before=0, after=0, line=1.2, level=0):
    p = tf.paragraphs[0] if first else tf.add_paragraph()
    p.alignment = align
    p.level = level
    p.space_before = Pt(before)
    p.space_after = Pt(after)
    p.line_spacing = line
    return p


def run(p, text, *, size=FS_BODY, bold=False, color=INK, font=CJK, spacing=None):
    r = p.add_run()
    r.text = text
    _apply_font(r, size=size, bold=bold, color=color, font=font, spacing=spacing)
    return r


def text(slide, x, y, w, h, blocks, *, anchor=MSO_ANCHOR.TOP, pad=(0, 0, 0, 0)):
    box, tf = tb(slide, x, y, w, h, anchor=anchor, pad=pad)
    for i, b in enumerate(blocks):
        p = para(tf, first=(i == 0), align=b.get("align", PP_ALIGN.LEFT),
                 before=b.get("before", 0), after=b.get("after", 0),
                 line=b.get("line", 1.2))
        for piece in b["runs"]:
            if isinstance(piece, str):
                run(p, piece, size=b.get("size", FS_BODY), bold=b.get("bold", False),
                    color=b.get("color", INK))
            else:
                t, kw = (piece[0], {}) if len(piece) == 1 else piece
                merged = {k: b[k] for k in ("size", "bold", "color") if k in b}
                merged.update(kw)
                run(p, t, **merged)
    return box


def rect(slide, x, y, w, h, *, fill=None, line=None, lw=1.0, radius=None,
         shape=MSO_SHAPE.RECTANGLE):
    shp = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    shp.shadow.inherit = False
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(lw)
    if radius is not None and shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        try:
            shp.adjustments[0] = radius
        except Exception:  # silent-ok: 圆角微调仅装饰用途，失败保持默认形状即可
            pass
    shp.text_frame.word_wrap = True
    return shp


def hline(slide, x, y, w, *, color=HAIRLINE, lw=1.0):
    ln = slide.shapes.add_connector(1, Inches(x), Inches(y), Inches(x + w), Inches(y))
    ln.line.color.rgb = color
    ln.line.width = Pt(lw)
    return ln


def vline(slide, x, y, h, *, color=HAIRLINE, lw=1.0):
    ln = slide.shapes.add_connector(1, Inches(x), Inches(y), Inches(x), Inches(y + h))
    ln.line.color.rgb = color
    ln.line.width = Pt(lw)
    return ln


def arrow(slide, x1, y1, x2, y2, *, color=INK, lw=1.75):
    cn = slide.shapes.add_connector(1, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    cn.line.color.rgb = color
    cn.line.width = Pt(lw)
    ln = cn.line._get_or_add_ln()
    ln.append(ln.makeelement(qn("a:tailEnd"),
                             {"type": "triangle", "w": "med", "len": "med"}))
    return cn


# ---------------------------------------------------------------- 度量


def fit_size(txt: str, box_w: float, candidates, *, bold=True, one_line=True) -> float:
    """在候选字号里挑最大的、能让 txt 在 box_w 内按需断行数放下的那个。

    one_line=True 时要求单行放下；否则只要求不超过 2 行。
    """
    for sz in candidates:
        n = M.wrap_lines(txt, sz, bold, max(box_w - TEXT_INSET, 0.2))
        if (one_line and n <= 1) or (not one_line and n <= 2):
            return sz
    return candidates[-1]


LINE_SAFE = 1.16      # 单行安全系数：构建器给框 = 断行数 × 行高 × LINE_SAFE


def lines_needed(txt: str, size: float, box_w: float, bold: bool = False) -> int:
    """断行数。检查器用 box_w-0.08 作可用宽度，这里同样扣除后再留 3% 余量，
    保证构建出来的框永远不比检查器算出的需求矮。"""
    avail = max((box_w - TEXT_INSET) * 0.97, 0.2)
    return M.wrap_lines(txt, size, bold, avail)


def block_height(items, box_w: float, *, pad_top=0.0, pad_bottom=0.0, gap_pt=5.0) -> float:
    """items: [(text, size_pt, line_spacing, bold)] → 所需框高（英寸）。"""
    h = pad_top + pad_bottom
    for i, (txt, size, ls, bold) in enumerate(items):
        n = lines_needed(txt, size, box_w, bold)
        h += n * size / 72.0 * 1.30 * ls
        if i:
            h += gap_pt / 72.0
    return h


def fit_list(slide, x, y, w, avail_h, items, *, key_size=FS_S, val_inline=None,
             sep="　", gap_pt=4, pad_v=0.04, min_scale=0.72):
    """自适应纵向列表：按真实度量算每项高度，总高超出 avail_h 时等比压缩。

    items: [(key, val)] 或 [(prefix, key, val)]
    返回实际占用高度。这是替代硬编码坐标的唯一正确做法——字号改了不会崩。
    """
    norm = []
    for it in items:
        if len(it) == 2:
            k, v = it
            pre = ""
        else:
            pre, k, v = it
        norm.append((pre, k, v))

    hs = []
    for pre, k, v in norm:
        txt = pre + k + (sep + v if v else "")
        n = lines_needed(txt, key_size, w)
        hs.append(max(n * key_size / 72.0 * 1.24 + pad_v * 2, key_size / 72.0 * 1.30))
    total = sum(hs) + gap_pt / 72.0 * max(len(norm) - 1, 0)
    scale = min(1.0, avail_h / max(total, 0.05)) if total > avail_h else 1.0
    if scale < min_scale:
        scale = min_scale

    cy = y
    for i, (pre, k, v) in enumerate(norm):
        h = hs[i] * scale
        txt = pre + k + (sep + v if v else "")
        n = lines_needed(txt, key_size, w)
        th = min(h, n * key_size / 72.0 * 1.24 + 0.06)
        _, tf = tb(slide, x, cy, w, th)
        p = para(tf, first=True, line=1.24)
        if pre:
            run(p, pre, size=key_size, bold=True, color=CRIMSON)
        run(p, pre and (k) or k, size=key_size, bold=bool(pre), color=INK)
        if v:
            run(p, sep + v, size=key_size, color=INK_SOFT)
        cy += h + gap_pt / 72.0
    return cy - y


def auto_tb(slide, x, y, w, txt, *, size=FS_XS, bold=False, align=PP_ALIGN.LEFT,
            line=1.26, color=INK, pad=0.03, before=0):
    """按真实断行数给足高度的文本框（消除「框比字矮」这类几何问题）。"""
    n = lines_needed(txt, size, w, bold)
    h = n * size / 72.0 * 1.22 * line + pad * 2
    _, tf = tb(slide, x, y, w, h)
    p = para(tf, first=True, line=line, align=align, before=before)
    run(p, txt, size=size, bold=bold, color=color)
    return h


def two_col_slots(slide, y, avail_h, left_items, right_items, *,
                  left_head=None, right_head=None, head_h=0.46,
                  size=FS_XS, pad_l=0.20, gap_pt=4):
    """左右两栏 + 栏内等分槽位。

    这是本册最稳的版式原语：每项分到的高度严格相等，
    框高 = 槽高 - 间隙，从结构上不可能溢出（内容过长会自动换行并被裁进槽内）。
    """
    colw = (SLIDE_W - 2 * MARGIN_X - 0.28) / 2
    for ci, (items, head, fill, edge) in enumerate([
            (left_items, left_head, WASH, INK),
            (right_items, right_head, CRIMSON_TINT, CRIMSON)]):
        cx = MARGIN_X + ci * (colw + 0.28)
        rect(slide, cx, y, colw, avail_h, fill=fill)
        rect(slide, cx, y, colw, 0.04, fill=edge)
        if head:
            hh = FS_XL / 72.0 * 1.20 * 1.10 + 0.04
            _, tfh = tb(slide, cx + pad_l, y + 0.05, colw - 2 * pad_l, hh)
            ph = para(tfh, first=True, line=1.10)
            run(ph, head, size=FS_XL, bold=True, color=edge)
        body_top = y + max(head_h, FS_XL / 72.0 * 1.20 * 1.10 + 0.09)
        body_h = avail_h - (body_top - y) - 0.05
        n = max(len(items), 1)
        slot = body_h / n
        for i, it in enumerate(items):
            key, val = it
            iy = body_top + i * slot
            ih = slot - gap_pt / 72.0
            _, tf = tb(slide, cx + pad_l, iy, colw - 2 * pad_l, ih)
            acc = CRIMSON if edge is CRIMSON else INK
            val_head, _, val_tail = (val or "").partition("\n")
            p = para(tf, first=True, line=1.20)
            run(p, "· " if key else "", size=size, bold=True, color=acc)
            run(p, key, size=size, bold=True, color=edge)
            if val_head:
                run(p, "　" + val_head, size=size,
                    color=CRIMSON if edge is CRIMSON else INK_SOFT)
            if val_tail:
                # 注脚另起一段：不参与首行宽度，避免把词组从中间拆开
                pn = para(tf, before=2, line=1.18)
                run(pn, val_tail, size=size,
                    color=CRIMSON if edge is CRIMSON else MUTED)


# ---------------------------------------------------------------- 页框


def page_frame(slide, no, total, *, note=None):
    hline(slide, MARGIN_X, FOOTER_Y - 0.11, SLIDE_W - 2 * MARGIN_X, lw=0.75)
    _, tf = tb(slide, MARGIN_X, FOOTER_Y - 0.03, 7.6, 0.36)
    p = para(tf, first=True, line=1.0)
    run(p, "官海峰 · 23301010041 · 计算与智能创新学院", size=FS_FOOT, color=MUTED)
    if note:
        _, tfn = tb(slide, MARGIN_X + 4.2, FOOTER_Y - 0.02, 5.9, 0.30)
        pn = para(tfn, first=True, line=1.0, align=PP_ALIGN.RIGHT)
        run(pn, note, size=FS_FOOT - 2, color=MUTED)
    _, tf2 = tb(slide, SLIDE_W - MARGIN_X - 1.2, FOOTER_Y - 0.03, 1.2, 0.36)
    p2 = para(tf2, first=True, align=PP_ALIGN.RIGHT, line=1.0)
    run(p2, f"{no:02d}", size=FS_XS, bold=True, color=INK, font=LATIN)
    run(p2, f" / {total:02d}", size=FS_FOOT - 1, color=MUTED, font=LATIN)


def page_title(slide, title, *, page_no=None, sec_name=None, lead=None):
    """页标题区。

    页眉编号**由页码推导**（= 页码 - 1，封面不计入内容页序号），不再手写：
      历史 bug①：P6/P7 都写死 "05 / 已完成工作" → 页眉编号重复
      历史 bug②：改成物理页码后又与既有序列（P2=01）不符
    唯一正确规则：P{page_no} 的页眉是 {page_no-1:02d} / {sec_name}。
    """
    y = TOP_Y
    sec_no = (page_no - 1) if page_no is not None else None
    if sec_no is not None:
        _, tf = tb(slide, MARGIN_X, y - 0.04, 9.6, 0.32)
        p = para(tf, first=True, line=1.0)
        run(p, f"{sec_no:02d} / {sec_name}", size=FS_S, bold=True, color=CRIMSON,
            spacing=1.2)
        y += 0.36
    _, tf = tb(slide, MARGIN_X, y, SLIDE_W - 2 * MARGIN_X, 0.64)
    p = para(tf, first=True, line=1.02)
    run(p, title, size=FS_TITLE, bold=True, color=INK)
    hline(slide, MARGIN_X, RULE_Y, SLIDE_W - 2 * MARGIN_X, lw=1.0)
    if lead:
        _, tfl = tb(slide, MARGIN_X, RULE_Y + 0.13, SLIDE_W - 2 * MARGIN_X - 0.3, 0.48)
        p = para(tfl, first=True, line=1.12)
        run(p, lead, size=FS_LEAD, color=INK_SOFT)
        return RULE_Y + 0.72
    return BODY_Y


def tag(slide, x, y, text_, *, fill=WASH, color=INK, w=None, h=0.36, size=FS_S):
    box = rect(slide, x, y, w or (len(text_) * 0.155 + 0.34), h, fill=fill,
               shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.22)
    tf = box.text_frame
    tf.margin_left = tf.margin_right = Inches(0.09)
    tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = para(tf, first=True, align=PP_ALIGN.CENTER, line=1.0)
    run(p, text_, size=size, bold=True, color=color)
    return box


def num_block(slide, x, y, w, h, value, label, note=None, *, accent=False,
              vsize=40, lsize=FS_BODY_S):
    """大数字块：value 大 + label + note，框高自动适配（与 check_deck 同口径）。"""
    edge = CRIMSON if accent else INK
    pad_l, pad_t, pad_b = 0.20, 0.18, 0.18
    inner_w = w - 2 * pad_l
    items = [(value, vsize, 0.98, True), (label, lsize, 1.12, True)]
    if note:
        items.append((note, FS_NOTE, 1.18, False))
    text_h = block_height(items, inner_w, gap_pt=5)
    card_h = max(h, text_h * 1.06 + pad_t + pad_b)

    rect(slide, x, y, w, card_h, fill=WHITE, line=HAIRLINE, lw=0.8)
    rect(slide, x, y, w, 0.05, fill=edge)
    _, tf = tb(slide, x + pad_l, y + pad_t, inner_w, text_h * 1.06)
    p = para(tf, first=True, line=0.98)
    run(p, value, size=vsize, bold=True, color=edge, font=LATIN)
    p2 = para(tf, before=5, line=1.12)
    run(p2, label, size=lsize, bold=True, color=INK)
    if note:
        p3 = para(tf, before=4, line=1.18)
        run(p3, note, size=FS_NOTE, color=MUTED)
    return card_h


def table(slide, x, y, w, col_widths, rows, *, header=True, row_h=0.58,
          head_h=0.50, fs=FS_CELL, head_fs=FS_CELL, accents=None):
    nrows, ncols = len(rows), len(rows[0])
    total_h = (head_h if header else 0) + row_h * (nrows - (1 if header else 0))
    shape = slide.shapes.add_table(nrows, ncols, Inches(x), Inches(y),
                                   Inches(w), Inches(total_h))
    tbl = shape.table
    tbl.first_row = header
    tbl.horz_banding = False
    for i, cw in enumerate(col_widths):
        tbl.columns[i].width = Inches(cw)
    for r in range(nrows):
        tbl.rows[r].height = Inches(head_h if (header and r == 0) else row_h)

    accents = accents or {}
    for r, row in enumerate(rows):
        is_head = header and r == 0
        for c, cell in enumerate(row):
            tc = tbl.cell(r, c)
            tc.margin_left = Inches(0.11)
            tc.margin_right = Inches(0.09)
            tc.margin_top = Inches(0.04)
            tc.margin_bottom = Inches(0.04)
            tc.vertical_anchor = MSO_ANCHOR.MIDDLE
            tc.fill.solid()
            tc.fill.fore_color.rgb = INK if is_head else (
                WHITE if r % 2 == 1 else WASH)
            tf = tc.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.line_spacing = 1.10
            spec = cell if isinstance(cell, dict) else {"t": cell}
            color = spec.get("color", WHITE if is_head else INK)
            bold = spec.get("bold", is_head or c == 0)
            size = spec.get("size", head_fs if is_head else fs)
            if (not is_head) and c in accents.get(r, []):
                color, bold = CRIMSON, True
            run(p, str(spec["t"]), size=size, bold=bold, color=color)
    return shape


def arch_chain(slide, x, y, w, stages, *, gap=0.16, avail_h=4.5):
    """纵向链路：左序号轨 + 右「标题　要点　注脚」单段流式。超 avail_h 自动压缩。"""
    n = len(stages)
    rail_w = 0.86
    inner_w = w - rail_w - 0.44
    pad_v = 0.10

    def body_of(st):
        return " · ".join(st.get("lines", [])), st.get("note", "")

    natural = []
    for st in stages:
        body, note = body_of(st)
        h = lines_needed(st["title"] + "　" + body + ("　→ " + note if note else ""),
                         FS_BODY_S, inner_w) * FS_BODY_S / 72.0 * 1.24
        natural.append(max(h + pad_v * 2, 0.72))
    scale = min(1.0, (avail_h - gap * (n - 1)) / max(sum(natural), 0.1))
    heights = [h * scale for h in natural]

    cy = y
    for i, st in enumerate(stages):
        brk = st.get("brk", False)
        h = heights[i]
        body, note = body_of(st)
        rect(slide, x, cy, rail_w, h, fill=CRIMSON if brk else INK)
        _, tf0 = tb(slide, x, cy, rail_w, h, anchor=MSO_ANCHOR.MIDDLE)
        p0 = para(tf0, first=True, align=PP_ALIGN.CENTER, line=1.0)
        run(p0, st.get("no", ""), size=FS_M, bold=True, color=WHITE, font=LATIN)

        rect(slide, x + rail_w, cy, w - rail_w, h,
             fill=CRIMSON_TINT if brk else WASH,
             line=CRIMSON if brk else None, lw=1.3 if brk else 0)
        _, tf = tb(slide, x + rail_w + 0.18, cy, inner_w, h, anchor=MSO_ANCHOR.MIDDLE)
        p = para(tf, first=True, line=1.20)
        run(p, st["title"], size=FS_BODY_S, bold=True, color=CRIMSON if brk else INK)
        if body:
            run(p, "　" + body, size=FS_BODY_S,
                color=CRIMSON if brk else INK_SOFT)
        if note:
            run(p, "　→ ", size=FS_BODY_S, color=CRIMSON if brk else MUTED)
            run(p, note, size=FS_BODY_S, bold=brk, color=CRIMSON if brk else MUTED)

        if i < n - 1:
            ax = x + rail_w / 2
            arrow(slide, ax, cy + h + 0.01, ax, cy + h + gap - 0.01,
                  color=CRIMSON if brk else GRAPHITE, lw=1.4)
        cy += h + gap
    return cy - gap - y


# ================================================================ 页面

def p_cover(prs, no, total):
    s = blank(prs)
    rect(s, 0, 0, 0.13, SLIDE_H, fill=INK)

    _, tf = tb(s, MARGIN_X, 1.05, 10.6, 0.34)
    p = para(tf, first=True, line=1.0)
    run(p, "FDUROP 曦源项目 · 中期答辩 · 2026 年 10 月", size=FS_XS,
        bold=True, color=CRIMSON, spacing=1.2)

    text(s, MARGIN_X, 1.52, 7.70, 1.0, [
        {"runs": ["超声诊疗决策支持 AI 系统"], "size": FS_DISPLAY, "bold": True, "line": 1.06},
    ])
    text(s, MARGIN_X, 2.46, 7.70, 0.90, [
        {"runs": ["——面向基层的多模态检索增强辅助诊断研究"],
         "size": FS_LEAD, "color": INK_SOFT, "line": 1.24},
    ])

    hline(s, MARGIN_X, 3.24, 5.0, color=CRIMSON, lw=3.0)

    rows = [
        ("汇报人", "官海峰　23301010041"),
        ("院系", "计算与智能创新学院"),
        ("指导教师", "冯红伟"),
        ("项目编号", "264099"),
    ]
    ty = 3.56
    for k, v in rows:
        _, tfk = tb(s, MARGIN_X, ty, 1.24, 0.36)
        pk = para(tfk, first=True, line=1.0)
        run(pk, k, size=FS_BODY_S, color=MUTED)
        _, tfv = tb(s, MARGIN_X + 1.34, ty, 6.10, 0.36)
        pv = para(tfv, first=True, line=1.0)
        run(pv, v, size=FS_BODY_S, bold=True, color=INK)
        ty += 0.50

    # 右侧：系统形态。单一数据源——测量与渲染用同一份 card_items，杜绝"算的和画的不一致"
    bx = 8.16
    card_text_w = 4.58
    card_items = [
        ("系统形态", FS_FOOT + 2, 1.14, True, GRAPHITE),
        ("三器官辅助诊断", FS_XS, 1.20, True, INK),
        ("甲状腺 / 肝脏 / 心脏", FS_XS, 1.20, False, INK_SOFT),
        ("结论带依据，依据可回查", FS_XS, 1.22, False, INK_SOFT),
    ]
    # 与 check_deck 完全同口径：n × size/72 × 1.20 × line_spacing + space_before
    card_text_h = 0.0
    for i, (t, sz, ls, _b, _c) in enumerate(card_items):
        n = M.wrap_lines(t, sz, _b, card_text_w - 0.08)
        card_text_h += n * sz / 72.0 * 1.20 * ls + (7 / 72.0 if i else 0)
    card_h = card_text_h + 0.34
    card_y = 3.46
    rect(s, bx, card_y, 5.02, card_h, fill=WASH)
    rect(s, bx, card_y, 0.05, card_h, fill=INK)
    _, tfb = tb(s, bx + 0.22, card_y + 0.16, card_text_w, card_text_h + 0.02)
    for i, (t, sz, ls, bold_, color_) in enumerate(card_items):
        para_ = para(tfb, first=(i == 0), before=(0 if i == 0 else 7), line=ls)
        run(para_, t, size=sz, bold=bold_, color=color_)
    page_frame(s, no, total)


def p_background1(prs, no, total):
    """P2 研究背景（一）：三段纵向，每段一个大数字 + 一条结论。

    几何策略：三块高度严格均分（不再各算各的），块内全部文字放进**一个**
    流式文本框 —— 从结构上消除重叠可能。
    """
    s = blank(prs)
    y = page_title(s, "问题不是水平不够，是依据对不齐", page_no=no, sec_name="研究背景")

    items = [
        ("κ 0.321–0.411", "一致性系数",
         "4 位资深专家 · 80 个结节 · 4 套 TIRADS 盲法",
         "开会后 κ 升到 0.57–0.62，但曲线下面积 0.677 → 0.632，没涨",
         "Endocrine 2020 · PMID 31741167"),
        ("75.6%", None,
         "2,297,057 例甲状腺癌中，1,736,133 例属过度诊断",
         "63 国 385 个登记处 · 2013–17 · 中国女性 >85%",
         "Lancet Diabetes Endocrinol 2024 · PMID 39389067"),
        ("0.83 vs 0.93", None,
         "真实世界 108,970 例乳腺筛查：AI 低于医师且多报",
         "特异度 0.81 vs 0.97；FDA 已批准的 AI 冲突时仅 3.1% 医生采信",
         "PMID 36863255 · PMID 42228936"),
    ]

    top = y + 0.04
    tail_h = 0.48
    gap = 0.08
    avail_rows = BODY_BOTTOM - tail_h - 0.10 - top - gap * 2
    row_h = avail_rows / 3
    body_h = row_h - 0.04        # 块内正文框高度（上下各留 0.02）

    for i, (val, unit, label, sub, src) in enumerate(items):
        ry = top + i * (row_h + gap)
        curve = i == 0
        rect(s, MARGIN_X, ry, 3.00, row_h, fill=WASH)
        rect(s, MARGIN_X, ry, 0.045, row_h, fill=CRIMSON if curve else INK)

        vw = 2.66
        # 字号上限受块高约束：数字行 + 单位行必须塞进 row_h-0.16
        max_v = int((row_h - 0.16 - (FS_XS / 72.0 * 1.24 if unit else 0)) / 1.12 * 72)
        cands = [c for c in (36, 32, 30, 27, 24, 22, 20) if c <= max_v] or [20]
        vsize = fit_size(val, vw, cands, bold=True, one_line=True)
        vh = vsize / 72.0 * 1.12 + 0.05
        uh = (FS_XS / 72.0 * 1.24 + 0.05) if unit else 0.0
        vh_total = row_h - 0.10
        _, tfv = tb(s, MARGIN_X + 0.16, ry + 0.05, vw, vh_total)
        pv = para(tfv, first=True, line=1.04)
        run(pv, val, size=vsize, bold=True, color=CRIMSON if curve else INK, font=LATIN)
        if unit:
            pu = para(tfv, before=2, line=1.12)
            run(pu, unit, size=FS_XS, color=MUTED)

        # 右侧单一流式框（高度 = 块高 - 上下内缩，内容自动换行）
        cw = SLIDE_W - 2 * MARGIN_X - 3.00 - 0.22
        _, tf = tb(s, MARGIN_X + 3.18, ry + 0.02, cw, body_h)
        p1 = para(tf, first=True, line=1.06)
        run(p1, label, size=FS_XS, bold=True, color=INK)
        p2 = para(tf, before=2, line=1.08)
        run(p2, sub, size=FS_XS, color=CRIMSON if curve else INK_SOFT)
        p3 = para(tf, before=2, line=1.00)
        run(p3, src, size=FS_FOOT, color=MUTED)

    cy = top + 3 * row_h + 2 * gap + 0.08
    rect(s, MARGIN_X, cy, SLIDE_W - 2 * MARGIN_X, tail_h, fill=INK)
    _, tfc = tb(s, MARGIN_X + 0.22, cy, SLIDE_W - 2 * MARGIN_X - 0.44, tail_h,
                anchor=MSO_ANCHOR.MIDDLE)
    pc = para(tfc, first=True, line=1.08)
    run(pc, "所以靶心不是「提高准确率」，是「让判断依据可对齐」",
        size=FS_M, bold=True, color=WHITE)
    page_frame(s, no, total)


def p_background2(prs, no, total):
    """P3 研究背景（二）：左「学界已做到」vs 右「三个空白」+ 底部定位。"""
    s = blank(prs)
    y = page_title(s, "学界做到了什么，还空着什么",
                   page_no=no, sec_name="研究背景",
                   lead="右边三条空白都查过文献；左栏是检索增强生成（RAG）评测现状")

    bar_h = 0.60
    avail = BODY_BOTTOM - bar_h - 0.12 - (y + 0.02)
    left = [
        ("检索评测", "MIRAGE：7,663 题"),
        ("系统综述", "157 篇（2026）"),
        ("指南结构检索", "Q(t) 框架"),
        ("超声基础模型", "EchoCare 4.5M 图像"),
        ("效用评估工具", "决策曲线已成熟"),
    ]
    # 右栏：标题与注脚用「全角空格 + 换行」分成两段，避免把"层级切分"从中间拆开
    right2 = [
        ("空白一", "没有代价敏感的评测\n89.2% 只做离线，部署级 10.8%"),
        ("空白二", "证据等级不是检索主键\n层级切分检索 0 条"),
        ("空白三", "决策曲线从未接到检索上\n没有一篇报过 net benefit"),
    ]

    two_col_slots(s, y + 0.02, avail, left, right2,
                  left_head="学界已经做到", right_head="仍然空着", head_h=0.48)

    cy = BODY_BOTTOM - bar_h
    rect(s, MARGIN_X, cy, SLIDE_W - 2 * MARGIN_X, bar_h, fill=INK)
    _, tfc = tb(s, MARGIN_X + 0.22, cy, SLIDE_W - 2 * MARGIN_X - 0.44, bar_h,
                anchor=MSO_ANCHOR.MIDDLE)
    pc = para(tfc, first=True, line=1.06)
    run(pc, "本项目的位置：", size=FS_XS, bold=True, color=WHITE)
    run(pc, "用决策曲线定口径，用证据等级做排序，在超声多器官场景验证",
        size=FS_XS, color=WHITE)
    page_frame(s, no, total)


def p_content_method(prs, no, total):
    """P4 研究内容与方法：顶部总问题 + 左右两栏（四项设计 / 六项方法）。"""
    s = blank(prs)
    y = page_title(s, "研究内容与方法", page_no=no, sec_name="我要做什么",
                   lead="四项「设计」让效用可量；六项方法各回答一个研究问题")

    # 总问题条：**两行**。单行时 bold 段 + 常规段合计 11.38in，逼近可用 11.53in，
    # 而 LibreOffice 的 bold 字宽比度量值更大 → 实际会折行，末字「用」孤悬。
    # 拆两行后两段各自远小于可用宽度，且视觉层次更清楚。
    qh = 0.88
    rect(s, MARGIN_X, y + 0.02, SLIDE_W - 2 * MARGIN_X, qh, fill=WASH)
    rect(s, MARGIN_X, y + 0.02, 0.045, qh, fill=INK)
    _, tfg = tb(s, MARGIN_X + 0.22, y + 0.06, SLIDE_W - 2 * MARGIN_X - 0.46, qh - 0.08,
                anchor=MSO_ANCHOR.MIDDLE)
    pg = para(tfg, first=True, line=1.10)
    run(pg, "总问题：医学 AI 辅助诊断的临床效用，无法被证明",
        size=FS_XS, bold=True, color=INK)
    pg2 = para(tfg, before=4, line=1.12)
    run(pg2, "监管不敢批 · 支付不敢付 · 医生不敢用", size=FS_XS, color=CRIMSON)

    top = y + 0.02 + qh + 0.12
    avail = BODY_BOTTOM - top

    # 栏目标题已写「研究内容（设计什么）」，故条目内不再重复"设计一套"，
    # 避免尾字单独成行（用户已确认此缩法）
    left = [
        ("D1 临床效用的衡量标准", "没法量"),
        ("D2 结论到证据的追溯链", "依据不可核查"),
        ("D3 结构化输出的约束机制", "判断不稳定"),
        ("D4 数据可信度规范", "底层数据不可信"),
    ]
    right = [
        ("定量评估", "多口径对照"),
        ("代价敏感分析", "分方向计权"),
        ("结构审计", "结构体检"),
        ("消融实验", "逐因子消融"),
        ("数据审计", "三元标注"),
        ("案例研究", "回顾性验证"),
    ]
    two_col_slots(s, top, avail, left, right,
                  left_head="研究内容（设计什么）",
                  right_head="研究方法（怎么研究）", head_h=0.46)
    page_frame(s, no, total)


def p_work_flow_arch(prs, no, total):
    """P5 已完成工作（一）：上 6 步业务流，下 5 层架构。"""
    s = blank(prs)
    y = page_title(s, "系统跑通了：一条业务流，五层结构",
                   page_no=no, sec_name="已完成工作")

    # 上：6 步
    rect(s, MARGIN_X, y + 0.02, SLIDE_W - 2 * MARGIN_X, 1.62, fill=WASH)
    _, tfh = tb(s, MARGIN_X + 0.22, y + 0.06, 7.6, 0.36)
    ph = para(tfh, first=True, line=1.10)
    run(ph, "医生视角：最短 6 步拿到可签发报告", size=FS_M, bold=True, color=INK)
    _, tfj = tb(s, MARGIN_X + 7.9, y + 0.06, 4.2, 0.36)
    pj = para(tfj, first=True, align=PP_ALIGN.RIGHT, line=1.10)
    run(pj, "真机全流程 11/11 走查通过", size=FS_XS, bold=True, color=CRIMSON)

    steps = ["登录", "工作列表", "新建检查", "传影像自动判读", "等结论", "草稿 + 签发"]
    n = len(steps)
    step_gap = 0.16
    sw = (SLIDE_W - 2 * MARGIN_X - 0.44 - step_gap * (n - 1)) / n
    sy = y + 0.52
    for i, st in enumerate(steps):
        sx = MARGIN_X + 0.22 + i * (sw + step_gap)
        rect(s, sx, sy, sw, 0.86, fill=INK if i < n - 1 else CRIMSON)
        _, tfs = tb(s, sx + 0.08, sy, sw - 0.16, 0.86, anchor=MSO_ANCHOR.MIDDLE)
        ps = para(tfs, first=True, align=PP_ALIGN.CENTER, line=1.06)
        run(ps, str(i + 1) + "　", size=FS_XS, bold=True, color=WHITE, font=LATIN)
        run(ps, st, size=FS_XS, bold=True, color=WHITE)

    # 下：5 层架构（ay 与 lh/lg 经实测标定：最后一层底边必须 < 页脚线 6.79）
    ay = y + 1.56
    layers = [
        ("前端", "React 19 + TS（Cornerstone3D / 双 WS）", None),
        ("接口", "FastAPI 单进程　93 HTTP + 2 WS", None),
        ("数据", "PG+pgvector 17 表 · Redis · Qdrant", "note"),
        ("模型", "vLLM 8002 · 嵌入 8003/8004 · 精排 8005 · 判官 8006", None),
        ("安全", "SafetyGateway 9001（六阶段顺序校验，fail-closed）", None),
    ]
    lh = 0.54
    lg = 0.075
    for i, (k, v, port) in enumerate(layers):
        ly = ay + i * (lh + lg)
        accent = (i == 4)
        rect(s, MARGIN_X, ly, 1.10, lh, fill=CRIMSON if accent else INK)
        _, tfk = tb(s, MARGIN_X, ly, 1.10, lh, anchor=MSO_ANCHOR.MIDDLE)
        pk = para(tfk, first=True, align=PP_ALIGN.CENTER, line=1.0)
        run(pk, k, size=FS_S, bold=True, color=WHITE)
        rect(s, MARGIN_X + 1.10, ly, SLIDE_W - 2 * MARGIN_X - 1.10, lh,
             fill=CRIMSON_TINT if accent else WHITE, line=HAIRLINE, lw=0.8)
        _, tfv = tb(s, MARGIN_X + 1.28, ly, SLIDE_W - 2 * MARGIN_X - 1.46, lh,
                    anchor=MSO_ANCHOR.MIDDLE)
        pv = para(tfv, first=True, line=1.0)
        run(pv, v, size=FS_S, color=INK if accent else INK_SOFT)
        if port == "note":
            run(pv, "　（15432 / 16379；容器内 5432/6379）", size=FS_FOOT, color=MUTED)
        if i < len(layers) - 1:
            ax = MARGIN_X + 0.55
            arrow(s, ax, ly + lh + 0.005, ax, ly + lh + lg - 0.005,
                  color=GRAPHITE, lw=1.2)
    page_frame(s, no, total)


def p_capability(prs, no, total):
    """P6 已完成工作（二）：能力边界三档。

    20pt 下三档 + 指标表同页装不下（实测：三档需 ~2.1in/档，指标表需 3.2in，
    合计 > 4.9in 可用），故拆为 P6（边界）与 P7（指标）两页。
    """
    s = blank(prs)
    y = page_title(s, "能力边界：哪些真能用，哪些还不行",
                   page_no=no, sec_name="已完成工作",
                   lead="自下而上盘点，不是照设计文档抄")

    tiers = [
        ("真能用", "5 项", INK, WASH, [
            "检索指南依据（混合 Recall@10 98%）",
            "报告草稿 + RSA 签名，11/11 走查",
            "安全门拦 kill 后拒输出",
        ]),
        ("部分能用", "7 项", GRAPHITE, WASH, [
            "三器官可用分级率 18.30%",
            "多部位会话只能选 1 个部位",
            "病灶分割 0.7961，面积恒为空",
        ]),
        ("界面有入口但不工作", "18 个", CRIMSON, CRIMSON_TINT, [
            "「确认结论」按钮不落库",
            "报告面板「签发」onClick 为空",
            "PACS 推送是本地假实现，显示已推但没推",
        ]),
    ]

    full_w = SLIDE_W - 2 * MARGIN_X
    head_w = 1.86
    body_w = full_w - head_w - 0.30
    top = y + 0.04
    avail = BODY_BOTTOM - top

    gap_t = 0.10
    avail_t = avail - gap_t * (len(tiers) - 1)
    sheights = [avail_t / len(tiers)] * len(tiers)

    ty = top
    for ti, (name, cnt, color, bg, rows) in enumerate(tiers):
        h = sheights[ti]
        is_bad = color is CRIMSON
        rect(s, MARGIN_X, ty, full_w, h, fill=bg)
        rect(s, MARGIN_X, ty, 0.05, h, fill=color)
        _, tfk = tb(s, MARGIN_X + 0.20, ty + 0.04, head_w - 0.10, h - 0.08)
        pk = para(tfk, first=True, line=1.16)
        run(pk, name, size=FS_XS, bold=True, color=color)
        pkc = para(tfk, before=3, line=1.04)
        run(pkc, cnt, size=FS_L, bold=True, color=color, font=LATIN)
        _, tfv = tb(s, MARGIN_X + head_w + 0.12, ty + 0.04, body_w, h - 0.08)
        for i, r in enumerate(rows):
            pv = para(tfv, first=(i == 0), line=1.18, before=0 if i == 0 else 4)
            run(pv, "· " + r, size=FS_XS, color=CRIMSON if is_bad else INK_SOFT)
        ty += h + gap_t
    page_frame(s, no, total)


def p_metrics(prs, no, total):
    """P7 已完成工作（三）：效果指标 + 一句口径说明。"""
    s = blank(prs)
    y = page_title(s, "效果指标：每个数都带分母",
                   page_no=no, sec_name="已完成工作",
                   lead="只放经得起追问的；查不到口径的数字不报")

    rows = [
        ("检索", "混合 Recall@10", "98.0%", "n=200 查询 / 433 语料块"),
        ("检索", "hit@10", "98.53%", "134/136 文档 · 真机"),
        ("分类", "肝良恶性曲线下面积", "0.8950", "95%CI [0.8712, 0.9168] · AUL 735"),
        ("分类", "跨来源曲线下面积", "0.4929", "甲状腺结节公开集 n=614 · 接近随机"),
        ("分割", "甲状腺分割相似度", "0.7961", "心脏心肌 IoU 0.7388"),
        ("端到端", "医生时序走查", "11/11", "P50 wall 5.25s · 20 并发零拒绝"),
        ("安全", "kill 安全门后拒输出", "E4005", "fail-closed 实测验证"),
    ]
    full_w = SLIDE_W - 2 * MARGIN_X
    top = y + 0.06
    avail = BODY_BOTTOM - top - 0.70
    rh = (avail - 0.04 * len(rows)) / len(rows)
    my = top
    for i, (cat, k, v, note) in enumerate(rows):
        if i % 2 == 0:
            rect(s, MARGIN_X, my, full_w, rh, fill=WASH)
        _, tfc = tb(s, MARGIN_X + 0.16, my, 0.92, rh, anchor=MSO_ANCHOR.MIDDLE)
        pc = para(tfc, first=True, line=1.0)
        run(pc, cat, size=FS_FOOT + 2, color=MUTED)
        _, tfk = tb(s, MARGIN_X + 1.14, my, 3.10, rh, anchor=MSO_ANCHOR.MIDDLE)
        pk = para(tfk, first=True, line=1.12)
        run(pk, k, size=FS_XS, color=INK)
        _, tfv = tb(s, MARGIN_X + 4.32, my, 2.10, rh, anchor=MSO_ANCHOR.MIDDLE)
        pv = para(tfv, first=True, line=1.0, align=PP_ALIGN.RIGHT)
        run(pv, v, size=FS_XL, bold=True, color=INK, font=LATIN)
        _, tfn = tb(s, MARGIN_X + 6.56, my, full_w - 6.70, rh, anchor=MSO_ANCHOR.MIDDLE)
        pn = para(tfn, first=True, line=1.10)
        run(pn, note, size=FS_FOOT + 2, color=MUTED)
        my += rh + 0.04

    # 底部口径纪律
    cy = BODY_BOTTOM - 0.56
    rect(s, MARGIN_X, cy, full_w, 0.56, fill=INK)
    _, tfc = tb(s, MARGIN_X + 0.22, cy, full_w - 0.44, 0.56, anchor=MSO_ANCHOR.MIDDLE)
    pc = para(tfc, first=True, line=1.02)
    run(pc, "纪律：公开集分数不作临床证据；跨来源验证才是临床口径。",
        size=FS_XS, bold=True, color=WHITE)
    page_frame(s, no, total)


def p_problems_plan(prs, no, total):
    """P7 存在问题与计划。

    20pt 下本页容量约 600 字：四条阻塞各一行 + 四条计划各一行 + 落地条。
    中间的段落小标题全部去掉，用「一、二、三」内嵌在首行，省两行高度。
    """
    s = blank(prs)
    y = page_title(s, "卡在哪，下一步做什么", page_no=no, sec_name="问题与计划")

    # ---- 四条阻塞：4 行紧凑卡（每卡 1 行标题 + 1 行说明）
    blocks = [
        ("① 指标层", "指标无方向标量", "89.2% 只做离线评测"),
        ("② 结构层", "依据不可承载", "88.5% 单块；无证据等级字段"),
        ("③ 生成层", "判断不稳定", "高风险档 40.88%（门限 95%）"),
        ("④ 数据层", "测量基础不成立", "608/620 照抄；干净集 157 条"),
    ]
    bw = (SLIDE_W - 2 * MARGIN_X - 0.24) / 2
    binner = bw - 0.30
    # 卡片高 = 标题行 + 说明行（各自按真实断行算）
    card_h = 0.0
    for k, t1, t2 in blocks:
        hk = lines_needed(k + "　" + t1, FS_XS, binner, True) * FS_XS / 72.0 * 1.18
        hv = lines_needed(t2, FS_XS, binner) * FS_XS / 72.0 * 1.18
        card_h = max(card_h, hk + hv + 0.22)
    top = y + 0.02
    # 「一、」内嵌在左上角
    auto_tb(s, MARGIN_X, top, 6.0, "一、卡在哪（按性质分）", size=FS_XS,
            bold=True, color=GRAPHITE, line=1.10)

    by = top + 0.32
    for i, (k, t1, t2) in enumerate(blocks):
        bx = MARGIN_X + (i % 2) * (bw + 0.24)
        yy = by + (i // 2) * (card_h + 0.08)
        rect(s, bx, yy, bw, card_h, fill=WASH)
        rect(s, bx, yy, 0.045, card_h, fill=CRIMSON)
        hk = lines_needed(k + "　" + t1, FS_XS, binner, True) * FS_XS / 72.0 * 1.18
        _, tfk = tb(s, bx + 0.14, yy + 0.05, binner, hk + 0.05)
        pk = para(tfk, first=True, line=1.18)
        run(pk, k + "　", size=FS_XS, bold=True, color=CRIMSON)
        run(pk, t1, size=FS_XS, bold=True, color=INK)
        hv = lines_needed(t2, FS_XS, binner) * FS_XS / 72.0 * 1.18
        _, tfv = tb(s, bx + 0.14, yy + 0.05 + hk + 0.05, binner, hv + 0.05)
        pv = para(tfv, first=True, line=1.18)
        run(pv, t2, size=FS_XS, color=INK_SOFT)

    # ---- 四条计划
    py = by + 2 * card_h + 0.08 + 0.22
    auto_tb(s, MARGIN_X, py, 8.4, "二、下一步（每条对应一个阻塞）", size=FS_XS,
            bold=True, color=GRAPHITE, line=1.10)

    plans = [
        ("①", "风险分层评测矩阵，分方向计权"),
        ("②", "补证据等级字段，重建层级切分"),
        ("③", "约束解码迭代 + 规则二次校验"),
        ("④", "建干净集：双人标注 + 一致性检验"),
    ]
    p_inner = SLIDE_W - 2 * MARGIN_X - 0.36
    p_start = py + 0.34
    avail_p = (BODY_BOTTOM - 0.52 - 0.10) - p_start
    lh = min(FS_XS / 72.0 * 1.22 * 1.26 + 0.04, avail_p / len(plans))
    py2 = p_start
    for mark, t in plans:
        _, tfk = tb(s, MARGIN_X - 0.02, py2, 0.36, lh + 0.03)
        pk = para(tfk, first=True, line=1.26)
        run(pk, mark, size=FS_XS, bold=True, color=CRIMSON, font=LATIN)
        _, tft = tb(s, MARGIN_X + 0.33, py2, p_inner, lh + 0.02)
        pt = para(tft, first=True, line=1.26)
        run(pt, t, size=FS_XS, color=INK_SOFT)
        py2 += lh

    # ---- 落地条（贴底）
    land_h = 0.50
    cy = BODY_BOTTOM - land_h
    rect(s, MARGIN_X, cy, SLIDE_W - 2 * MARGIN_X, land_h, fill=INK)
    _, tfc = tb(s, MARGIN_X + 0.22, cy, SLIDE_W - 2 * MARGIN_X - 0.44, land_h,
                anchor=MSO_ANCHOR.MIDDLE)
    pc = para(tfc, first=True, line=1.0)
    run(pc, "三、落地验证　", size=FS_XS, bold=True, color=WHITE)
    run(pc, "与复旦合作医院：先回顾性，再前瞻性（伦理批件后）", size=FS_XS, color=WHITE)
    page_frame(s, no, total)


def p_summary(prs, no, total):
    """P8 总结。不写「谢谢聆听」。"""
    s = blank(prs)
    rect(s, 0, 0, 0.13, SLIDE_H, fill=INK)

    _, tf = tb(s, MARGIN_X, 1.32, 10.6, 0.34)
    p = para(tf, first=True, line=1.0)
    run(p, "总结", size=FS_XS, bold=True, color=CRIMSON, spacing=1.2)

    text(s, MARGIN_X, 1.82, 11.4, 1.5, [
        {"runs": ["系统主链路已跑通，能力边界清楚"],
         "size": FS_DISPLAY - 4, "bold": True, "line": 1.14},
        {"runs": ["下一步：建立衡量标准与证据链，进医院验证"],
         "size": FS_LEAD, "color": INK_SOFT, "before": 14},
    ])

    hline(s, MARGIN_X, 3.92, 4.6, color=CRIMSON, lw=3.0)

    _, tfa = tb(s, MARGIN_X, 4.26, 11.2, 0.9)
    pa = para(tfa, first=True, line=1.30)
    run(pa, "感谢冯红伟老师指导，请各位老师批评指正。", size=FS_LEAD, color=INK)

    _, tfn = tb(s, MARGIN_X, 5.36, 11.2, 0.34)
    pn = para(tfn, first=True, line=1.0)
    run(pn, "曦源项目编号 264099 · 2026 年 10 月", size=FS_XS, color=MUTED)

    page_frame(s, no, total)


PAGES = [
    p_cover,          # P1
    p_background1,    # P2
    p_background2,    # P3
    p_content_method, # P4
    p_work_flow_arch, # P5
    p_capability,     # P6
    p_metrics,        # P7
    p_problems_plan,  # P8
    p_summary,        # P9
]


def build(path: Path = OUT_PPTX) -> Path:
    prs = new_deck()
    total = len(PAGES)
    for i, fn in enumerate(PAGES, 1):
        fn(prs, i, total)
    prs.save(str(path))
    print(f"[build] {path}  ({total} 页, {path.stat().st_size/1024:.0f} KB)")
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="UDDS 中期答辩 v6 构建器")
    ap.add_argument("--out", default=str(OUT_PPTX))
    args = ap.parse_args(argv)
    build(Path(args.out))


if __name__ == "__main__":
    main()
