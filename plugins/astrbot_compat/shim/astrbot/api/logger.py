"""AstrBot logger shim — delegates to Python's logging module.

真实 AstrBot 的 `from astrbot.api import logger` 拿到的是 logger 实例
（LoggerAdapter）；本 shim 暴露的是这个模块本身，因此除日志方法外还需提供
setLevel/addFilter/getChild 等实例方法，供 vendored 代码和社区插件调用
（如 astrbot_plugin_media_parser 的 config_manager 会调用 logger.setLevel）。
"""

import logging

logger = logging.getLogger("AstrBotCompat.Shim")


def info(msg: str, /, *args, **kwargs):
    logger.info(msg, *args, **kwargs)


def debug(msg: str, /, *args, **kwargs):
    logger.debug(msg, *args, **kwargs)


def warning(msg: str, /, *args, **kwargs):
    logger.warning(msg, *args, **kwargs)


def error(msg: str, /, *args, **kwargs):
    logger.error(msg, *args, **kwargs)


def critical(msg: str, /, *args, **kwargs):
    logger.critical(msg, *args, **kwargs)


def exception(msg: str, /, *args, **kwargs):
    logger.exception(msg, *args, **kwargs)


def setLevel(level):
    logger.setLevel(level)


def addFilter(flt):
    logger.addFilter(flt)


def getChild(suffix: str, /):
    return logger.getChild(suffix)
