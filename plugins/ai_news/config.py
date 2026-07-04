from __future__ import annotations

import logging
from typing import Any

from core.config_loader import load_plugin_config

logger = logging.getLogger("HikariBot.AiNews.Config")

DEFAULT_AI_NEWS_CONFIG: dict[str, Any] = {
    "enabled": True,
    "timeout_seconds": 20,
    "proxy": "",
    "user_agent": "{bot_name} AI News Reader",
    "max_feed_bytes": 2097152,
    "fetch_concurrency": 4,
    "max_items": 10,
    "max_per_source": 3,
    "max_age_hours": 168,
    "summary_max_chars": 180,
    "ai_summary": {
        "enabled": False,
        "translate": True,
        "target_language": "zh-CN",
        "max_input_items": 8,
        "max_input_chars": 9000,
        "max_summary_bullets": 4,
        "max_item_summary_chars": 120,
        "temperature": 0.2,
        "max_tokens": 1600,
        "timeout_seconds": 60,
        "fallback_to_original": True,
    },
    "only_new": True,
    "send_first_run": True,
    "max_state_entries": 5000,
    "cache_dir": "/tmp/hikari_bot/ai_news",
    "render": {
        "image_format": "PNG",
        "jpeg_quality": 86,
    },
    "keyword_boosts": [
        "OpenAI",
        "GPT",
        "Claude",
        "Anthropic",
        "Gemini",
        "DeepMind",
        "Llama",
        "Mistral",
        "agent",
        "agents",
        "multimodal",
        "reasoning",
        "模型",
        "智能体",
        "多模态",
    ],
    "sources": [
        {
            "id": "openai_news",
            "enabled": True,
            "title": "OpenAI News",
            "group": "official",
            "url": "https://openai.com/news/rss.xml",
            "weight": 120,
        },
        {
            "id": "google_ai",
            "enabled": True,
            "title": "Google AI",
            "group": "official",
            "url": "https://blog.google/technology/ai/rss/",
            "weight": 105,
        },
        {
            "id": "huggingface_blog",
            "enabled": True,
            "title": "Hugging Face Blog",
            "group": "research",
            "url": "https://huggingface.co/blog/feed.xml",
            "weight": 90,
        },
        {
            "id": "arxiv_ai",
            "enabled": True,
            "title": "arXiv AI",
            "group": "research",
            "url": "https://rss.arxiv.org/rss/cs.AI+cs.LG+cs.CL+cs.CV",
            "weight": 75,
        },
        {
            "id": "hn_ai",
            "enabled": True,
            "title": "Hacker News AI",
            "group": "community",
            "url": "https://hnrss.org/newest?q=AI+OR+LLM+OR+OpenAI+OR+Claude+OR+Gemini&points=50&count=20",
            "weight": 70,
        },
        {
            "id": "techcrunch_ai",
            "enabled": True,
            "title": "TechCrunch AI",
            "group": "media",
            "url": "https://techcrunch.com/category/artificial-intelligence/feed/",
            "weight": 65,
        },
        {
            "id": "theverge_ai",
            "enabled": True,
            "title": "The Verge AI",
            "group": "media",
            "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
            "weight": 60,
        },
        {
            "id": "venturebeat_ai",
            "enabled": True,
            "title": "VentureBeat AI",
            "group": "media",
            "url": "https://venturebeat.com/category/ai/feed/",
            "weight": 58,
        },
    ],
}

_first_load_done = False


def get_config() -> dict[str, Any]:
    global _first_load_done
    cfg = load_plugin_config("ai_news", DEFAULT_AI_NEWS_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        sources = cfg.get("sources") if isinstance(cfg.get("sources"), list) else []
        enabled_sources = [item for item in sources if isinstance(item, dict) and bool(item.get("enabled", True))]
        logger.info(
            "AI 资讯配置加载完成 -> enabled=%s, sources=%d/%d",
            cfg.get("enabled"),
            len(enabled_sources),
            len(sources),
        )
    return cfg
