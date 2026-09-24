import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any

from .mapper import KeyMapper

logger = logging.getLogger(__name__)


class ProgressCallback:
    """进度回调基类"""

    def on_batch_start(self, batch_num: int, total_batches: int | None = None):
        """批次开始回调"""
        pass

    def on_key_processed(self, key: str, new_key: str, status: str, duration_ms: float):
        """单个键处理完成回调"""
        pass

    def on_batch_complete(self, batch_num: int, stats: dict[str, Any]):
        """批次完成回调"""
        pass

    def on_complete(self, final_stats: dict[str, Any]):
        """全部完成回调"""
        pass


class SimpleProgressCallback(ProgressCallback):
    """简单的进度回调实现 - 打印到控制台"""

    def __init__(self, log_interval: int = 100):
        self.log_interval = log_interval
        self.processed_count = 0

    def on_key_processed(self, key: str, new_key: str, status: str, duration_ms: float):
        self.processed_count += 1
        if self.processed_count % self.log_interval == 0:
            logger.info(f"Processed {self.processed_count} keys: {key} → {new_key} [{status}]")

    def on_batch_complete(self, batch_num: int, stats: dict[str, Any]):
        logger.info(f"Batch {batch_num} complete: {stats}")

    def on_complete(self, final_stats: dict[str, Any]):
        logger.info("Migration complete", final_stats=final_stats)


class PerformanceStats:
    """性能统计收集器"""

    def __init__(self):
        self.start_time = time.time()
        self.key_times = []
        self.batch_times = []
        self.total_keys = 0

    def record_key_time(self, duration_ms: float):
        self.key_times.append(duration_ms)
        self.total_keys += 1

    def record_batch_time(self, duration_ms: float):
        self.batch_times.append(duration_ms)

    def get_summary(self) -> dict[str, Any]:
        elapsed = time.time() - self.start_time
        avg_key_time = sum(self.key_times) / len(self.key_times) if self.key_times else 0
        avg_batch_time = sum(self.batch_times) / len(self.batch_times) if self.batch_times else 0
        tps = self.total_keys / elapsed if elapsed > 0 else 0

        return {
            'elapsed_seconds': elapsed,
            'total_keys': self.total_keys,
            'avg_key_time_ms': avg_key_time,
            'avg_batch_time_ms': avg_batch_time,
            'keys_per_second': tps
        }


class RedisKeyMigrator:
    """Redis 键迁移执行器"""

    def __init__(
        self,
        redis_client,
        mapper: KeyMapper,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None
    ):
        self.redis_client = redis_client
        self.mapper = mapper
        self.batch_size = config.get('batch_size', 100)
        self.rate_limit = config.get('rate_limit', 1000)
        self.progress_file = config.get('progress_file', '.migrate_progress.json')
        self.progress_callback = progress_callback or SimpleProgressCallback()

    async def migrate(self) -> dict[str, Any]:
        """执行完整迁移流程"""
        perf_stats = PerformanceStats()

        stats = {
            'scanned': 0,
            'migrated': 0,
            'failed': 0,
            'skipped': 0,
            'start_time': datetime.now().isoformat(),
        }

        cursor = 0
        batch_num = 0
        first_pass = True
        start_time = time.time()
        processed_in_second = 0

        logger.info("Starting key migration", batch_size=self.batch_size, rate_limit=self.rate_limit)

        # 至少扫描一轮（空库也要走一次 SCAN，进度里才能体现 scanned=0），
        # 之后严格按 Redis 返回的 cursor 判断是否结束。
        # 原条件 `while cursor != 0 or stats['scanned'] == 0` 在库为空
        # （cursor=0 且没有任何键）时永真 —— 死循环，每轮还会重写进度文件，
        # 对空库/已迁移完的实例执行迁移会永久驻留。
        while first_pass or cursor != 0:
            first_pass = False
            batch_num += 1
            batch_start = time.time()
            self.progress_callback.on_batch_start(batch_num)

            if processed_in_second >= self.rate_limit:
                elapsed = time.time() - start_time
                if elapsed < 1:
                    await asyncio.sleep(1 - elapsed)
                start_time = time.time()
                processed_in_second = 0

            cursor, keys = await self.redis_client.scan(
                cursor=cursor,
                count=self.batch_size,
            )

            for key in keys:
                key_start = time.time()
                key_str = key.decode() if isinstance(key, bytes) else key

                new_key = self.mapper.get_new_key(key_str)
                if not new_key:
                    stats['skipped'] += 1
                    key_duration = (time.time() - key_start) * 1000
                    self.progress_callback.on_key_processed(key_str, '', 'skipped', key_duration)
                    continue

                try:
                    status = await self._migrate_key(key_str, new_key)
                    if status == 'migrated':
                        stats['migrated'] += 1
                    else:
                        # 源键在扫描与读取之间消失（并发过期/删除）：计 skipped，
                        # 不能计入 migrated
                        stats['skipped'] += 1
                except Exception as e:
                    stats['failed'] += 1
                    logger.error("key_migration_failed", old_key=key_str, error=str(e))
                    status = 'failed'

                key_duration = (time.time() - key_start) * 1000
                perf_stats.record_key_time(key_duration)
                self.progress_callback.on_key_processed(key_str, new_key, status, key_duration)

                stats['scanned'] += 1
                processed_in_second += 1

            batch_duration = (time.time() - batch_start) * 1000
            perf_stats.record_batch_time(batch_duration)
            self.progress_callback.on_batch_complete(batch_num, stats.copy())

            await self._save_progress(stats)

        stats['end_time'] = datetime.now().isoformat()
        stats['performance'] = perf_stats.get_summary()
        self.progress_callback.on_complete(stats)

        logger.info("Migration completed", performance=stats['performance'])
        return stats

    async def _migrate_key(self, old_key: str, new_key: str) -> str:
        """迁移单个键。

        Returns:
            ``'migrated'``（已写入新键）或 ``'skipped'``（源键已不存在）。
        """
        ttl = await self.redis_client.ttl(old_key)
        data = await self.redis_client.get(old_key)

        if data is None:
            return 'skipped'

        if ttl > 0:
            await self.redis_client.setex(new_key, ttl, data)
        else:
            await self.redis_client.set(new_key, data)
        return 'migrated'

    async def _save_progress(self, stats: dict[str, Any]) -> None:
        """保存迁移进度"""
        with open(self.progress_file, 'w') as f:
            json.dump(stats, f, indent=2)

    async def verify(self) -> dict[str, Any]:
        """验证迁移结果"""
        perf_stats = PerformanceStats()

        stats = {
            'checked': 0,
            'matched': 0,
            'mismatched': 0,
            'missing_new': 0,
            'missing_old': 0,
        }

        cursor = 0
        logger.info("Starting verification")

        while cursor != 0 or stats['checked'] == 0:
            cursor, keys = await self.redis_client.scan(
                cursor=cursor,
                count=self.batch_size,
            )

            for key in keys:
                key_start = time.time()
                key_str = key.decode() if isinstance(key, bytes) else key

                new_key = self.mapper.get_new_key(key_str)
                if not new_key:
                    continue

                old_data = await self.redis_client.get(key_str)
                new_data = await self.redis_client.get(new_key)

                stats['checked'] += 1

                if old_data is None and new_data is None:
                    stats['matched'] += 1
                elif old_data is None:
                    stats['missing_old'] += 1
                elif new_data is None:
                    stats['missing_new'] += 1
                elif old_data == new_data:
                    stats['matched'] += 1
                else:
                    stats['mismatched'] += 1

                key_duration = (time.time() - key_start) * 1000
                perf_stats.record_key_time(key_duration)

        stats['performance'] = perf_stats.get_summary()
        logger.info("Verification completed", performance=stats['performance'])
        return stats

    async def cleanup_old_keys(self) -> dict[str, Any]:
        """清理旧键"""
        perf_stats = PerformanceStats()

        stats = {
            'deleted': 0,
            'failed': 0,
        }

        cursor = 0
        logger.info("Starting old key cleanup")

        while cursor != 0 or stats['deleted'] == 0:
            cursor, keys = await self.redis_client.scan(
                cursor=cursor,
                count=self.batch_size,
            )

            for key in keys:
                key_start = time.time()
                key_str = key.decode() if isinstance(key, bytes) else key

                new_key = self.mapper.get_new_key(key_str)
                if new_key:
                    try:
                        await self.redis_client.delete(key_str)
                        stats['deleted'] += 1
                        status = 'deleted'
                    except Exception as e:
                        stats['failed'] += 1
                        logger.error("key_cleanup_failed", key=key_str, error=str(e))
                        status = 'failed'

                    key_duration = (time.time() - key_start) * 1000
                    perf_stats.record_key_time(key_duration)

        stats['performance'] = perf_stats.get_summary()
        logger.info("Cleanup completed", performance=stats['performance'])
        return stats
