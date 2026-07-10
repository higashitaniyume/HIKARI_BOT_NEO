from __future__ import annotations

from pathlib import Path

from plugins import voice_library
from plugins.media_transcoder import STICKER_INPUT_EXTS

ALLOWED_EXTS = STICKER_INPUT_EXTS
VOICE_ALLOWED_EXTS = voice_library.MEDIA_EXTS
MAX_UPLOAD_FILES = 99
MAX_VOICE_UPLOAD_FILES = 20
PACKAGE_ROOT = Path(__file__).parent
_TEMPLATE_PATH = PACKAGE_ROOT / "templates" / "index.html"
_STATIC_ROOT = PACKAGE_ROOT / "static"
_COOKIE_NAME = "hikari_sticker_session"
_PLUGIN_CONFIG_DIR = Path("BotData/plugin_configs")
_LOG_DIR = Path("BotData/logs")
_MAX_CONFIG_EDIT_BYTES = 2 * 1024 * 1024
_MAX_LOG_TAIL_BYTES = 256 * 1024
_ACCESS_RULE_PLUGINS = {
    "media_parser.json/bilibili": "B站解析",
    "media_parser.json/douyin": "抖音解析",
    "media_parser.json/tiktok": "TikTok 解析",
    "media_parser.json/kuaishou": "快手解析",
    "media_parser.json/weibo": "微博解析",
    "media_parser.json/xiaohongshu": "小红书解析",
    "media_parser.json/xianyu": "闲鱼解析",
    "media_parser.json/toutiao": "今日头条解析",
    "media_parser.json/xiaoheihe": "小黑盒解析",
    "media_parser.json/twitter": "Twitter/X 解析",
    "pixiv_parser.json": "Pixiv 解析",
    "cobalt_parser.json": "Instagram / Facebook 解析",
    "youtube_downloader.json": "YouTube 下载",
    "aiagent.json": "AI Agent 聊天",
}

