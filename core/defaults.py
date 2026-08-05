"""
默认配置模块。

存放所有默认配置字典和路径常量，与核心加载逻辑分离。
"""

import copy
from pathlib import Path
from typing import Any

from core.access_control import DEFAULT_ACCESS_RULES

# =========================
# 路径常量（相对于项目根目录）
# =========================

BOT_DATA = Path("BotData")
CONFIG_FILE = BOT_DATA / "config.json"
PLUGIN_CONFIGS_DIR = BOT_DATA / "plugin_configs"
USER_DATA = Path("UserData")
LOGS_DIR = BOT_DATA / "logs"

# =========================
# 默认配置
# =========================

DEFAULT_MAIN_CONFIG: dict[str, Any] = {
    "bot": {
        "name": "HikariBotNeo",
        "superuser_id": "你的QQ号",
        "log_level": "INFO",
        "api_timeout": 120,
    },
    "napcat": {
        "ws_url": "ws://192.168.31.2:54253/",
        "token": "你的NapCat Token",
        "protocol": "websocket",
    },
    "paths": {
        "bot_data": "BotData",
        "user_data": "UserData",
        "logs": "BotData/logs",
        "plugin_configs": "BotData/plugin_configs",
        "temp_media": "/tmp/hikari_bot",
    },
    "features": {
        "pixiv_parser": True,
        "cobalt_parser": True,
    },
    "media": {
        "send_path_prefix": "file://",
    },
}

DEFAULT_PIXIV_CONFIG: dict[str, Any] = {
    "cookie": "",
    "auto_parse": True,
    "max_links_per_message": 20,
    "max_send": 6,
    "max_file_mb": 25,
    "allow_r18": False,
    "send_link_info": True,
    "cache_dir": "/tmp/hikari_bot",
    "cache_ttl_seconds": 600,
    "proxy": "",
    "send_strategy": {
        "prefer_forward_message": True,
        "fallback_to_separate_images": True,
    },
    "permissions": copy.deepcopy(DEFAULT_ACCESS_RULES),
}

DEFAULT_COBALT_CONFIG: dict[str, Any] = {
    "auto_parse": True,
    "max_links_per_message": 20,
    "cobalt_api": "http://192.168.31.2:54257/",
    "api_timeout": 90,
    "max_send": 6,
    "max_file_mb": 200,
    "send_link_info": True,
    "parse_retry_count": 2,
    "parse_retry_delay_seconds": 2.0,
    "cache_dir": "/tmp/hikari_bot",
    "cache_ttl_seconds": 600,
    "api_key": "",
    "instagram_cookie": "",
    "send_strategy": {
        "prefer_forward_message": True,
        "fallback_to_separate_media": True,
    },
    "permissions": copy.deepcopy(DEFAULT_ACCESS_RULES),
}

DEFAULT_YOUTUBE_DOWNLOADER_CONFIG: dict[str, Any] = {
    "enabled": True,
    "auto_parse": True,
    "max_links_per_message": 20,
    "max_file_mb": 1024,
    "max_height": 720,
    "send_link_info": True,
    "download_timeout": 1800,
    "socket_timeout": 30,
    "retries": 5,
    "cache_dir": "/tmp/hikari_bot/youtube_downloader",
    "cache_ttl_seconds": 600,
    "cookiefile": "",
    "format": "",
    "permissions": copy.deepcopy(DEFAULT_ACCESS_RULES),
}

DEFAULT_MEDIA_DETAIL_WEB_CONFIG: dict[str, Any] = {
    "enabled": True,
    "host": "0.0.0.0",
    "port": 53123,
    "max_links_per_request": 8,
    "auto_download": True,
    "token_ttl_seconds": 3600,
    "max_registry_entries": 512,
    "max_remote_proxy_mb": 1024,
    "operation_timeout_seconds": 1800,
    "request_body_limit_bytes": 1048576,
}

DEFAULT_MEDIA_PARSER_CONFIG: dict[str, Any] = {
    "enabled": True,
    "api_timeout": 120,
    "max_links_per_message": 20,
    "parse_retry_count": 4,
    "parse_retry_delay_seconds": 3.0,
    "parse_retry_403_delay_base": 5.0,
    "parse_queue": {
        "enabled": True,
        "max_size": 100,
        "max_concurrent": 2,
        "delay_seconds": 0.8,
    },
    "max_send": 80,
    "trigger": {
        "auto_parse": True,
        "keywords": ["视频解析", "解析视频", "媒体解析"],
        "reply_trigger": False,
    },
    "parsers": {
        "bilibili": "全部发送",
        "douyin": "全部发送",
        "tiktok": "全部发送",
        "kuaishou": "全部发送",
        "weibo": "全部发送",
        "xiaohongshu": "全部发送",
        "xianyu": "全部发送",
        "toutiao": "全部发送",
        "xiaoheihe": "全部发送",
        "twitter": "全部发送",
    },
    "message": {
        "packing": {
            "mode": "按条件打包",
            "thresholds": {
                "image_count": 3,
                "video_count": 2,
                "node_count": 5,
            },
        },
        "media_display": {
            "video_cover_only": False,
        },
        "text_metadata": {
            "quote_user_message": False,
            "max_desc_chars": 600,
        },
        "simplified_output": [],
        "opening": {
            "enable": False,
            "content": "媒体解析中...",
        },
        "hot_comments": {
            "count": 0,
            "bilibili": True,
            "weibo": True,
            "xiaohongshu": True,
        },
    },
    "permissions": {},
    "download": {
        "max_video_size_mb": 1000,
        "large_video_threshold_mb": 100,
        "cache_dir": "/tmp/hikari_bot/media_parser",
        "cache_ttl_seconds": 600,
        "max_concurrent": 5,
    },
    "parse_rate_limit": {
        "same_link": {
            "max_count": 0,
            "window_seconds": 3600,
        },
        "same_user": {
            "max_count": 0,
            "window_seconds": 3600,
        },
    },
    "proxy": {
        "address": "",
        "tiktok": False,
        "xiaoheihe_video": True,
        "twitter": {
            "parse": False,
            "image": True,
            "video": True,
        },
    },
    "bilibili_enhanced": {
        "use_cookie": False,
        "cookie": "",
        "max_quality": "不限制",
        "admin_assist": {
            "enable": False,
            "reply_timeout_minutes": 1440,
            "request_cooldown_minutes": 1440,
        },
    },
    "media_relay": {
        "enable": False,
        "callback_url": "",
        "ttl": 300,
    },
    "translation": {
        "enable": False,
    },
    "admin": {
        "clean_cache_keyword": "清理媒体",
        "debug": False,
    },
    "send_strategy": {
        "prefer_forward_message": True,
        "fallback_to_separate_media": True,
        "include_text_in_forward": True,
        "forward_timeout_seconds": 90,
    },
}

DEFAULT_NETEASE_CONFIG: dict[str, Any] = {
    "auto_parse": True,
    "max_links_per_message": 5,
    "parse_queue": {
        "enabled": True,
        "max_size": 100,
        "max_concurrent": 2,
        "delay_seconds": 0.8,
    },
    "parse_retry_count": 2,
    "parse_retry_delay_seconds": 2.0,
    "api_base_url": "http://127.0.0.1:3000",
    "api_timeout": 30,
    "real_ip": "",
    "high_quality": True,
    "quality_switch": True,
    "manual_parse": {
        "enable": False,
        "groups": [],
    },
    "cookie": "",
    "max_file_mb": 200,
    "send_link_info": True,
    "send_strategy": {
        "multi_file_mode": "zip",
        "zip_max_files": 50,
        "zip_max_mb": 200,
    },
    "cache_dir": "/tmp/hikari_bot/netease",
    "cache_ttl_seconds": 600,
    "permissions": copy.deepcopy(DEFAULT_ACCESS_RULES),
}

DEFAULT_SOUNDCLOUD_PARSER_CONFIG: dict[str, Any] = {
    "enabled": True,
    "auto_parse": True,
    "max_links_per_message": 3,
    "max_file_mb": 1024,
    "send_link_info": True,
    "send_strategy": "upload",  # "record" = MessageSegment.record(), "upload" = upload_group_file
    "download_timeout": 600,
    "socket_timeout": 30,
    "retries": 3,
    "cache_dir": "/tmp/hikari_bot/soundcloud",
    "cache_ttl_seconds": 600,
    "preferred_codec": "best",  # "best" = 原始格式不转码, 或 m4a/mp3/opus/flac
    "cookiefile": "",
    "permissions": copy.deepcopy(DEFAULT_ACCESS_RULES),
}

DEFAULT_STICKER_CONFIG: dict[str, Any] = {
    "triggers": {
        "capoo_gif": ["capoo", "猫猫虫"],
    },
}
