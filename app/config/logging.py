"""日志配置模块 - 支持运行时动态调整日志级别"""
import structlog
from structlog import get_logger
from typing import Optional, Dict, Any
from app.config.settings import settings

logger = get_logger()

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

_current_log_level = settings.log_level.upper()


def configure_logging():
    """配置日志系统"""
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )
    logger.info("logging_configured", log_level=_current_log_level)


def get_current_log_level() -> str:
    """获取当前日志级别"""
    return _current_log_level


def set_log_level(level: str) -> Dict[str, Any]:
    """
    动态设置日志级别
    
    Args:
        level: 日志级别，可选值: DEBUG, INFO, WARNING, ERROR, CRITICAL
    
    Returns:
        包含状态和原日志级别的字典
    """
    global _current_log_level
    
    level = level.upper()
    if level not in LOG_LEVELS:
        raise ValueError(f"Invalid log level: {level}. Must be one of {LOG_LEVELS}")
    
    previous_level = _current_log_level
    _current_log_level = level
    
    logger.info(
        "log_level_changed",
        previous_level=previous_level,
        current_level=level
    )
    
    return {
        "status": "success",
        "previous_level": previous_level,
        "current_level": level
    }


def is_log_level_enabled(level: str) -> bool:
    """检查指定日志级别是否启用"""
    level_order = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
    current_index = level_order.get(_current_log_level, 1)
    target_index = level_order.get(level.upper(), 1)
    return target_index >= current_index


def get_log_level_info() -> Dict[str, Any]:
    """获取日志级别信息"""
    return {
        "current_level": _current_log_level,
        "available_levels": LOG_LEVELS,
        "debug_enabled": is_log_level_enabled("DEBUG"),
        "info_enabled": is_log_level_enabled("INFO"),
        "warning_enabled": is_log_level_enabled("WARNING"),
        "error_enabled": is_log_level_enabled("ERROR"),
        "critical_enabled": is_log_level_enabled("CRITICAL")
    }