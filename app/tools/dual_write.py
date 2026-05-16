from typing import Callable, Any, Tuple
import asyncio
import logging

logger = logging.getLogger(__name__)


class DualWriteManager:
    """双写管理器 - 管理迁移阶段的双写操作"""

    def __init__(self, enabled: bool = False):
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self):
        """启用双写模式"""
        self._enabled = True
        logger.info("dual_write_mode_enabled")

    def disable(self):
        """禁用双写模式"""
        self._enabled = False
        logger.info("dual_write_mode_disabled")


dual_write_manager = DualWriteManager()


def dual_write(old_key_func: Callable[..., str], new_key_func: Callable[..., str]):
    """
    双写装饰器 - 在迁移阶段同时写入新旧键
    
    :param old_key_func: 获取旧键名的函数
    :param new_key_func: 获取新键名的函数
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        async def async_wrapper(*args, **kwargs) -> Any:
            old_key = old_key_func(*args, **kwargs)
            new_key = new_key_func(*args, **kwargs)
            
            result = await func(*args, **kwargs)
            
            if dual_write_manager.enabled and old_key != new_key:
                await _sync_to_old_key(func, args, kwargs, old_key, new_key)
            
            return result
        
        def sync_wrapper(*args, **kwargs) -> Any:
            old_key = old_key_func(*args, **kwargs)
            new_key = new_key_func(*args, **kwargs)
            
            result = func(*args, **kwargs)
            
            if dual_write_manager.enabled and old_key != new_key:
                _sync_to_old_key_sync(func, args, kwargs, old_key, new_key)
            
            return result
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


async def _sync_to_old_key(
    func: Callable[..., Any],
    args: Tuple[Any, ...],
    kwargs: dict,
    old_key: str,
    new_key: str
) -> None:
    """异步同步数据到旧键"""
    try:
        redis_client = None
        for arg in args:
            if hasattr(arg, '_redis_client'):
                redis_client = arg._redis_client
                break
        
        if redis_client:
            ttl = await redis_client.ttl(new_key)
            data = await redis_client.get(new_key)
            
            if data is not None:
                if ttl > 0:
                    await redis_client.setex(old_key, ttl, data)
                else:
                    await redis_client.set(old_key, data)
                logger.debug("dual_write_sync_completed", old_key=old_key, new_key=new_key)
    except Exception as e:
        logger.error("dual_write_sync_failed", old_key=old_key, error=str(e))


def _sync_to_old_key_sync(
    func: Callable[..., Any],
    args: Tuple[Any, ...],
    kwargs: dict,
    old_key: str,
    new_key: str
) -> None:
    """同步同步数据到旧键"""
    try:
        redis_client = None
        for arg in args:
            if hasattr(arg, '_redis_client'):
                redis_client = arg._redis_client
                break
        
        if redis_client:
            ttl = redis_client.ttl(new_key)
            data = redis_client.get(new_key)
            
            if data is not None:
                if ttl > 0:
                    redis_client.setex(old_key, ttl, data)
                else:
                    redis_client.set(old_key, data)
                logger.debug("dual_write_sync_completed", old_key=old_key, new_key=new_key)
    except Exception as e:
        logger.error("dual_write_sync_failed", old_key=old_key, error=str(e))