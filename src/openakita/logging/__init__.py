"""
OpenAkita 日志系统

功能:
- 日志文件输出（按天轮转 + 按大小轮转）
- 分离 error.log（只记录 ERROR/CRITICAL）
- 自动清理过期日志
- 支持控制台彩色输出
- 会话级日志缓存（供 AI 查询）
"""

from .cleaner import LogCleaner
from .config import (
    add_console_suppression_filter,
    get_logger,
    quiet_logger_family_on_console,
    remove_named_logger_console_handlers,
    set_console_log_level,
    set_named_logger_level,
    setup_logging,
)
from .session_buffer import SessionLogBuffer, get_session_log_buffer

__all__ = [
    "setup_logging",
    "add_console_suppression_filter",
    "set_console_log_level",
    "quiet_logger_family_on_console",
    "remove_named_logger_console_handlers",
    "set_named_logger_level",
    "get_logger",
    "LogCleaner",
    "SessionLogBuffer",
    "get_session_log_buffer",
]
