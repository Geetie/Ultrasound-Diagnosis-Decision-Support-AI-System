"""守卫：SG 的 Redis **阻塞读**必须严格短于 socket 读超时。

## 为什么需要它（2026-09-28 / 09-29 两次真机暴露，同一类缺陷）

Redis 的阻塞命令（`XREADGROUP`、`BLPOP`、`Pub/Sub listen`）会占用 socket 读。
只要"等待时长 ≥ 连接池的 socket_timeout"，这次读就必然以
`TimeoutError: Timeout reading from ...` 收场，而**上层会把它当成依赖不可用**：

* 09-28：阈值流消费者 `xreadgroup(block=5000)` vs `socket_timeout=5` → 每 ~6s 报错；
* 09-29：脱敏规则订阅 `pubsub.listen()`（等价 `get_message(timeout=None)`，**无限阻塞**）
  → 每 ~9s 报 `sg_redis_pubsub_error` → SG 回 E4005B → 主服务
  `safety_gateway_fast_fail_threshold_reached`。

本地跑不出来：单测用替身，没人真去阻塞读；只有真机长时间运行才会稳定复现。
"""
from __future__ import annotations

import ast
import pathlib

from safety_gateway.redis_pool import effective_socket_timeout

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SUBSCRIBER = REPO_ROOT / "safety_gateway" / "redis_subscriber.py"
POOL = REPO_ROOT / "safety_gateway" / "redis_pool.py"


class TestEffectiveSocketTimeout:
    def test_超时值有下限(self):
        """socket 读超时必须留出余量，否则任何阻塞读都会立刻撞上它。"""
        assert effective_socket_timeout() >= 8

    def test_连接池用的是同一个事实源(self):
        src = POOL.read_text(encoding="utf-8")

        assert "socket_timeout=effective_socket_timeout()" in src, (
            "socket_timeout 必须来自 effective_socket_timeout()，否则两处会漂移"
        )


class TestPubSubUsesBoundedPoll:
    def test_不再使用无限阻塞的_listen(self):
        src = SUBSCRIBER.read_text(encoding="utf-8")
        tree = ast.parse(src)

        listen_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "listen"
        ]

        assert listen_calls == [], (
            "`pubsub.listen()` 是无限阻塞读，必然撞上 socket_timeout；"
            "应改用 get_message(ignore_subscribe_messages=True, timeout=<有界>)"
        )

    def test_轮询时长严格小于_socket_超时(self):
        src = SUBSCRIBER.read_text(encoding="utf-8")

        assert "get_message(" in src and "timeout=poll_timeout" in src
        assert "effective_socket_timeout() - 3.0" in src, (
            "轮询时长必须从 effective_socket_timeout() 推导并留出余量"
        )


class TestStreamConsumerBlockFits:
    def test_流消费的block短于socket超时(self):
        """`xreadgroup(block=5000ms)`：5s < socket 超时（≥8s）才成立。"""
        src = SUBSCRIBER.read_text(encoding="utf-8")

        assert "block=5000" in src
        assert effective_socket_timeout() * 1000 > 5000
