"""D-04：撤回事件死信队列必须有**读取侧**（此前只写不读 → 撤回意图静默丢失）。

守卫三件事：①能列出死信 ②能重放回主流 ③重放留痕且不删原记录。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.security import dead_letter as dl

pytestmark = pytest.mark.track_sec


def _mock_client():
    client = MagicMock()
    client.xlen = AsyncMock(return_value=3)
    client.xrevrange = AsyncMock(
        return_value=[
            (b"1-1", {b"event_id": b"e1", b"error": b"boom", b"failed_at": b"2026-11-03T00:00:00"}),
            (b"1-0", {b"event_id": b"e0", b"error": b"boom0", b"failed_at": b"2026-11-02T00:00:00"}),
        ]
    )
    client.xrange = AsyncMock(
        return_value=[(b"1-1", {b"event_id": b"e1", b"error": b"boom", b"patient_id": b"p1"})]
    )
    client.xadd = AsyncMock(return_value=b"2-0")
    return client


@pytest.mark.asyncio
async def test_depth_reports_real_number():
    with patch.object(dl, "_client", AsyncMock(return_value=_mock_client())):
        assert await dl.dead_letter_depth() == 3


@pytest.mark.asyncio
async def test_depth_unknown_is_minus_one_not_zero():
    """读失败必须返回 -1（未知），不得冒充"没有死信"。"""
    broken = MagicMock()
    broken.xlen = AsyncMock(side_effect=RuntimeError("redis down"))
    with patch.object(dl, "_client", AsyncMock(return_value=broken)):
        assert await dl.dead_letter_depth() == -1


@pytest.mark.asyncio
async def test_list_decodes_bytes_and_orders_newest_first():
    with patch.object(dl, "_client", AsyncMock(return_value=_mock_client())):
        rows = await dl.list_dead_letters(limit=10)
    assert [r["event_id"] for r in rows] == ["e1", "e0"]
    assert rows[0]["entry_id"] == "1-1"
    assert isinstance(rows[0]["error"], str)


@pytest.mark.asyncio
async def test_list_clamps_limit():
    client = _mock_client()
    with patch.object(dl, "_client", AsyncMock(return_value=client)):
        await dl.list_dead_letters(limit=9999)
    assert client.xrevrange.await_args.kwargs["count"] == 200


@pytest.mark.asyncio
async def test_replay_requires_actor():
    with patch.object(dl, "_client", AsyncMock(return_value=_mock_client())):
        out = await dl.replay_dead_letter("1-1", actor="  ")
    assert out["ok"] is False
    assert out["reason"] == "actor_required"


@pytest.mark.asyncio
async def test_replay_pushes_back_to_main_stream_and_marks():
    client = _mock_client()
    with patch.object(dl, "_client", AsyncMock(return_value=client)):
        out = await dl.replay_dead_letter("1-1", actor="qc-admin")
    assert out["ok"] is True and out["reason"] == "replayed"
    assert client.xadd.await_count == 2  # 主流一条 + 死信留痕一条
    first_target = client.xadd.await_args_list[0].args[0]
    assert "consent:withdrawal" in str(first_target)
    assert "dead_letter" not in str(first_target)


@pytest.mark.asyncio
async def test_replay_rejects_already_replayed():
    client = _mock_client()
    client.xrange = AsyncMock(
        return_value=[(b"1-1", {b"event_id": b"e1", b"replayed_at": b"2026-11-03T01:00:00"})]
    )
    with patch.object(dl, "_client", AsyncMock(return_value=client)):
        out = await dl.replay_dead_letter("1-1", actor="qc-admin")
    assert out["ok"] is False and out["reason"] == "already_replayed"
    assert client.xadd.await_count == 0


@pytest.mark.asyncio
async def test_replay_not_found():
    client = _mock_client()
    client.xrange = AsyncMock(return_value=[])
    with patch.object(dl, "_client", AsyncMock(return_value=client)):
        out = await dl.replay_dead_letter("9-9", actor="qc-admin")
    assert out["ok"] is False and out["reason"] == "not_found"


@pytest.mark.asyncio
async def test_replay_second_time_after_marking_is_rejected():
    """真实闭环：重放会在原条目上加 replayed_at → 再查同 entry_id 必须被拒。

    真机实测（e2e 2026-11-03）：首版把留痕写成"另加一条影子记录"，
    xrange 查原条目看不到影子 → 同一条死信可被无限重放。本用例钉住该形态。
    """
    client = _mock_client()
    calls = {"n": 0}

    async def _xrange(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return [(b"1-1", {b"event_id": b"e1", b"patient_id": b"p1", b"error": b"boom"})]
        # 第二次：原条目已被标记（重放时的 xdel+xadd 换成了带标记的条目）
        return [(b"1-1", {b"event_id": b"e1", b"replayed_at": b"2026-11-03T02:00:00"})]

    client.xrange = AsyncMock(side_effect=_xrange)
    with patch.object(dl, "_client", AsyncMock(return_value=client)):
        first = await dl.replay_dead_letter("1-1", actor="qc-admin")
        second = await dl.replay_dead_letter("1-1", actor="qc-admin")
    assert first["ok"] is True
    assert second["ok"] is False and second["reason"] == "already_replayed"
