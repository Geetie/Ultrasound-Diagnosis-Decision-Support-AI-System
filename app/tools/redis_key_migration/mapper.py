from typing import Optional, List, Dict, Any
import re
import json
import asyncio
import time
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class KeyMapper:
    """键名映射器 - 负责新旧键名转换"""

    def __init__(self, mappings: List[Dict[str, Any]]):
        self.mappings = mappings

    def get_new_key(self, old_key: str) -> Optional[str]:
        """根据旧键名获取新键名"""
        for mapping in self.mappings:
            pattern = mapping['old_pattern']
            if self._matches_pattern(old_key, pattern):
                return self._transform_key(old_key, mapping)
        return None

    def _matches_pattern(self, key: str, pattern: str) -> bool:
        """检查键是否匹配模式"""
        pattern_re = pattern.replace('*', '.*')
        return re.match(pattern_re, key) is not None

    def _transform_key(self, old_key: str, mapping: dict) -> str:
        """转换旧键名为新键名"""
        old_pattern = mapping['old_pattern']
        new_pattern = mapping['new_pattern']

        if '*' in old_pattern:
            old_prefix = old_pattern[:old_pattern.index('*')]
            new_prefix = new_pattern[:new_pattern.index('*')]
            suffix = old_key[len(old_prefix):]
            return new_pattern.replace('*', suffix)
        return new_pattern

    def get_old_key(self, new_key: str) -> Optional[str]:
        """根据新键名获取旧键名（反向映射）"""
        for mapping in self.mappings:
            pattern = mapping['new_pattern']
            if self._matches_pattern(new_key, pattern):
                return self._transform_key(new_key, {
                    'old_pattern': mapping['new_pattern'],
                    'new_pattern': mapping['old_pattern']
                })
        return None