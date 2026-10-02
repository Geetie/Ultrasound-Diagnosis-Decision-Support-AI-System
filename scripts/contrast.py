# -*- coding: utf-8 -*-
"""量叠加标注在**真实超声组织纹理**上的实际对比度。

背景：设计包的占位是深色渐变，叠加文字色（text-primary #E6E8EB /
attention #8C99A6）在深底上可读；但 AGENT_TASKS §九.1 明确标注
「叠加标注与真实组织纹理的对比度从未验证」。

做法：在真实渲染截图上，对每个叠加元素取其包围盒内的像素，
算「叠加色 vs 底纹最亮/最暗像素」的 WCAG 对比度，取最差值。
底纹是灰度超声，范围很宽，所以要看最坏情况而不是平均值。
"""
import os
import sys

from PIL import Image

# 默认量基准截图；也可传文件名量候选方案：python contrast.py candidate-A-stroke.png
_name = sys.argv[1] if len(sys.argv) > 1 else "real-dicom-overlay.png"
SHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots", _name)

ATTENTION = (140, 153, 166)   # --color-attention  #8C99A6
PRIMARY = (230, 232, 235)     # --color-text-primary #E6E8EB
SECONDARY = (168, 174, 182)   # --color-text-secondary #A8AEB6


def lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def lum(rgb):
    r, g, b = (lin(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


im = Image.open(SHOT).convert("RGB")
px = im.load()
W, H = im.size
print("shot", W, H)

# 元素包围盒（来自 design_data 的 x/y/w/h + 常驻条 40；截图 dpr=1）
BOXES = [
    ("结节A·未校验 标签 (attention)", ATTENTION, 360, 40 + 146, 81, 16),
    ("结节B·已校验 标签 (text-primary)", PRIMARY, 830, 40 + 276, 81, 16),
    ("参数角标 (text-secondary)", SECONDARY, 24, 40 + 20, 234, 16),
    ("时间角标 (text-secondary)", SECONDARY, 1256, 40 + 20, 160, 13),
    ("测量值 18.4×12.1mm (text-primary)", PRIMARY, 540, 40 + 408, 83, 15),
    ("深度刻度 10cm (text-secondary)", SECONDARY, 1368, 40 + 460, 48, 16),
]

print()
print("%-34s %8s %8s %9s %9s %7s" % ("叠加元素", "底纹中位", "底纹p90亮", "中位对比", "p10对比", "判定"))
print("-" * 82)
results = []
for name, color, x, y, w, h in BOXES:
    # 取包围盒内部的底纹像素（排除字本身与抗锯齿边缘：与叠加色距离小的都丢掉）
    samples = []
    for yy in range(max(0, y), min(H, y + h)):
        for xx in range(max(0, x), min(W, x + w)):
            c = px[xx, yy]
            if sum(abs(c[i] - color[i]) for i in range(3)) > 90:
                samples.append(c)
    if not samples:
        print("%-34s %8s" % (name, "无底纹样本"))
        continue
    samples.sort(key=lum)
    n = len(samples)
    median = samples[n // 2]
    p90 = samples[int(n * 0.90)]
    p10 = samples[int(n * 0.10)]
    r_med = ratio(color, median)
    # p10 场景：叠加色 vs 较亮的底纹（浅色文字压在亮组织上最吃亏）
    r_p10 = ratio(color, p10) if lum(p10) > lum(color) else ratio(color, p90)
    worst = min(r_med, r_p10)
    verdict = "AA正文" if worst >= 4.5 else ("AA大字" if worst >= 3.0 else "不足")
    results.append((name, r_med, r_p10, verdict))
    print("%-34s %8s %8s %9.2f %9.2f %7s" % (
        name,
        "%d,%d,%d" % median,
        "%d,%d,%d" % p90,
        r_med,
        r_p10,
        verdict,
    ))
print()
print("叠加色: attention=%s  text-primary=%s  text-secondary=%s" % (ATTENTION, PRIMARY, SECONDARY))
print("阈值: WCAG AA 正文 4.5 / AA 大字与图形 3.0")
print()
bad = [r for r in results if r[3] == "不足"]
print("结论：%d / %d 个叠加元素在真实组织上不达AA 大字 3.0" % (len(bad), len(results)))

# ------------------------------------------------------------------
# 底板方案（已落地）下的解析式对比度
#
# 上面的截图测量有个固有偏差：字与底板之间的**抗锯齿边缘**像素既不等于
# 文字色也不等于底板色，会被当成"底纹"算进去，把结果压低。所以这里改用
# 解析式：底板是不透明的 color-image-bg，文字周围的背景已被完全控制，
# 于是对比度变成一个常量，可以直接算。
# ------------------------------------------------------------------
PLATE = (21, 23, 27)  # --color-image-bg #15171B
print()
print("===== 底板方案：叠加色 vs 不透明底板 #15171B（解析式）=====")
print("%-30s %8s %9s %7s" % ("叠加元素", "底板", "对比度", "判定"))
print("-" * 58)
for name, color in [
    ("结节A·未校验 (attention)", ATTENTION),
    ("结节B·已校验 (text-primary)", PRIMARY),
    ("参数/时间角标 (text-secondary)", SECONDARY),
    ("测量值 (text-primary)", PRIMARY),
    ("深度刻度 (text-secondary)", SECONDARY),
]:
    r = ratio(color, PLATE)
    verdict = "AA正文" if r >= 4.5 else ("AA大字" if r >= 3.0 else "不足")
    print("%-30s %8s %9.2f %7s" % (name, "#15171B", r, verdict))
print()
print("说明：深度刻度落在扇区外的纯黑区（实测底纹 0,0,0），未加底板，")
print("      其对比度为9.39（AA 正文），不受影响。")
