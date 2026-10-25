"""Q3 / p64：497 条判读里 **251 条「无正文」的成因归因**（只读，默认 dry-run）。

## 这个脚本要回答什么

真机上 `diagnosis_results.findings->'fusion_result'->>'content'` 为空的有 251 条（50.4%，
全库 498 行时的快照；实际数字以报告 `totals` 与 `generated_at` 为准）。
但只要不把**分母与根因**定义清楚，任何人问「系统可用率」都能在 50%~100% 之间挑一个
好看的数。本脚本把「无正文」逐条归因，每个归因都必须带**证据来源**，并给出
**服务正常期内的可用率**。

## 归因：两级模型（先「为什么没有正文」，再「为什么没有可用结论」）

只有一级分类会把完全不同的东西混在一起。本脚本显式分两级：

* **mechanism** —— 为什么 `findings` 里没有正文字段（写/存环节）；
* **root_cause** —— 面向任务的根因标签（有唯一值，含 `service_outage` 等）；
* **sub_detail** —— 根因内部的细节（如 `service_outage` 下是 llm 报错还是落库断连）。

## 证据强度（每条都标，不许"看起来像"）

| confidence | 含义 |
|---|---|
| `direct` | 记录里有**字段**直接说明原因（`error_type` / `knowledge_ids` 空 / 正文标记串 / `status`） |
| `inferred` | 多条独立证据交叉指向，但缺决定性字段（在 `missing_evidence` 写明缺什么） |
| `unknown` | 证据不足（本数据集为 0，但**保留该出口**，不允许为了提高"有归属率"硬塞） |

## 三个已被实测证伪的"直觉归因"（务必别重犯）

1. **`no_image` 不是原因**：全库 92.6% 无 `image_ids`，**有正文**的组里也有 85.0% 无图
   （两者几乎一样高，见报告 `calibers.no_image_counter_evidence`）→ **没有区分性**。
   把 251 条归因于"没影像"属于典型的"看起来像"。
2. **`upstream_timeout` 在无正文组里是 0 条**：`status='timeout'` **从不落库**
   （全库 0 行，它是读路径 `app/api/v1/diagnosis.py` 按 `status=='processing'` 派生的值）。
   真正的超时证据是 `findings.fusion_result.reason == 'stale_processing'`，共 **107 条**，
   而这些条**有可读正文**（"已等待超过 30 分钟…"）→ 与「无正文」互斥，不在本组。
3. **`status=needs_review 且 content 空 ⇒ grade_unparseable` 这个判据不成立**：
   按该判据会算出 128 条 `grade_unparseable`，但真带
   `grade_unparseable` / `lvef_unparseable` 标记的只有 **3 条**，另外 125 条是
   `evidence_insufficient`（检索空）。两者修法完全不同（改输出格式 vs 补知识库）。
   报告里专门给出 `naive_literal_definition_would_count` 暴露这个膨胀量。

## 两个结构性发现（本脚本的主要产出）

### ① `write_gap_produced_ok`：64 条其实**产出了可用分级**

`_fusion_result_to_dict` 直到 commit `5de3a81`（2026-09-30 17:35:50 UTC）才把
`final_response.content` 写进 `findings.fusion_result["content"]`。在此之前正文**只**经
`recommendations` 列落库。实测 `fusion_result` 含 `content` 键的比例：

| 时段 | 含 `content` 键 |
|---|---|
| 早于该 commit | **27.4%** |
| 晚于该 commit | **98.7%** |

（这两个比例由脚本运行时现算并写进 `calibers.content_contract_fix`，不在代码里写死。）

所以「无正文」里有 64 条能直接从 `recommendations` 抽出可解析分级（TR1~TR5）。
把它们叫「模型没产出」会严重高估故障面。

### ② `kb_not_ingested`：129 条是**当时那个器官还没有知识库**

`knowledge_chunks` 里各器官最早灌库时间（UTC）：
`thyroid 2026-09-29 15:40` / `liver 2026-09-30 18:09` / `cardiac 2026-09-30 18:09`。
而在 9/28–9/30 的 249 条无正文里，**129 条空检索记录全部早于本器官首次灌库** ——
liver 97/97、cardiac 77/77 都是"库还没有"。同期对照：正常期 liver/cardiac
空检索率 **0%**。这不是模型故障，补知识库就没了。

## 「正常期」怎么定（三条证据，报告 `calibers` 里全部回链）

1. 排除 **9/28–9/30** 模型服务故障窗；
2. 排除 `content` 契约缺口期（< 2026-09-30 17:35:50 UTC，commit `5de3a81` 之前）；
3. 排除 `cancelled`（同意撤回是政策终止，不是失败）。

> 只排除第 1 条**不够**：契约缺口一直持续到 10-01 01:35 (+08)，该期间的记录
> 即使模型完全正常，`findings` 里也没有 `content` 键。只用时间窗当"正常期"
> 会把契约缺口算成服务故障 —— 这正是"50%~100% 随便挑"的成因之一。

## 用法（服务器）

    ~/uai/venv/bin/python scripts/audit/run_with_env.py \\
        scripts/audit/p64_degraded_cause_attribution.py          # dry-run：只打印

    ~/uai/venv/bin/python scripts/audit/run_with_env.py \\
        scripts/audit/p64_degraded_cause_attribution.py --emit    # 另写 reports/degraded_cause.json

退出码：0 出数；2 库不可达。

**只读**：全脚本只有 `SELECT`，不执行任何 INSERT/UPDATE/DELETE，不改配置、不重启服务。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

# ----------------------------------------------------------------------
# 口径常量（全部带证据出处，便于复核）
# ----------------------------------------------------------------------

#: 模型服务故障窗（含当日全天，按 UTC 日）。证据：16 号 A.70.7 —— 9/30 `vllm` 引擎错误
#: 1636 次、9/29 timeout 471 次；本脚本 `logs.outage_day_totals` 独立复核同向
#: （9/30 命中 1642 次、9/28~9/29 各 4/2 次）。
OUTAGE_WINDOW = ("2026-09-28", "2026-09-30")

#: `content` 写入侧接线时刻 = commit `5de3a81` 提交时间（2026-10-01 01:35:50 +08）。
#: 早于此刻的 `findings` 里**不存在** `content` 键，与模型是否产出无关。
CONTENT_CONTRACT_FIX_UTC = datetime(2026, 9, 30, 17, 35, 50, tzinfo=timezone.utc)
CONTENT_CONTRACT_COMMIT = "5de3a81"

#: 本地时区（Asia/Shanghai, UTC+8）。库里 created_at 是 aware(UTC)，
#: 但运维人员看日志/报表常用本地日 —— 两种日界会差 8 小时，报告里两种都给。
CST = timezone(timedelta(hours=8))

#: 「status 异常」的定义（任务口径）。注意：该字段**不能单独定罪** ——
#: 实测 9/28–9/30 有 37 条 `llm_unavailable` 被写成 `status=completed`（见 calibers）。
ABNORMAL_STATUS = frozenset({"failed", "needs_review", "timeout", "processing"})

#: 结论正文里的**降级标记**（有输出但不是可用结论）
_RE_GRADE_UNPARSE = re.compile(r"(grading_unparseable|grade_unparseable|lvef_unparseable|未解析出)", re.IGNORECASE)
_RE_RETRIEVAL_EMPTY = re.compile(r"(evidence_insufficient|未检索到)", re.IGNORECASE)
_RE_LLM_UNAVAILABLE = re.compile(r"(llm_unavailable|InferenceError)", re.IGNORECASE)
_RE_STALE_PROCESSING = re.compile(r"(未能在预期时间内完成|stale_processing)", re.IGNORECASE)
_RE_PERSIST_FAILURE = re.compile(r"(underlying connection is closed|InterfaceError)", re.IGNORECASE)
_RE_CONSENT_WITHDRAWN = re.compile(r"consent_withdrawn", re.IGNORECASE)
#: 模型**明确说"判不了"**（有正文，但不是可用分级）
_RE_UNABLE_TO_GRADE = re.compile(
    r"(?:分级|grading)\s*[:：]\s*(?:无法判断|无法分级|无法计算|未测值|unknown|n/?a)"
    r"|LVEF\s*=\s*unknown"
    r"|LVEF\s*[:：]\s*(?:无法计算|未测值|unknown)"
    r"|严重程度\s*[:：]\s*无法判断",
    re.IGNORECASE,
)
#: 模型说"缺影像特征"（T3 的主要成因，与 no_image 区分：后者是**没影像**，前者是**没影像描述**）
_RE_MISSING_IMAGE_FEATURES = re.compile(r"(影像特征|影像元数据|影像描述)", re.IGNORECASE)

#: 根因枚举（互斥，每条记录只有一个）。任务要求的 5 类都在内，另加 6 类由实测证据逼出的细分类。
ROOT_CAUSES: tuple[str, ...] = (
    "service_outage",  # 9/28-9/30 窗口 ∧ 服务侧证据（llm 报错 / 落库断连）
    "kb_not_ingested",  # 空检索 ∧ 早于该器官首次灌库（补库即可，非服务故障）
    "retrieval_empty",  # 空检索 ∧ KB 已存在（知识覆盖问题）
    "grade_unparseable",  # 有输出但抽不出分级/EF
    "llm_inference_error",  # 推理调用异常（窗口外）
    "persist_failure",  # 落库时连接被掐断（窗口外）
    "write_gap_produced_ok",  # findings 缺 content 键，但 recommendations 里有可解析分级
    "no_image",  # 无影像 ∧ 有影像腿跳过留痕（**两个条件都要**）
    "upstream_timeout",  # stale_processing 清扫 / status=timeout
    "consent_withdrawn",  # 同意撤回（政策终止，非失败）
    "unknown",  # 证据不足（保留出口，不为好看硬塞）
)

#: 任务要求的 5 类 → 本脚本根因的映射（报告里显式给出，避免两套口径打架）
TASK_TAXONOMY_MAP: dict[str, tuple[str, ...]] = {
    "service_outage": ("service_outage",),
    "grade_unparseable": ("grade_unparseable",),
    "no_image": ("no_image",),
    "upstream_timeout": ("upstream_timeout",),
    # 任务口径的 unknown 要看**证据强度**，不只 root_cause 名：
    # 这里先放 root_cause==unknown，报告再补 inferred 计数（见 summarize_task_taxonomy）
    "unknown": ("unknown",),
}


# ----------------------------------------------------------------------
# 纯函数（单测钉住；不碰 DB、不碰 IO）
# ----------------------------------------------------------------------


def body_of(findings: object) -> str:
    """`findings.fusion_result.content`（判读正文）。空/缺失 → ``""``。纯函数。"""
    if not isinstance(findings, dict):
        return ""
    fusion = findings.get("fusion_result")
    if not isinstance(fusion, dict):
        return ""
    content = fusion.get("content")
    return content.strip() if isinstance(content, str) else ""


def has_content_key(findings: object) -> bool:
    """`fusion_result` 里**是否出现过** `content` 键（与"值为空"区分）。纯函数。

    这是区分「写的人没写这个键」（契约缺口）与「写了但内容是空」的关键 ——
    后者才是"模型产出空"，前者只是"读不到"。
    """
    if not isinstance(findings, dict):
        return False
    fusion = findings.get("fusion_result")
    return isinstance(fusion, dict) and "content" in fusion


def readable_text(record: object) -> str:
    """这条记录**人能看到**的正文：`findings.content` 优先，回退 `recommendations`。

    为什么要回退：`recommendations` 是 `_extract_recommendation()` 截断 content 前 500 字
    落的列（`app/services/diagnosis_pipeline.py`），契约缺口期正文都在这里。纯函数。
    """
    body = body_of(getattr(record, "findings", None))
    if body:
        return body
    rec = getattr(record, "recommendations", None)
    return rec.strip() if isinstance(rec, str) else ""


def in_outage_window(created_at: datetime | None) -> bool:
    """`created_at` 是否落在服务故障窗（按 **UTC** 日）。纯函数。

    按 UTC 而非本地：库里 `created_at` 是 aware(UTC)，历史统计（16 号 A.70.x）也是
    UTC 日口径。报告里另给本地(CST)口径的敏感性数字，避免读者对不上。
    """
    if created_at is None:
        return False
    day = created_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
    return OUTAGE_WINDOW[0] <= day <= OUTAGE_WINDOW[1]


def day_key(created_at: datetime | None, tz) -> str:
    """把 aware 时间戳按给定时区折成日期字符串。纯函数。"""
    if created_at is None:
        return "(无日期)"
    return created_at.astimezone(tz).strftime("%Y-%m-%d")


def before_content_contract(created_at: datetime | None) -> bool:
    """`created_at` 早于 content 契约接线时刻（该时段读不到正文是**结构性**的）。纯函数。"""
    if created_at is None:
        return False
    return created_at.astimezone(timezone.utc) < CONTENT_CONTRACT_FIX_UTC


def attribute(
    record: object,
    *,
    kb_first_ingest: dict[str, datetime],
    has_service_evidence: bool,
    has_image_leg_trace: bool,
) -> dict:
    """把一条「无正文」记录归到**唯一**根因，并给出证据与置信度。纯函数（单测钉住）。

    Args:
        record: 需有 ``findings`` / ``status`` / ``created_at`` / ``organ`` /
            ``knowledge_ids`` / ``image_ids`` / ``recommendations`` 属性。
        kb_first_ingest: 器官 → 该器官知识库**最早**灌库时间（UTC）。用于判
            「当时到底有没有这个器官的知识」。
        has_service_evidence: 当日日志侧是否真的有服务故障签名（由调用方算好传入，
            避免纯函数里读文件）。
        has_image_leg_trace: `findings` 里是否有影像腿跳过留痕
            （`augmentation_skip_reason` / `image_leg`）。

    Returns:
        ``{"root_cause", "mechanism", "sub_detail", "evidence": [], "sources": [],
           "confidence", "missing_evidence": []}``

    **判定顺序即证据强度顺序**：先"字段直接说话"，再"交叉推断"，最后 unknown。
    每一步的 `sources` 都必须指向**真实存在的字段**，便于别人复核。
    """
    findings = getattr(record, "findings", None) or {}
    status = str(getattr(record, "status", "") or "")
    organ = str(getattr(record, "organ", "") or "")
    created_at = getattr(record, "created_at", None)
    knowledge_ids = getattr(record, "knowledge_ids", None) or []
    image_ids = getattr(record, "image_ids", None) or []
    text = readable_text(record)
    in_window = in_outage_window(created_at)

    found = findings if isinstance(findings, dict) else {}
    error_type = str(found.get("error_type") or "")
    error_raw = str(found.get("error") or "")
    reason_raw = str(found.get("reason") or "")
    error_blob = f"{error_type} {error_raw} {reason_raw}"

    # ① 同意撤回：政策终止，不是系统失败（不该计入可用率分母）
    if status == "cancelled" or _RE_CONSENT_WITHDRAWN.search(error_blob):
        return {
            "root_cause": "consent_withdrawn",
            "mechanism": "cancelled_before_write",
            "sub_detail": "consent_withdrawn",
            "evidence": [
                f"status={status or '(空)'}",
                "findings.error 含 consent_withdrawn",
                "findings 无 fusion_result 键（管线在写结果前被取消）",
            ],
            "sources": ["diagnosis_results.status", "findings.error"],
            "confidence": "direct",
            "missing_evidence": [],
        }

    # ② 上游超时：stale_processing 清扫留痕（`status='timeout'` 从不落库，见 calibers）
    fusion = found.get("fusion_result")
    fusion_reason = str(fusion.get("reason") or "") if isinstance(fusion, dict) else ""
    if status == "timeout" or _RE_STALE_PROCESSING.search(text) or fusion_reason == "stale_processing":
        return {
            "root_cause": "upstream_timeout",
            "mechanism": "stale_processing_sweeper",
            "sub_detail": "30min_timeout",
            "evidence": [
                f"status={status or '(空)'}",
                f"findings.fusion_result.reason={fusion_reason or '(空)'}",
                "正文/留痕含 stale_processing 或 '未能在预期时间内完成'",
            ],
            "sources": ["diagnosis_results.status", "findings.fusion_result.reason"],
            "confidence": "direct",
            "missing_evidence": [],
        }

    # ③ 落库断连：findings 只有 error，正文从未写进库。
    #    在故障窗内 → 计入 service_outage（长 LLM 调用把连接熬到 idle 超时，是故障的直接后果）；
    #    窗口外 → 单独记 persist_failure。
    if _RE_PERSIST_FAILURE.search(error_blob):
        if in_window and has_service_evidence:
            return {
                "root_cause": "service_outage",
                "mechanism": "persist_failure",
                "sub_detail": "db_connection_closed",
                "evidence": [
                    f"created_at 落在故障窗（UTC 日 {OUTAGE_WINDOW[0]}~{OUTAGE_WINDOW[1]}）",
                    f"findings.error_type={error_type or '(空)'}",
                    "错误原文含 'underlying connection is closed'（长 LLM 调用熬断连接）",
                    "当日日志有服务故障签名（logs.by_day）",
                ],
                "sources": [
                    "diagnosis_results.created_at",
                    "findings.error_type",
                    "logs.by_day 服务故障签名",
                ],
                "confidence": "direct",
                "missing_evidence": [],
            }
        return {
            "root_cause": "persist_failure",
            "mechanism": "persist_failure",
            "sub_detail": "db_connection_closed",
            "evidence": [
                f"findings.error_type={error_type or '(空)'}",
                "findings.error 含 'underlying connection is closed'",
                "findings 无 fusion_result 键（正文从未写入）",
                f"不在故障窗内（UTC 日不在 {OUTAGE_WINDOW}）",
            ],
            "sources": ["findings.error", "findings.error_type"],
            "confidence": "direct",
            "missing_evidence": [],
        }

    # ④ 检索零命中 —— 先判「当时有没有这个器官的知识库」（两条独立证据）
    if not knowledge_ids:
        return _retrieval_verdict(
            organ=organ,
            created_at=created_at,
            status=status,
            kb_first_ingest=kb_first_ingest,
            text=text,
            in_window=in_window,
            has_service_evidence=has_service_evidence,
        )

    # ⑤ 推理服务不可用（有检索块、但 LLM 调用异常）
    if _RE_LLM_UNAVAILABLE.search(text):
        if in_window and has_service_evidence:
            return {
                "root_cause": "service_outage",
                "mechanism": "write_contract_gap",
                "sub_detail": "llm_inference_error",
                "evidence": [
                    f"created_at 落在故障窗（UTC 日 {OUTAGE_WINDOW[0]}~{OUTAGE_WINDOW[1]}）",
                    "正文含 'llm_unavailable' / 'InferenceError'",
                    "当日日志有服务故障签名（logs.by_day）",
                    f"knowledge_ids 非空（{len(knowledge_ids)} 块，排除检索问题）",
                    f"注意 status={status or '(空)'} —— 故障窗内有 llm_unavailable 却写成 "
                    "completed 的记录，status 字段不可单独定罪",
                ],
                "sources": [
                    "diagnosis_results.created_at",
                    "recommendations / findings.fusion_result.content",
                    "logs.by_day 服务故障签名",
                ],
                "confidence": "direct",
                "missing_evidence": [],
            }
        return {
            "root_cause": "llm_inference_error",
            "mechanism": "write_contract_gap",
            "sub_detail": "llm_inference_error",
            "evidence": [
                "正文含 'llm_unavailable' / 'InferenceError'",
                f"knowledge_ids 非空（{len(knowledge_ids)} 块，排除检索问题）",
                f"不在故障窗内（UTC 日不在 {OUTAGE_WINDOW}）",
            ],
            "sources": ["recommendations", "diagnosis_results.knowledge_ids"],
            "confidence": "direct",
            "missing_evidence": [],
        }

    # ⑥ 有输出但抽不出分级（格式跑偏，服务是好的；与故障窗无关）
    if _RE_GRADE_UNPARSE.search(text):
        hit = _RE_GRADE_UNPARSE.search(text)
        return {
            "root_cause": "grade_unparseable",
            "mechanism": "write_contract_gap",
            "sub_detail": "grade_marker_missing",
            "evidence": [
                f"正文含降级标记 '{hit.group(1) if hit else ''}'",
                f"status={status or '(空)'}（服务可达，模型有输出）",
                f"knowledge_ids 非空（{len(knowledge_ids)} 块）",
            ],
            "sources": ["recommendations", "findings.fusion_result.content"],
            "confidence": "direct",
            "missing_evidence": [],
        }

    # ⑦ 无影像 AND 有影像腿留痕 —— 两个条件缺一不可。
    # 只凭"无 image_ids"不算：全库约 92% 无图，有正文组也约 85% 无图（无区分性）。
    if not image_ids and has_image_leg_trace:
        return {
            "root_cause": "no_image",
            "mechanism": "image_leg_skipped",
            "sub_detail": "no_image_bytes",
            "evidence": [
                "image_ids 为空",
                "findings 里有影像腿跳过留痕（augmentation_skip_reason / image_leg）",
            ],
            "sources": ["diagnosis_results.image_ids", "findings.augmentation_skip_reason"],
            "confidence": "direct",
            "missing_evidence": [],
        }

    # ⑧ 契约缺口但**其实产出了可用分级**（最容易被误判成"模型没输出"的一类）
    if not has_content_key(found) and text:
        return {
            "root_cause": "write_gap_produced_ok",
            "mechanism": "write_contract_gap",
            "sub_detail": "content_key_absent",
            "evidence": [
                "findings.fusion_result 缺 content 键（写路径当时未装配）",
                "recommendations 列有正文（正文其实是产出了的）",
                f"早于 commit {CONTENT_CONTRACT_COMMIT} 的 content 接线时刻",
            ],
            "sources": [
                "findings.fusion_result 键集合",
                "diagnosis_results.recommendations",
                f"git commit {CONTENT_CONTRACT_COMMIT} 时间",
            ],
            "confidence": "direct",
            "missing_evidence": [],
        }

    # ⑨ 兜底：证据不足，如实标 unknown（保留出口，不许硬塞）
    return {
        "root_cause": "unknown",
        "mechanism": "unknown",
        "sub_detail": "insufficient_evidence",
        "evidence": [
            f"status={status or '(空)'}",
            f"knowledge_ids={len(knowledge_ids)} 块",
            f"image_ids={len(image_ids)} 张",
            f"可见正文长度={len(text)}",
        ],
        "sources": ["status", "knowledge_ids", "image_ids"],
        "confidence": "unknown",
        "missing_evidence": [
            "该请求没有任何降级标记串（llm_unavailable / grade_unparseable / evidence_insufficient）",
            "findings 里没有 segmentation / augmentation_skip_reason / image_leg 留痕",
            "缺当日该 request_id 的检索耗时与 LLM 调用日志（日志已轮转，request_id 全文检索 0 命中）",
        ],
    }


def _retrieval_verdict(
    *,
    organ: str,
    created_at: datetime | None,
    status: str,
    kb_first_ingest: dict[str, datetime],
    text: str,
    in_window: bool,
    has_service_evidence: bool,
) -> dict:
    """「检索零命中」的子判定：**知识库还没建** vs **知识覆盖不足**。纯函数。

    两条证据（按强度）：
      1. `knowledge_ids` 为空 —— 字段直接说话（direct）；
      2. 该器官知识库最早灌库时间**晚于**本记录 —— 当时压根没有该器官的知识（inferred：
         `knowledge_chunks.created_at` 只反映**当前驻留版本**的写入时间，
         历史版本可能被覆盖，因此不能当铁证 → 写进 missing_evidence）。

    **不能**归成 `service_outage`：它与模型服务好坏无关，补知识库就没了。
    实测：9/28–9/30 的 129 条空检索**全部**早于本器官首次灌库（liver 97/97、cardiac 77/77、
    thyroid 9 条）；而正常期 liver/cardiac 空检索率 0%。
    """
    first = kb_first_ingest.get(organ)
    evidence = [
        "knowledge_ids 为空（检索零命中）",
        f"status={status or '(空)'}",
    ]
    missing: list[str] = []
    confidence = "direct"

    kb_not_ready = bool(first and created_at and created_at.astimezone(timezone.utc) < first)
    if kb_not_ready:
        evidence.append(f"本记录早于 {organ} 知识库当前版本首次灌库（{first:%Y-%m-%d %H:%M} UTC）")
        confidence = "inferred"
        missing.append(
            f"缺当时的 {organ} 知识库版本快照：`knowledge_chunks.created_at` 只反映当前驻留版本，"
            "若历史版本被覆盖/重灌，该时间戳不能证明当时无 KB"
        )
    if not text:
        evidence.append("recommendations 也为空（连降级说明都没有）")
    if in_window:
        evidence.append("落在服务故障窗内（但检索零命中的直接原因是知识缺失，不是服务）")
    if has_service_evidence:
        evidence.append("当日日志有服务故障签名（仅旁证，不作为本条的定罪依据）")
    missing.append(
        "缺检索层逐请求日志（query / top_k / 命中数）：日志已轮转，request_id 全文检索 0 命中，"
        "无法区分『KB 里确实没有』与『检索服务当时不可用』"
    )
    return {
        "root_cause": "kb_not_ingested" if kb_not_ready else "retrieval_empty",
        "mechanism": "empty_retrieval",
        "sub_detail": "organ_kb_absent_at_request_time" if kb_not_ready else "kb_present_but_zero_hit",
        "evidence": evidence,
        "sources": ["diagnosis_results.knowledge_ids", "knowledge_chunks.created_at"],
        "confidence": confidence,
        "missing_evidence": missing,
    }


def availability_tier(record: object, grading_extractor) -> str:
    """这条记录落在可用性的**哪一层**（全库通用，不只 251 条）。纯函数。

    分层（从差到好），判据全部来自正文原文，不猜：

    | tier | 判据 |
    |---|---|
    | `T0_no_text` | `findings.content` 与 `recommendations` 都空 |
    | `T1_timeout` | 正文是 30 分钟超时清扫文案 / stale_processing |
    | `T2_degraded` | 正文含 llm_unavailable / grade_unparseable / evidence_insufficient |
    | `T3_unable` | 模型**明确说判不了**（「分级: 无法判断」「LVEF: 无法计算」） |
    | `T5_usable_grade` | 能抽出可解析分级（用生产同一把尺子 `extract_grading_severest`） |
    | `T4_text_no_grade` | 有正文、既没说判不了、也抽不出分级 |

    ⚠️ `T3` 必须**排在 `T5` 之前**：`extract_grading_severest` 有"全文兜底扫描"，
    「甲状腺 · TR2 — 分级: 无法判断」会被兜底扫出 `TR2`（标题里的码），
    不先拦就成了假高 —— 这是本项目 A.91 的实测教训。
    """
    text = readable_text(record)
    if not text:
        return "T0_no_text"
    if _RE_STALE_PROCESSING.search(text):
        return "T1_timeout"
    if _RE_LLM_UNAVAILABLE.search(text) or _RE_GRADE_UNPARSE.search(text) or _RE_RETRIEVAL_EMPTY.search(text):
        return "T2_degraded"
    if _RE_UNABLE_TO_GRADE.search(text):
        return "T3_unable"
    organ = str(getattr(record, "organ", "") or "")
    if grading_extractor(text, organ):
        return "T5_usable_grade"
    return "T4_text_no_grade"


def summarize_by_root_cause(records: list[dict]) -> dict:
    """按根因汇总（含置信度分布与机制交叉）。纯函数。"""
    counts = Counter(r["root_cause"] for r in records)
    conf = Counter()
    for r in records:
        conf[(r["root_cause"], r["confidence"])] += 1
    mechanisms = Counter(r["mechanism"] for r in records)
    return {
        "total": len(records),
        "by_root_cause": {k: counts.get(k, 0) for k in ROOT_CAUSES},
        "by_confidence": {
            cause: {c: conf.get((cause, c), 0) for c in ("direct", "inferred", "unknown")}
            for cause in ROOT_CAUSES
            if counts.get(cause, 0)
        },
        "by_mechanism": dict(mechanisms.most_common()),
        "unknown_kept": counts.get("unknown", 0),
    }


def summarize_matrix(records: list[dict]) -> list[dict]:
    """器官 × 根因 × 日期 三维统计（按条数降序）。纯函数。"""
    cell: dict[tuple[str, str, str], int] = defaultdict(int)
    for r in records:
        cell[(r["organ"], r["root_cause"], r["day"])] += 1
    rows = [{"organ": o, "root_cause": c, "day": d, "n": n} for (o, c, d), n in cell.items()]
    rows.sort(key=lambda x: (-x["n"], x["organ"], x["root_cause"], x["day"]))
    return rows


def summarize_task_taxonomy(records: list[dict], counter_evidence: dict | None = None) -> dict:
    """任务要求的 5 类口径汇总。纯函数。

    额外给出 `naive_defect`：如果**照任务字面定义**（`status=needs_review 且 content 空
    ⇒ grade_unparseable`）会算出多少条 —— 用来暴露该判据会把 `evidence_insufficient`
    一起吞掉，从而误导修法方向。

    另对 `unknown` 类补 `inferred_count`：任务口径关心"证据是否足够"，
    而不只是 root_cause 字面名 —— 证据只到 inferred 的条目在口径上也属于"证据不足"。
    """
    out: dict[str, dict] = {}
    for task_name, causes in TASK_TAXONOMY_MAP.items():
        hit = [r for r in records if r["root_cause"] in causes]
        out[task_name] = {"n": len(hit)}
        if not hit:
            out[task_name]["why_zero"] = _why_zero(task_name, records, counter_evidence)
        if task_name == "unknown":
            inferred = [r for r in records if r["confidence"] == "inferred"]
            out[task_name]["by_confidence"] = dict(Counter(r["confidence"] for r in hit).most_common())
            out[task_name]["evidence_inferred_note"] = (
                f"另有 {len(inferred)} 条证据只到 inferred（多为 kb_not_ingested 的"
                "『早于灌库』推断）—— 本报告把它们归到各自真因并标注置信度，"
                "不塞进 unknown；若按『证据不足即算 unknown』的宽口径，"
                f"unknown 为 {len(hit) + len(inferred)} 条"
            )
    # 照字面定义会多算多少
    naive = sum(1 for r in records if r["status"] == "needs_review" and not r["has_body"])
    actual = out["grade_unparseable"]["n"]
    out["grade_unparseable"]["naive_literal_definition_would_count"] = naive
    out["grade_unparseable"]["inflation"] = naive - actual
    out["grade_unparseable"]["note"] = (
        "照任务字面判据（status=needs_review 且 content 空）会把 `kb_not_ingested` / "
        f"`retrieval_empty` 一起吞掉；本报告只认**真的有降级标记串**的 {actual} 条，"
        f"其余 {naive - actual} 条归各自真因"
    )
    return out


def _why_zero(task_name: str, records: list[dict], counter_evidence: dict | None = None) -> str:
    """某类为 0 时的实证解释（不是"没数据"，是有反证）。纯函数。

    ``counter_evidence`` 传实测的反证数字（见 ``calibers.no_image_counter_evidence``）；
    传 None 时只说清逻辑，不编造百分比。
    """
    if task_name == "no_image":
        ce = counter_evidence or {}
        if ce.get("all_rows_without_image_ratio") is not None:
            a = ce["all_rows_without_image_ratio"]
            b = ce.get("has_body_without_image_ratio")
            detail = f"全库 {a:.1%} 的判读同样无图" + (
                f"（有正文的对照组里也有 {b:.1%} 无图）" if b is not None else ""
            )
        else:
            detail = "全库绝大多数判读同样无图"
        return (
            f"本组 {len(records)} 条**全部** image_ids 为空，但这不是区分性证据："
            f"{detail}，且 findings 里没有任何影像腿跳过留痕"
            "（image_leg / augmentation_skip_reason 全库 0 条）。"
            "归因于「没影像」会把全库一起指为故障，故本类记 0 并说明。"
        )
    if task_name == "upstream_timeout":
        return (
            "`status='timeout'` **从不落库**（全库 0 行）：它是读路径 "
            "`app/api/v1/diagnosis.py` 里按 `status=='processing'` 派生的临时值。"
            "真正的超时证据是 `findings.fusion_result.reason=='stale_processing'`，"
            "共 107 条，而它们**有可读正文**（30 分钟清扫文案）→ 与「无正文」互斥，不在本组。"
        )
    return "（无可归入记录）"


def summarize_availability(tiers: list[str]) -> dict:
    """把分层计数折成三个**明确定义**的可用率。纯函数。

    三个口径都给，因为"可用率"这个词本身没有唯一答案 —— 关键是**说清分子分母**：

    * `body_rate`：有正文 / 全部（最宽松；只要求"人能看到字"）
    * `non_degraded_rate`：不是占位/超时/降级标记 / 全部
      —— **含**模型明确说「无法判断」的 T3（那是模型给出的判断，不是系统故障）
    * `usable_grade_rate`：有可解析分级 / 全部（最严；临床真正能用的）

    三者关系必然：``usable_grade_rate <= non_degraded_rate <= body_rate``。
    故意不给一个叫"有结论率"的模糊口径 —— 那个词正是"50%~100% 随便挑"的来源。
    """
    total = len(tiers)
    c = Counter(tiers)
    if total == 0:
        return {
            "total": 0,
            "body_rate": None,
            "non_degraded_rate": None,
            "usable_grade_rate": None,
        }
    body = total - c.get("T0_no_text", 0)
    non_degraded = total - sum(c.get(k, 0) for k in ("T0_no_text", "T1_timeout", "T2_degraded"))
    usable = c.get("T5_usable_grade", 0)
    return {
        "total": total,
        "by_tier": {
            k: c.get(k, 0)
            for k in (
                "T0_no_text",
                "T1_timeout",
                "T2_degraded",
                "T3_unable",
                "T4_text_no_grade",
                "T5_usable_grade",
            )
        },
        "body_rate": round(body / total, 4),
        "non_degraded_rate": round(non_degraded / total, 4),
        "usable_grade_rate": round(usable / total, 4),
        "numerator_definition": {
            "body_rate": "findings.content 或 recommendations 非空",
            "non_degraded_rate": (
                "非 T0/T1/T2 —— 即不是空、不是超时清扫、不是降级标记串；"
                "**包含** T3（模型明确说「无法判断」，那是判断不是故障）"
            ),
            "usable_grade_rate": ("T5：extract_grading_severest 能抽出分级，且模型没说「无法判断」"),
        },
    }


# ----------------------------------------------------------------------
# 数据采集（只读）
# ----------------------------------------------------------------------


async def _collect_rows() -> list:
    """只读拉全表（497 行，量级很小，一次取回即可）。"""
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import DiagnosisResult

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(DiagnosisResult).order_by(DiagnosisResult.created_at))
        return list(result.scalars().all())


async def _collect_kb_first_ingest() -> dict[str, datetime]:
    """各器官知识库**最早**灌库时间（UTC）。只读。"""
    from sqlalchemy import func, select

    from app.config.database import AsyncSessionLocal
    from app.models.database import KnowledgeChunkModel

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    KnowledgeChunkModel.organ,
                    func.min(KnowledgeChunkModel.created_at),
                ).group_by(KnowledgeChunkModel.organ)
            )
        ).all()
    return {str(organ): ts for organ, ts in rows if ts is not None}


def _log_evidence_by_day(log_dir: pathlib.Path, max_bytes: int) -> dict:
    """日志侧独立复核：按天统计服务故障签名。读失败**必须留痕**（零静默纪律）。

    这里只做"旁证"：记录侧已能自证的事不靠日志定罪；日志用来核对
    「故障窗那几个小时服务端确实在报错」，以及给 `service_outage` 提供第二条证据。
    """
    signatures = (
        ("vllm_engine_error", re.compile(r"(EngineDeadError|AsyncLLMEngine|vllm)", re.IGNORECASE)),
        ("timeout", re.compile(r"(TimeoutError|timed out|ReadTimeout)", re.IGNORECASE)),
        ("http_5xx", re.compile(r"(\b50[0234]\b|Bad Gateway|Service Unavailable)", re.IGNORECASE)),
        ("conn_refused", re.compile(r"(Connection refused|ConnectError)", re.IGNORECASE)),
        ("conn_closed", re.compile(r"(underlying connection is closed|InterfaceError)", re.IGNORECASE)),
        ("llm_unavailable", re.compile(r"llm_unavailable", re.IGNORECASE)),
    )
    day_re = re.compile(r"(2026-\d{2}-\d{2})")
    by_day: dict[str, Counter] = defaultdict(Counter)
    used: list[str] = []
    unreadable: list[str] = []
    lines = 0
    for path in sorted(log_dir.glob("*.log")):
        try:
            size = path.stat().st_size
            with path.open("rb") as fh:
                if max_bytes and size > max_bytes:
                    fh.seek(size - max_bytes)
                blob = fh.read()
        except OSError as exc:  # silent-ok: 读失败已记入 unreadable_logs，并在报告里列出
            unreadable.append(f"{path.name}:{type(exc).__name__}")
            continue
        used.append(path.name)
        for line in blob.decode("utf-8", errors="replace").splitlines():
            lines += 1
            match = day_re.search(line)
            if not match:
                continue
            for name, pattern in signatures:
                if pattern.search(line):
                    by_day[match.group(1)][name] += 1
    return {
        "files_used": used,
        "unreadable_files": unreadable,
        "lines_scanned": lines,
        "by_day": {d: dict(c.most_common()) for d, c in sorted(by_day.items())},
        "outage_day_totals": {d: sum(by_day.get(d, Counter()).values()) for d in OUTAGE_WINDOW},
    }


def _session_kind(session_id: object) -> str:
    """流量属性：自测/评测流量 vs 业务会话（按 session 命名前缀，见报告 calibers）。"""
    sid = str(session_id or "")
    if sid.startswith(("smoke", "t2c", "perf", "probe", "int-", "bench")):
        return "selftest"
    if sid.startswith("sess_2026"):
        return "business"
    return "other"


def _import_grading_extractor():
    """取生产同一把尺子（分级抽取），保证"能抽到分级"与线上判定同源。"""
    from app.inference.grading_definitions import extract_grading_severest

    return extract_grading_severest


def build_report(rows: list, kb_first_ingest: dict, logs: dict) -> dict:
    """组装完整报告（纯装配，便于测试）。"""
    extractor = _import_grading_extractor()

    no_body = [r for r in rows if not body_of(getattr(r, "findings", None))]

    attributed: list[dict] = []
    for r in no_body:
        findings = getattr(r, "findings", None) or {}
        has_trace = isinstance(findings, dict) and bool({"augmentation_skip_reason", "image_leg"} & set(findings))
        created = getattr(r, "created_at", None)
        day = created.astimezone(timezone.utc).strftime("%Y-%m-%d") if created else "(无日期)"
        verdict = attribute(
            r,
            kb_first_ingest=kb_first_ingest,
            has_service_evidence=bool(logs["by_day"].get(day)),
            has_image_leg_trace=has_trace,
        )
        attributed.append(
            {
                "request_id": str(getattr(r, "request_id", "")),
                "organ": str(getattr(r, "organ", "") or ""),
                "day": day,
                "status": str(getattr(r, "status", "") or ""),
                "session_kind": _session_kind(getattr(r, "session_id", None)),
                "has_body": False,
                "n_knowledge_ids": len(getattr(r, "knowledge_ids", None) or []),
                "n_image_ids": len(getattr(r, "image_ids", None) or []),
                "in_outage_window": in_outage_window(created),
                "before_content_contract": before_content_contract(created),
                **verdict,
            }
        )

    # 全库可用性分层（251 条只是其中一层）
    all_tiers = {str(getattr(r, "request_id", "")): availability_tier(r, extractor) for r in rows}
    normal = [
        r
        for r in rows
        if getattr(r, "created_at", None) is not None
        and not before_content_contract(getattr(r, "created_at", None))
        and str(getattr(r, "status", "") or "") != "cancelled"
    ]
    normal_tiers = [availability_tier(r, extractor) for r in normal]

    # 器官级空检索率（服务窗 vs 正常期）—— 这是 kb_not_ingested 的关键旁证
    organ_empty: dict[str, dict] = {}
    for organ in sorted({str(getattr(r, "organ", "") or "") for r in rows}):
        for seg, pred in (
            ("outage_window", lambda r: in_outage_window(getattr(r, "created_at", None))),
            ("normal_period", lambda r: not in_outage_window(getattr(r, "created_at", None))),
        ):
            sub = [r for r in rows if str(getattr(r, "organ", "") or "") == organ and pred(r)]
            if not sub:
                continue
            empty_n = sum(1 for r in sub if not (getattr(r, "knowledge_ids", None) or []))
            organ_empty[f"{organ}/{seg}"] = {
                "total": len(sub),
                "retrieval_empty": empty_n,
                "rate": round(empty_n / len(sub), 4),
            }

    # 「no_image」的反证数据：全库 vs 有正文组 的无图比例
    all_no_img = sum(1 for r in rows if not (getattr(r, "image_ids", None) or []))
    body_rows = [r for r in rows if body_of(getattr(r, "findings", None))]
    body_no_img = sum(1 for r in body_rows if not (getattr(r, "image_ids", None) or []))
    no_image_counter = {
        "all_rows_without_image_ids": all_no_img,
        "all_rows": len(rows),
        "all_rows_without_image_ratio": round(all_no_img / len(rows), 4) if rows else None,
        "has_body_rows_without_image_ids": body_no_img,
        "has_body_rows": len(body_rows),
        "has_body_without_image_ratio": round(body_no_img / len(body_rows), 4) if body_rows else None,
        "conclusion": (
            "『无图』在无正文组与有正文组里比例几乎相同（都接近全量），没有区分性 → 不可把无正文归因于 no_image"
        ),
    }

    # 同一批 251 条在 UTC / CST 两种日界下的按天分布（日界差 8 小时，数字会对不上）。
    # 必须从**原始 created_at** 折算 —— 拿已折成日期字符串的 day 再折时区是错的。
    by_day_utc = Counter(day_key(getattr(r, "created_at", None), timezone.utc) for r in no_body)
    by_day_cst = Counter(day_key(getattr(r, "created_at", None), CST) for r in no_body)
    day_tz_sensitivity = {
        "utc": dict(sorted(by_day_utc.items())),
        "cst": dict(sorted(by_day_cst.items())),
        "in_window_utc": sum(
            v
            for d, v in by_day_utc.items()
            if OUTAGE_WINDOW[0] <= d <= OUTAGE_WINDOW[1]
        ),
        "in_window_cst": sum(
            v
            for d, v in by_day_cst.items()
            if OUTAGE_WINDOW[0] <= d <= OUTAGE_WINDOW[1]
        ),
        "note": (
            "两者是同一批记录的两种日界；跨日界的记录会落到不同日期，"
            "对账前先确认对方用的时区，否则数字必然对不上（历史统计是 UTC 日口径）"
        ),
    }

    # 时间窗 / 契约缺口交叉（解释"为什么同一批人算出 50%~100%"）
    cross = Counter()
    for a in attributed:
        cross[(a["in_outage_window"], a["before_content_contract"])] += 1

    # 业务流量子集（口径污染的主要来源：497 里 379 是自测/评测）
    business = [r for r in normal if _session_kind(getattr(r, "session_id", None)) == "business"]

    return {
        "probe": "p64_degraded_cause_attribution",
        "readonly": True,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "script": "scripts/audit/p64_degraded_cause_attribution.py",
        "calibers": {
            "no_body_definition": (
                "findings->'fusion_result'->>'content' 为空/缺失"
                "（任务给定口径；注意它**不等于**「模型没产出」，见 write_gap_produced_ok）"
            ),
            "outage_window": {
                "utc_days": list(OUTAGE_WINDOW),
                "evidence": (
                    "16 号 A.70.7：9/30 vllm 引擎错误 1636 次、9/29 timeout 471 次；"
                    "本脚本 logs.outage_day_totals 独立复核同向"
                ),
            },
            "service_outage_rule": (
                "根因=service_outage 需**同时**满足：①created_at 落在故障窗（UTC 日）"
                "②记录有服务侧证据（llm_unavailable/InferenceError 或落库断连 InterfaceError）"
                "③当日日志确有服务故障签名。"
                "**不用 status 字段定罪**：故障窗内 37 条 llm_unavailable 被写成 "
                "status=completed（216~218 行注释里的旧口径会漏掉它们）"
            ),
            "content_contract_fix": {
                "commit": CONTENT_CONTRACT_COMMIT,
                "committed_at_utc": CONTENT_CONTRACT_FIX_UTC.isoformat(),
                "before_fix_content_key_rate": None,  # main() 里填
                "after_fix_content_key_rate": None,
            },
            "normal_period_definition": (
                "created_at >= 5de3a81（content 契约接线）且 status != cancelled；"
                "即同时排除服务故障窗、契约缺口期与政策终止"
            ),
            "why_not_only_outage_window": (
                "只排除 9/28–9/30 不够：content 契约缺口一直持续到 10-01 01:35 (+08)，"
                "该期间的记录即使模型完全正常，findings 里也没有 content 键。"
                "只用时间窗当'正常期'会把契约缺口算成服务故障。"
            ),
            "session_kind_definition": {
                "selftest": "session_id 前缀 smoke/t2c/perf/probe/int-/bench（自测与评测流量）",
                "business": "session_id 前缀 sess_2026（真实业务会话）",
                "why_it_matters": (
                    f"497 条里 {sum(1 for r in rows if _session_kind(getattr(r, 'session_id', None)) == 'selftest')} "
                    "条是自测/评测流量。把评测失败当成'系统可用率'是口径污染的主要来源之一，"
                    "故单列但不剔除（剔除会让分母失真）"
                ),
            },
            "no_image_counter_evidence": {
                "all_rows_without_image_ids": all_no_img,
                "all_rows": len(rows),
                "all_rows_without_image_ratio": round(all_no_img / len(rows), 4) if rows else None,
                "has_body_rows_without_image_ids": body_no_img,
                "has_body_rows": len(body_rows),
                "has_body_without_image_ratio": round(body_no_img / len(body_rows), 4) if body_rows else None,
                "conclusion": (
                    "『无图』在无正文组与有正文组里比例几乎相同（都接近全量），没有区分性 → 不可把无正文归因于 no_image"
                ),
            },
            "no_body_by_day_utc_vs_cst": day_tz_sensitivity,
            "no_body_in_outage_x_before_contract": {
                f"in_window={k[0]},before_contract={k[1]}": v for k, v in sorted(cross.items())
            },
            "organ_retrieval_empty_rates": organ_empty,
        },
        "totals": {
            "all_rows": len(rows),
            "no_body": len(no_body),
            "no_body_share": round(len(no_body) / len(rows), 4) if rows else None,
            "has_body": len(rows) - len(no_body),
        },
        "attribution": {
            "by_root_cause": summarize_by_root_cause(attributed),
            "by_task_taxonomy": summarize_task_taxonomy(attributed, no_image_counter),
            "matrix_organ_root_day": summarize_matrix(attributed),
            "records": attributed,
        },
        "availability": {
            "normal_period": {
                **summarize_availability(normal_tiers),
                "definition": "created_at >= 5de3a81 且 status != cancelled",
                "days": dict(
                    sorted(Counter(r.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d") for r in normal).items())
                ),
                "by_organ": _availability_by_organ(normal, extractor),
                "business_only": {
                    **summarize_availability([availability_tier(r, extractor) for r in business]),
                    "note": "只取 sess_2026* 真实业务会话（剔除自测/评测流量）",
                },
                "t3_cause_breakdown": _t3_breakdown(normal),
            },
            "all_rows_same_caliber": summarize_availability(list(all_tiers.values())),
            "sensitivity": {
                "exclude_deploy_day_2026_10_03": _exclude_day(normal, "2026-10-03", extractor),
                "note": (
                    "10/03 是部署重启日（20 条超时文案集中在此），单独看更接近稳态；"
                    "两个数字都给，避免读者以为挑了一个好看的"
                ),
            },
        },
        "logs": logs,
        "caveats": [
            "本脚本只读：全程 SELECT，不写库、不改配置、不重启。",
            "日志已被轮转：按 request_id 全文检索 0 命中，故无法对单条请求做日志级定罪，"
            "日志只作日级旁证（`logs.by_day`）。",
            "`knowledge_chunks.created_at` 只反映**当前驻留版本**的写入时间；历史版本若被重灌，"
            "『早于灌库』这条推断会失真 —— 因此 kb_not_ingested 的该子证据标 inferred。",
            "`image_ids` 全库仅 36/497 非空：本口径下 no_image 无法从「有没有图」判定，"
            "必须依赖影像腿跳过留痕，而该留痕全库 0 条 → 该类在本数据上不可判定，已如实给 0 并说明。",
            "`status='timeout'` 从不落库（全库 0 行）：任何按 status=timeout 统计的口径都会得到 0，"
            "这是读路径派生值，不代表没有超时（真超时证据见 stale_processing 107 条）。",
            "正常期只有 152 条且 133 条是 thyroid：liver 仅 4 条、cardiac 15 条，"
            "分器官数字的置信区间很宽，不能当稳定基线用。",
        ],
    }


def _availability_by_organ(rows: list, extractor) -> dict:
    """按器官的可用性分层（器官间差异很大，混在一起会掩盖真问题）。"""
    grouped: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        grouped[str(getattr(r, "organ", "") or "")].append(availability_tier(r, extractor))
    return {organ: summarize_availability(tiers) for organ, tiers in sorted(grouped.items())}


def _exclude_day(rows: list, day: str, extractor) -> dict:
    """剔除某一天后的可用性（敏感性分析）。"""
    kept = [
        r
        for r in rows
        if not (getattr(r, "created_at", None) and r.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d") == day)
    ]
    out = summarize_availability([availability_tier(r, extractor) for r in kept])
    out["excluded_day"] = day
    out["excluded_n"] = len(rows) - len(kept)
    return out


def _t3_breakdown(rows: list) -> dict:
    """`T3_无法判断` 内部成因：是「没影像描述」还是「没临床信息」。

    这一步很重要：T3 是全库最大的一层（69/152 正常期），但它的修法不是"修服务"，
    而是"给模型影像特征描述"（对应 p65 的影像腿 A/B）。不拆开就会把它当成故障。
    """
    extractor = _import_grading_extractor()
    t3 = [r for r in rows if availability_tier(r, extractor) == "T3_unable"]
    causes = Counter()
    for r in t3:
        text = readable_text(r)
        if _RE_MISSING_IMAGE_FEATURES.search(text):
            causes["missing_image_features"] += 1
        elif "缺少" in text or "不足" in text:
            causes["missing_clinical_text"] += 1
        else:
            causes["other"] += 1
    return {
        "n": len(t3),
        "by_cause": dict(causes.most_common()),
        "by_organ": dict(Counter(str(getattr(r, "organ", "") or "") for r in t3).most_common()),
        "with_image_ids": sum(1 for r in t3 if (getattr(r, "image_ids", None) or [])),
        "note": (
            "T3 是模型**明确说判不了**（不是系统故障）。"
            "若多数是 missing_image_features，修法是给影像特征（影像腿开关），不是修服务。"
        ),
    }


def print_human_summary(report: dict) -> None:
    """stdout 人可读汇总。"""
    t = report["totals"]
    att = report["attribution"]
    print("=" * 78)
    print("p64 无正文成因归因（只读）")
    print("=" * 78)
    print(
        f"全库 {t['all_rows']} 条；findings.content 为空 {t['no_body']} 条"
        f"（{t['no_body_share']:.1%}）；有正文 {t['has_body']} 条"
    )

    print("\n-- 251 条按根因（互斥，含置信度）--")
    by = att["by_root_cause"]["by_root_cause"]
    total = att["by_root_cause"]["total"]
    for cause in ROOT_CAUSES:
        n = by.get(cause, 0)
        if not n:
            continue
        conf = att["by_root_cause"]["by_confidence"].get(cause, {})
        print(
            f"  {n:>4}  {n / total:6.1%}  {cause:<22} "
            f"(direct={conf.get('direct', 0)} inferred={conf.get('inferred', 0)} "
            f"unknown={conf.get('unknown', 0)})"
        )
    print(f"  → unknown 保留 {att['by_root_cause']['unknown_kept']} 条（未硬塞）")
    print(f"  机制分布：{att['by_root_cause']['by_mechanism']}")

    print("\n-- 任务要求的 5 类口径 --")
    for name, info in att["by_task_taxonomy"].items():
        print(f"  {name:<20} {info['n']:>4}")
        if info.get("why_zero"):
            print(f"      ⚠ 为 0 的原因：{info['why_zero']}")
        if info.get("note"):
            print(f"      注：{info['note']}")
        if info.get("evidence_inferred_note"):
            print(f"      注：{info['evidence_inferred_note']}")

    print(f"\n-- 器官 × 根因 × 日期（三维，前 12 / 共 {len(att['matrix_organ_root_day'])} 组）--")
    for row in att["matrix_organ_root_day"][:12]:
        print(f"  {row['organ']:<9} {row['root_cause']:<22} {row['day']}  {row['n']:>3}")

    av = report["availability"]
    np_ = av["normal_period"]
    print("\n" + "=" * 78)
    print("★ 服务正常期内的可用率（本次核心产出）")
    print(f"   正常期定义：{np_['definition']}")
    print(f"   样本 {np_['total']} 条")
    print("   三个口径（分子分母都写清，避免「随便挑」）：")
    print(f"     ① 有正文率    {np_['body_rate']:.2%}   ← findings.content 或 recommendations 非空")
    print(f"     ② 非降级率    {np_['non_degraded_rate']:.2%}   ← 非空/非超时清扫/非降级标记串（含「无法判断」）")
    print(f"     ③ 可用分级率  {np_['usable_grade_rate']:.2%}   ← 能抽出分级且模型没说「无法判断」")
    print(f"   分层：{np_['by_tier']}")
    dep = av["sensitivity"]["exclude_deploy_day_2026_10_03"]
    print(f"   敏感性（剔除部署日 10/03）：可用分级率 {dep['usable_grade_rate']:.2%}（剔除 {dep['excluded_n']} 条）")
    biz = np_["business_only"]
    print(f"   仅业务流量（sess_2026*，{biz['total']} 条）：可用分级率 {biz['usable_grade_rate']:.2%}")

    print("\n   按器官（可用分级率）：")
    for organ, slot in np_["by_organ"].items():
        print(
            f"     {organ:<9} {slot['total']:>3} 条  "
            f"可用 {slot['usable_grade_rate']:.1%}   "
            f"T3_无法判断 {slot['by_tier']['T3_unable']}"
        )

    t3 = np_["t3_cause_breakdown"]
    print(f"\n   T3「无法判断」{t3['n']} 条拆解（最大短板，但不是故障）：")
    print(f"     {t3['by_cause']}；器官 {t3['by_organ']}；其中有 image_ids {t3['with_image_ids']} 条")

    print("\n-- 对照：全库同口径 --")
    allr = av["all_rows_same_caliber"]
    print(
        f"   全库 {allr['total']} 条：有正文 {allr['body_rate']:.2%} / "
        f"非降级 {allr['non_degraded_rate']:.2%} / 可用分级 {allr['usable_grade_rate']:.2%}"
    )

    lg = report["logs"]
    print("\n-- 日志旁证（独立复核，仅日级）--")
    print(f"   扫 {len(lg['files_used'])} 个文件 / {lg['lines_scanned']} 行；不可读 {len(lg['unreadable_files'])}")
    print(f"   故障窗签名总数：{lg['outage_day_totals']}")
    for day in OUTAGE_WINDOW:
        if day in lg["by_day"]:
            print(f"   {day}: {lg['by_day'][day]}")

    print("\n-- 口径自证（防'50%~100%随便挑'）--")
    fix = report["calibers"]["content_contract_fix"]
    print(
        f"   content 键存在率：接线前 {fix['before_fix_content_key_rate']:.1%} → "
        f"接线后 {fix['after_fix_content_key_rate']:.1%}"
        f"（commit {fix['commit']}）"
    )
    ni = report["calibers"]["no_image_counter_evidence"]
    print(
        f"   无图比例：全库 {ni['all_rows_without_image_ratio']:.1%} vs "
        f"有正文组 {ni['has_body_without_image_ratio']:.1%} → no_image 无区分性"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--report",
        default=str(pathlib.Path.home() / "uai" / "reports" / "degraded_cause.json"),
        help="报告输出路径（默认 ~/uai/reports/degraded_cause.json）",
    )
    parser.add_argument(
        "--emit",
        action="store_true",
        help="真正写报告文件；**不加则 dry-run**（只在 stdout 打印）",
    )
    parser.add_argument("--log-dir", default=str(pathlib.Path.home() / "uai" / "logs"), help="日志目录")
    parser.add_argument("--max-log-bytes", type=int, default=40 * 1024 * 1024, help="每个日志读末尾多少字节")
    parser.add_argument("--json-only", action="store_true", help="stdout 只输出 JSON（供管道）")
    args = parser.parse_args()

    try:
        rows = await _collect_rows()
        kb_first = await _collect_kb_first_ingest()
    except Exception as exc:  # 库不可达 → 明确退出码，不假装成功
        print(f"[FATAL] 取数失败（库不可达？）：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    logs = _log_evidence_by_day(pathlib.Path(args.log_dir).expanduser(), args.max_log_bytes)
    report = build_report(rows, kb_first, logs)

    # 补齐 calibers 里两个"接线前后 content 键存在率"实测值
    before = [r for r in rows if getattr(r, "created_at", None) is not None and before_content_contract(r.created_at)]
    after = [
        r for r in rows if getattr(r, "created_at", None) is not None and not before_content_contract(r.created_at)
    ]
    fix = report["calibers"]["content_contract_fix"]
    fix["before_fix_content_key_rate"] = (
        round(
            sum(1 for r in before if has_content_key(getattr(r, "findings", None))) / len(before),
            4,
        )
        if before
        else None
    )
    fix["after_fix_content_key_rate"] = (
        round(
            sum(1 for r in after if has_content_key(getattr(r, "findings", None))) / len(after),
            4,
        )
        if after
        else None
    )

    if args.json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human_summary(report)

    if not args.emit:
        print(f"\n[dry-run] 未写报告文件。加 --emit 写到 {pathlib.Path(args.report).expanduser()}")
        return 0

    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写：{out}（{out.stat().st_size} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
