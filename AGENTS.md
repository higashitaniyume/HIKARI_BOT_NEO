# AGENTS.md

## Task Workflow

- After each completed task, commit the task-scoped changes to the repository. Stage only files that belong to the current task, leave unrelated dirty files alone, and use a small descriptive commit message.
- Before changing code or docs, inspect the relevant project structure and current implementation. Ground answers and edits in the repo's actual behavior, runtime paths, logs, and README rather than generic assumptions.
- **Never guess API response formats, parameter names, or endpoint paths.** When working with an external API (Fish Audio, DeepSeek, or any other service), always look up the official documentation via web search before writing code that reads or sends data. Guessing field names leads to silent "?" values or broken integrations.
- If the worktree is dirty, assume unrelated changes belong to the user or another task. Do not revert them; work around them and keep the current task's diff narrow.
- Validate before finishing and before committing. For Python changes, run `uv run python -m compileall <changed paths>` at minimum; add targeted tests when behavior changes.
- For docs-only changes, review the diff for accuracy against the current repo. Do not run Python validation unless Python files changed.

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

## Project Shape

### Overview

HIKARI BOT NEO is a QQ bot built on **NoneBot 2** using the **OneBot V11** adapter, connecting through **NapCat** WebSocket. It parses media links (Pixiv, Bilibili, Douyin, Xiaohongshu, etc.), manages sticker packs, runs AI chat, handles scheduled pushes, and provides a web admin panel. Deployment is source-mounted Docker.

- `bot.py` is the entry point, and `pyproject.toml` declares `plugin_dirs = ["plugins"]`.
- Core shared behavior lives in `core/`: command routing, config loading, resource loading, rendering, logging, lifecycle/error reporting, message pipeline handling, temp media cleanup, stats, and access control.
- Feature code should live in `plugins/<plugin_name>/`. Explicit user commands should register through `core.command_router.command`; automatic URL/message parsing should use the existing message-pipeline patterns in the relevant parser plugins.
- `plugins/bot_admin/` is the integrated admin surface. It uses Python backend modules plus plain templates/static HTML/CSS/JS; there is no separate frontend build chain.
- `third_party/astrbot_plugin_media_parser/` is vendored upstream parser code. Keep local integrations in repo-owned plugins unless a task explicitly requires touching vendored code.
- Deployment is source-mounted Docker. Treat `deploy.ps1`, `docker-compose.yml`, `deploy/docker-compose.server.yml`, and `docker/entrypoint.sh` as part of the runtime contract.

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

  priority=2, block=False → plugins/astrbot_compat/loader.py
    - AstrBot plugin @filter.regex / @filter.on_message dispatch
    - Created lazily when the first astrbot plugin with such handlers is loaded

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
| [`astrbot_compat`](plugins/astrbot_compat) | AstrBot plugin compatibility layer — run community astrbot plugins, with web admin panel for management |

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
- **Message key defaults in code**: When adding a new `get_message()` key reference in plugin code, ALWAYS add the corresponding default value to the `DEFAULT_MESSAGES` dict in `core/bot_messages.py`. The runtime auto-backfills missing keys from this dict into the user's `bot_messages.json`, so adding the default in code ensures the message works on existing deployments without manual config edits. `BotData/resources/bot_messages.json` is gitignored and cannot be relied upon for deployment. Test by searching for the new key in `DEFAULT_MESSAGES` after adding it.

## Config And Data Boundaries

- The bot does not use `.env` for runtime bot settings. Runtime config belongs in `BotData/config.json` and `BotData/plugin_configs/*.json`; keep real config and user data out of git.
- When adding a plugin config, provide `BotData/plugin_configs/<plugin_name>.example.json`, document required fields in `README.md`, and load runtime values through the shared config loader.
- `core.config_loader.load_plugin_config()` deep-merges user config over defaults. Prefer adding defaults and example keys over inventing separate hidden knobs.
- Whenever code, resources, admin UI, prompts, rendered images, docs, or agent-facing text need to mention the bot's display name, use `BotData/config.json` `bot.name` through the existing identity/config helpers instead of hard-coding the project/default name. Repo names, paths, environment variables, logger names, and other non-display identifiers may keep their stable literal values.
- Fixed user-facing bot replies should live in `BotData/resources/bot_messages.json` and be read through `core.bot_messages.get_message`; avoid hard-coding reusable reply text inside plugins.
- Keep `BotData/resources/*.example.json`, `BotData/config.example.json`, and `BotData/fonts/.gitkeep` in git. Do not commit runtime resource JSON files, real config JSON files, real fonts, logs, or `UserData` state.
- Generated images must use `core.rendering.load_font` so `BotData/resources/rendering.json` controls regular and bold fonts consistently.
- Temporary media that NapCat needs to read should be written under `/tmp/hikari_bot` or a configurable subdirectory mounted by both bot and NapCat containers.
- QQ-to-osu! bindings, statistics, RSS state, AI-agent memory, and similar per-user/per-group data belong under `UserData/` and must stay out of git.

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

## Feature Rules

- New bot features should be implemented as plugins under `plugins/<plugin_name>/`; avoid growing unrelated core code unless shared behavior is genuinely needed.
- Keep public chat failures quiet and user-friendly. Log details and notify/admin-report where appropriate instead of sending raw tracebacks or upstream errors to ordinary users.
- For user-visible integrations, ship the full surface together: command behavior, config defaults/example config, README documentation, resources/messages, admin UI wiring when applicable, and validation.
- If a visible admin UI element exists, make it actually interactive. Do not leave buttons, tabs, or controls as static placeholders.
- For rendered cards/images, avoid fixed-width assumptions that can clip long names, localized strings, large numbers, or beatmap titles. Prefer dynamic measurement and responsive layout.
- When changing shared resources or fonts needed on the server, confirm `deploy.ps1` continues uploading `BotData/resources/` and `BotData/fonts/`.
- If a plugin capability should be callable by `plugins/aiagent` as a tool, register an explicit contract through `core.ai_tool_registry.register_ai_tool()` instead of exposing command handlers wholesale. The contract must include a stable tool name, plugin name, clear description, strict JSON object schema, readonly/side-effect metadata, and a handler that returns structured JSON-serializable data.
- Default AI tools should be read-only and should not directly send messages, upload files, trigger pushes, mutate bindings, write plugin config, or consume paid/limited external APIs unexpectedly. Side-effect tools require an explicit config gate, permission boundary, tests, and documentation before being enabled.
- Never expose bot admin operations, runtime config secrets, `BotData/config.json`, `BotData/plugin_configs/*.json`, arbitrary filesystem access, media parser download/send actions, or other sensitive operations as AI tools. If a plugin uses API keys internally, return only sanitized public results.

## Validation

- Python syntax smoke test: `uv run python -m compileall <changed paths>`.
- Full Python suite when risk justifies it: `uv run python -m unittest discover -s tests`.
- Admin/static JS syntax check for changed JavaScript: `node --check <changed js files>`.
- JSON example/config check when edited: `python -m json.tool <file>`.
- If cache or permission issues make `compileall` noisy, fall back to a source-only compile check for the changed Python files and explain the fallback.

## Deploy And Runtime Checks

- When the user asks to run `deploy.ps1`, execute the repo script directly and report the real result.
- Use `deploy.ps1 -l` / `-Local` for local bot-only Docker runs; do not combine it with remote push/all-service deploy flags.
- For runtime issues, verify the live path before answering. Server config commonly lives under `/opt/hikaribot-docker/BotData/...`; downloaded/shared media should be checked under `/tmp/hikari_bot/...` or the configured shared temp path.
- If shell scripts are changed, preserve LF line endings so Linux containers can execute them.

## osu! Plugin Notes

- `plugins/osu_info` uses osu!api v2 Client Credentials with `public` scope; credentials live in `BotData/plugin_configs/osu_info.json`.
- QQ-to-osu! bindings are user data and live in `UserData/osu_bindings.json`.
- osu! query output should be sent as images, and text/card layouts should avoid fixed-width assumptions that can clip long names, numbers, beatmap titles, or localized strings.
