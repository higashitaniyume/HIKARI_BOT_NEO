# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Workflow

- After each completed task, commit the changes to the repository. Stage only files that belong to the current task, leave unrelated dirty files alone, and use a descriptive commit message ending with `Co-Authored-By: Claude <noreply@anthropic.com>`.
- Before changing code or docs, inspect the relevant project structure and current implementation. Ground answers in the repo's actual behavior, runtime paths, logs, and README rather than generic assumptions.
- If the working tree is dirty, assume unrelated changes belong to the user or another task. Do not revert them; work around them and keep the current task's diff narrow.
- Validate before finishing and before committing. Run `uv run python -m compileall <changed paths>` at minimum; add targeted tests when behavior changes.

## Commands

```bash
# Install dependencies (uses uv, project uses >=Python 3.10)
uv sync

# Run the bot (local development)
uv run python bot.py

# Run with NoneBot CLI
uv run nb run

# Python syntax check (always run after Python changes)
uv run python -m compileall <changed paths>

# Run all tests
uv run python -m unittest discover -s tests

# Run a single test
uv run python -m unittest tests.test_<name>

# Validate JSON config
python -m json.tool BotData/plugin_configs/<file>.json

# Check JS syntax (when admin JS changes)
node --check plugins/bot_admin/static/<file>.js

# Update vendored media parser
.\scripts\update_media_parser_vendor.ps1
```

No `.env` or dotenv files are used. All runtime config lives in `BotData/config.json` and `BotData/plugin_configs/*.json`.

## Project Architecture

### Overview
HIKARI BOT NEO is a QQ bot built on **NoneBot 2** using the **OneBot V11** adapter, connecting through **NapCat** WebSocket. It parses media links (Pixiv, Bilibili, Douyin, Xiaohongshu, etc.), manages sticker packs, runs AI chat, handles scheduled pushes, and provides a web admin panel. Deployment is source-mounted Docker with 5 services.

### Entry & Config

| File | Role |
|------|------|
| [`bot.py`](bot.py) | Entry point — loads JSON config, initializes NoneBot driver with NapCat WS URL, loads plugins |
| [`pyproject.toml`](pyproject.toml) | Python deps (nonebot2, httpx, pillow, yt-dlp, jmcomic, etc.) and NoneBot plugin dir config |
| `BotData/config.json` | Main bot config (superuser, NapCat WS URL/token, log level, paths) |
| `BotData/plugin_configs/*.json` | Per-plugin configs (hot-reloadable, checked by mtime/size cache) |
| `BotData/resources/*.json` | Hot-replaceable rendering config and bot reply messages |

### Message Flow

```
Message from QQ → NapCat → OneBot V11 WS → NoneBot

  priority=0, block=False → core/command_router.py
    - Explicit commands registered via @command() decorator
    - Creates CommandContext, marks event handled on match
    - Falls through if no command matched

  priority=1, block=False → core/message_pipeline.py
    - URL/auto-parse handlers registered via register_handler()
    - Implements URLHandler protocol (match + handle)
    - Skips if command_router already handled the event

  All other plugins (on_message, priority=...)
    - AI Agent is lowest-priority fallback
    - `plugins/aiagent` — only responds when no other plugin handled the message
```

### Core Modules (`core/`)

| Module | Responsibility |
|--------|---------------|
| [`config_loader.py`](core/config_loader.py) | Load `BotData/config.json` + per-plugin JSON with deep-merge over defaults; mtime/size-based cache for hot reload |
| [`command_router.py`](core/command_router.py) | Lightweight explicit command dispatch via `@command()` decorator; priority=0 matcher |
| [`message_pipeline.py`](core/message_pipeline.py) | URL/auto-parse handler registry via `register_handler()`; priority=1 matcher |
| [`rendering.py`](core/rendering.py) | Image text rendering — `load_font()` reads `BotData/resources/rendering.json` for font paths with fallback chain |
| [`bot_messages.py`](core/bot_messages.py) | Centralized user-facing replies — `get_message(key)` from `BotData/resources/bot_messages.json` with defaults |
| [`ai_tool_registry.py`](core/ai_tool_registry.py) | `register_ai_tool()` for exposing plugin functions as AI Agent callable tools (OpenAI function-calling schema) |
| [`access_control.py`](core/access_control.py) | Shared QQ/group whitelist/blacklist check per plugin config |
| [`error_notifier.py`](core/error_notifier.py) | User-friendly error replies + admin traceback notifications |
| [`lifecycle_logging.py`](core/lifecycle_logging.py) | Startup summary, plugin load logging, event description helpers |
| [`temp_media_cleaner.py`](core/temp_media_cleaner.py) | Schedules cleanup of temporary downloaded media |
| [`activity_tracker.py`](core/activity_tracker.py) | Live activity tracking (parsing, downloading, replying) for the admin overview dashboard |
| [`stats_tracker.py`](core/stats_tracker.py) | Per-session usage statistics |
| [`bot_identity.py`](core/bot_identity.py) | Bot name/identity from config, used by messages and rendering |
| [`resources.py`](core/resources.py) | Load/backfill JSON resources from `BotData/resources/` |
| [`runtime_info.py`](core/runtime_info.py) | Uptime, version info from `version.json` |

### Plugin Organization

Each capability is a self-contained NoneBot plugin under `plugins/<name>/`:

| Plugin | Function |
|--------|----------|
| [`pixiv_parser`](plugins/pixiv_parser) | Pixiv artwork download — URL auto-parse via message_pipeline handler |
| [`media_parser`](plugins/media_parser) | Aggregated multi-platform parser (Bilibili, Douyin, TikTok, Kuaishou, Weibo, Xiaohongshu, Xianyu, Toutiao, Xiaoheihe, Twitter/X) — wraps vendored `third_party/astrbot_plugin_media_parser` |
| [`cobalt_parser`](plugins/cobalt_parser) | Instagram/Facebook — proxies through self-hosted cobalt API |
| [`youtube_downloader`](plugins/youtube_downloader) | YouTube video download via yt-dlp |
| [`media_detail_web`](plugins/media_detail_web) | Standalone web page at `:53123` for previewing/downloading parsed media |
| [`tg_sticker_parser`](plugins/tg_sticker_parser) | Telegram sticker pack import via Bot API, converts to GIF via transcoder |
| [`sticker_trigger`](plugins/sticker_trigger) | Local sticker keyword matching and sending |
| [`sticker_collector`](plugins/sticker_collector) | Silent collection of chat images into inbox for manual review |
| [`voice_trigger`](plugins/voice_trigger) | Local voice keyword matching |
| [`tts_speaker`](plugins/tts_speaker) | Fish Audio TTS — `说话`, `音色列表`, `切换音色` commands |
| [`aiagent`](plugins/aiagent) | AI chat — lowest-priority fallback, OpenAI-compatible API, persona skills, persistent memory, search/file/plugin tools |
| [`bot_admin`](plugins/bot_admin) | Web admin panel at `:54213` — sticker/voice/TTS/AI Agent/push/config management |
| [`bot_help`](plugins/bot_help) | `帮助` / `关于` commands |
| [`push_framework`](plugins/push_framework) | Generic timed push skeleton — register sources via `register_push_source()`, schedule jobs in config |
| [`steam_deals`](plugins/steam_deals) | Steam daily deal report with rendered images |
| [`ai_news`](plugins/ai_news) | AI news aggregation from RSS sources, rendered as images |
| [`zhihu_hot`](plugins/zhihu_hot) | Zhihu hot list rendered as images |
| [`rss_subscriber`](plugins/rss_subscriber) | RSS/Atom subscription commands and push source |
| [`osu_info`](plugins/osu_info) | osu! API v2 queries — user lookup, scores, beatmaps, rankings (images) |
| [`stardew_wiki`](plugins/stardew_wiki) | Stardew Valley Wiki MediaWiki API search |
| [`mc_wiki`](plugins/mc_wiki) | Minecraft Wiki MediaWiki API search |
| [`sts2_wiki`](plugins/sts2_wiki) | Slay the Spire 2 Wiki via Spire Codex API |
| [`jmcomic_api`](plugins/jmcomic_api) | JMComic PDF download/send |
| [`profile_like`](plugins/profile_like) | QQ profile like command |
| [`mention_reaction`](plugins/mention_reaction) | Emoji reaction when bare `@bot` is sent |
| [`poke_back`](plugins/poke_back) | Auto-poke-back on notification |
| [`media_transcoder`](plugins/media_transcoder) | Cross-plugin GIF conversion service (video/WebP/TGS → GIF) |

### Vendored Code

`third_party/astrbot_plugin_media_parser/` — upstream AGPL media parser library. Do not modify directly; update via `scripts/update_media_parser_vendor.ps1`. Local NoneBot integration code stays in `plugins/media_parser/`.

### Deployment Architecture

5 Docker Compose services (source-mounted, no project image):

| Service | Image | Role |
|---------|-------|------|
| `hikaribot` | `python:3.12-slim-bookworm` | Bot + Admin panel + Media detail web |
| `napcat` | `mlikiowa/napcat-docker` | QQ / OneBot V11 gateway |
| `cobalt` | `ghcr.io/imputnet/cobalt:11` | Instagram/Facebook media API |
| `searxng` | `searxng/searxng` | Web search for AI Agent |
| `searxng-valkey` | `valkey/valkey:9-alpine` | SearXNG cache |

`docker/entrypoint.sh` bootstraps: creates dirs, checks/installs system deps (ffmpeg, cairo, pango, Noto CJK fonts, 7zip), sets up venv with uv, copies example configs, runs `uv sync --frozen --no-dev`, then executes the bot.

### AI Agent Response Formatting

The AI Agent strips Markdown from model replies before sending to QQ. The `strip_markdown()` function in `plugins/aiagent/utils.py` is called at `plugins/aiagent/__init__.py:119` on every model response, removing: headings, bold/italic/strikethrough, inline and fenced code blocks, links/images (keeps alt/label text), lists, blockquotes, and horizontal rules. Italic `_underscore_` patterns use word-boundary guards to avoid mangling identifiers like `really_important`. If adding a new entry point that sends AI-generated text to chat, apply `strip_markdown()` before `bot.send()`.

### Key Patterns

- **Plugin config**: Define defaults in the plugin, load via `core.config_loader.load_plugin_config("name", DEFAULT_CONFIG)` — deep-merges user JSON over defaults. Configs hot-reload via mtime/size cache.
- **Explicit commands**: Register with `@command()` decorator from `core.command_router`. Provides `CommandContext` with parsed args, handles scope (private/group/@-required).
- **Auto-parse handlers**: Implement `URLHandler` protocol and call `register_handler()`. Match on URL patterns in `match()`, download/send in `handle()`.
- **AI Tools**: Register with `@register_ai_tool()` from `core.ai_tool_registry`. Must be read-only by default, return JSON-serializable data. No side effects without explicit config gate.
- **Admin API**: Bot admin endpoints under `/api/*` support `X-Admin-Token` header auth and session cookies.
- **Rendered images**: Always use `core.rendering.load_font()` so fonts are configurable. Avoid fixed-width layouts. Use wrapped text and dynamic measurement for CJK strings.
- **User-facing replies**: Go through `core.bot_messages.get_message()` — never hard-code reply text in plugins. Keys live in `BotData/resources/bot_messages.json`.

### Data Boundaries

| Path | Contents | Git-tracked? |
|------|----------|-------------|
| `BotData/config.json` | Bot master config (superuser, NapCat token) | No |
| `BotData/plugin_configs/*.json` | Per-plugin config | No (examples: `*.example.json` → yes) |
| `BotData/resources/*.json` | Hot-replaceable rendering & messages | No (examples: yes) |
| `BotData/fonts/` | Custom fonts | No (`.gitkeep` only) |
| `BotData/agent_personas/` | AI persona skill files | No (`.gitkeep` only) |
| `BotData/Gifs/` | Sticker files | No |
| `BotData/Voices/` | Voice files | No |
| `BotData/logs/` | Runtime logs | No |
| `UserData/` | State, bindings, AI memory, stats | Selectively ignored |
| `third_party/` | Vendored upstream code | Yes |

The bot never reads `.env` for runtime settings. `.env` only sets Docker Compose ports/image/account. When displaying the bot name to users, use `BotData/config.json` `bot.name` via identity helpers, not hard-coded values.

### Validation

- After Python changes: `uv run python -m compileall <changed paths>`
- Full test suite: `uv run python -m unittest discover -s tests`
- After JSON config changes: `python -m json.tool <file>`
- After JS changes (admin UI): `node --check <js file>`
- Deploy: `./deploy.ps1` from PowerShell (uses 7z + SSH)
