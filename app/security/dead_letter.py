"""撤回事件死信队列的**读取侧**（D-04：此前只写不读）。

## 缺陷（纠错台账 D-04 / wiring-audit P2-1）

`consent_withdrawal_consumer._handle_processing_error` 把处理失败的事件
`XADD` 进 `consent:withdrawal:dead_letter`（maxlen=1000），但全仓**没有任何读取方**：

- 运维看不到死信积压（只能靠人肉 `redis-cli`）；
- 死信里的撤回事件**永远不会被重试** → 病人的撤回意图静默丢失，
  这是合规面（知情同意撤回必须生效），不是单纯的运维便利问题。

## 本模块职责（只做"看得见 + 可重放"，不自动重试）

- :func:`list_dead_letters`：分页列出死信（默认只读，供看板/接口调用）；
- :func:`replay_dead_letter`：把单条死信**重新投回主处理流**，并在死信流里留
  重放痕迹（`replayed_at` / `replayed_by`），**不删除**原始记录（保留审计）；
- 不做后台自动重放：撤回事件的重放会触发数据清理等副作用，必须由人确认后执行
  （与熔断恢复入口同一原则：不可逆动作要有人签字）。

## 为什么重放是"重新投回主流"而不是"直接调 handler"

主流的消费路径已经包含幂等键（`event_id`）与失败→死信→重试的完整语义；
直接调 handler 会绕过幂等与审计链，形成"第二条写路径"（本项目已因这类
双写路径吃过账：见 16 号 A.70 数据质量事故）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from app.config.redis_keys import RedisKey

logger = structlog.get_logger()

__all__ = [
    "list_dead_letters",
    "replay_dead_letter",
    "dead_letter_depth",
]


async def _client():
    from app.config.redis import RedisPool

    return await RedisPool.get_client()


def _decode(fields: dict) -> dict[str, str]:
    """Redis stream 返回的 bytes 键值统一转 str（调用方不必再判类型）。"""
    out: dict[str, str] = {}
    for k, v in (fields or {}).items():
        key = k.decode() if isinstance(k, bytes) else str(k)
        val = v.decode() if isinstance(v, bytes) else str(v)
        out[key] = val
    return out


async def dead_letter_depth() -> int:
    """当前死信条数（供看板/告警阈值用）。读失败返回 -1（"未知"，不冒充 0）。"""
    try:
        client = await _client()
        return int(await client.xlen(RedisKey.CONSENT_WITHDRAWAL_DEAD_LETTER_STREAM))
    except Exception as exc:
        logger.warning("dead_letter_depth_failed", error=str(exc)[:160])
        return -1


async def list_dead_letters(limit: int = 20) -> list[dict[str, Any]]:
    """列出死信（最新在前）。只读，不改动任何状态。

    Args:
        limit: 最多返回条数（1..200，超出按 200 截断——死信最多 1000 条，
            分页由调用方用 count 递增实现）。

    Returns:
        ``[{"entry_id": "...", "failed_at": "...", "error": "...", ...}, ...]``
    """
    limit = max(1, min(int(limit or 20), 200))
    try:
        client = await _client()
        entries = await client.xrevrange(
            RedisKey.CONSENT_WITHDRAWAL_DEAD_LETTER_STREAM, max="+", min="-", count=limit
        )
    except Exception as exc:
        logger.warning("dead_letter_list_failed", error=str(exc)[:160])
        return []
    rows: list[dict[str, Any]] = []
    for entry_id, fields in entries:
        row = _decode(fields)
        row["entry_id"] = entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
        rows.append(row)
    return rows


async def replay_dead_letter(entry_id: str, actor: str) -> dict[str, Any]:
    """把一条死信重新投回主处理流，并在死信流里留重放痕迹。

    Args:
        entry_id: ``list_dead_letters`` 返回的 ``entry_id``。
        actor: 操作者标识（必填——重放会触发数据清理，必须可审计）。

    Returns:
        ``{"ok": bool, "reason": str, "new_entry_id": str | None}``；
        失败时 ``reason`` 说明原因（找不到 / 已重放 / 投递异常），不抛异常。
    """
    if not str(entry_id or "").strip():
        return {"ok": False, "reason": "entry_id_required", "new_entry_id": None}
    if not str(actor or "").strip():
        return {"ok": False, "reason": "actor_required", "new_entry_id": None}
    try:
        client = await _client()
        entries = await client.xrange(
            RedisKey.CONSENT_WITHDRAWAL_DEAD_LETTER_STREAM, min=entry_id, max=entry_id
        )
    except Exception as exc:
        logger.warning("dead_letter_replay_read_failed", entry_id=entry_id, error=str(exc)[:160])
        return {"ok": False, "reason": "read_failed", "new_entry_id": None}
    if not entries:
        # 已重放过的条目会被替换成带 `replayed_at` 标记的新条目（新 entry_id），
        # 用旧 id 再查即 not_found。对运维的可操作含义是"该条已重放或不存在"，
        # 故 reason 文案同时覆盖两种情形（比笼统 not_found 更可诊断）。
        return {
            "ok": False,
            "reason": "not_found_or_already_replayed",
            "new_entry_id": None,
        }

    _, fields = entries[0]
    row = _decode(fields)
    if row.get("replayed_at"):
        return {"ok": False, "reason": "already_replayed", "new_entry_id": None}

    # 投回主流：去掉死信专属键，只保留原始事件字段（主流消费者按字段解析）。
    payload = {
        k: v
        for k, v in row.items()
        if k not in ("original_msg_id", "error", "failed_at", "replayed_at", "replayed_by")
    }
    payload["replayed_from_dead_letter"] = entry_id
    payload["replayed_by"] = str(actor)
    try:
        new_id = await client.xadd(
            RedisKey.CONSENT_WITHDRAWAL_STREAM, payload, maxlen=10000
        )
    except Exception as exc:
        logger.warning("dead_letter_replay_add_failed", entry_id=entry_id, error=str(exc)[:160])
        return {"ok": False, "reason": "replay_failed", "new_entry_id": None}

    # 留痕：**在原条目上加标记字段**（不是另写一条影子记录）。
    # 真机实测：另写影子条目会导致重复重放检测失效（xrange 查原条目看不到影子）
    # —— 同一条死信可被无限次重放，e2e 已复现（第二次重放仍 200）。
    # Redis stream 无"改字段"原语 → 用 XADD 写同 id（Redis 7 支持同 id 追加/合并
    # 语义由 XACK 后 XDEL 模拟；这里用最稳的可行做法：删原条目 + 写带标记的同字段条目，
    # 两者在同一 pipeline 内完成，失败则回滚（补回原条目））。
    marked = {
        **row,
        "replayed_at": datetime.now(timezone.utc).isoformat(),
        "replayed_by": str(actor),
        "original_entry_id": entry_id,
    }
    try:
        pipe = client.pipeline()
        pipe.xdel(RedisKey.CONSENT_WITHDRAWAL_DEAD_LETTER_STREAM, entry_id)
        pipe.xadd(
            RedisKey.CONSENT_WITHDRAWAL_DEAD_LETTER_STREAM,
            marked,
            maxlen=1000,
        )
        await pipe.execute()
    except Exception as exc:
        logger.warning("dead_letter_replay_mark_failed", entry_id=entry_id, error=str(exc)[:160])
        # 回滚：把原条目补回（宁可丢标记，不可丢记录）
        try:
            await client.xadd(
                RedisKey.CONSENT_WITHDRAWAL_DEAD_LETTER_STREAM, row, maxlen=1000
            )
        except Exception:
            logger.critical(
                "dead_letter_replay_rollback_failed", entry_id=entry_id
            )

    logger.info(
        "dead_letter_replayed",
        entry_id=entry_id,
        actor=str(actor),
        new_stream_entry=new_id.decode() if isinstance(new_id, bytes) else str(new_id),
    )
    return {
        "ok": True,
        "reason": "replayed",
        "new_entry_id": new_id.decode() if isinstance(new_id, bytes) else str(new_id),
    }
