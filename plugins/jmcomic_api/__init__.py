import asyncio
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Optional

import jmcomic
from jmcomic import Feature
from nonebot import on_regex
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, PrivateMessageEvent
from nonebot.params import RegexGroup

from core.bot_messages import get_message as msg

from .config import get_config

try:
    from jmcomic import DirRule
except ImportError:
    from jmcomic.jm_option import DirRule


logger = logging.getLogger("HikariBot.JMComicAPI")

JM_ID_RE = re.compile(r"(?:JM)?(\d{3,})", re.IGNORECASE)

# 配置文件仍然放在 BotData 中
BASE_DIR = Path("BotData/jmcomic")
OPTION_PATH = BASE_DIR / "option.yml"

# 只有下载漫画和合成 PDF 使用临时目录
# Linux/macOS: /tmp/hikari_bot
# Windows: 当前用户 TEMP/hikari_bot
if os.name == "nt":
    TEMP_ROOT = Path(tempfile.gettempdir()) / "hikari_bot"
else:
    TEMP_ROOT = Path("/tmp/hikari_bot")

JM_TEMP_DIR = TEMP_ROOT / "jmcomic"
DOWNLOAD_DIR = JM_TEMP_DIR / "download"
PDF_DIR = JM_TEMP_DIR / "pdf"

# 同一时间只允许一个下载任务，避免并发打爆磁盘/网络
_download_sem = asyncio.Semaphore(1)

# PDF 合成成功后是否删除原始图片
DELETE_ORIGINAL_IMAGES_AFTER_PDF = True



async def _jm_scope_rule(event: MessageEvent) -> bool:
    if isinstance(event, PrivateMessageEvent):
        return True
    if isinstance(event, GroupMessageEvent):
        return bool(get_config().get("allow_group", False))
    return False


plain_jm_download = on_regex(
    r"(?i)^\s*jm\s+(?:JM)?(\d{3,})\s*$",
    priority=10,
    block=True,
    rule=_jm_scope_rule,
)


def ensure_temp_dirs() -> None:
    """只创建下载和 PDF 所需临时目录。"""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)


def extract_jm_id(text: str) -> Optional[str]:
    match = JM_ID_RE.search(text)
    return match.group(1) if match else None


def load_option():
    """
    从 BotData/jmcomic/option.yml 读取配置，
    但强制把下载根目录改到临时目录。
    """
    if not OPTION_PATH.exists():
        raise FileNotFoundError(f"找不到配置文件: {OPTION_PATH}")

    ensure_temp_dirs()

    logger.info(f"加载 JMComic 配置文件: {OPTION_PATH.resolve()}")

    option = jmcomic.create_option_by_file(str(OPTION_PATH))

    old_dir_rule = getattr(option, "dir_rule", None)

    if old_dir_rule is not None:
        old_rule_dsl = getattr(old_dir_rule, "rule_dsl", None) or "Bd / JM{Aid}-{Atitle}"
        old_normalize_zh = getattr(old_dir_rule, "normalize_zh", None)
    else:
        old_rule_dsl = "Bd / JM{Aid}-{Atitle}"
        old_normalize_zh = None

    # 保留 option.yml 里的 rule / normalize_zh，只覆盖 base_dir
    option.dir_rule = DirRule(
        rule=old_rule_dsl,
        base_dir=str(DOWNLOAD_DIR),
        normalize_zh=old_normalize_zh,
    )

    logger.info(f"JMComic 下载目录已覆盖为: {DOWNLOAD_DIR.resolve()}")
    logger.info(f"JMComic PDF 输出目录: {PDF_DIR.resolve()}")
    logger.info(f"JMComic dir_rule.rule: {old_rule_dsl}")

    return option


def find_pdf_file(album_id: str, started_at: float) -> Path:
    """
    找到本次生成的 PDF。

    正常情况下 filename_rule='Aid' 会生成:
    PDF_DIR / f'{album_id}.pdf'

    这里做一层兜底，防止 jmcomic 版本变化或文件名规则变化。
    """
    expected_pdf = PDF_DIR / f"{album_id}.pdf"

    if expected_pdf.exists():
        return expected_pdf

    candidates: list[Path] = []

    for path in PDF_DIR.glob("*.pdf"):
        try:
            stat = path.stat()
        except OSError:
            continue

        if stat.st_mtime >= started_at - 2:
            candidates.append(path)

    if candidates:
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]

    raise FileNotFoundError(f"未找到生成的 PDF 文件，PDF 目录: {PDF_DIR.resolve()}")


def assert_path_under_temp(path: Path) -> None:
    """
    确保要给 NapCat 读取的文件位于临时目录下。
    """
    abs_path = path.resolve()
    temp_root = TEMP_ROOT.resolve()

    try:
        abs_path.relative_to(temp_root)
    except ValueError:
        raise RuntimeError(f"文件不在 NapCat 可读取目录中: {abs_path}")


async def upload_pdf_if_possible(bot: Bot, event: MessageEvent, pdf_path: Path) -> bool:
    """通过 OneBot/NapCat 上传 PDF 到当前会话。"""
    pdf_path = pdf_path.resolve()
    assert_path_under_temp(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

    file_name = pdf_path.name
    if isinstance(event, GroupMessageEvent):
        logger.info(f"开始上传群文件: group_id={event.group_id}, file={pdf_path}")
        await bot.call_api(
            "upload_group_file",
            group_id=event.group_id,
            file=str(pdf_path),
            name=file_name,
        )
        logger.info(f"群文件上传完成: {file_name}")
        return True

    if isinstance(event, PrivateMessageEvent):
        logger.info(f"开始上传私聊文件: user_id={event.user_id}, file={pdf_path}")
        await bot.call_api(
            "upload_private_file",
            user_id=event.user_id,
            file=str(pdf_path),
            name=file_name,
        )
        logger.info(f"私聊文件上传完成: {file_name}")
        return True

    raise RuntimeError(f"不支持的事件类型，无法上传 PDF: {type(event).__name__}")




@plain_jm_download.handle()
async def handle_plain_jm_download(
    bot: Bot,
    event: MessageEvent,
    matched: tuple[str, ...] = RegexGroup(),
):
    jm_id = matched[0]
    raw = event.get_plaintext().strip()
    await _download_and_send_pdf(bot, event, jm_id, raw)


async def _download_and_send_pdf(
    bot: Bot,
    event: MessageEvent,
    jm_id: str,
    raw: str,
) -> None:
    logger.info(f"收到 JM 下载请求: user={event.user_id}, raw={raw!r}, jm_id={jm_id}")

    await bot.send(event, msg("jmcomic.start", jm_id=jm_id))

    result_message = ""

    async with _download_sem:
        try:
            ensure_temp_dirs()

            started_at = time.time()
            option = load_option()

            logger.info(f"开始下载 JM{jm_id}")
            logger.info(f"漫画下载目录: {DOWNLOAD_DIR.resolve()}")
            logger.info(f"PDF 输出目录: {PDF_DIR.resolve()}")

            # jmcomic 的 PDF 导出 Feature 是同步下载 API。
            # 在 NoneBot 异步 handler 里用 asyncio.to_thread 包起来，避免阻塞事件循环。
            album, downloader = await asyncio.to_thread(
                jmcomic.download_album,
                jm_id,
                option,
                extra=Feature.export_pdf(
                    pdf_dir=str(PDF_DIR),
                    filename_rule="Aid",
                    delete_original_file=DELETE_ORIGINAL_IMAGES_AFTER_PDF,
                ),
            )

            album_id = str(album.id)
            pdf_path = find_pdf_file(album_id, started_at)

            logger.info(f"下载/转换 PDF 完成: JM{album_id}")
            logger.info(f"标题: {album.name}")
            logger.info(f"PDF 文件: {pdf_path.resolve()}")

            upload_ok = False
            upload_error: Optional[Exception] = None

            try:
                upload_ok = await upload_pdf_if_possible(bot, event, pdf_path)
            except Exception as e:
                upload_error = e
                logger.exception(f"PDF 上传失败: {pdf_path.resolve()}")

            if upload_ok:
                result_message = msg("jmcomic.done", album_id=album_id)
            else:
                result_message = msg("jmcomic.upload_failed")

                if upload_error is not None:
                    logger.error(f"上传错误：{type(upload_error).__name__}: {upload_error}")

        except Exception as e:
            logger.exception(f"下载/转换 PDF 失败：JM{jm_id}")
            result_message = msg("jmcomic.failed")

    # 不在 try 里面调用 finish，避免 NoneBot 的 FinishedException 被误判成下载失败。
    await bot.send(event, result_message)
    return
