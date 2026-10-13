#!/usr/bin/env python3
"""渲染层「降级可见性」像素判定（A.58；20 号研究产出 2 第 2 条的离线判定部分）。

问的是什么
----------
真实数据与**占位/空白底板**在截图里差别很大：前者有丰富的灰度层次（超声灰阶、
文字、刻度），后者接近纯色。所以取灰度**方差**作为"这块区域到底有没有内容"的判据。

为什么要像素级
--------------
20 号原文：「降级可见性：占位与真实数据在截图像素级难辨」——A.35 那次影像层三处
catch 链吞掉 chunk 求值失败，页面**空白活了 3 天**，而所有 DOM 断言都是绿的
（空白也是合法 DOM）。DOM 断言 + 像素判定，两层缺一不可。

用法
----
    python scripts/degradation_visibility.py --shot <png> [--shot <png> ...] \\
        [--json-out <out.json>] [--min-variance N]

退出码：0 = 全部达标；1 = 有截图未达标（即"该区域是空白/占位"）；2 = 输入不合格
（文件缺失 / 缺少 Pillow / 参数非法）。

阈值来源（可复算）
------------------
``DEFAULT_MIN_VARIANCE = 200`` 取自真机实测（A.58，1512×950 视口、DPR=1；
原始数据 `C:/Windows/TEMP/uai_p28/p28_variance*.json`）：

| 形态 | 区域尺寸 | 灰度方差 |
|---|---|---|
| 真实影像区（CAMUS 多帧上屏） | 1440×600 | **4830.19** |
| 真实结论区（真机字段文案） | 1440×200 | **3231.92** |
| 坏形态影像区（`page.route` 屏蔽影像字节） | 1440×600 | **72.88** |

阈值 200 落在两者之间：距坏形态 2.7 倍余量、距真实形态低 24 倍 —— **不是拍出来的**。
重算方式：`node scripts/audit/p28_degradation_visibility.mjs`（产出上面那两个 JSON）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 实测（A.58，1512x950 视口、DPR=1；详见附录表）：
#   真实影像区 3600 量级 / 真实结论区 1000 量级 / 坏形态影像区 10 量级
DEFAULT_MIN_VARIANCE = 200.0


def _stats(path: Path) -> dict[str, float | int | str]:
    from PIL import Image, ImageStat

    with Image.open(path) as img:
        rgb = img.convert("RGB")
        gray = rgb.convert("L")
        stat = ImageStat.Stat(gray)
        variance = float(stat.var[0])
        mean = float(stat.mean[0])
        unique = int(len(rgb.getcolors(maxcolors=1 << 24) or []))
    return {
        "file": path.name,
        "variance": round(variance, 2),
        "mean": round(mean, 2),
        "unique_colors": unique,
        "width": rgb.width,
        "height": rgb.height,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shot", action="append", default=[], help="待判定的截图（可多次给出）")
    ap.add_argument("--json-out", default=None, help="判定结果 JSON 落盘路径")
    ap.add_argument("--min-variance", type=float, default=DEFAULT_MIN_VARIANCE,
                    help=f"灰度方差下限（默认 {DEFAULT_MIN_VARIANCE}）")
    args = ap.parse_args(argv)

    if not args.shot:
        print("缺 --shot", file=sys.stderr)
        return 2
    paths = [Path(p) for p in args.shot]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        print(f"截图不存在：{missing}", file=sys.stderr)
        return 2
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("缺少 Pillow（pip install pillow）", file=sys.stderr)
        return 2

    shots = [_stats(p) for p in paths]
    failed = [s for s in shots if float(s["variance"]) < args.min_variance]
    for s in shots:
        flag = "PASS" if float(s["variance"]) >= args.min_variance else "FAIL"
        print(
            f"[{flag}] {s['file']}: variance={s['variance']} mean={s['mean']} "
            f"unique={s['unique_colors']} {s['width']}x{s['height']}"
        )
    result = {
        "passed": not failed,
        "min_variance": args.min_variance,
        "shots": shots,
    }
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if failed:
        print(
            "空白/占位形态：方差低于阈值 → 该区域没有真实数据（A.35 形态）",
            file=sys.stderr,
        )
        return 1
    print(f"全部 {len(shots)} 张截图方差达标（≥{args.min_variance}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
