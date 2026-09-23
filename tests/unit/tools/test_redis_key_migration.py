import pytest
from unittest.mock import AsyncMock, MagicMock
import json
from datetime import datetime

from app.tools.redis_key_migration.mapper import KeyMapper
from app.tools.redis_key_migration.migrator import RedisKeyMigrator


class TestKeyMapper:
    """键名映射器测试"""

    @pytest.fixture
    def mappings(self):
        return [
            {
                'old_pattern': 'refresh_token:*',
                'new_pattern': ':auth:token:*',
                'ttl_preserve': True
            },
            {
                'old_pattern': 'user_tokens:*',
                'new_pattern': ':auth:user:tokens:*',
                'ttl_preserve': True
            },
            {
                'old_pattern': 'consent:withdrawal',
                'new_pattern': ':consent:withdrawal:stream',
                'is_stream': True
            }
        ]

    def test_get_new_key_basic(self, mappings):
        mapper = KeyMapper(mappings)
        result = mapper.get_new_key('refresh_token:abc123')
        assert result == ':auth:token:abc123'

    def test_get_new_key_user_tokens(self, mappings):
        mapper = KeyMapper(mappings)
        result = mapper.get_new_key('user_tokens:user123')
        assert result == ':auth:user:tokens:user123'

    def test_get_new_key_exact_match(self, mappings):
        mapper = KeyMapper(mappings)
        result = mapper.get_new_key('consent:withdrawal')
        assert result == ':consent:withdrawal:stream'

    def test_get_new_key_no_match(self, mappings):
        mapper = KeyMapper(mappings)
        result = mapper.get_new_key('unknown:key:123')
        assert result is None

    def test_get_old_key_reverse(self, mappings):
        mapper = KeyMapper(mappings)
        result = mapper.get_old_key(':auth:token:abc123')
        assert result == 'refresh_token:abc123'


class TestRedisKeyMigrator:
    """Redis键迁移执行器测试"""

    @pytest.fixture
    def mock_redis_client(self):
        client = AsyncMock()
        client.scan.return_value = (0, [b'refresh_token:abc123'])
        client.ttl.return_value = 3600
        client.get.return_value = b'{"data": "test"}'
        return client

    @pytest.fixture
    def mappings(self):
        return [
            {
                'old_pattern': 'refresh_token:*',
                'new_pattern': ':auth:token:*',
                'ttl_preserve': True
            }
        ]

    @pytest.fixture
    def migrator(self, mock_redis_client, mappings, tmp_path):
        mapper = KeyMapper(mappings)
        config = {
            'batch_size': 100,
            'rate_limit': 1000,
            # 注意：进度文件必须落在 tmp_path。此前硬编码为仓库根的
            # `.test_progress.json`，与 pytest 进度插件写入的同名文件冲突，
            # 两个写入者争抢同一文件会导致写入阻塞（用例挂死）并污染工作区。
            'progress_file': str(tmp_path / 'migration_progress.json')
        }
        return RedisKeyMigrator(mock_redis_client, mapper, config)

    @pytest.mark.asyncio
    async def test_migrate_single_key(self, migrator, mock_redis_client):
        stats = await migrator.migrate()
        
        assert stats['scanned'] == 1
        assert stats['migrated'] == 1
        assert stats['failed'] == 0
        assert stats['skipped'] == 0
        
        mock_redis_client.setex.assert_called_once_with(
            ':auth:token:abc123', 3600, b'{"data": "test"}'
        )

    @pytest.mark.asyncio
    async def test_migrate_empty_redis(self, migrator, mock_redis_client):
        mock_redis_client.scan.return_value = (0, [])
        
        stats = await migrator.migrate()
        
        assert stats['scanned'] == 0
        assert stats['migrated'] == 0

    @pytest.mark.asyncio
    async def test_migrate_key_not_found(self, migrator, mock_redis_client):
        mock_redis_client.get.return_value = None
        
        stats = await migrator.migrate()
        
        assert stats['migrated'] == 0
        mock_redis_client.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_migration(self, migrator, mock_redis_client):
        mock_redis_client.get.side_effect = [b'{"data": "test"}', b'{"data": "test"}']
        
        stats = await migrator.verify()
        
        assert stats['checked'] == 1
        assert stats['matched'] == 1
        assert stats['mismatched'] == 0

    @pytest.mark.asyncio
    async def test_cleanup_old_keys(self, migrator, mock_redis_client):
        stats = await migrator.cleanup_old_keys()
        
        assert stats['deleted'] == 1
        mock_redis_client.delete.assert_called_once_with('refresh_token:abc123')