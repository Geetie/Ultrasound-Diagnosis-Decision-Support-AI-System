"""服务器真机验证：真实 Redis(uai-redis) 刷新令牌语义 + SoftHSM 签名字节。

用法（服务器）:
    set -a; source ~/uai/.env; set +a
    PYTHONPATH=$HOME/uai/src ~/uai/venv/bin/python /tmp/_server_real_checks.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys

logger = logging.getLogger(__name__)

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((name, ok, note))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {note}", flush=True)


async def real_redis_checks() -> None:
    from app.config.redis import RedisPool
    from app.config.redis_keys import RedisKey
    from app.security.refresh_token import RedisRefreshTokenStore

    store = RedisRefreshTokenStore()
    client = await RedisPool.get_client()
    # 确认这是真 Redis（PING）
    await client.ping()
    record("redis_ping", True, str(client))

    user = "real-redis-probe"
    # 清理残留
    for tid in list(await client.smembers(RedisKey.auth_user_tokens(user)) or []):
        tid = tid.decode() if isinstance(tid, bytes) else tid
        await client.delete(RedisKey.auth_token(tid))

    token_id = await store.create_refresh_token(user, {"username": "probe"})
    data = await store.verify_refresh_token(token_id)
    record("refresh_create_verify", bool(data and data["token_id"] == token_id))

    new_id = await store.rotate_refresh_token(token_id)
    record("refresh_rotate", bool(new_id and new_id != token_id))

    # 旧令牌重放必须被拒（家族撤销）
    try:
        await store.rotate_refresh_token(token_id)
        record("refresh_replay_rejected", False, "replay was allowed")
    except ValueError:
        logger.warning("real_redis_checks: 未预期异常，按原逻辑降级", exc_info=True)
        record("refresh_replay_rejected", True)

    # 重放触发家族撤销后：用户集合里的令牌应全部 revoked（revoke_all
    # 的返回 0 是正确行为——没有"未撤销"令牌可再撤）
    all_revoked = True
    for tid in await client.smembers(RedisKey.auth_user_tokens(user)) or []:
        tid = tid.decode() if isinstance(tid, bytes) else tid
        raw = await client.get(RedisKey.auth_token(tid))
        if raw is None:
            continue  # 已过期清除，视为不可用
        if not json.loads(raw).get("revoked"):
            all_revoked = False
    record("refresh_family_revoked_all_tokens", all_revoked)

    # 清理
    for tid in list(await client.smembers(RedisKey.auth_user_tokens(user)) or []):
        tid = tid.decode() if isinstance(tid, bytes) else tid
        await client.delete(RedisKey.auth_token(tid))
        await client.delete(RedisKey.auth_token_family(tid))
    await client.delete(RedisKey.auth_user_tokens(user))


async def softhsm_checks() -> None:
    from app.security.hsm import hsm_manager

    status = hsm_manager.get_soft_hsm_status()
    print("soft_hsm_status:", status, flush=True)
    record(
        "softhsm_status_contract",
        isinstance(status.get("is_soft_hsm"), bool)
        and status.get("expiry_level") in ("none", "approaching", "warning", "critical"),
    )

    data = b"real-machine-signature-probe-2026-10-03"
    from app.security.hsm import KeySlot

    probe_key = "real_probe_signing_key"
    hsm_manager.generate_key_pair(probe_key, key_size=2048, slot=KeySlot.JWT_SIGNING)
    sig = hsm_manager.sign(probe_key, data)
    ok = hsm_manager.verify_signature(probe_key, data, sig)
    record("softhsm_sign_verify_bytes", bool(ok), f"sig_len={len(sig)}")

    tampered = b"tampered" + data
    bad = hsm_manager.verify_signature(probe_key, tampered, sig)
    record("softhsm_verify_tamper_rejected", bad is False)


async def main() -> int:
    failures = 0
    try:
        await real_redis_checks()
    except Exception as exc:
        failures += 1
        record("real_redis_suite", False, f"{type(exc).__name__}: {exc}")
    try:
        await softhsm_checks()
    except Exception as exc:
        failures += 1
        record("softhsm_suite", False, f"{type(exc).__name__}: {exc}")

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n=== real-machine checks: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ===", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
