"""Q2/Q3b：**影像检索腿 → 判读质量** A/B（只读，真机）。

## 这份探针要回答的问题

22 号文档 Q3b（A.94 实测）定位：甲状腺 306 条判读里 59 条（19.3%）写「分级: 无法判断」，
其中 45/59（76%）正文说「缺乏具体的影像特征描述」。根因假设是：

    thyroid.md 要求成分/回声/形态/边缘/钙化；而 settings.rag_use_multimodal=False
    → 影像腿不参与 → 模型拿不到影像特征 → 只能写"缺影像特征"。

A.90 的 `p62` 已证明影像腿在**检索层**能产出命中（GAIN），但从未在完整判读链路里以
`on` 状态跑过 —— 检索层的 GAIN **不等于**判读质量的增益。本探针补这一环。

## 判据（**跑之前写死**，不许看完结果再改）

| 编号 | 判据 | 裁决码 |
|---|---|---|
| **P1 可达** | `on` 臂里，影像腿**真的被执行**的例数比例 ≥ **0.9**（即检索侧 `image_leg_skip_reason` 为空），且至少 1 例真的产出了影像块命中 | `LEG_UNREACHABLE` |
| **P2 有增益** | `on` 臂「无法判断率」相对 `off` 臂的**相对降幅 ≥ 30%** | `NO_GAIN` |
| **P3 不异常** | `on` 臂分级分布不得塌陷：① 单一分级占比 ≤ **0.80**；② 至少出现 2 个不同分级（含"无法判断"）；③ `on` 臂平均主级（TR1..TR5 → 1..5，无法判断不计）与 `off` 臂之差 ≤ **0.75** | `DISTRIBUTION_COLLAPSE` |
| **P0 控制臂** | 两次 `off`（off 与 off2）的无法判断率相对差 ≤ **20%** | `UNSTABLE_CONTROL` |
| **P1b 送达**（附加诊断，见下） | `on` 臂里，影像块**真的出现在送进 prompt 的 top-k 里**的例数比例 —— 不计入裁决，但必须报出 | — |

裁决顺序：P0（控制不稳 → `INCONCLUSIVE`）→ P1（不可达 → `LEG_UNREACHABLE`）→
P3（塌陷 → `DISTRIBUTION_COLLAPSE`）→ P2（无增益 → `NO_GAIN`）→ `GAIN`。

### 为什么必须有 P1b（**这是本轮侦察发现后补的，跑 A/B 之前就写进本文件**）

侦察阶段（未跑 A/B）用与生产同装配的 `Retriever`/`RAGServiceImpl` 直接取块，发现
影像块虽然被 `image_leg_hits_admitted` 收下，但**到不了 prompt**：

1. `app/rag/service.py` Stage 3 精排把 21 个候选压到 `rag_rerank_output_top_k=5`
   —— 影像块正文是 `[影像参考] img_…；模态=US；…`（`retriever._image_chunk_text`），
   没有任何医学文本，精排必排末位；
2. Stage 4 `bm25_fallback.should_trigger`（`low_top1_score`）触发时
   **整个 `result.chunks` 被 BM25 结果替换**（`service.py`：`result.chunks = fallback_chunks`）
   —— 影像块**连残留都没有**。

也就是说：`rag_use_multimodal=True` 能让这条腿"跑起来"，但"跑起来"与"模型看到"
之间还差两段后处理。P1b 就是量这一段的。**它不影响 P1/P2/P3 的裁决口径**，
只用来解释"P2 为什么达标/不达标"。

## 「无法判断率」的口径（预注册，与 A.94 对齐）

单例判为**无法判断** = 满足任一条：
* **未给出可判定分级**：`extract_grading_severest(结论, "thyroid")` 返回 `None`
  （复用 `app/inference/grading_definitions` 单一事实源，不新写正则）；
* **正文自述判不出**：结论含 `无法判断 / 信息不足 / 缺乏具体的影像特征 / 缺少必要 /
  未能解析出` 之一。

两条**分开计数并各自报出**，主指标取并集（更严：既抓"解析不到"也抓"解析到了但
自述依据不足"——A.93 那类"标题 TR3、正文说判不了"正是后者）。

## 三臂设计（同一批影像、同一批 session、同一 clinical_text）

| 臂 | `RAG_USE_MULTIMODAL` | 用途 |
|---|---|---|
| `off`  | `false` | 生产默认（基线） |
| `on`   | `true`  | 处理臂 |
| `off2` | `false` | 控制臂（重跑基线，验一致性） |

**每臂之间必须**：① 改 `~/uai/.env` 的 `RAG_USE_MULTIMODAL` → ② 重启 18000 →
③ 读 `/api/v1/health` 的 `services.image_leg.enabled` 确认**生效值**（不信 .env 文本）→
④ 清 Redis 的 `kb:cache:*` / `rag:cache:*`。

第 ④ 步不是洁癖：`KnowledgeAgentImpl` 的缓存键
（`app/agents/knowledge.py::_make_cache_key`）只含 organ/kb_version/query 与**检索档位**
指纹，**不含 `use_multimodal`**。先跑 `off` 臂会把纯文本块写进 L1（Redis，TTL 3600s），
`on` 臂同一 query 直接命中缓存 → 根本不调用检索 → 影像腿永远不执行，实验会
**假阴性**（两臂差异恒为 0，看起来像"开关没用"）。

## 用法（服务器，由 `p65_image_leg_grading_ab.sh` 驱动）

    # 只取检索层证据（不需要切开关、不需要 LLM）
    python scripts/audit/p65_image_leg_grading_ab.py --mode probe --limit 12
    # 每个臂跑一次真判读（臂间由驱动脚本切开关 + 重启）
    python scripts/audit/p65_image_leg_grading_ab.py --mode arm --arm off  --limit 12
    python scripts/audit/p65_image_leg_grading_ab.py --mode arm --arm on   --limit 12
    python scripts/audit/p65_image_leg_grading_ab.py --mode arm --arm off2 --limit 12
    # 合并 + 按预注册判据裁决
    python scripts/audit/p65_image_leg_grading_ab.py --mode aggregate

退出码：0 有结论（GAIN / NO_GAIN / DISTRIBUTION_COLLAPSE / INCONCLUSIVE 都算有结论）；
2 前置缺失（无合格样本 / 臂不全）；3 `LEG_UNREACHABLE`（接线有问题，本轮结论作废）。

## 明令禁止

* **不许改 prompt 来凑效果** —— A/B 只切开关。本脚本对 `app/**` 全只读。
* 不许用"看起来像"的文本判断顶替分级抽取 —— 一律走 `grading_definitions`。
* 不许把"臂没跑起来"（开关没生效 / 缓存串味）算成"开关无用"。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys
import time
from datetime import datetime, timezone

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
_REPO_ROOT = _SCRIPTS.parent
for _p in (str(_REPO_ROOT), str(_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

BASE = "http://127.0.0.1:18000"
UAI = pathlib.Path.home() / "uai"
REPORT_DIR = UAI / "reports"
LOG_PATH = UAI / "logs" / "api.log"

#: 报告文件名（聚合结果）
FINAL_REPORT = REPORT_DIR / "image_leg_grading_ab.json"

# ---------- 预注册阈值（**跑前写死**）----------
P1_MIN_LEG_EXECUTED_RATIO = 0.90   # on 臂影像腿被执行的比例下限
P2_MIN_RELATIVE_DROP = 0.30        # 无法判断率相对降幅下限
P3_MAX_SINGLE_GRADE_SHARE = 0.80   # 单一分级占比上限
P3_MAX_MEAN_LEVEL_DELTA = 0.75     # 两臂平均主级之差上限
P0_MAX_RELATIVE_DIFF = 0.20        # 两次 off 的无法判断率相对差上限

ARM_OFF = "off"
ARM_ON = "on"
ARM_OFF2 = "off2"
ARM_NAMES = (ARM_OFF, ARM_ON, ARM_OFF2)

TERMINAL = ("completed", "needs_review", "failed", "cancelled")

#: 正文自述"判不出来"的标记（A.94 口径：76% 说缺影像特征）
UNABLE_MARKERS = (
    "无法判断",
    "信息不足",
    "缺乏具体的影像特征",
    "缺少必要的影像特征",
    "缺影像特征",
    "缺少必要",
    "未能解析出",
    "无法给出",
    "无法进行",
)

_GRADE_LABELS = ("TR1", "TR2", "TR3", "TR4", "TR5", "无法判断")

#: 结论正文的 clinical_text：三臂**必须完全一致**（否则差异不可归因）。
#: 刻意用一句中性的、不提供任何影像特征的问诊文本 —— 与 A.94 那批
#: "只有文本、没给特征"的真实请求同形。
DEFAULT_CLINICAL_TEXT = "甲状腺超声检查，请结合影像给出 TI-RADS 分级。"


# ======================================================================
# 纯函数（可单测 / 可离线复算）
# ======================================================================
def classify_conclusion(text: str) -> dict:
    """结论正文 → 「无法判断」判定（纯函数，口径见模块 docstring）。

    Returns:
        ``{"grade_raw", "grade_level", "unparseable", "says_unable", "unable"}``
    """
    from app.inference.grading_definitions import (
        extract_grading_severest,
        grade_major_level,
    )

    body = str(text or "")
    raw = extract_grading_severest(body, "thyroid")
    level = grade_major_level("thyroid", raw)
    says_unable = any(m in body for m in UNABLE_MARKERS)
    unparseable = level is None
    return {
        "grade_raw": raw,
        "grade_level": level,
        "unparseable": unparseable,
        "says_unable": says_unable,
        # 主指标：任一条成立即"无法判断"（见 docstring 口径段）
        "unable": bool(unparseable or says_unable),
    }


def grade_label(level: int | None) -> str:
    """主级整数 → 可读标签（无法判断 = ``level is None``）。"""
    if level is None:
        return "无法判断"
    idx = int(level) - 1
    return _GRADE_LABELS[idx] if 0 <= idx < 5 else f"L{level}"


def summarize_arm(records: list[dict]) -> dict:
    """单臂汇总（纯函数）：无法判断率 + 分级分布 + 各项计数。"""
    n = len(records)
    if n == 0:
        return {"n": 0}
    unable = sum(1 for r in records if r["judgement"]["unable"])
    unparseable = sum(1 for r in records if r["judgement"]["unparseable"])
    says_unable = sum(1 for r in records if r["judgement"]["says_unable"])
    dist: dict[str, int] = {}
    levels: list[int] = []
    for r in records:
        lab = grade_label(r["judgement"]["grade_level"])
        dist[lab] = dist.get(lab, 0) + 1
        if r["judgement"]["grade_level"] is not None:
            levels.append(int(r["judgement"]["grade_level"]))
    # 「反向索要守卫」把正文替换成系统兜底文案的例数（A.58）。
    # 这些例的"无法判断"来自**守卫拦截**，不是模型给不出分级 —— 必须单独计数，
    # 否则会把"守卫在正常工作"读成"影像腿没用"。
    handoff_text = _handoff_text()
    n_handoff = sum(
        1 for r in records
        if handoff_text and str(r.get("conclusion") or "").strip() == handoff_text.strip()
    )
    # SG 禁忌命中 + 结论被抑制（首轮实测 36/36 —— 传 patient_context.conditions=[]
    # 后应归零；两个数都留在报告里，作为"混淆变量是否已消除"的证据）。
    n_contra = sum(
        1 for r in records if (r.get("sg") or {}).get("contraindication_hit")
    )
    n_suppressed = sum(
        1 for r in records if (r.get("sg") or {}).get("conclusion_suppressed")
    )
    return {
        "n": n,
        "unable": unable,
        "unable_rate": round(unable / n, 4),
        "unparseable": unparseable,
        "unparseable_rate": round(unparseable / n, 4),
        "says_unable": says_unable,
        "says_unable_rate": round(says_unable / n, 4),
        "handoff_guard_cases": n_handoff,
        "sg_contraindication_hit": n_contra,
        "sg_conclusion_suppressed": n_suppressed,
        "distribution": dict(sorted(dist.items())),
        "single_grade_max_share": round(max(dist.values()) / n, 4) if dist else 0.0,
        "distinct_grades": len(dist),
        "mean_grade_level": round(sum(levels) / len(levels), 4) if levels else None,
        "n_graded": len(levels),
        "image_leg_executed": sum(
            1 for r in records
            if r.get("leg", {}).get("executed") is True
            and not r.get("leg", {}).get("attached_skip_reason")
        ),
        "image_leg_disabled_cases": sum(
            1 for r in records
            if r.get("leg", {}).get("attached_skip_reason") == "leg_disabled"
        ),
        "prompt_visible_image_chunks": sum(
            1 for r in records if (r.get("leg", {}).get("prompt_visible_image_chunks") or 0) > 0
        ),
        "evidence_sources": _count_by(
            (r.get("leg", {}).get("evidence_source") or "none") for r in records
        ),
        "wall_s_median": _median([r.get("wall_s") for r in records if r.get("wall_s")]),
    }


def _handoff_text() -> str:
    """A.58 索要守卫的系统兜底文案（导入失败返回空串，不影响主指标）。"""
    try:
        from app.agents.specialist.solicitation import HANDOFF_TEXT

        return HANDOFF_TEXT
    except Exception:  # silent-ok: 该文案只用于归因计数，导入失败不该让汇总炸掉
        return ""


def _count_by(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return out


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    vals = sorted(values)
    mid = len(vals) // 2
    if len(vals) % 2:
        return round(vals[mid], 2)
    return round((vals[mid - 1] + vals[mid]) / 2, 2)


def relative_drop(baseline_rate: float, treatment_rate: float) -> float:
    """相对降幅 ``(base - treat) / base``；base=0 时：treat>0 → 0，treat=0 → 0。

    基线为 0 时"降幅"无定义（无法再降），按 0 处理并在报告里显式标注
    ``baseline_zero=True`` —— 不许静默当成达标。
    """
    if baseline_rate <= 0:
        return 0.0
    return round((baseline_rate - treatment_rate) / baseline_rate, 4)


def verdict(arm_summaries: dict[str, dict]) -> tuple[str, dict]:
    """按**预注册**判据裁决（纯函数，守卫钉住）。

    顺序：P0 控制 → P1 可达 → P3 塌陷 → P2 增益 → GAIN。
    先判 P0/P1 的理由：控制不稳或腿没跑起来时，P2 的差异**不可归因**，
    此时报"无增益"就是把"实验没做对"说成"开关没用"（A.86/A.63 同族错误）。
    """
    off = arm_summaries.get(ARM_OFF) or {}
    on = arm_summaries.get(ARM_ON) or {}
    off2 = arm_summaries.get(ARM_OFF2) or {}
    facts: dict = {
        "off": {k: off.get(k) for k in ("n", "unable_rate", "distribution")},
        "on": {k: on.get(k) for k in ("n", "unable_rate", "distribution")},
        "off2": {k: off2.get(k) for k in ("n", "unable_rate", "distribution")},
    }
    if not (off.get("n") and on.get("n") and off2.get("n")):
        return "ARMS_INCOMPLETE", facts

    # ---- P0 控制臂 ----
    r1, r2 = float(off["unable_rate"]), float(off2["unable_rate"])
    control_rel = (
        abs(r1 - r2) / max(r1, r2) if max(r1, r2) > 0 else 0.0
    )
    facts["P0_control"] = {
        "off_rate": r1,
        "off2_rate": r2,
        "relative_diff": round(control_rel, 4),
        "threshold": P0_MAX_RELATIVE_DIFF,
        "passed": control_rel <= P0_MAX_RELATIVE_DIFF,
    }

    # ---- P1 可达 ----
    # 口径（跑前写死）：on 臂里"影像腿被执行"= 日志 `image_leg_attached` 的
    # skip_reason 为空（即真的取了字节、没被能力闸/开关/无 session 挡掉）。
    executed_ratio = (on.get("image_leg_executed") or 0) / max(on["n"], 1)
    facts["P1_reachable"] = {
        "leg_executed_cases": on.get("image_leg_executed"),
        "n": on["n"],
        "ratio": round(executed_ratio, 4),
        "threshold": P1_MIN_LEG_EXECUTED_RATIO,
        "threshold_ok": executed_ratio >= P1_MIN_LEG_EXECUTED_RATIO,
        # 对照：off 臂必须**基本为 0**，否则说明开关没真的关（实验无效）
        "off_leg_executed_cases": off.get("image_leg_executed"),
        "off_leg_disabled_cases": off.get("image_leg_disabled_cases"),
        "off_leg_disabled_ratio": round(
            (off.get("image_leg_disabled_cases") or 0) / max(off["n"], 1), 4),
        "evidence_source": (on.get("evidence_sources") or {}),
    }
    # ---- P1b 送达（诊断，不计入裁决）----
    facts["P1b_prompt_delivery"] = {
        "cases_with_image_chunk_in_prompt": on.get("prompt_visible_image_chunks"),
        "n": on["n"],
        "ratio": round((on.get("prompt_visible_image_chunks") or 0) / max(on["n"], 1), 4),
        "leg_hits_admitted_total_arm_level": on.get("arm_level_hits_admitted"),
        "note": "影像块是否真的出现在送进 prompt 的 top-k（见模块 docstring 的 P1b 段）。"
                "命中数只能臂级统计：`image_leg_hits_admitted` 日志行不带 request_id",
    }

    # ---- P3 分布 ----
    share = float(on.get("single_grade_max_share") or 0.0)
    distinct = int(on.get("distinct_grades") or 0)
    mean_off, mean_on = off.get("mean_grade_level"), on.get("mean_grade_level")
    mean_delta = (
        abs(float(mean_off) - float(mean_on))
        if mean_off is not None and mean_on is not None
        else None
    )
    p3_ok = (
        share <= P3_MAX_SINGLE_GRADE_SHARE
        and distinct >= 2
        and (mean_delta is None or mean_delta <= P3_MAX_MEAN_LEVEL_DELTA)
    )
    facts["P3_distribution"] = {
        "on_single_grade_max_share": share,
        "max_share_threshold": P3_MAX_SINGLE_GRADE_SHARE,
        "on_distinct_grades": distinct,
        "mean_level_off": mean_off,
        "mean_level_on": mean_on,
        "mean_level_delta": round(mean_delta, 4) if mean_delta is not None else None,
        "max_mean_delta_threshold": P3_MAX_MEAN_LEVEL_DELTA,
        "passed": p3_ok,
    }

    # ---- 归因分解（**不计入裁决**，但必须报出：否则"无法判断"会被单一成因冒充）----
    facts["attribution"] = {
        "handoff_guard_cases": {
            "off": off.get("handoff_guard_cases"),
            "on": on.get("handoff_guard_cases"),
            "off2": off2.get("handoff_guard_cases"),
            "note": "正文被 A.58 反向索要守卫替换为系统兜底文案的例数"
                    "（模型在向医生索要影像特征，守卫按设计拦截）",
        },
        "sg_contraindication_hit": {
            "off": off.get("sg_contraindication_hit"),
            "on": on.get("sg_contraindication_hit"),
            "off2": off2.get("sg_contraindication_hit"),
            "warn_if_nonzero": "非 0 说明请求没带 patient_context.conditions=[]，"
                               "SG fail-closed 恒判禁忌 → 混淆变量未消除",
        },
        "sg_conclusion_suppressed": {
            "off": off.get("sg_conclusion_suppressed"),
            "on": on.get("sg_conclusion_suppressed"),
            "off2": off2.get("sg_conclusion_suppressed"),
        },
    }

    # ---- P2 增益 ----
    drop = relative_drop(r1, float(on["unable_rate"]))
    facts["P2_gain"] = {
        "baseline_rate": r1,
        "treatment_rate": float(on["unable_rate"]),
        "relative_drop": drop,
        "threshold": P2_MIN_RELATIVE_DROP,
        "baseline_zero": r1 <= 0,
        "passed": drop >= P2_MIN_RELATIVE_DROP,
    }

    if not facts["P0_control"]["passed"]:
        return "INCONCLUSIVE_UNSTABLE_CONTROL", facts
    if not facts["P1_reachable"]["threshold_ok"]:
        return "LEG_UNREACHABLE", facts
    if not p3_ok:
        return "DISTRIBUTION_COLLAPSE", facts
    if not facts["P2_gain"]["passed"]:
        return "NO_GAIN", facts
    return "GAIN", facts


# ======================================================================
# 取样本
# ======================================================================
async def pick_cases(limit: int) -> list[dict]:
    """挑 N 个「已同意 + 有真实影像（盘上有字节）+ 影像已入向量库」的 thyroid 病例。

    三个条件缺一不可：
    * 已同意 —— 未同意会被护栏 403 挡（那是护栏，不是缺陷）；
    * 盘上有字节 —— 否则 `image_bytes_unavailable`，影像腿一定不执行；
    * 影像已入向量库 —— 否则即使开关打开也检索不到（`image_leg_no_hits`），
      会把"这例没灌向量"错算成"开关无用"。

    每个 session 只取 1 张代表影像（三臂用同一张，保证同批）。
    """
    from app.config.database import AsyncSessionLocal
    from app.config.settings import settings

    # 向量库里有哪些 session（按 payload.session_id）
    indexed: set[str] = set()
    try:
        base = f"http://{settings.qdrant_host}:{settings.qdrant_port}"
        headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as c:
            offset = None
            while True:
                body: dict = {"limit": 256, "with_payload": ["session_id"], "with_vector": False}
                if offset is not None:
                    body["offset"] = offset
                r = await c.post(
                    f"{base}/collections/{settings.rag_image_collection}/points/scroll",
                    json=body,
                )
                res = (r.json() or {}).get("result") or {}
                page = res.get("points") or []
                if not page:
                    break
                for p in page:
                    sid = (p.get("payload") or {}).get("session_id")
                    if sid:
                        indexed.add(str(sid))
                offset = res.get("next_page_offset")
                if offset is None:
                    break
    except Exception as exc:  # 探不到 ≠ 没有：显式报出，不静默当成"集合空"
        print(f"[WARN] 向量库 session 清单获取失败：{type(exc).__name__}: {exc}")

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(
            """
            SELECT s.session_id, s.patient_id, MIN(di.image_id) AS image_id,
                   MIN(di.storage_path) AS storage_path,
                   MAX(d.clinical_text) AS clinical_text
            FROM sessions s
            JOIN dicom_images di ON di.session_id = s.session_id
            JOIN consent_records c ON c.patient_id = s.patient_id AND c.status = 'granted'
            LEFT JOIN diagnosis_results d
                   ON d.session_id = s.session_id AND d.organ = 'thyroid'
            WHERE s.target_organs::text LIKE '%thyroid%'
              AND di.storage_path IS NOT NULL
            GROUP BY s.session_id, s.patient_id
            ORDER BY s.session_id
            """
        ))).all()

    root = UAI / "src"
    out: list[dict] = []
    skipped: list[dict] = []
    for sid, pid, iid, spath, ctext in rows:
        sid, iid, spath = str(sid), str(iid), str(spath)
        if sid not in indexed:
            skipped.append({"session_id": sid, "reason": "image_not_in_vector_collection"})
            continue
        p = pathlib.Path(spath)
        if not p.is_absolute():
            p = root / spath.lstrip("./")
        if not p.exists() or p.stat().st_size <= 0:
            skipped.append({"session_id": sid, "reason": "image_bytes_missing_on_disk"})
            continue
        out.append({
            "session_id": sid,
            "patient_id": str(pid),
            "image_id": iid,
            "storage_path": spath,
            "clinical_text": (str(ctext) if ctext and str(ctext) != "None"
                              else DEFAULT_CLINICAL_TEXT),
            "dicom_bytes": p.stat().st_size,
        })
        if len(out) >= limit:
            break

    print(f"[样本] 合格 {len(out)} 例，跳过 {len(skipped)} 例")
    if skipped:
        agg: dict[str, int] = {}
        for s in skipped:
            agg[s["reason"]] = agg.get(s["reason"], 0) + 1
        print(f"       跳过原因分布：{agg}")
    return out


# ======================================================================
# mode=probe：检索层证据（不切开关、不调 LLM）
# ======================================================================
async def _build_rag_service():
    """按 `app/main.py` 同一装配构造 RAGService（只读副本，不动生产进程）。"""
    from app.config.database import AsyncSessionLocal
    from app.config.settings import settings
    from app.embedding.bge_m3 import BgeM3MedicalService
    from app.embedding.index_factory import create_index_client_from_settings
    from app.embedding.ultrasound_clip import UltrasoundClipService
    from app.knowledge.udt_parser import UDTParser
    from app.knowledge.vocabulary import UDAFVocabulary
    from app.rag.bm25_fallback import BM25Fallback, BM25Index
    from app.rag.query_enhancer import QueryEnhancer
    from app.rag.reranker import BgeRerankerService, HeuristicReranker
    from app.rag.retriever import Retriever
    from app.rag.service import RAGServiceImpl

    vector_client = await create_index_client_from_settings()
    emb = BgeM3MedicalService(
        base_url=f"http://{settings.bge_m3_host}:{settings.bge_m3_port}"
    )
    clip = UltrasoundClipService(
        base_url=f"http://{settings.ultrasound_clip_host}:{settings.ultrasound_clip_port}"
    )
    reranker = BgeRerankerService(
        base_url=f"http://{settings.bge_reranker_host}:{settings.bge_reranker_port}",
        model=settings.bge_reranker_model,
        timeout_seconds=settings.bge_reranker_timeout_seconds,
        health_check_timeout_seconds=settings.bge_reranker_health_check_timeout_seconds,
    )
    bm25 = BM25Index(session_factory=AsyncSessionLocal)
    try:
        await bm25.ensure_built()
    except Exception as exc:
        print(f"[WARN] BM25 预热失败：{type(exc).__name__}: {exc}")

    return RAGServiceImpl(
        query_enhancer=QueryEnhancer(vocabulary=UDAFVocabulary(), udt_parser=UDTParser()),
        retriever=Retriever(
            vector_client=vector_client,
            embedding_service=emb,
            ultrasound_clip=clip,
            bm25_index=bm25,
            session_factory=AsyncSessionLocal,
        ),
        reranker=reranker,
        heuristic_reranker=HeuristicReranker(),
        bm25_fallback=BM25Fallback(
            bm25,
            min_top1_score=settings.rag_bm25_min_top1_score,
            min_results=settings.rag_bm25_min_results,
        ),
        kb_version=settings.rag_kb_version,
        # probe 模式**一律不挂缓存**：本探针要看的是"真实检索排序"，
        # 缓存会把上一次的结果原样返回，测不到影像块在哪一步消失。
        cache=None,
    )


async def _retrieve_once(svc, *, case: dict, image_data: bytes | None,
                         profile: dict, tag: str) -> dict:
    """跑一次检索，返回「送出去/进 prompt」的块清单。"""
    from app.rag.protocols import RetrievalRequest

    req = RetrievalRequest(
        query=case["clinical_text"],
        organ="thyroid",
        top_k=profile["top_k"],
        use_query_enhancement=profile["use_query_enhancement"],
        use_reranking=profile["use_reranking"],
        use_bm25_fallback=profile["use_bm25_fallback"],
        use_llm_enhancement=False,
        use_multimodal=True,
        image_data=image_data,
        image_format="png",
        session_id=case["session_id"],
        request_id=f"p65-probe-{tag}-{case['session_id']}",
    )
    res = await svc.retrieve(req)
    chunks = [
        {
            "chunk_id": sc.chunk.chunk_id,
            "is_image_chunk": str(sc.chunk.chunk_id).startswith("img_"),
            "source": sc.retrieval_source.value,
            "content_preview": str(sc.chunk.content or "")[:120],
        }
        for sc in res.chunks
    ]
    return {
        "skip_reason": getattr(res, "image_leg_skip_reason", None) or "",
        "retrieval_method": res.retrieval_method.value,
        "triggered_bm25_fallback": bool(getattr(res, "triggered_bm25_fallback", False)),
        "n_chunks": len(chunks),
        "n_image_chunks": sum(1 for c in chunks if c["is_image_chunk"]),
        "chunks": chunks,
    }


async def mode_probe(limit: int) -> int:
    """检索层证据：同一批影像，四组对照，定位影像块在哪一步消失。"""
    from app.rag.image_leg import middle_frame_png
    from app.services.dicom_storage import get_dicom_storage

    cases = await pick_cases(limit)
    if not cases:
        print("[FATAL] 没有合格样本")
        return 2

    svc = await _build_rag_service()
    storage = get_dicom_storage()

    # 生产档位（与 routing.full() 一致）；以及"关掉后处理"的对照档
    PROD = {"top_k": 20, "use_query_enhancement": True, "use_reranking": True,
            "use_bm25_fallback": True}
    RAW = {"top_k": 8, "use_query_enhancement": True, "use_reranking": False,
           "use_bm25_fallback": False}

    rows: list[dict] = []
    for i, case in enumerate(cases):
        try:
            raw = await storage.retrieve(case["storage_path"])
            png = middle_frame_png(raw) if raw else None
        except Exception as exc:
            rows.append({"case_index": i, "session_id": case["session_id"],
                         "error": f"{type(exc).__name__}: {exc}"[:160]})
            continue
        if not png:
            rows.append({"case_index": i, "session_id": case["session_id"],
                         "error": "frame_decode_failed"})
            continue

        prod_off = await _retrieve_once(
            svc, case=case, image_data=None, profile=PROD, tag=f"{i}-prod-off")
        prod_on = await _retrieve_once(
            svc, case=case, image_data=png, profile=PROD, tag=f"{i}-prod-on")
        raw_on = await _retrieve_once(
            svc, case=case, image_data=png, profile=RAW, tag=f"{i}-raw-on")

        rows.append({
            "case_index": i,
            "session_id": case["session_id"],
            "image_id": case["image_id"],
            "png_bytes": len(png),
            "prod_off": prod_off,
            "prod_on": prod_on,
            "raw_on": raw_on,
            # 三处关键结论（报告直接引用）
            "leg_executed": prod_on["skip_reason"] == "",
            "image_chunk_in_prompt_prod": prod_on["n_image_chunks"] > 0,
            "image_chunk_in_chunks_raw": raw_on["n_image_chunks"] > 0,
            "prompt_delta_chunks": (
                len({c["chunk_id"] for c in prod_on["chunks"]}
                    - {c["chunk_id"] for c in prod_off["chunks"]})
            ),
        })
        rows[-1]["image_chunk_content"] = next(
            (c["content_preview"] for c in raw_on["chunks"] if c["is_image_chunk"]), ""
        )
        print(f"  [{i}] {case['session_id']}: leg={rows[-1]['leg_executed']} "
              f"prompt_img={rows[-1]['image_chunk_in_prompt_prod']} "
              f"raw_img={rows[-1]['image_chunk_in_chunks_raw']} "
              f"prompt_delta={rows[-1]['prompt_delta_chunks']}")

    usable = [r for r in rows if "error" not in r]
    facts = {
        "n_cases": len(usable),
        "leg_executed": sum(1 for r in usable if r["leg_executed"]),
        "image_chunk_reached_prompt_prod": sum(
            1 for r in usable if r["image_chunk_in_prompt_prod"]),
        "image_chunk_present_before_postprocess": sum(
            1 for r in usable if r["image_chunk_in_chunks_raw"]),
        "prompt_delta_total_chunks": sum(r["prompt_delta_chunks"] for r in usable),
        "bm25_fallback_triggered": sum(
            1 for r in usable if r["prod_on"]["triggered_bm25_fallback"]),
    }
    payload = {
        "probe": "p65_image_leg_grading_ab",
        "mode": "probe",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profiles": {"prod": PROD, "raw": RAW},
        "facts": facts,
        "cases": rows,
    }
    out = REPORT_DIR / "image_leg_ab_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n== 检索层证据 ==")
    print(json.dumps(facts, ensure_ascii=False, indent=2))
    print(f"报告：{out}")
    return 0


# ======================================================================
# mode=arm：跑一轮真判读
# ======================================================================
def _parse_evidence_chain_log(log_text: str) -> dict[str, list[str]]:
    """从 api.log 抓 `evidence_chain` 行 → ``request_id -> prompt_chunk_ids``。

    为什么必须看生产日志而不是猜：prompt 里到底喂了哪几块，只有生产侧
    （`RSSpecialistAgent._log_evidence_chain`，`UAI_EVIDENCE_CHAIN_DEBUG=1`）知道。
    进程外探针拦不到 worker 的检索调用（A.62 实测）。

    真机日志形态（structlog 的 key=value，**request_id 在后面**）::

        evidence_chain  organ=thyroid prompt_chunk_ids=[...] prompt_chunks=5 request_id=req_x

    同一 request_id 会有**多条**（每次检索一轮 + 落库一轮，CRAG 重检索还会再来一轮），
    因此：只收带 ``prompt_chunk_ids`` 的行，并**取并集**（模型看到过任何一块都算见过）。
    """
    out: dict[str, list[str]] = {}
    for line in log_text.splitlines():
        if "evidence_chain" not in line or "prompt_chunk_ids" not in line:
            continue
        m_rid = re.search(r"request_id=(\S+)", line) or re.search(
            r'"request_id":\s*"([^"]+)"', line
        )
        m_ids = re.search(r"prompt_chunk_ids=\[([^\]]*)\]", line) or re.search(
            r'"prompt_chunk_ids":\s*\[([^\]]*)\]', line
        )
        if not m_rid or not m_ids:
            continue
        rid = m_rid.group(1).strip().strip('",')
        ids = [x.strip().strip("'\"") for x in m_ids.group(1).split(",") if x.strip()]
        bucket = out.setdefault(rid, [])
        for i in ids:
            if i not in bucket:
                bucket.append(i)
    return out


def _parse_leg_events_by_request(log_text: str) -> dict[str, dict]:
    """从 api.log 抓**每条请求**的影像腿事件（比 probe 的"同参数"更有说服力）。

    真机日志形态::

        image_leg_attached  bytes=0 ready=False request_id=req_x skip_reason=leg_disabled
        image_leg_skipped   organ=thyroid reason=no_image_data request_id=req_x

    ## 一个必须写清的**能力边界**（真机实测）

    `image_leg_hits_admitted` 那一行**不带 `request_id`**
    （`app/rag/retriever.py::_wrap_search_results` 只打了 count/chunk_ids/hint）。
    所以"命中"**无法归到具体请求**。本函数对这类行**不猜测归属**：
    只累计到返回值的 ``__arm_level__`` 键，绝不按顺序硬配给某条请求
    （那会把 A 例的命中算到 B 例头上，与 A.86/A.90 的"原因串味"同族）。
    """
    out: dict[str, dict] = {}

    def _slot(rid: str) -> dict:
        return out.setdefault(rid, {
            "attached": False,
            "attached_skip_reason": None,
            "retrieval_skipped_reason": None,
        })

    for line in log_text.splitlines():
        if "image_leg" not in line:
            continue
        if "image_leg_hits_admitted" in line:
            # 无 request_id → 只做**臂级**计数，不做逐例归属
            arm = out.setdefault("__arm_level__", {"hits_admitted": 0,
                                                   "admitted_chunk_ids": []})
            m_cnt = re.search(r"count=(\d+)", line)
            arm["hits_admitted"] += int(m_cnt.group(1)) if m_cnt else 1
            m_ids = re.search(r"chunk_ids=\[([^\]]*)\]", line)
            if m_ids:
                for cid in [x.strip().strip("'\"") for x in m_ids.group(1).split(",")
                            if x.strip()]:
                    if cid not in arm["admitted_chunk_ids"]:
                        arm["admitted_chunk_ids"].append(cid)
            continue
        m_rid = re.search(r"request_id=(\S+)", line)
        if not m_rid:
            continue
        rid = m_rid.group(1).strip().strip('",')
        if "image_leg_attached" in line:
            s = _slot(rid)
            s["attached"] = True
            m = re.search(r"skip_reason=(\S+)", line)
            raw = m.group(1) if m else None
            # structlog 把 None 打成字面量 `None` —— 它不是"跳过原因"，
            # 归一成真正的 None，否则"执行成功"会被读成"跳过原因是 'None'"。
            if raw is not None:
                raw = raw.strip().strip("'\",")
                if raw in ("None", "none", "null", ""):
                    raw = None
            s["attached_skip_reason"] = raw
        elif "image_leg_skipped" in line:
            s = _slot(rid)
            m = re.search(r"reason=(\S+)", line)
            if m:
                s["retrieval_skipped_reason"] = m.group(1)
    return out


def _scan_log_for_leg(log_text: str) -> dict:
    """统计日志里的影像腿事件（定性佐证，样本级明细以 probe 为准）。"""
    return {
        "image_leg_hits_admitted": len(re.findall(r"image_leg_hits_admitted", log_text)),
        "image_leg_skipped": len(re.findall(r"image_leg_skipped\b", log_text)),
        "image_leg_filter_relaxed": len(re.findall(r"image_leg_filter_relaxed", log_text)),
        "rag_cache_bypassed_image_leg": len(
            re.findall(r"rag_cache_bypassed_image_leg", log_text)),
        "image_leg_attached": len(re.findall(r"image_leg_attached", log_text)),
    }


async def _read_db_record(request_id: str) -> dict:
    """从 DB 读该请求的真实落库结果（与 A.94 统计 19.3% 时的同一字段）。"""
    from app.config.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        row = (await db.execute(text(
            """
            SELECT status, confidence,
                   findings->'fusion_result'->>'content' AS content,
                   findings->'fusion_result'->>'compared_with_prior' AS compared_prior,
                   findings->'segmentation'->>'available' AS seg_available,
                   findings->'segmentation'->>'reason' AS seg_reason,
                   findings->'timings_ms' AS timings,
                   findings->'sg_result' AS sg
            FROM diagnosis_results WHERE request_id = :rid
            """
        ), {"rid": request_id})).all()
    if not row:
        return {}
    r = row[0]
    sg = r[7] if len(r) > 7 and isinstance(r[7], dict) else {}
    sgd = sg.get("degraded_output") or {}
    return {
        "status": r[0],
        "confidence": r[1],
        "content": r[2] or "",
        "compared_with_prior": r[3],
        "segmentation_available": r[4],
        "segmentation_reason": r[5],
        "timings": r[6] or {},
        "sg": {
            "passed": sg.get("passed"),
            "degraded_mode": sg.get("degraded_mode"),
            "degraded_reason": sg.get("degraded_reason"),
            "contraindication_hit": bool(sgd.get("contraindication_hit")),
            "conclusion_suppressed": bool(sgd.get("conclusion_suppressed")),
            "message": str(sg.get("message") or "")[:120],
        },
    }


async def _submit_with_backoff(client, headers: dict, body: dict,
                               *, max_attempts: int = 8):
    """提交判读，遇 429 退避重试（**不加 retry 标志**，保持生产口径）。

    限流口径：`/api/v1/diagnosis/request` 为 10/min（`app/config/rate_limit.py`）。
    三臂共 3×N 次提交，若不退避会有大量 429 —— 但**不能**用 ``retry=True`` 绕过，
    那会顺带把 ``skip_self_rag`` 打开（见 mode_arm 里的说明）。

    429 是**被拒绝**（没有 request_id、不落库），重试是安全的；其它状态码原样返回。
    退避用 ``Retry-After``（服务端给），没给就指数退避封顶 20s。
    """
    delay = 2.0
    last = None
    for attempt in range(max_attempts):
        try:
            rs = await client.post(f"{BASE}/api/v1/diagnosis/request",
                                   headers=headers, json=body)
        except Exception:
            await asyncio.sleep(delay)
            delay = min(delay * 2, 20.0)
            continue
        last = rs
        if rs.status_code != 429:
            return rs
        wait = rs.headers.get("Retry-After")
        try:
            wait_s = float(wait) if wait else delay
        except (TypeError, ValueError):
            wait_s = delay
        wait_s = min(max(wait_s, 1.0), 30.0)
        print(f"       429 限流，退避 {wait_s:.0f}s（第 {attempt + 1} 次）")
        await asyncio.sleep(wait_s)
        delay = min(delay * 2, 20.0)
    return last


async def mode_arm(arm: str, limit: int, cases_file: pathlib.Path) -> int:
    """跑一个臂：同一批 case（从 cases_file 读，保证三臂同批）→ 真判读 → 落臂报告。"""
    from app.config.settings import settings

    if not cases_file.exists():
        print(f"[FATAL] 样本清单不存在：{cases_file}（先跑 --mode pin）")
        return 2
    cases = json.loads(cases_file.read_text(encoding="utf-8"))
    print(f"[臂 {arm}] 样本 {len(cases)} 例；进程内 rag_use_multimodal={settings.rag_use_multimodal}")

    expected = arm == ARM_ON
    if bool(settings.rag_use_multimodal) != expected:
        # 开关与臂不符 → 直接失败，不产出"看起来正常"的假数据（零静默）。
        print(f"[FATAL] 开关未按预期生效：期望 {expected}，实际 {settings.rag_use_multimodal}")
        print("        请先由驱动脚本切 .env + 重启 18000，再跑本臂。")
        return 2

    pw = (UAI / ".smoke_pw").read_text(encoding="utf-8").strip()
    records: list[dict] = []
    async with httpx.AsyncClient(timeout=90.0) as c:
        r = await c.post(f"{BASE}/api/v1/auth/login",
                         json={"username": "smoke-doctor", "password": pw})
        if r.status_code != 200:
            print(f"[FATAL] 登录失败 http={r.status_code}")
            return 2
        token = (r.json() or {}).get("access_token")
        headers = {"Authorization": f"Bearer {token}"}

        # 健康端点确认"开关在该进程里真的生效"（读的是进程内 settings，不是 .env 文本）
        h = await c.get(f"{BASE}/api/v1/health")
        leg_health = ((h.json() or {}).get("services") or {}).get("image_leg") or {}
        print(f"[臂 {arm}] health.image_leg.enabled={leg_health.get('enabled')} "
              f"ready={leg_health.get('ready')} note={leg_health.get('note')}")
        if bool(leg_health.get("enabled")) != expected:
            print("[FATAL] health 报的开关值与臂不符 → 重启没生效，停止")
            return 2

        for i, case in enumerate(cases):
            t0 = time.monotonic()
            body = {
                "session_id": case["session_id"],
                "image_ids": [case["image_id"]],
                "clinical_text": case["clinical_text"],
                "target_organ": "thyroid",
                # ⚠️ **必须显式传 patient_context 且 conditions=[]**：
                # `app/models/schemas.py` 的语义约定是 `None`=未提供 → SafetyGateway
                # **保守判命中**（fail-closed），结果每例都报
                # `contraindication_hit` + `conclusion_suppressed`，正文进入降级路径。
                # 首轮实测：36/36 全命中 —— 那是**恒定的**混淆变量（三臂都一样，
                # 不偏倚 on/off 对比），但会让"无法判断"里混进 SG 降级的成因，
                # 报告可辩护性变差。传 `[]`（已确认无条件）走正常评估。
                "patient_context": {"age": None, "gender": None, "conditions": []},
                # ⚠️ **不传 retry=True**：该标志会让管线 skip_self_rag=True
                # （`app/api/v1/diagnosis.py:416` 写 Redis 标志 →
                # `diagnosis_pipeline._read_retry_skip_self_rag` 读到后跳过质检），
                # 那等于给三臂都加了一个偏离生产默认的改动。限流改用**排队节奏**
                # 处理（下面 _submit_with_backoff），保持三臂都是生产口径。
            }
            rs = await _submit_with_backoff(c, headers, body)
            if rs is None:
                records.append({"case_index": i, "session_id": case["session_id"],
                                "error": "submit_exception"})
                print(f"  [{i}] submit 异常（已重试）")
                continue
            if rs.status_code not in (200, 202):
                records.append({"case_index": i, "session_id": case["session_id"],
                                "error": f"submit_http_{rs.status_code}",
                                "detail": rs.text[:200]})
                print(f"  [{i}] submit http={rs.status_code} {rs.text[:120]}")
                continue
            rid = (rs.json() or {}).get("request_id")

            final: dict | None = None
            while time.monotonic() - t0 < 300:
                await asyncio.sleep(3)
                try:
                    rr = await c.get(f"{BASE}/api/v1/diagnosis/result/{rid}",
                                     headers=headers, timeout=30)
                except Exception:
                    continue
                if rr.status_code == 200 and isinstance(rr.json(), dict) \
                        and rr.json().get("status") in TERMINAL:
                    final = rr.json()
                    break
            wall = round(time.monotonic() - t0, 1)

            if final is None:
                records.append({"case_index": i, "session_id": case["session_id"],
                                "request_id": rid, "error": "no_terminal", "wall_s": wall})
                print(f"  [{i}] 未到终态 wall={wall}s")
                continue

            db = await _read_db_record(rid)
            text_out = db.get("content") or _pick_conclusion_from_payload(final)
            records.append({
                "case_index": i,
                "session_id": case["session_id"],
                "image_id": case["image_id"],
                "request_id": rid,
                "wall_s": wall,
                "status": final.get("status"),
                "needs_review": final.get("requires_manual_review"),
                "review_reason": final.get("review_reason"),
                "confidence": db.get("confidence"),
                "conclusion": text_out[:1200],
                "judgement": classify_conclusion(text_out),
                "compared_with_prior": db.get("compared_with_prior"),
                "segmentation_available": db.get("segmentation_available"),
                "segmentation_reason": db.get("segmentation_reason"),
                "sg": db.get("sg") or {},
                "timings_ms": db.get("timings"),
            })
            j = records[-1]["judgement"]
            print(f"  [{i}] {case['session_id']} wall={wall}s status={final.get('status')} "
                  f"grade={j['grade_raw']!r} unparseable={j['unparseable']} "
                  f"says_unable={j['says_unable']} → {'无法判断' if j['unable'] else '可判定'}")

    # ---- 影像腿证据：probe（同批样本、同参数）----
    leg_by_session = {}
    probe_file = REPORT_DIR / "image_leg_ab_probe.json"
    if probe_file.exists():
        probe = json.loads(probe_file.read_text(encoding="utf-8"))
        for pc in probe.get("cases", []):
            if "error" in pc:
                continue
            leg_by_session[pc["session_id"]] = {
                "executed": pc.get("leg_executed"),
                "skip_reason": (pc.get("prod_on") or {}).get("skip_reason", ""),
                "prompt_visible_image_chunks": (
                    1 if pc.get("image_chunk_in_prompt_prod") else 0),
            }

    # ---- 真实 prompt 证据：api.log 的 evidence_chain ----
    # 本函数只被**单臂**进程调用，且驱动脚本每臂都会重启 18000
    # （重启脚本用 `>` 覆盖 api.log）→ 此刻日志里只有本臂的行，不会串臂。
    chain: dict[str, list[str]] = {}
    leg_events: dict[str, dict] = {}
    log_facts: dict = {}
    try:
        if LOG_PATH.exists():
            raw_log = LOG_PATH.read_bytes().replace(b"\x00", b"").decode("utf-8", "replace")
            chain = _parse_evidence_chain_log(raw_log)
            leg_events = _parse_leg_events_by_request(raw_log)
            log_facts = _scan_log_for_leg(raw_log)
    except Exception as exc:  # 日志不可读不该让臂失败，但必须留痕（零静默）
        log_facts = {"log_read_error": f"{type(exc).__name__}: {exc}"[:160]}

    # ---- 影像腿证据：**按 request_id 对齐** ----
    #
    # 三条证据优先级（从强到弱），每条都写回 evidence_source，绝不混用：
    #   ① api.log 的 evidence_chain：prompt 里**真的**有哪些块（最强）；
    #   ② api.log 的 image_leg_*：这条腿**真的**有没有执行/命中（次强）；
    #   ③ 同参数 retrieval probe：兜底（日志开关没开时才用，并显式标注）。
    # 旧版把这三种混成一个 `executed` 布尔，导致 off 臂也报 executed=True
    # （而日志里明明是 `skip_reason=leg_disabled`）—— 那是把"腿被关着"
    # 显示成"腿跑了"。这类**证据串味**正是本项目零静默纪律要防的形态。
    probe_file = REPORT_DIR / "image_leg_ab_probe.json"
    leg_by_session = {}
    if probe_file.exists():
        try:
            probe = json.loads(probe_file.read_text(encoding="utf-8"))
            for pc in probe.get("cases", []):
                if "error" in pc:
                    continue
                leg_by_session[pc["session_id"]] = {
                    "executed": pc.get("leg_executed"),
                    "skip_reason": (pc.get("prod_on") or {}).get("skip_reason", ""),
                    "prompt_visible_image_chunks": (
                        1 if pc.get("image_chunk_in_prompt_prod") else 0),
                }
        except Exception as exc:  # silent-ok: probe 是可选佐证，坏了只影响 P1b 的兜底
            log_facts.setdefault("probe_read_error", f"{type(exc).__name__}")

    for rec in records:
        rid = rec.get("request_id") or ""
        chunk_ids = chain.get(rid, [])
        ev = leg_events.get(rid, {})
        img_in_prompt = [c for c in chunk_ids if c.startswith("img_")]

        if chunk_ids:
            # ① ＋ ② 都有/只有①：以真实 prompt 为准
            rec["leg"] = {
                "executed": (ev.get("attached") is True
                             and not ev.get("attached_skip_reason")),
                "attached_skip_reason": ev.get("attached_skip_reason"),
                "retrieval_skipped_reason": ev.get("retrieval_skipped_reason"),
                "prompt_chunks": len(chunk_ids),
                "prompt_visible_image_chunks": len(img_in_prompt),
                "prompt_chunk_ids": chunk_ids[:20],
                "evidence_source": "api.log:evidence_chain",
            }
        elif ev:
            # ② 只有腿事件（evidence_chain 没开）：仍按真实事件判"跑没跑"
            rec["leg"] = {
                "executed": (ev.get("attached") is True
                             and not ev.get("attached_skip_reason")),
                "attached_skip_reason": ev.get("attached_skip_reason"),
                "retrieval_skipped_reason": ev.get("retrieval_skipped_reason"),
                "prompt_visible_image_chunks": 0,
                "prompt_chunk_ids": [],
                "evidence_source": "api.log:image_leg_events",
            }
        else:
            # ③ 兜底：同参数 probe（日志完全不可用时才走到这里）
            leg = leg_by_session.get(rec.get("session_id", ""), {})
            rec["leg"] = {
                "executed": bool(leg.get("executed")),
                "attached_skip_reason": None,
                "prompt_visible_image_chunks": int(
                    leg.get("prompt_visible_image_chunks") or 0),
                "prompt_chunk_ids": [],
                "evidence_source": "retrieval_probe(同参数兜底)",
            }

    # ---- 臂级命中数（`image_leg_hits_admitted` 无 request_id，只能臂级统计）----
    arm_level = leg_events.get("__arm_level__") or {}

    summary = summarize_arm(records)
    # 臂级命中数单独写进 summary（逐例归属不可得，见 _parse_leg_events_by_request）
    summary["arm_level_hits_admitted"] = int(arm_level.get("hits_admitted") or 0)
    summary["arm_level_admitted_chunk_ids"] = (arm_level.get("admitted_chunk_ids") or [])[:5]
    payload = {
        "probe": "p65_image_leg_grading_ab",
        "mode": "arm",
        "arm": arm,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "switch_expected": expected,
        "switch_effective_in_process": bool(settings.rag_use_multimodal),
        "health_image_leg": leg_health,
        "log_facts": log_facts,
        "summary": summary,
        "cases": records,
    }
    out = REPORT_DIR / f"image_leg_ab_arm_{arm}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n== 臂 {arm} 汇总 ==")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"臂报告：{out}")
    return 0


def _pick_conclusion_from_payload(result: dict) -> str:
    """API payload → 医生可见结论（DB 不可用时的兜底；跳过技术字段）。"""
    skip_keys = ("trace", "evidence", "reasoning", "chunk", "preview", "timing",
                 "url", "hash", "id", "version", "status", "organ", "created_at",
                 "updated_at")
    found: list[str] = []

    def walk(node: object, key: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k))
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str):
            if any(s in key.lower() for s in skip_keys):
                return
            s = node.strip()
            if len(s) >= 20 and any("\u4e00" <= ch <= "\u9fff" for ch in s):
                found.append(s)

    walk(result)
    return found[0] if found else ""


# ======================================================================
# mode=pin / mode=aggregate
# ======================================================================
async def mode_pin(limit: int) -> int:
    """把样本清单钉成文件（三臂读同一份 —— 这是"同批"的机械保证）。"""
    cases = await pick_cases(limit)
    if len(cases) < 10:
        print(f"[FATAL] 合格样本仅 {len(cases)} 例（要求 >= 10）")
        return 2
    out = REPORT_DIR / "image_leg_ab_cases.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[钉样本] {len(cases)} 例 → {out}")
    return 0


def mode_aggregate() -> int:
    """合并三臂 + 按预注册判据裁决 → `reports/image_leg_grading_ab.json`。"""
    arms: dict[str, dict] = {}
    missing: list[str] = []
    for name in ARM_NAMES:
        f = REPORT_DIR / f"image_leg_ab_arm_{name}.json"
        if not f.exists():
            missing.append(name)
            continue
        arms[name] = json.loads(f.read_text(encoding="utf-8"))
    if missing:
        print(f"[FATAL] 缺臂报告：{missing}")
        return 2

    summaries = {name: arms[name]["summary"] for name in ARM_NAMES}
    code, facts = verdict(summaries)

    probe_file = REPORT_DIR / "image_leg_ab_probe.json"
    probe_facts = {}
    if probe_file.exists():
        probe_facts = (json.loads(probe_file.read_text(encoding="utf-8")) or {}).get("facts") or {}

    payload = {
        "probe": "p65_image_leg_grading_ab",
        "mode": "aggregate",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question": (
            "rag_use_multimodal=True 能否降低甲状腺「分级: 无法判断」率"
            "（A.94 基线 59/306 = 19.3%）"
        ),
        "pre_registered_criteria": {
            "P1_reachable": f"on 臂影像腿被执行比例 >= {P1_MIN_LEG_EXECUTED_RATIO}",
            "P2_gain": f"on 臂无法判断率相对降幅 >= {P2_MIN_RELATIVE_DROP}",
            "P3_distribution": (
                f"单一分级占比 <= {P3_MAX_SINGLE_GRADE_SHARE} 且 >=2 个不同分级 "
                f"且两臂平均主级差 <= {P3_MAX_MEAN_LEVEL_DELTA}"
            ),
            "P0_control": f"两次 off 的无法判断率相对差 <= {P0_MAX_RELATIVE_DIFF}",
            "P1b_prompt_delivery": "附加诊断，不计入裁决",
        },
        "unable_definition": (
            "未给出可判定分级（extract_grading_severest 返回 None）"
            " 或 正文自述无法判断/信息不足/缺影像特征"
        ),
        "verdict": code,
        "facts": facts,
        "arm_summaries": summaries,
        "retrieval_probe_facts": probe_facts,
        "arm_meta": {
            name: {
                "switch_expected": arms[name].get("switch_expected"),
                "switch_effective_in_process": arms[name].get("switch_effective_in_process"),
                "health_image_leg_enabled": (arms[name].get("health_image_leg") or {}).get("enabled"),
                "log_facts": arms[name].get("log_facts"),
            }
            for name in ARM_NAMES
        },
        "cases_per_arm": {
            name: [
                {
                    "session_id": c.get("session_id"),
                    "request_id": c.get("request_id"),
                    "status": c.get("status"),
                    "grade": (c.get("judgement") or {}).get("grade_raw"),
                    "unable": (c.get("judgement") or {}).get("unable"),
                    "says_unable": (c.get("judgement") or {}).get("says_unable"),
                    "leg": c.get("leg"),
                    "conclusion": (c.get("conclusion") or "")[:300],
                }
                for c in arms[name].get("cases", [])
            ]
            for name in ARM_NAMES
        },
    }
    FINAL_REPORT.parent.mkdir(parents=True, exist_ok=True)
    FINAL_REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print("== 裁决 ==")
    print(code)
    print(json.dumps(facts, ensure_ascii=False, indent=2))
    print(f"\n最终报告：{FINAL_REPORT}")
    return 3 if code == "LEG_UNREACHABLE" else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", required=True,
                    choices=["pin", "probe", "arm", "aggregate"])
    ap.add_argument("--arm", choices=list(ARM_NAMES))
    ap.add_argument("--limit", type=int, default=12, help="样本数（要求 >= 10）")
    ap.add_argument("--cases-file",
                    default=str(REPORT_DIR / "image_leg_ab_cases.json"))
    args = ap.parse_args()

    if args.mode == "pin":
        return asyncio.run(mode_pin(args.limit))
    if args.mode == "probe":
        return asyncio.run(mode_probe(args.limit))
    if args.mode == "arm":
        if not args.arm:
            print("[FATAL] --mode arm 需要 --arm off|on|off2")
            return 2
        return asyncio.run(mode_arm(args.arm, args.limit, pathlib.Path(args.cases_file)))
    return mode_aggregate()


if __name__ == "__main__":
    sys.exit(main())
