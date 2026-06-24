# AGENTS.md

## Project Rules

- The bot does not use `.env` for runtime settings. Runtime config belongs in `BotData/config.json` and `BotData/plugin_configs/*.json`; keep real config and user data out of git.
- New bot features should be implemented as plugins under `plugins/<plugin_name>/` and registered through `core.command_router.command` for explicit commands.
- Fixed user-facing bot replies should live in `BotData/resources/bot_messages.json` and be read through `core.bot_messages.get_message`; avoid hard-coding reusable reply text inside plugins.
- Generated images must use the shared font loader from `core.rendering.load_font` so `BotData/resources/rendering.json` controls regular and bold fonts consistently.
- Keep `BotData/resources/*.example.json` and `BotData/fonts/.gitkeep` in git, but do not commit runtime resource JSON files or real font files.
- When resource files or fonts are needed on the server, make sure `deploy.ps1` keeps uploading `BotData/resources/` and `BotData/fonts/`.
- Temporary media that NapCat needs to read should be written under `/tmp/hikari_bot` or a configurable subdirectory mounted by both bot and NapCat containers.
- When adding a plugin config, provide `BotData/plugin_configs/<plugin_name>.example.json` and document required fields in `README.md`.
- After changing Python code, run `uv run python -m compileall <changed paths>` before finishing.
- Do not revert unrelated user changes in the worktree. Work with dirty files conservatively and keep edits scoped to the requested feature or fix.

## osu! Plugin Notes

- `plugins/osu_info` uses osu!api v2 Client Credentials with `public` scope; credentials live in `BotData/plugin_configs/osu_info.json`.
- QQ-to-osu! bindings are user data and live in `UserData/osu_bindings.json`.
- osu! query output should be sent as images, and text/card layouts should avoid fixed-width assumptions that can clip long names, numbers, beatmap titles, or localized strings.
