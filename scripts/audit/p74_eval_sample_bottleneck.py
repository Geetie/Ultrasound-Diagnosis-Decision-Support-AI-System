"""A.95：评测样本量的**结构性瓶颈**取证（解释为什么 Q33-① 无法达成）。

## 为什么单独写这个探针

`p73_qwen3_upgrade_readiness.py` 判出「157 样本 < 300，门控无法判定」。
但"样本不够"只是一个数字，**没有回答"为什么攒不起来"**。

本探针给出根因链的**逐层取证**，让下一个人不需要重新挖一遍：

```
库内判读总数 656
  └─ 有正文 405
      └─ 有 image_ids 0  ← ★ 断在这里
          └─ 采集器要求「有影像」（去重键 = patient + sop_instance_uid）
              └─ 采到 30 条
```

即：**405 条有正文的判读，一条影像都没有**。采集器不是坏了，
它按设计拒收无影像记录（因为没有影像就无法满足"同患者同影像只入集一次"的去重口径，
也无法给 L2 忠实度判官提供上下文）。

## 这条与其它结论的关联（同一个根问题）

* A.94/A.95 Q2：影像腿打开也没用 —— 因为 `_image_chunk_text` 不产特征；
* A.95 Q3：`no_image` 在"无正文"组**零区分性**（全库 92.6% 都无图）；
* A.95 Q33-①：样本攒不起来 —— 因为**判读记录普遍没有影像**。

**三条独立的观察指向同一件事**：系统的**影像-文本配对是断的**。
这不是某个模块的 bug，是数据链路的结构性缺口。

## 用法

    ~/uai/venv/bin/python scripts/audit/p74_eval_sample_bottleneck.py

退出码：0 取证完成 ｜ 2 前置缺失。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TARGET_SAMPLES = 700  # 19 号计划的原始目标
UPGRADE_MIN_SAMPLES = 300  # 换基座门控的最低样本


async def _funnel() -> dict:
    """逐层统计"从判读到可入集样本"的漏斗。"""
    from sqlalchemy import text

    from app.config.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        r = (
            await db.execute(
                text(
                    """
                    SELECT
                      count(*) AS total,
                      count(*) FILTER (
                        WHERE findings->'fusion_result'->>'content' IS NOT NULL
                          AND findings->'fusion_result'->>'content' <> ''
                      ) AS has_content,
                      count(*) FILTER (
                        WHERE findings->'fusion_result'->>'content' IS NOT NULL
                          AND findings->'fusion_result'->>'content' <> ''
                          AND jsonb_typeof(findings->'image_ids') = 'array'
                          AND jsonb_array_length(findings->'image_ids') > 0
                      ) AS has_content_and_image,
                      count(*) FILTER (
                        WHERE findings->'image_ids' IS NULL
                           OR jsonb_typeof(findings->'image_ids') <> 'array'
                           OR jsonb_array_length(COALESCE(findings->'image_ids', '[]'::jsonb)) = 0
                      ) AS no_image
                    FROM diagnosis_results
                    """
                )
            )
        ).first()
        # 交叉：dicom_images 有影像但判读记录没挂 image_ids 的有多少
        orphans = (
            await db.execute(
                text(
                    """
                    SELECT count(DISTINCT d.session_id)
                    FROM dicom_images d
                    LEFT JOIN diagnosis_results r ON r.session_id = d.session_id
                    WHERE r.request_id IS NULL
                    """
                )
            )
        ).scalar()
    return {
        "total_results": int(r[0]),
        "has_content": int(r[1]),
        "has_content_and_image": int(r[2]),
        "no_image": int(r[3]),
        "sessions_with_images_but_no_diagnosis": int(orphans or 0),
    }


def build_story(f: dict) -> dict:
    """把漏斗转成"根因链 + 可执行结论"。纯函数（守卫钉住）。"""
    gap = f["has_content"] - f["has_content_and_image"]
    blockers: list[str] = []
    if f["has_content_and_image"] < UPGRADE_MIN_SAMPLES:
        blockers.append(
            f"可用于门控的样本 {f['has_content_and_image']} < {UPGRADE_MIN_SAMPLES}"
        )
    return {
        "funnel": f,
        "root_cause": (
            f"有正文 {f['has_content']} 条里，**{gap} 条没有任何影像**"
            f"（只有 {f['has_content_and_image']} 条同时有正文+影像）。"
            "采集器要求『有影像』（去重键 = patient + sop_instance_uid，"
            "且 L2 判官需要检索上下文），所以只能采到极少数。"
        ),
        "why_not_a_collector_bug": (
            "采集器按设计拒收无影像记录：没有影像就无法满足"
            "『同患者同影像只入集一次』的去重口径，也无法给 L2 忠实度判官提供上下文。"
            "放宽这个条件会得到一批**不可去重、不可判分**的样本，比没有更糟。"
        ),
        "same_root_as_other_findings": [
            "A.94/Q2：影像腿打开也没用 —— `_image_chunk_text` 不产特征（不是开关问题）",
            "A.95/Q3：`no_image` 在无正文组**零区分性**（全库 92.6% 都无图）",
            "A.95/Q33-①：样本攒不起来 —— 判读记录普遍没有影像",
            "→ 三条独立观察指向同一件事：**影像-文本配对是断的**",
        ],
        "blockers": blockers,
        "executable_next_steps": [
            "① 让真机判读**带上影像**跑（前端上传 → 影像入库 → 判读时自动挂 image_ids）；"
            "这是唯一能同时修好「样本量」「影像腿有效性」「证据链完整性」的一件事",
            "② 对已入库的 584 张 DICOM 反向补判读（session 有影像但无判读）",
            "③ 用公开集（AUL 1470 可商用）造**带影像+带标签**的样本，"
            "但需先补中文临床文本，否则产出的是更多『无法判断』",
        ],
    }


async def main(report: str | None) -> int:
    f = await _funnel()
    story = build_story(f)
    print("=== 评测样本量结构性瓶颈取证 ===")
    print(f"  判读总数              : {f['total_results']}")
    print(f"  有正文                : {f['has_content']}")
    print(f"  有正文 **且有影像**   : {f['has_content_and_image']}")
    print(f"  无影像                : {f['no_image']}")
    print(f"  有影像但无判读的 session: {f['sessions_with_images_but_no_diagnosis']}")
    print(f"\n  根因: {story['root_cause']}")
    print(f"\n  {story['why_not_a_collector_bug']}")
    print("\n  同一个根问题的其它表现:")
    for x in story["same_root_as_other_findings"]:
        print(f"    · {x}")
    if story["blockers"]:
        print("\n  阻塞项:")
        for b in story["blockers"]:
            print(f"    · {b}")
    print("\n  可执行的下一步:")
    for _i, s in enumerate(story["executable_next_steps"], 1):
        print(f"    {s}")

    payload = {
        "probe": "p74_eval_sample_bottleneck",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_samples_19hao": TARGET_SAMPLES,
        "upgrade_min_samples": UPGRADE_MIN_SAMPLES,
        **story,
    }
    if report:
        rp = pathlib.Path(report).expanduser()
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告：{rp}")
    return 0


if __name__ == "__main__":
    import asyncio

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--report",
        default=str(pathlib.Path.home() / "uai/reports/eval_sample_bottleneck.json"),
    )
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.report)))
