# AGENTS.md

## Task Workflow

- After each completed task, commit the task-scoped changes to the repository. Stage only files that belong to the current task, leave unrelated dirty files alone, and use a small descriptive commit message.
- Before changing code or docs, inspect the relevant project structure and current implementation. Ground answers and edits in the repo's actual behavior, runtime paths, logs, and README rather than generic assumptions.
- If the worktree is dirty, assume unrelated changes belong to the user or another task. Do not revert them; work around them and keep the current task's diff narrow.
- Validate before finishing and before committing. For Python changes, run `uv run python -m compileall <changed paths>` at minimum; add targeted tests when behavior changes.
- For docs-only changes, review the diff for accuracy against the current repo. Do not run Python validation unless Python files changed.

## Project Shape

- This is a NoneBot QQ bot using the OneBot V11 adapter. `bot.py` is the entry point, and `pyproject.toml` declares `plugin_dirs = ["plugins"]`.
- Core shared behavior lives in `core/`: command routing, config loading, resource loading, rendering, logging, lifecycle/error reporting, message pipeline handling, temp media cleanup, stats, and access control.
- Feature code should live in `plugins/<plugin_name>/`. Explicit user commands should register through `core.command_router.command`; automatic URL/message parsing should use the existing message-pipeline patterns in the relevant parser plugins.
- `plugins/bot_admin/` is the integrated admin surface. It uses Python backend modules plus plain templates/static HTML/CSS/JS; there is no separate frontend build chain.
- `third_party/astrbot_plugin_media_parser/` is vendored upstream parser code. Keep local integrations in repo-owned plugins unless a task explicitly requires touching vendored code.
- Deployment is source-mounted Docker. Treat `deploy.ps1`, `docker-compose.yml`, `deploy/docker-compose.server.yml`, and `docker/entrypoint.sh` as part of the runtime contract.

## Config And Data Boundaries

- The bot does not use `.env` for runtime bot settings. Runtime config belongs in `BotData/config.json` and `BotData/plugin_configs/*.json`; keep real config and user data out of git.
- When adding a plugin config, provide `BotData/plugin_configs/<plugin_name>.example.json`, document required fields in `README.md`, and load runtime values through the shared config loader.
- `core.config_loader.load_plugin_config()` deep-merges user config over defaults. Prefer adding defaults and example keys over inventing separate hidden knobs.
- Fixed user-facing bot replies should live in `BotData/resources/bot_messages.json` and be read through `core.bot_messages.get_message`; avoid hard-coding reusable reply text inside plugins.
- Keep `BotData/resources/*.example.json`, `BotData/config.example.json`, and `BotData/fonts/.gitkeep` in git. Do not commit runtime resource JSON files, real config JSON files, real fonts, logs, or `UserData` state.
- Generated images must use `core.rendering.load_font` so `BotData/resources/rendering.json` controls regular and bold fonts consistently.
- Temporary media that NapCat needs to read should be written under `/tmp/hikari_bot` or a configurable subdirectory mounted by both bot and NapCat containers.
- QQ-to-osu! bindings, statistics, RSS state, AI-agent memory, and similar per-user/per-group data belong under `UserData/` and must stay out of git.

## Feature Rules

- New bot features should be implemented as plugins under `plugins/<plugin_name>/`; avoid growing unrelated core code unless shared behavior is genuinely needed.
- Keep public chat failures quiet and user-friendly. Log details and notify/admin-report where appropriate instead of sending raw tracebacks or upstream errors to ordinary users.
- For user-visible integrations, ship the full surface together: command behavior, config defaults/example config, README documentation, resources/messages, admin UI wiring when applicable, and validation.
- If a visible admin UI element exists, make it actually interactive. Do not leave buttons, tabs, or controls as static placeholders.
- For rendered cards/images, avoid fixed-width assumptions that can clip long names, localized strings, large numbers, or beatmap titles. Prefer dynamic measurement and responsive layout.
- When changing shared resources or fonts needed on the server, confirm `deploy.ps1` continues uploading `BotData/resources/` and `BotData/fonts/`.

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
