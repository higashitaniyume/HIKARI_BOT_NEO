# 部署与本地开发

## Docker Compose 部署（推荐）

本项目采用**源码挂载部署**方式，不再构建或分发 Docker 镜像。Compose 直接拉取官方 Python 基础镜像，将项目源码目录只读挂载进容器；依赖安装在名为 `hikaribot_venv` 的 Docker volume 中。更新代码时仅同步源码并重启 `hikaribot`，启动过程会按 `uv.lock` 自动同步 Python 依赖。

Docker 启动脚本（`docker/entrypoint.sh`）负责：创建目录、检查/安装系统依赖（ffmpeg、cairo、pango、Noto CJK 字体、7zip）、创建 venv、复制示例配置、执行 `uv sync --frozen --no-dev`，最后启动机器人。

## 一键安装脚本

服务器需要预先安装 Docker Engine、Docker Compose v2 和 Git。

**Linux / macOS（bash）：**
```bash
curl -fsSL https://raw.githubusercontent.com/higashitaniyume/HIKARI_BOT_NEO/main/install.sh | sudo sh
```

**PowerShell（Windows / Linux）：**
```powershell
irm https://raw.githubusercontent.com/higashitaniyume/HIKARI_BOT_NEO/main/install.ps1 | iex
```

脚本会拉取源码到 `/opt/hikaribot-docker/app/`、创建持久化数据目录和 `.env`，然后执行 `docker compose up -d` 启动全部 5 个服务。

支持自定义仓库地址和部署目录：
```bash
curl -fsSL https://raw.githubusercontent.com/.../install.sh | sudo env \
  HIKARI_REPOSITORY_URL=https://example.com/repo.git \
  HIKARI_DEPLOY_DIR=/opt/hikari \
  sh
```

脚本会保护 `app/` 中的本地源码改动，发现未提交或未跟踪文件时会停止，不会强制覆盖。

> 部署编排会同时启动 SearXNG 搜索服务和 Valkey 缓存，供 AI Agent 的搜索工具使用。默认外部端口为 `54261`，可在 `.env` 中调整。

## 手动部署

```bash
git clone <本仓库地址> /opt/hikaribot-docker/app
cd /opt/hikaribot-docker
cp app/deploy/docker-compose.server.yml docker-compose.yml
cp app/.env.example .env
mkdir -p searxng/core-config && cp app/deploy/searxng/core-config/settings.yml searxng/core-config/settings.yml
sed -i "s/__SEARXNG_SECRET__/$(openssl rand -hex 32)/g" searxng/core-config/settings.yml
docker compose up -d
```

首次启动会在 `/opt/hikaribot-docker/BotData/` 中生成真实配置文件。编辑这些配置，至少修改以下必填项：

| 配置文件 | 必改项 |
|----------|--------|
| `BotData/config.json` | `bot.superuser_id`、`napcat.token` |
| `BotData/plugin_configs/pixiv_parser.json` | Pixiv Cookie 或代理 |
| `BotData/plugin_configs/bot_admin.json` | `password` |
| `BotData/plugin_configs/tg_sticker_parser.json` | Telegram Bot Token |

如果 NapCat 和机器人在同一个 compose 网络内，`BotData/config.json` 可以保持：

```json
{
  "napcat": {
    "ws_url": "ws://napcat:54253/",
    "token": "你的NapCat Token",
    "protocol": "websocket"
  }
}
```

改完后重启机器人：

```bash
docker compose restart hikaribot
```

编辑 `.env`，按需填写 NapCat 账号：

```text
NAPCAT_ACCOUNT=你的QQ号
```

## 部署到服务器

仓库内的 `deploy.ps1` 可通过 SSH 将源码同步到服务器：

```powershell
.\deploy.ps1 -ServerIP 你的服务器IP -ServerUser root -DeployPath /opt/hikaribot-docker -NapcatAccount 你的QQ号
```

> 注意：`deploy.ps1` 使用了 `&&` 等 PowerShell 7 语法，请使用 PowerShell 7 运行（不是 Windows 自带的 PowerShell 5.1）。

部署流程：
1. 首次使用时将历史目录 `/opt/hikaribot-dockcer` 迁移为正确的 `/opt/hikaribot-docker`
2. 上传源码到 `app/`（不上传 `.env`、真实配置或用户数据）
3. 上传服务器 Compose 文件
4. 默认更新并重启 `hikaribot`；首次部署或共享目录挂载变化时加 `-AllServices` 更新所有服务

```powershell
.\deploy.ps1 -AllServices
```

数据持久化策略：

| 位置 | 用途 | 是否覆盖 |
|------|------|----------|
| `app/` | 源码、静态资源与 example 配置 | ✅ 是 |
| `BotData/`、`UserData/` | 真实配置、贴纸、语音、日志与用户数据 | ❌ 否 |
| `runtime/` | `shared/` 跨容器文件、`tmp/hikari_bot/` 临时媒体 | ❌ 否 |
| Volume `hikaribot_venv` | Python 依赖与启动标记 | 仅锁文件变化时同步 |

`deploy.ps1` 用 `7z` 打包上传。部署和安装脚本会在启动前刷新 `version.json`（`0.0.x` 递增版本、短 hash、提交标题）。`关于` 命令与 Bot 后台总览页都会读取它显示版本。

## 访问地址

| 服务 | 地址 |
|------|------|
| Bot 后台 | `http://服务器IP:54213/` |
| 媒体详情 Web | `http://服务器IP:53123/` |
| NapCat WebUI | `http://服务器IP:3000/` |
| Cobalt API | `http://服务器IP:54257/` |

## 常用维护命令

```bash
docker compose ps
docker compose logs -f hikaribot
docker compose logs -f napcat
docker compose restart hikaribot
docker compose pull
docker compose up -d
```

## 本地开发

### 安装依赖

```bash
uv sync
```

### 生成配置

首次启动会自动创建默认配置文件：

```bash
uv run python bot.py
```

也可以复制示例配置：

```bash
# 主配置
cp BotData/config.example.json BotData/config.json

# 插件配置
cp BotData/plugin_configs/pixiv_parser.example.json BotData/plugin_configs/pixiv_parser.json
cp BotData/plugin_configs/media_parser.example.json BotData/plugin_configs/media_parser.json
cp BotData/plugin_configs/cobalt_parser.example.json BotData/plugin_configs/cobalt_parser.json
cp BotData/plugin_configs/youtube_downloader.example.json BotData/plugin_configs/youtube_downloader.json
cp BotData/plugin_configs/media_detail_web.example.json BotData/plugin_configs/media_detail_web.json
cp BotData/plugin_configs/bot_admin.example.json BotData/plugin_configs/bot_admin.json
cp BotData/plugin_configs/media_transcoder.example.json BotData/plugin_configs/media_transcoder.json
cp BotData/plugin_configs/osu_info.example.json BotData/plugin_configs/osu_info.json
cp BotData/plugin_configs/steam_deals.example.json BotData/plugin_configs/steam_deals.json
cp BotData/plugin_configs/ai_news.example.json BotData/plugin_configs/ai_news.json
cp BotData/plugin_configs/zhihu_hot.example.json BotData/plugin_configs/zhihu_hot.json
cp BotData/plugin_configs/push_framework.example.json BotData/plugin_configs/push_framework.json
cp BotData/plugin_configs/rss_subscriber.example.json BotData/plugin_configs/rss_subscriber.json
cp BotData/plugin_configs/voice_trigger.example.json BotData/plugin_configs/voice_trigger.json
cp BotData/plugin_configs/tts_speaker.example.json BotData/plugin_configs/tts_speaker.json
cp BotData/plugin_configs/aiagent.example.json BotData/plugin_configs/aiagent.json
cp BotData/plugin_configs/profile_like.example.json BotData/plugin_configs/profile_like.json
cp BotData/plugin_configs/mention_reaction.example.json BotData/plugin_configs/mention_reaction.json
cp BotData/plugin_configs/poke_back.example.json BotData/plugin_configs/poke_back.json
```

### 修改配置

编辑 `BotData/config.json`：

```json
{
  "bot": {
    "name": "HikariBotNeo",
    "superuser_id": "你的QQ号",
    "log_level": "INFO",
    "api_timeout": 120
  },
  "napcat": {
    "ws_url": "ws://你的NapCat地址:端口/",
    "token": "你的NapCat Token",
    "protocol": "websocket"
  }
}
```

### 启动

```bash
uv run python bot.py
```

启动后，在 QQ 里发送一个 Pixiv 作品链接或贴纸关键词进行测试。
