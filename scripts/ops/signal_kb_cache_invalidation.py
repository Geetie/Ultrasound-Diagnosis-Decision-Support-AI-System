"""通知运行中的服务：知识库内容已变，请立即清检索缓存（G8）。

## 为什么需要这个脚本

知识缓存有两层（L1 Redis 24h / L2 进程内 1h）。以前知识库更新后**只能等 TTL**
——新知识最长 24 小时不可见，医生继续看到旧知识检索结果。

现在 `KnowledgeAgentImpl.query` 每次会读一个 KV 更新戳，戳变了就主动清两层缓存。
本脚本负责**写这个戳**；任何离线导入/重建流程（qdrant_reindex_kb 等）跑完后调用它：

```bash
python scripts/ops/signal_kb_cache_invalidation.py --reason "reindex kb_v1.4.0"
```

## 为什么是戳而不是广播

导入脚本与服务可能不在同一进程/主机；一条 KV 戳足以表达"缓存已过期"，
不需要投递保证：消息丢了也只是退化为原来的 TTL 语义，不会坏得更糟。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config.redis_keys import RedisKey  # noqa: E402


async def _run(reason: str, actor: str) -> int:
    from app.config.redis import RedisPool

    client = await RedisPool.get_client()
    stamp = f"{datetime.now(timezone.utc).isoformat()}|{reason}|{actor}"
    try:
        await client.set(RedisKey.KB_CACHE_INVALIDATION_STAMP, stamp)
    except Exception as exc:
        print(f"[FAIL] 写更新戳失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"[OK] 已写入知识库更新戳：{stamp}")
    print("     运行中的服务将在下一次检索时清空两层缓存。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="通知服务清空知识检索缓存（G8）")
    parser.add_argument("--reason", required=True, help="失效原因（写进戳，便于溯源）")
    parser.add_argument("--actor", default="ops-script", help="操作者标识")
    args = parser.parse_args()
    return asyncio.run(_run(args.reason, args.actor))


if __name__ == "__main__":
    raise SystemExit(main())
