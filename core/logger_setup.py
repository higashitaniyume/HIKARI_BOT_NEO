"""
日志初始化模块。

负责：
1. 初始化日志系统
2. 日志输出到控制台
3. 日志输出到文件
4. 每次启动生成新的日志文件 (BotData/logs/YYYY-MM-DD_HH-MM-SS.log)
"""

import logging
import sys
from datetime import datetime
from logging import Handler
from pathlib import Path
from typing import Any


def setup_logging(config: dict[str, Any]) -> Path:
    """
    初始化全局日志系统。

    日志同时输出到：
    1. 控制台（stdout）
    2. 文件（BotData/logs/<timestamp>.log）
    """
    paths = config.get("paths", {})
    log_dir = Path(paths.get("logs", "BotData/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    log_level_str = str(config.get("bot", {}).get("log_level", "INFO")).upper()
    log_level = getattr(logging, log_level_str, None)
    if not isinstance(log_level, int):
        log_level = logging.INFO
        log_level_str = "INFO"

    # 日志文件名包含启动时间
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = log_dir / f"{timestamp}.log"

    # 日志格式
    console_fmt = logging.Formatter(
        "[%(asctime)s.%(msecs)03d] [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_fmt = logging.Formatter(
        "[%(asctime)s.%(msecs)03d] [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 根 logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # 清除已有的 handlers（避免重复）
    root_logger.handlers.clear()

    # 控制台 handler
    console_handler: Handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(console_fmt)
    root_logger.addHandler(console_handler)

    # 文件 handler
    file_handler: Handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)  # 文件记录所有级别
    file_handler.setFormatter(file_fmt)
    root_logger.addHandler(file_handler)

    logging.captureWarnings(True)

    # 抑制过于啰嗦的第三方库日志
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    logger = logging.getLogger("HikariBot")
    logger.info("日志系统初始化完成")
    logger.info("控制台日志级别: %s", log_level_str)
    logger.info("文件日志级别: DEBUG")
    logger.info("日志文件: %s", log_file)
    return log_file
