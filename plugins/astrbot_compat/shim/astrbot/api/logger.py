"""AstrBot logger shim — delegates to Python's logging module."""

import logging

logger = logging.getLogger("AstrBotCompat.Shim")


def info(msg: str, /):
    logger.info(msg)


def debug(msg: str, /):
    logger.debug(msg)


def warning(msg: str, /):
    logger.warning(msg)


def error(msg: str, /):
    logger.error(msg)


def critical(msg: str, /):
    logger.critical(msg)
