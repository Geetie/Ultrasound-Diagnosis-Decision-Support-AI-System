import os
from typing import Any

import yaml

DEFAULT_CONFIG = {
    'mappings': [
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
            'old_pattern': 'family_tokens:*',
            'new_pattern': ':auth:family:tokens:*',
            'ttl_preserve': True
        },
        {
            'old_pattern': 'consent:withdrawal',
            'new_pattern': ':consent:withdrawal:stream',
            'is_stream': True
        },
        {
            'old_pattern': 'consent:withdrawal:dead_letter',
            'new_pattern': ':consent:withdrawal:dead_letter',
            'is_stream': True
        },
        {
            'old_pattern': 'consent:withdrawn:session:*',
            'new_pattern': ':consent:withdrawn:session:*',
            'ttl_preserve': True
        },
        {
            'old_pattern': 'consent:withdrawal:notification',
            'new_pattern': ':consent:withdrawal:notification',
            'is_stream': True
        },
        {
            'old_pattern': 'edge:session:*',
            'new_pattern': ':edge:session:*',
            'ttl_preserve': True
        },
        {
            'old_pattern': 'edge:stop_ai_annotation:*',
            'new_pattern': ':edge:stop_ai_annotation:*',
            'ttl_preserve': True
        },
        {
            'old_pattern': 'desens:session:*',
            'new_pattern': ':desens:session:*',
            'ttl_preserve': True
        },
        {
            'old_pattern': 'desens:restore_rate:*',
            'new_pattern': ':desens:restore_rate:*',
            'ttl_preserve': True
        },
        {
            'old_pattern': 'sg:threshold:update',
            'new_pattern': ':sg:threshold:update',
            'is_stream': True
        },
        {
            'old_pattern': 'hsm:keys:*',
            'new_pattern': ':hsm:keys:*',
            'ttl_preserve': True
        },
        {
            'old_pattern': 'hsm:degraded',
            'new_pattern': ':hsm:degraded',
            'ttl_preserve': True
        },
        {
            'old_pattern': 'hsm:degraded_at',
            'new_pattern': ':hsm:degraded_at',
            'ttl_preserve': True
        },
        {
            'old_pattern': 'hsm:degraded_reason',
            'new_pattern': ':hsm:degraded_reason',
            'ttl_preserve': True
        },
        {
            'old_pattern': 'hsm:emergency_signing:*',
            'new_pattern': ':hsm:emergency_signing:*',
            'ttl_preserve': True
        },
        {
            'old_pattern': 'hsm:emergency_pending_verification:*',
            'new_pattern': ':hsm:emergency_pending_verification:*',
            'ttl_preserve': True
        },
    ],
    'migration': {
        'batch_size': 100,
        'rate_limit': 1000,
        'parallel_workers': 4,
        'progress_file': '.migrate_progress.json'
    }
}


def load_config(config_path: str = None) -> dict[str, Any]:
    """加载配置文件"""
    if config_path and os.path.exists(config_path):
        with open(config_path) as f:
            return yaml.safe_load(f)
    return DEFAULT_CONFIG


def save_config(config: dict[str, Any], config_path: str) -> None:
    """保存配置文件"""
    with open(config_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False)


def get_migration_config(config: dict[str, Any]) -> dict[str, Any]:
    """获取迁移配置"""
    return config.get('migration', DEFAULT_CONFIG['migration'])


def get_mappings(config: dict[str, Any]) -> list[dict[str, Any]]:
    """获取键名映射列表"""
    return config.get('mappings', DEFAULT_CONFIG['mappings'])
