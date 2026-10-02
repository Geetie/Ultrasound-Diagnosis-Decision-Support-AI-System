import argparse
import asyncio
import json
import logging
import sys

from app.config.redis import RedisPool
from app.tools.redis_key_migration.config import get_mappings, get_migration_config, load_config
from app.tools.redis_key_migration.mapper import KeyMapper
from app.tools.redis_key_migration.migrator import ProgressCallback, RedisKeyMigrator

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)-8s] %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


class DetailedProgressCallback(ProgressCallback):
    """详细的进度回调实现 - 显示进度条和详细统计"""

    def __init__(self, log_interval: int = 10):
        self.log_interval = log_interval
        self.processed_count = 0
        self.last_update = 0

    def _get_progress_bar(self, current: int, bar_length: int = 40) -> str:
        """生成进度条"""
        if current == 0:
            return '[' + ' ' * bar_length + ']'
        filled = int(current / max(current, 100) * bar_length)
        return '[' + '=' * filled + ' ' * (bar_length - filled) + ']'

    def _clear_line(self):
        """清除当前行"""
        sys.stdout.write('\r')
        sys.stdout.flush()

    def on_batch_start(self, batch_num: int, total_batches: int | None = None):
        logger.info(f"Starting batch #{batch_num}" + (f"/{total_batches}" if total_batches else ""))

    def on_key_processed(self, key: str, new_key: str, status: str, duration_ms: float):
        self.processed_count += 1

        if self.processed_count % self.log_interval == 0:
            self._clear_line()
            progress_bar = self._get_progress_bar(self.processed_count)
            sys.stdout.write(f"{progress_bar} Processed {self.processed_count} keys...")
            sys.stdout.flush()

    def on_batch_complete(self, batch_num: int, stats: dict):
        self._clear_line()
        print(f"✓ Batch #{batch_num} complete: "
              f"scanned={stats['scanned']}, "
              f"migrated={stats['migrated']}, "
              f"failed={stats['failed']}, "
              f"skipped={stats['skipped']}")

    def on_complete(self, final_stats: dict):
        self._clear_line()
        print("\n" + "=" * 60)
        print("MIGRATION COMPLETED")
        print("=" * 60)
        self._print_summary(final_stats)
        print("=" * 60 + "\n")

    def _print_summary(self, stats: dict):
        """打印摘要信息"""
        print(f"• Scanned:       {stats.get('scanned', 0)}")
        print(f"• Migrated:      {stats.get('migrated', 0)}")
        print(f"• Failed:        {stats.get('failed', 0)}")
        print(f"• Skipped:       {stats.get('skipped', 0)}")

        if 'performance' in stats:
            perf = stats['performance']
            print("\nPerformance:")
            print(f"• Elapsed:       {perf.get('elapsed_seconds', 0):.2f}s")
            print(f"• Keys/sec:      {perf.get('keys_per_second', 0):.1f}")
            print(f"• Avg/key:       {perf.get('avg_key_time_ms', 0):.2f}ms")


def _format_stats_output(stats: dict) -> str:
    """格式化统计信息输出"""
    output = []
    output.append(json.dumps(stats, indent=2, ensure_ascii=False))

    if 'performance' in stats:
        perf = stats['performance']
        output.append("\n" + "=" * 40)
        output.append("PERFORMANCE SUMMARY")
        output.append("=" * 40)
        output.append(f"Elapsed time:    {perf.get('elapsed_seconds', 0):.2f} seconds")
        output.append(f"Total keys:      {perf.get('total_keys', 0)}")
        output.append(f"Throughput:      {perf.get('keys_per_second', 0):.2f} keys/sec")
        output.append(f"Avg key time:    {perf.get('avg_key_time_ms', 0):.3f} ms")
        output.append(f"Avg batch time:  {perf.get('avg_batch_time_ms', 0):.3f} ms")

    return "\n".join(output)


async def run_migrate(args):
    """执行迁移命令"""
    config = load_config(args.config)
    mappings = get_mappings(config)
    migration_config = get_migration_config(config)

    mapper = KeyMapper(mappings)
    redis_client = await RedisPool.get_client()

    progress_cb = DetailedProgressCallback(log_interval=1 if args.verbose else 50)
    migrator = RedisKeyMigrator(redis_client, mapper, migration_config, progress_cb)

    logger.info("Starting key migration")
    stats = await migrator.migrate()
    print(_format_stats_output(stats))


async def run_verify(args):
    """执行验证命令"""
    config = load_config(args.config)
    mappings = get_mappings(config)
    migration_config = get_migration_config(config)

    mapper = KeyMapper(mappings)
    redis_client = await RedisPool.get_client()

    progress_cb = DetailedProgressCallback(log_interval=1 if args.verbose else 50)
    migrator = RedisKeyMigrator(redis_client, mapper, migration_config, progress_cb)

    logger.info("Starting key verification")
    stats = await migrator.verify()

    print("\n" + "=" * 60)
    print("VERIFICATION RESULTS")
    print("=" * 60)
    print(f"• Checked:       {stats.get('checked', 0)}")
    print(f"• Matched:       {stats.get('matched', 0)}")
    print(f"• Mismatched:    {stats.get('mismatched', 0)}")
    print(f"• Missing new:   {stats.get('missing_new', 0)}")
    print(f"• Missing old:   {stats.get('missing_old', 0)}")
    print(_format_stats_output(stats))


async def run_cleanup(args):
    """执行清理命令"""
    config = load_config(args.config)
    mappings = get_mappings(config)
    migration_config = get_migration_config(config)

    mapper = KeyMapper(mappings)
    redis_client = await RedisPool.get_client()

    progress_cb = DetailedProgressCallback(log_interval=1 if args.verbose else 50)
    migrator = RedisKeyMigrator(redis_client, mapper, migration_config, progress_cb)

    logger.info("Starting old key cleanup")
    stats = await migrator.cleanup_old_keys()

    print("\n" + "=" * 60)
    print("CLEANUP RESULTS")
    print("=" * 60)
    print(f"• Deleted:       {stats.get('deleted', 0)}")
    print(f"• Failed:        {stats.get('failed', 0)}")
    print(_format_stats_output(stats))


async def run_list_keys(args):
    """列出所有匹配的键"""
    config = load_config(args.config)
    mappings = get_mappings(config)

    mapper = KeyMapper(mappings)
    redis_client = await RedisPool.get_client()

    cursor = 0
    matched_keys = []

    print("Scanning keys...")

    while cursor != 0 or len(matched_keys) == 0:
        cursor, keys = await redis_client.scan(
            cursor=cursor,
            count=args.count,
        )

        for key in keys:
            key_str = key.decode() if isinstance(key, bytes) else key
            new_key = mapper.get_new_key(key_str)
            if new_key:
                matched_keys.append({
                    'old_key': key_str,
                    'new_key': new_key
                })

    if matched_keys:
        print(f"\nFound {len(matched_keys)} keys to migrate:\n")
        for item in matched_keys:
            print(f"  {item['old_key']:40} → {item['new_key']}")
        print(f"\nTotal: {len(matched_keys)} keys\n")
    else:
        print("\nNo keys found for migration.\n")


def main():
    parser = argparse.ArgumentParser(description='Redis Key Migration Tool')
    parser.add_argument('-v', '--verbose', action='store_true', help='Show detailed progress')

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    migrate_parser = subparsers.add_parser('migrate', help='Migrate keys from old to new format')
    migrate_parser.add_argument('-c', '--config', help='Path to config file')

    verify_parser = subparsers.add_parser('verify', help='Verify migration results')
    verify_parser.add_argument('-c', '--config', help='Path to config file')

    cleanup_parser = subparsers.add_parser('cleanup', help='Clean up old keys')
    cleanup_parser.add_argument('-c', '--config', help='Path to config file')

    list_parser = subparsers.add_parser('list', help='List keys to migrate')
    list_parser.add_argument('-c', '--config', help='Path to config file')
    list_parser.add_argument('-n', '--count', type=int, default=100, help='Number of keys to scan per batch')

    args = parser.parse_args()

    if args.command == 'migrate':
        asyncio.run(run_migrate(args))
    elif args.command == 'verify':
        asyncio.run(run_verify(args))
    elif args.command == 'cleanup':
        asyncio.run(run_cleanup(args))
    elif args.command == 'list':
        asyncio.run(run_list_keys(args))
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
