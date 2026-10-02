"""诊断 DLQ 运维出口（wiring-audit P2-1：DLQ 此前只写不读）。

用法（服务器）:
    PYTHONPATH=$HOME/uai/src ~/uai/venv/bin/python scripts/diag_dlq_dump.py \
        [--tail 20] [--drain]

默认只读：打印 DLQ 长度与最近 N 条（request_id|时间戳|错误摘要）。
--drain 将队列清空（先打印再删除，用于故障归档后释放）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys


async def main(tail: int, drain: bool) -> int:
    from app.config.redis import RedisPool
    from app.config.redis_keys import RedisKey

    client = await RedisPool.get_client()
    key = RedisKey.DIAGNOSIS_DLQ
    length = await client.llen(key)
    print(f"[dlq] {key} length={length}")

    if length == 0:
        return 0

    entries = await client.lrange(key, -tail, -1)
    print(f"[dlq] last {len(entries)} entries:")
    for raw in entries:
        line = raw.decode() if isinstance(raw, bytes) else raw
        print("  ", line)

    if drain:
        await client.delete(key)
        print(f"[dlq] drained {length} entries")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tail", type=int, default=20)
    parser.add_argument("--drain", action="store_true",
                        help="打印后清空队列（归档后释放）")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.tail, args.drain)))
