# HIKARI BOT NEO

<div align="center">

基于 [NoneBot 2](https://nonebot.dev/) + [NapCat](https://napneko.github.io/) OneBot V11 的 QQ 机器人

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

</div>

HIKARI BOT NEO 是一个功能丰富的 QQ 机器人，通过 NapCat 的 OneBot V11 WebSocket 接入 QQ。它能自动解析主流媒体平台链接（Pixiv、Bilibili、抖音、小红书、YouTube、网易云音乐等），管理贴纸包和语音包，运行 AI 对话（支持人格技能、持久化记忆、联网搜索与工具调用），提供定时推送能力（Steam 日报、AI 资讯、知乎热搜、RSS 订阅），并自带 Web 管理后台。

> [!IMPORTANT]
> 机器人本体不读取 `.env`。运行配置来自 `BotData/config.json` 和 `BotData/plugin_configs/*.json`；根目录 `.env` 只给 Docker Compose 设置端口、Python 基础镜像和 NapCat 账号。

---

## 📖 目录

- [一、项目简介](#一项目简介)
- [二、功能一览](#二功能一览)
- [三、快速部署](#三快速部署)
  - [Docker Compose 部署（推荐）](#docker-compose-部署推荐)
  - [一键安装脚本](#一键安装脚本)
  - [手动部署](#手动部署)
  - [部署到服务器](#部署到服务器)
  - [访问地址](#访问地址)
  - [常用维护命令](#常用维护命令)
- [四、本地开发](#四本地开发)
  - [安装依赖](#安装依赖)
  - [生成配置](#生成配置)
  - [修改配置](#修改配置)
  - [启动](#启动)
- [五、插件功能详解](#五插件功能详解)
  - [5.1 Pixiv 作品解析](#51-pixiv-作品解析)
  - [5.2 聚合媒体解析](#52-聚合媒体解析)
  - [5.3 Instagram / Facebook 解析](#53-instagram--facebook-解析)
  - [5.4 YouTube 视频下载](#54-youtube-视频下载)
  - [5.5 网易云音乐解析](#55-网易云音乐解析)
  - [5.6 媒体详情 Web](#56-媒体详情-web)
  - [5.7 Telegram 贴纸包解析](#57-telegram-贴纸包解析)
  - [5.8 本地贴纸包](#58-本地贴纸包)
  - [5.9 本地语音触发](#59-本地语音触发)
  - [5.10 TTS 语音合成](#510-tts-语音合成)
  - [5.11 AI Agent 聊天](#511-ai-agent-聊天)
  - [5.12 Bot 后台](#512-bot-后台)
  - [5.13 贴纸静默收集](#513-贴纸静默收集)
  - [5.14 定时推送框架](#514-定时推送框架)
  - [5.15 Steam 热门热卖日报](#515-steam-热门热卖日报)
  - [5.16 AI 最新资讯日报](#516-ai-最新资讯日报)
  - [5.17 知乎热搜](#517-知乎热搜)
  - [5.18 RSS 订阅](#518-rss-订阅)
  - [5.19 星露谷物语 Wiki](#519-星露谷物语-wiki)
  - [5.20 Minecraft Wiki](#520-minecraft-wiki)
  - [5.21 杀戮尖塔 2 Wiki](#521-杀戮尖塔-2-wiki)
  - [5.22 QQ 资料卡点赞](#522-qq-资料卡点赞)
  - [5.23 空 @ 表情回应](#523-空--表情回应)
  - [5.24 戳一戳回戳](#524-戳一戳回戳)
  - [5.25 媒体转码](#525-媒体转码)
  - [5.26 JMComic PDF 下载](#526-jmcomic-pdf-下载)
  - [5.27 帮助与关于](#527-帮助与关于)
  - [5.28 错误通知](#528-错误通知)
- [六、AstrBot 插件兼容层](#六astrbot-插件兼容层)
  - [6.1 概述](#61-概述)
  - [6.2 支持的 AstrBot API](#62-支持的-astrbot-api)
  - [6.3 架构](#63-架构)
  - [6.4 加载插件](#64-加载插件)
  - [6.5 依赖管理](#65-依赖管理)
  - [6.6 配置](#66-配置)
  - [6.7 Web 管理](#67-web-管理)
  - [6.8 限制](#68-限制)
- [七、核心模块](#七核心模块)
  - [消息处理流程](#消息处理流程)
  - [模块清单](#模块清单)
- [八、可热改资源](#八可热改资源)
  - [生成图片字体](#生成图片字体)
  - [机器人固定回复](#机器人固定回复)
- [九、NapCat 文件目录](#九napcat-文件目录)
- [十、项目结构](#十项目结构)
- [十一、常见问题](#十一常见问题)
- [十二、开发说明](#十二开发说明)
- [十三、许可证与致谢](#十三许可证与致谢)
- [十四、用户协议与隐私政策](#十四用户协议与隐私政策)

---

## 一、项目简介

HIKARI BOT NEO 是一个基于 [NoneBot 2](https://nonebot.dev/) 的 QQ 机器人，通过 NapCat 的 OneBot V11 WebSocket 接入 QQ。Bot 配置不读取 `.env`，运行配置来自 `BotData/config.json` 和 `BotData/plugin_configs/*.json`。

### 架构概览

```text
Message from QQ → NapCat → OneBot V11 WS → NoneBot

  priority=0, block=False → core/command_router.py
    - 显式命令注册 @command() 装饰器
    - AstrBot 插件 @filter.command 也注册在这里
    - 匹配成功标记已处理，未匹配则继续

  priority=1, block=False → core/message_pipeline.py
    - URL 自动解析处理器注册 register_handler()
    - 实现 URLHandler 协议 (match + handle)

  priority=2, block=False → astrbot_compat matcher (按需创建)
    - AstrBot 插件的 @filter.regex / @filter.on_message
    - 由 plugins/astrbot_compat/ 在首次加载插件时注册

  其余插件 (on_message, priority=...)
    - AI Agent 作为最低优先级兜底
```

### 5 个 Docker Compose 服务

| 服务 | 镜像 | 作用 |
|------|------|------|
| `hikaribot` | `python:3.12-slim-bookworm` | 机器人本体 + Bot 后台 + 媒体详情 Web |
| `napcat` | `mlikiowa/napcat-docker` | QQ / OneBot V11 网关 |
| `cobalt` | `ghcr.io/imputnet/cobalt:11` | Instagram / Facebook 媒体 API |
| `searxng` | `searxng/searxng` | AI Agent 网页搜索 |
| `searxng-valkey` | `valkey/valkey:9-alpine` | SearXNG 缓存 |

### 数据边界

| 路径 | 内容 | Git 跟踪 |
|------|------|----------|
| `BotData/config.json` | 主配置（超级管理员，NapCat Token） | ❌ |
| `BotData/plugin_configs/*.json` | 插件配置 | ❌（`*.example.json` ✅） |
| `BotData/resources/*.json` | 热改资源（字体、回复消息） | ❌（example ✅） |
| `BotData/Gifs/` | 贴纸文件 | ❌ |
| `BotData/Voices/` | 语音文件 | ❌ |
| `BotData/agent_personas/` | AI 人格 skill | ❌ |
| `UserData/` | 状态、绑定、AI 记忆、统计 | 选择性忽略 |
| `third_party/` | 上游 vendored 代码 | ✅ |

---

## 二、功能一览

| 功能 | 触发方式 | 详细章节 |
|------|----------|----------|
| Pixiv 作品解析 | 直接发送 Pixiv 链接 | [5.1](#51-pixiv-作品解析) |
| 聚合媒体解析（B站/抖音/小红书等） | 直接发送链接 / `媒体解析 <链接>` | [5.2](#52-聚合媒体解析) |
| Instagram / Facebook 解析 | 直接发送 IG/FB 链接 | [5.3](#53-instagram--facebook-解析) |
| YouTube 视频下载 | 直接发送 YouTube 链接 | [5.4](#54-youtube-视频下载) |
| 网易云音乐解析 | 发送网易云链接或 QQ 分享卡片 | [5.5](#55-网易云音乐解析) |
| 媒体详情 Web | 浏览器打开 `http://IP:53123/` | [5.6](#56-媒体详情-web) |
| Telegram 贴纸包解析 | `tg贴纸 <链接>` | [5.7](#57-telegram-贴纸包解析) |
| 本地贴纸包 | 关键词触发 / `贴纸包` 命令 | [5.8](#58-本地贴纸包) |
| 本地语音触发 | 关键词匹配 | [5.9](#59-本地语音触发) |
| TTS 语音合成 | `说话 <文本>` / `音色列表` / `切换音色` | [5.10](#510-tts-语音合成) |
| AI Agent 聊天 | 私聊文本 / 群聊 @机器人 | [5.11](#511-ai-agent-聊天) |
| Bot 后台 | 浏览器打开 `http://IP:54213/` | [5.12](#512-bot-后台) |
| 贴纸静默收集 | 自动 | [5.13](#513-贴纸静默收集) |
| 定时推送框架 | `推送 状态` / `推送 触发 <任务ID>` | [5.14](#514-定时推送框架) |
| Steam 日报 | `steam日报` / `steam免费` / `steam低价` | [5.15](#515-steam-热门热卖日报) |
| AI 最新资讯日报 | `ai资讯` | [5.16](#516-ai-最新资讯日报) |
| 知乎热搜 | `知乎热搜` | [5.17](#517-知乎热搜) |
| RSS 订阅 | `rss` 系列命令 | [5.18](#518-rss-订阅) |
| 星露谷物语 Wiki | `星露谷wiki <关键词>` | [5.19](#519-星露谷物语-wiki) |
| Minecraft Wiki | `mcwiki <关键词>` | [5.20](#520-minecraft-wiki) |
| 杀戮尖塔 2 Wiki | `塔2wiki <关键词>` / `sts2 <关键词>` | [5.21](#521-杀戮尖塔-2-wiki) |
| QQ 资料卡点赞 | `点赞` | [5.22](#522-qq-资料卡点赞) |
| 空 @ 表情回应 | 群聊只 @机器人 | [5.23](#523-空--表情回应) |
| 戳一戳回戳 | 自动 | [5.24](#524-戳一戳回戳) |
| 媒体转码 | 自动（贴纸转换） | [5.25](#525-媒体转码) |
| JMComic PDF | `jm <id>` | [5.26](#526-jmcomic-pdf-下载) |
| 帮助信息 | `帮助` | [5.27](#527-帮助与关于) |
| 关于信息 | `关于` | [5.27](#527-帮助与关于) |
| 错误通知 | 自动 | [5.28](#528-错误通知) |
| AstrBot 插件兼容 | 上传 / `astrbot load` / Web 面板 | [六](#六astrbot-插件兼容层) |

---

## 三、快速部署

### Docker Compose 部署（推荐）

本项目采用**源码挂载部署**方式，不再构建或分发 Docker 镜像。Compose 直接拉取官方 Python 基础镜像，将项目源码目录只读挂载进容器；依赖安装在名为 `hikaribot_venv` 的 Docker volume 中。更新代码时仅同步源码并重启 `hikaribot`，启动过程会按 `uv.lock` 自动同步 Python 依赖。

Docker 启动脚本（`docker/entrypoint.sh`）负责：创建目录、检查/安装系统依赖（ffmpeg、cairo、pango、Noto CJK 字体、7zip）、创建 venv、复制示例配置、执行 `uv sync --frozen --no-dev`，最后启动机器人。

### 一键安装脚本

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

### 手动部署

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

### 部署到服务器

仓库内的 `deploy.ps1` 可通过 SSH 将源码同步到服务器：

```powershell
.\deploy.ps1 -ServerIP 你的服务器IP -ServerUser root -DeployPath /opt/hikaribot-docker -NapcatAccount 你的QQ号
```

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

### 访问地址

| 服务 | 地址 |
|------|------|
| Bot 后台 | `http://服务器IP:54213/` |
| 媒体详情 Web | `http://服务器IP:53123/` |
| NapCat WebUI | `http://服务器IP:3000/` |
| Cobalt API | `http://服务器IP:54257/` |

### 常用维护命令

```bash
docker compose ps
docker compose logs -f hikaribot
docker compose logs -f napcat
docker compose restart hikaribot
docker compose pull
docker compose up -d
```

---

## 四、本地开发

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

---

## 五、插件功能详解

### 5.1 Pixiv 作品解析

**配置文件：** `BotData/plugin_configs/pixiv_parser.json`

自动解析消息中的 Pixiv 作品链接，下载并发送作品图片，支持多图合并转发。

**支持链接：**
- `https://www.pixiv.net/artworks/<pid>`
- `https://www.pixiv.net/i/<pid>`

> 不支持纯数字 PID、`pid:` 格式、用户主页、tag、novel 等链接。

**关键配置：**

| 字段 | 说明 |
|------|------|
| `cookie` | Pixiv Cookie，遇到 403 或 Cloudflare 拦截时需要补全 |
| `proxy` | Pixiv 请求代理，例如 `http://127.0.0.1:7890` |
| `auto_parse` | 是否自动解析消息中的 Pixiv 链接 |
| `max_send` | 单次最多发送图片数 |
| `allow_r18` | 是否允许 R18 内容 |
| `send_link_info` | 是否发送作品标题、作者、链接等详情 |
| `cache_dir` | 下载缓存目录，默认 `/tmp/hikari_bot` |
| `cache_ttl_seconds` | 下载媒体保留时间，默认 600 秒 |

---

### 5.2 聚合媒体解析

**配置文件：** `BotData/plugin_configs/media_parser.json`

基于 vendored 的 [`drdon1234/astrbot_plugin_media_parser`](third_party/astrbot_plugin_media_parser) 解析多个平台链接，使用 HIKARI 的 OneBot 发送链发送文本、图片和视频。

**支持平台：** B站、抖音、TikTok、快手、微博、小红书、闲鱼、今日头条、小黑盒、Twitter/X

> YouTube 由独立的 `youtube_downloader` 插件处理。

**关键配置：**

| 字段 | 说明 |
|------|------|
| `enabled` | 插件总开关 |
| `trigger.auto_parse` | 是否自动解析消息中的链接 |
| `max_links_per_message` | 单条消息最多处理几个链接 |
| `parse_retry_count` | 解析/下载失败重试次数，默认 2 |
| `parse_queue.enabled` | 是否启用解析队列（后台 worker） |
| `parse_queue.max_concurrent` | 同时解析的最大链接数 |
| `max_send` | 单条链接最多发送多少媒体，默认 80 |
| `parsers.<平台>` | 各平台输出模式：`关闭` / `全部发送` / `仅文本` / `仅富媒体` |
| `permissions` | QQ/群黑白名单 |
| `proxy.address` | 代理地址，例如 `http://127.0.0.1:7890` |
| `bilibili_enhanced.cookie` | B站 Cookie（高画质和受限内容） |
| `bilibili_enhanced.admin_assist.enable` | Cookie 失效时私聊管理员协助扫码登录 |
| `download.max_video_size_mb` | 单个视频大小上限 |

**B站 Cookie 辅助登录：** 开启 `bilibili_enhanced.use_cookie` 和 `admin_assist.enable` 后，Cookie 缺失或失效时 Bot 会私聊超级管理员。回复"确定"后会收到 Bilibili 登录二维码图片和备用链接；扫码成功后新 Cookie 自动保存，无需手动替换。超级管理员也可发送 `B站登录` / `B站Cookie` 手动触发。

**显式命令：**
```text
媒体解析 <链接>
解析媒体 <链接>
视频解析 <链接>
B站登录
B站Cookie
```

**更新上游：**
```powershell
.\scripts\update_media_parser_vendor.ps1
uv run python -m compileall plugins\media_parser third_party\astrbot_plugin_media_parser
```

---

### 5.3 Instagram / Facebook 解析

**配置文件：** `BotData/plugin_configs/cobalt_parser.json`

通过自部署 cobalt API 解析 Instagram 和 Facebook 的图片/视频。

> 不要直接使用 `api.cobalt.tools`，官方实例有 bot 保护，主要供 cobalt 前端使用。

**支持链接：** Instagram 的 `p`、`reel`、`stories`、`tv` 链接，以及 `facebook.com`、`fb.com`、`fb.watch` 链接。

**关键配置：**

| 字段 | 说明 |
|------|------|
| `cobalt_api` | 自部署 cobalt API 地址 |
| `api_key` | cobalt API Key（可为空） |
| `api_timeout` | API 超时时间 |
| `max_send` | 单次最多发送媒体数 |
| `send_link_info` | 是否发送来源、数量、链接等详情 |
| `cache_dir` | 下载缓存目录 |
| `cache_ttl_seconds` | 下载媒体保留时间，默认 600 秒 |

---

### 5.4 YouTube 视频下载

**配置文件：** `BotData/plugin_configs/youtube_downloader.json`

使用 `yt-dlp` 下载 YouTube 视频。直接发送视频链接即可触发解析。

**支持链接：** `youtube.com/watch`、`youtube.com/shorts`、`youtube.com/live`、`youtu.be`、`youtube-nocookie.com/embed`

> 播放列表不会批量下载，只处理单个视频。

**关键配置：**

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用 |
| `auto_parse` | 是否自动解析消息中的链接 |
| `max_links_per_message` | 单条消息最多处理的链接数，默认 1 |
| `max_file_mb` | 视频大小上限，默认 1024 MB |
| `max_height` | 默认最高清晰度，默认 720 |
| `send_link_info` | 是否发送标题、频道、时长等详情 |
| `download_timeout` | 下载超时（秒） |
| `cookiefile` | yt-dlp cookies 文件路径（登录验证） |
| `format` | yt-dlp format selector（为空则使用默认） |

---

### 5.5 网易云音乐解析

**配置文件：** `BotData/plugin_configs/netease_parser.json`

通过自部署 [api-enhanced](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced) 服务器解析网易云音乐链接。自动检测 `music.163.com` 歌曲链接和 `163cn.tv` 短链接（含 QQ 分享卡片）。

**发送方式：** 通过 NapCat 上传文件到聊天（`歌手 - 歌名.mp3` 或 `.flac`），不是语音消息。

**前置依赖：**
```bash
docker run -d -p 3000:3000 moefurina/ncm-api:latest
```

**支持链接：**
- `https://music.163.com/song/33894312`
- `https://music.163.com/#/song?id=33894312`
- `https://163cn.tv/xxxxx`（QQ 分享短链接）
- QQ 音乐分享卡片（自动提取 URL）

**关键配置：**

| 字段 | 说明 |
|------|------|
| `auto_parse` | 是否自动解析网易云链接 |
| `api_base_url` | api-enhanced 服务地址 |
| `api_timeout` | API 超时，默认 30s |
| `high_quality` | 是否请求最高音质（`br=999000`） |
| `cookie` | 网易云登录 Cookie（VIP 歌曲完整播放） |
| `real_ip` | 国内 IP（海外服务器绕过地区限制） |
| `max_file_mb` | 单文件大小上限，默认 50 MB |

---

### 5.6 媒体详情 Web

**配置文件：** `BotData/plugin_configs/media_detail_web.json`

独立的 Web 页面，默认监听 `0.0.0.0:53123`。

打开后可以粘贴 Pixiv、YouTube、Instagram/Facebook 或聚合媒体解析支持的链接，页面展示标题、作者、描述、标签、媒体数量等详情，并为解析到的图片/视频提供浏览器预览和下载入口。

**页面文件：** `plugins/media_detail_web/templates/index.html`

**关键配置：**

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用 |
| `host` / `port` | 监听地址，默认 `0.0.0.0:53123` |
| `max_links_per_request` | 单次最多解析几个链接 |
| `auto_download` | 页面默认是否勾选"自动下载" |
| `token_ttl_seconds` | 下载 token 有效期 |
| `max_remote_proxy_mb` | 远程媒体代理预览大小上限 |

---

### 5.7 Telegram 贴纸包解析

**配置文件：** `BotData/plugin_configs/tg_sticker_parser.json`

**必填配置：** Telegram Bot Token（`bot_token`），以及确保服务器能访问 Telegram API（必要时配置 `proxy`）。

**使用方式：**
```text
tg贴纸 https://t.me/addstickers/<set_name>
```

**处理流程：**
1. 优先复用本地贴纸库中已保存的同名贴纸包
2. 无缓存或带 `refresh` 时，调用 Telegram Bot API 获取
3. 调用 `media_transcoder` 统一转换为 GIF
4. 默认保存到 `BotData/Gifs/_library/`，更新贴纸库索引
5. 自动更新贴纸包关键词

**可选参数：**

| 参数 | 效果 |
|------|------|
| `zip` | 打包为 ZIP 发送 |
| `refresh` | 忽略本地缓存，重新获取并转换 |
| `nosave` | 只发送本次结果，不保存到本地 |
| `name=关键词` / `keyword=关键词` / `kw=关键词` | 额外注册触发词 |

**示例：**
```text
tg贴纸 https://t.me/addstickers/StickerSetName zip refresh name=猫猫虫
```

---

### 5.8 本地贴纸包

**配置文件：** `BotData/plugin_configs/sticker_library.json`

**贴纸文件目录：** `BotData/Gifs/_library/`

关键词可以关联多个贴纸包，一个贴纸也可以属于多个贴纸包。触发时自动合并并去重。贴纸最终只识别 `.gif` 格式。

**可用指令：**

| 消息 | 效果 |
|------|------|
| `猫猫虫` | 随机发送一张匹配贴纸 |
| `猫猫虫 10` | 随机发送 10 张（不重复） |
| `贴纸包 随机` | 从所有贴纸包随机发送 |
| `贴纸包 拼图 猫猫虫` | 将贴纸包第一帧拼成预览图 |
| `贴纸包 统计` | 查看贴纸数、贴纸包数和关键词数 |
| `贴纸包 列表` | 分页查看贴纸包和关键词 |
| `贴纸包 列表 全部` | 通过合并转发查看完整列表 |
| `贴纸包 预览` | 生成含名称、关键词和 6 张预览图的长图 |
| `贴纸包 帮助` / `帮助 贴纸包` | 查看贴纸包子命令 |
| `统计` | 查看当前会话统计 |

---

### 5.9 本地语音触发

**配置文件：** `BotData/plugin_configs/voice_trigger.json`

**语音文件目录：** `BotData/Voices/_library/`

用户发送纯文本关键词并完全匹配时，机器人随机发送关联语音。推荐使用 `.silk` 或 `.amr`；后台也允许上传 `.mp3`、`.wav`、`.ogg` 等格式，实际能否作为 QQ 语音发送取决于 NapCat/QQ 的支持。

---

### 5.10 TTS 语音合成

**配置文件：** `BotData/plugin_configs/tts_speaker.json`

使用 [Fish Audio](https://fish.audio) 合成语音。预置音色包括永雏塔菲、蒋介石和电棍，也可在 Bot 后台新增或编辑。

**可用指令：**

| 消息 | 效果 |
|------|------|
| `说话 你好哇` | 用当前音色合成语音 |
| `tts 你好哇` | 同上 |
| `音色列表` | 显示可用音色和当前使用的音色 |
| `切换音色 蒋介石` | 切换 Fish Audio 音色 |

**关键配置：**

| 字段 | 说明 |
|------|------|
| `selected_voice` | 当前使用的音色名称 |
| `voices` | 音色库（name + Fish reference_id） |
| `fish_audio.api_key` | Fish Audio API Key |
| `fish_audio.model` | 模型，默认 `s2-pro` |
| `fish_audio.backup_model` | 主模型失败时的备用模型 |
| `fish_audio.speed`、`volume` | 语速倍率和响度（dB） |
| `fish_audio.pitch_semitones` | 音高半音（FFmpeg 后处理） |
| `fish_audio.temperature`、`top_p` | 表现力参数 |
| `max_chars` | 单次合成文本长度上限 |
| `cooldown_seconds` | 同一用户冷却时间 |

---

### 5.11 AI Agent 聊天

**配置文件：** `BotData/plugin_configs/aiagent.json`

最低优先级兜底插件。调用 OpenAI-compatible 的 `chat/completions` 接口（可配置 OpenAI、DeepSeek 等）。

**行为：**
- **私聊：** 其他插件未处理时进入 AI Agent
- **群聊：** 必须 @机器人 且未被其他插件处理才回复
- 回复默认不超过 `max_reply_chars`（默认 3500），超出时自动以**合并转发**发送
- 支持白名单/黑名单权限控制，可在 Bot 后台"权限"页管理
- 受权限限制的用户消息会被静默忽略
- 抖音、Bilibili、小红书等媒体链接默认不会被 AI 兜底回复

**关键配置：**

| 字段 | 说明 |
|------|------|
| `enabled` | AI Agent 总开关 |
| `model.base_url` | OpenAI-compatible API 根地址 |
| `model.api_key` | API Key |
| `model.model` | 模型名称 |
| `model.temperature`、`top_p`、`max_tokens` | 生成参数 |
| `model.proxy` | 请求代理 |
| `persona.skill_path` | 人格 skill 路径（`BotData/agent_personas/` 下） |
| `persona.fallback_prompt` | skill 缺失时的备用提示词 |
| `chat.max_user_chars` | 单次用户消息最大字符数，默认 2000 |
| `chat.max_reply_chars` | 单次回复最大字符数，默认 3500 |
| `chat.cooldown_seconds` | 冷却秒数，默认 3 |
| `chat.max_history_messages` | 上下文保留消息数 |
| `chat.system_prompt_extra` | 额外系统提示词 |
| `memory.enabled` | 是否启用持久化记忆 |
| `memory.root` | 记忆根目录（默认 `UserData/aiagent_memory`） |
| `tools.search.enabled` | 是否启用网页搜索（SearXNG） |
| `tools.files.enabled` | 是否启用文件工具 |
| `tools.plugin_tools.enabled` | 是否启用插件 AI 工具 |
| `tools.max_tool_rounds` | 单次回复最多工具调用轮数，默认 4 |
| `permissions` | 白名单/黑名单 |

**AI Agent 工具：**

插件工具由各插件显式注册，默认只提供只读查询：

| 工具 | 来源 | 说明 |
|------|------|------|
| `web_search` | 内置 | 通过 SearXNG 搜索网页 |
| `mc_wiki_search` | mc_wiki | Minecraft Wiki 查询 |
| `stardew_wiki_search` | stardew_wiki | 星露谷 Wiki 查询 |
| `sts2_wiki_search` | sts2_wiki | 杀戮尖塔 2 Wiki 查询 |
| `zhihu_hot_list` | zhihu_hot | 知乎热搜列表 |
| `steam_deals_list` | steam_deals | Steam 游戏列表 |
| `ai_news_list` | ai_news | AI 资讯列表 |
| `rss_latest` | rss_subscriber | RSS 订阅最新 |
| `osu_user_lookup`、`osu_scores_lookup` 等 | osu_info | osu! 查询 |

**人格 skill 路径：** `BotData/agent_personas/`，支持目录结构（优先读取 `SKILL.md`、`skill.md`、`PERSONA.md` 等）或直接指向 `.md`、`.txt`、`.json` 文件。支持引用补充资源文件。

**可用指令：**

| 消息 | 效果 |
|------|------|
| 私聊 `你好` | 使用当前模型和人格 skill 回复 |
| 群聊 `@机器人 你好` | 同上 |
| `重置` / `ai 重置` / `清空上下文` | 清空当前会话上下文和持久化记忆 |
| `查看记忆` / `看记忆` / `memory` | 查看持久化记忆内容（隐藏命令） |
| `总结记忆` / `总结` / `summarize` | 手动触发 AI 记忆总结（隐藏命令） |

**持久化记忆文件结构：**
```text
UserData/aiagent_memory/private/<QQ>/memory.md
UserData/aiagent_memory/groups/<群号>/memory.md
UserData/aiagent_memory/groups/<群号>/users/<QQ>/memory.md
```

---

### 5.12 Bot 后台

**配置文件：** `BotData/plugin_configs/bot_admin.json`

Python 托管的 Web 管理后台，默认监听 `0.0.0.0:54213`。

**功能总览：**
- **总览页：** 机器人实时运行状态，包括各插件当前进行的解析、下载和回复活动
- **贴纸管理：** 上传贴纸素材到已有贴纸包或创建新包，保存前统一转换为 GIF；填写额外触发词
- **表情收集箱：** 整理机器人静默收集的待整理表情，批量加入贴纸包或删除
- **语音管理：** 上传语音文件，管理触发关键词，浏览器预览播放
- **TTS 管理：** 管理 Fish Audio 音色库、API Key、模型、语速、响度等参数
- **AI Agent 配置：** 配置 API 地址、模型参数、Key、人格 skill 路径和聊天限制
- **权限管理：** 管理各插件的 QQ/群黑白名单和启用状态
- **推送管理：** 管理定时推送任务、消息源参数、目标群号/私聊，支持立即推送测试
- **AstrBot 插件：** 管理 AstrBot 兼容插件的加载、卸载、配置编辑（自动表单）
- **配置编辑：** 在线编辑 `BotData/plugin_configs/*.json`，保存前校验 JSON
- **日志查看：** 查看 `BotData/logs/*.log` 尾部内容

**上传支持的素材格式：**
- **贴纸：** `.gif`、`.jpg`、`.jpeg`、`.png`、`.webp`、`.mp4`、`.webm`、`.mov`、`.mkv`、`.tgs` → 最终保存为 `.gif`（SHA256 哈希命名，去重）
- **语音：** `.silk`、`.amr`、`.mp3`、`.wav`、`.ogg`、`.m4a`、`.aac`、`.flac`、`.opus`（SHA256 去重）

**API 认证：**

```bash
curl -H "X-Admin-Token: <后台密码>" http://服务器IP:54213/api/aiagent-config
curl -H "Authorization: Bearer <后台密码>" http://服务器IP:54213/api/state
```

完整 HTTP API 文档见 [`docs/API.md`](docs/API.md)。

---

### 5.13 贴纸静默收集

**配置文件：** `BotData/plugin_configs/sticker_collector.json`

机器人静默收集群聊和私聊消息中的图片表情，统一转为 GIF 后放入待整理收集箱：

```text
BotData/Gifs/_inbox/
BotData/plugin_configs/sticker_inbox.json
```

待整理表情不会自动进入正式贴纸包，需要在 Bot 后台中手动分配或删除。收集箱按 GIF 哈希去重。

**关键配置：**

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用静默收集 |
| `collect_group` | 是否收集群聊图片 |
| `collect_private` | 是否收集私聊图片 |
| `allowed_groups` | 允许收集的群号（空 = 所有群） |
| `ignored_users` | 忽略的 QQ 用户 |
| `max_pending` | 收集箱最大待整理数 |

---

### 5.14 定时推送框架

**配置文件：** `BotData/plugin_configs/push_framework.json`

通用推送骨架：负责定时、目标发送、失败重试和同一轮去重；具体内容由消息源提供。插件可以调用 `register_push_source()` 注册自己的消息源。

**关键配置：**

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用 |
| `startup_delay_seconds` | 启动后等待秒数再开始检查 |
| `check_interval_seconds` | 检查间隔，默认 60 秒 |
| `jobs[].id` | 任务 ID，手动测试时使用 |
| `jobs[].trigger` | 触发器：`schedule` / `startup` / `shutdown` / `manual` |
| `jobs[].source` | 消息源名称 |
| `jobs[].time` / `times` | 推送时间（`HH:MM`，`times` 支持多点） |
| `jobs[].days` | 星期限制 |
| `jobs[].dedupe` | 去重方式：`daily` / `none` |
| `jobs[].targets` | 推送目标（群号、私聊） |
| `jobs[].source_options` | 消息源自定义参数 |

**可用指令（仅超级管理员）：**

| 消息 | 效果 |
|------|------|
| `推送 状态` | 查看框架、任务和消息源状态 |
| `推送 源` | 查看已注册消息源 |
| `推送 触发 <任务ID>` | 立即按该任务目标试发一次，不写入去重状态 |

**内置消息源：**

| source | 说明 |
|--------|------|
| `static_text` | 发送固定文本，用于测试链路 |
| `steam_deals` | 发送 Steam 日报图片 |
| `ai_news` | 发送 AI 最新资讯图片 |
| `zhihu_hot` | 发送知乎热搜图片 |
| `rss_feed` | 发送 RSS/Atom 订阅更新 |

**最小配置示例：**
```json
{
  "jobs": [{
    "id": "daily_text",
    "enabled": true,
    "trigger": "schedule",
    "source": "static_text",
    "time": "09:00",
    "timezone": "Asia/Shanghai",
    "targets": {"group_ids": [123456789]},
    "source_options": {"text": "早上好，今日推送测试。"}
  }]
}
```

**自定义消息源：**
```python
from plugins.push_framework import register_push_source, PushContext

@register_push_source("my_source", description="我的自定义推送源")
async def build_message(ctx: PushContext):
    keyword = ctx.options.get("keyword", "默认主题")
    return f"今日主题：{keyword}"
```

---

### 5.15 Steam 热门热卖日报

**配置文件：** `BotData/plugin_configs/steam_deals.json`

调用 Steam Store 接口生成日报图片。`steam日报` 展示热门热卖榜单；`steam低价` 筛选免费、超低价、大折扣和折扣加深游戏。默认不会主动每日推送；即使开启定时任务，也只发送到 `push_whitelist` 中列出的目标。

**可用指令：**

| 消息 | 效果 |
|------|------|
| `steam日报` | 查询免费、低价和大折扣游戏日报 |
| `steam免费` / `steam日报 免费` | 只看免费游戏 |
| `steam低价` / `steam日报 低价` | 查看低价和大折扣游戏 |
| `steam日报 刷新` | 忽略缓存重新获取 |

**关键配置：**

| 字段 | 说明 |
|------|------|
| `country` | Steam 地区代码，默认 `cn` |
| `language` | 语言，默认 `schinese` |
| `max_low_price_cents` | 低价阈值（分），默认 1000（¥10） |
| `min_discount_percent` | 大折扣阈值，默认 90 |
| `max_items` | 单张日报最多展示游戏数，默认 18 |
| `include_steamdb_free_promotions` | 是否用 SteamDB 标注限免/试玩 |
| `price_watch.enabled` | 本地价格快照（标记"新打折""折扣加深"） |
| `daily_filter` | 日报筛选（去同系列刷屏、最低评价数等） |
| `render.image_format` | 图片格式，默认 JPEG |
| `render.jpeg_quality` | JPEG 质量，默认 82 |
| `schedule.enabled` | 是否开启每日主动推送 |
| `schedule.time` | 推送时间 `HH:MM` |
| `push_whitelist` | 允许主动推送的群和私聊 |
| `proxy` | Steam API 代理 |

---

### 5.16 AI 最新资讯日报

**配置文件：** `BotData/plugin_configs/ai_news.json`

注册通用推送源 `ai_news`，从公开 RSS/Atom 源聚合 AI 最新资讯，按来源权重、发布时间和关键词加权筛选，去重后渲染成图片。

**默认源：** OpenAI News、Google AI、Hugging Face Blog、arXiv AI、Hacker News AI、TechCrunch AI、The Verge AI、VentureBeat AI

**可用指令：**

| 消息 | 效果 |
|------|------|
| `ai资讯` | 生成默认条数的 AI 资讯图片 |
| `ai资讯 5` | 生成最多 5 条资讯的图片 |
| `ai资讯 总结 5` | 使用 AI Agent 模型翻译并总结后生成图片 |

**关键配置：**

| 字段 | 说明 |
|------|------|
| `sources[].id` | 数据源 ID |
| `sources[].group` | 分组：`official` / `research` / `community` / `media` |
| `sources[].url` | RSS/Atom 地址 |
| `sources[].weight` | 来源权重 |
| `max_items` | 单张图片最多展示条数 |
| `max_per_source` | 单源最多展示条数 |
| `max_age_hours` | 时间范围限制 |
| `ai_summary.enabled` | 是否开启 AI 总结与翻译（复用 aiagent 模型配置） |
| `only_new` | 推送时是否只发送未见过的条目 |

---

### 5.17 知乎热搜

**配置文件：** `BotData/plugin_configs/zhihu_hot.json`

注册通用推送源 `zhihu_hot`，读取知乎热榜接口渲染成图片，展示排名、问题标题、摘要、回答/关注数和热度文本。

**可用指令：**

| 消息 | 效果 |
|------|------|
| `知乎热搜` | 生成默认条数的热搜图片 |
| `知乎热搜 10` | 生成最多 10 条 |
| `知乎热搜 10 刷新` | 忽略缓存重新读取 |
| `知乎热搜 链接` | 图片后额外发送问题链接 |

**关键配置：**

| 字段 | 说明 |
|------|------|
| `max_items` | 最多展示条数（最多 30） |
| `summary_max_chars` | 摘要截断字符数 |
| `cache_ttl_minutes` | 接口缓存时间 |
| `proxy` | 请求代理 |

---

### 5.18 RSS 订阅

**配置文件：** `BotData/plugin_configs/rss_subscriber.json`

支持常见 RSS 2.0 和 Atom Feed，不需要额外账号。后台"RSS"页面可维护同一份配置。

**可用指令：**

| 消息 | 效果 |
|------|------|
| `rss 列表` | 查看已配置订阅 |
| `rss 看 <订阅ID\|URL> [数量]` | 读取最新条目 |
| `rss 测试 <订阅ID\|URL> [数量]` | 超级管理员试读 |
| `rss 添加 <订阅ID> <URL> [标题]` | 超级管理员新增订阅 |
| `rss 删除 <订阅ID>` | 超级管理员删除订阅 |
| `rss 开启 <订阅ID>` / `rss 关闭 <订阅ID>` | 启停订阅 |

**关键配置：**

| 字段 | 说明 |
|------|------|
| `proxy` | HTTP 代理 |
| `max_items` | 默认读取条目数 |
| `summary_max_chars` | 摘要截断长度 |
| `subscriptions[].id` | 订阅 ID |
| `subscriptions[].url` | Feed URL |
| `subscriptions[].only_new` | 推送是否只发新条目 |

---

### 5.19 星露谷物语 Wiki

**配置文件：** `BotData/plugin_configs/stardew_wiki.json`

调用 Stardew Valley Wiki 的 MediaWiki API（默认中文站），不需要账号或密钥。以合并转发发送结果：链接 → 详细描述 → 主图。

**可用指令：**

| 消息 | 效果 |
|------|------|
| `星露谷wiki <关键词>` | 搜索中文 Wiki |
| `svwiki <关键词>` | 同上 |
| `stardewwiki <关键词>` | 同上 |

---

### 5.20 Minecraft Wiki

**配置文件：** `BotData/plugin_configs/mc_wiki.json`

调用 Minecraft Wiki 的 MediaWiki API（默认中文站），不需要账号或密钥。

**可用指令：**

| 消息 | 效果 |
|------|------|
| `mcwiki <关键词>` | 搜索中文 Wiki |
| `我的世界wiki <关键词>` | 同上 |
| `mc百科 <关键词>` | 同上 |

---

### 5.21 杀戮尖塔 2 Wiki

**配置文件：** `BotData/plugin_configs/sts2_wiki.json`

默认调用 Spire Codex 的 Slay the Spire 2 中文 API，不需要账号或密钥。优先读取本地缓存（默认 24 小时有效）。

> 灰机 Wiki 的 `api.php` 当前会对普通请求返回 Cloudflare challenge，不能作为稳定数据源。

**可用指令：**

| 消息 | 效果 |
|------|------|
| `塔2wiki <关键词>` | 搜索 Wiki 条目 |
| `塔2 <关键词>` | 同上 |
| `sts2 <关键词>` | 同上 |

**AI Agent 工具：** `sts2_wiki_search`（只读插件工具）

---

### 5.22 QQ 资料卡点赞

**配置文件：** `BotData/plugin_configs/profile_like.json`

调用 NapCat 的 `send_like` API。静默执行，不会在聊天里发送消息。

**可用指令：**

| 消息 | 效果 |
|------|------|
| `点赞` | 给自己点满赞（默认 10 次） |
| `点赞 @用户` | 给被 @ 的用户点赞 |
| `点赞 QQ号` | 给指定 QQ 号点赞 |
| `点赞 QQ号 5` | 点赞指定次数 |

**关键配置：**

| 字段 | 说明 |
|------|------|
| `default_times` | 默认点赞次数，默认 10 |
| `max_times` | 单次最大次数（最高 10） |

---

### 5.23 空 @ 表情回应

**配置文件：** `BotData/plugin_configs/mention_reaction.json`

群聊中，如果只发送 `@机器人`（无其他内容），调用 NapCat 的 `set_msg_emoji_like` 添加表情回应。默认使用 QQ 爱心表情（ID `66`）。

**关键配置：**

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用 |
| `group_enabled` | 群聊启用 |
| `emoji_ids` | 表情 ID 列表，默认 `["66"]`（爱心） |
| `random` | 多个表情时是否随机选择 |
| `allowed_groups` | 允许的群号（空 = 全部） |
| `ignored_users` | 忽略的用户 |

**常见表情 ID：** `66` 爱心、`76` 赞、`201` 点赞、`319` 比心、`124` OK、`99` 鼓掌

---

### 5.24 戳一戳回戳

**配置文件：** `BotData/plugin_configs/poke_back.json`

监听 OneBot V11 的戳一戳通知。被戳到时立刻戳回对方，不发送文字提示。

**关键配置：**

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用 |
| `group_enabled` | 群聊戳回 |
| `private_enabled` | 私聊戳回 |

---

### 5.25 媒体转码

**配置文件：** `BotData/plugin_configs/media_transcoder.json`

贴纸相关插件的统一转码服务。只要最终进入本地贴纸包，就必须保存为 GIF；非贴纸媒体不走这里。

**关键配置：**

| 字段 | 说明 |
|------|------|
| `sticker_gif_fps` | 视频/WebP 转 GIF 帧率 |
| `sticker_gif_width` | 转 GIF 宽度（0 = 保持原尺寸） |
| `sticker_gif_max_colors` | GIF 调色板颜色数（最大 256） |
| `sticker_gif_dither` | 抖动算法 |
| `sticker_ffmpeg_concurrency` | 同时转码数量 |
| `tgs_converter_cmd` | TGS 转 GIF 外部命令 |

---

### 5.26 JMComic PDF 下载

**配置文件：** `BotData/plugin_configs/jmcomic_api.json` + `BotData/jmcomic/option.yml`

默认仅私聊可用，所有用户都可触发。下载漫画、导出 PDF、通过 NapCat 上传文件。

```text
jm 123456
```

需要允许群聊时，将配置改为：
```json
{"allow_group": true}
```

---

### 5.27 帮助与关于

**配置文件：** `plugins/bot_help/`（无独立配置）

| 消息 | 效果 |
|------|------|
| 私聊 `帮助` | 查看可用能力和用法 |
| 群聊 `@机器人 帮助` | 同上 |
| 私聊 `关于` | 查看机器人描述、版本、Git 提交、运行时长、贴纸库统计 |
| 群聊 `@机器人 关于` | 同上 |

---

### 5.28 错误通知

自动错误处理。用户收到通用失败提示，管理员（超级管理员）收到脱敏后的异常 traceback 通知。

---

## 六、AstrBot 插件兼容层

**插件目录：** [`plugins/astrbot_compat/`](plugins/astrbot_compat/)

HIKARI BOT NEO 提供了一层 AstrBot 插件兼容适配器，让社区开发的 AstrBot 插件可以直接在机器人上运行。适配器通过 Shim（胶水层）模拟 AstrBot 的核心 API，将插件注册的命令、正则表达式和消息处理器桥接到机器人的 `command_router` 和 NoneBot 事件系统。

### 6.1 概述

```text
AstrBot 插件 (main.py)
  ↓ 调用 AstrBot API
Shim 层 (astrbot.api.*)
  ↓ 转为内部调用
兼容层 (plugins/astrbot_compat/)
  ├─ Loader  — 动态导入插件、扫描 Star 子类、注册处理器
  ├─ Manager — /astrbot 命令管理、生命周期
  ├─ Config  — _conf_schema.json → 配置持久化
  └─ Venv    — 公共虚拟环境隔离依赖
  ↓ 注册到
command_router / NoneBot matcher
```

插件上传到 `UserData/astrbot_plugins/` 后被自动发现，或通过 `/astrbot load` 命令 / Web 面板手动加载。

### 6.2 支持的 AstrBot API

| API | 支持情况 | 备注 |
|-----|----------|------|
| `Star` 基类 + `PluginKVStoreMixin` | ✅ | 文件 JSON 持久化 KV 存储 |
| `@register(name, author, desc, version)` | ✅ | 设置插件元数据 |
| `@filter.command(name, alias)` | ✅ | 含参数自动解析（int/float/bool/GreedyStr） |
| `@filter.regex(pattern)` | ✅ | 匹配组注入 handler **kwargs |
| `@filter.on_message()` | ✅ | 所有消息处理器 |
| `@filter.command_group()` | ✅ | 子命令分组 |
| `@filter.permission()` / `@filter.event_message_type()` | ✅ | 作用域和权限过滤 |
| `AstrMessageEvent` | ✅ | 包装 OneBot V11 MessageEvent |
| `event.plain_result()` / `image_result()` / `chain_result()` | ✅ | 回复构建 |
| `MessageChain` / `MessageEventResult` | ✅ | 链式构建 + 传播控制 |
| 消息组件（Plain, Image, At, Reply, Share, Record, Video 等） | ✅ | 自动转为 OneBot MessageSegment |
| `AstrBotConfig` | ✅ | 字典式 JSON 配置 + 自动写盘 |
| `text_to_image()` / `html_render()` | ✅ | 委托 `core.rendering` |
| `Context.get_config()` | ✅ | 返回当前插件配置 |
| `Context.send_message()` | ✅ | 按 session 发送消息 |
| `Context.llm_generate()` / `tool_loop_agent()` | ✅ | 桥接到内置 AI Agent |
| `Context.get_all_stars()` / `get_registered_star()` | ✅ | 插件信息查询 |
| `initialize()` / `terminate()` 生命周期 | ✅ | 加载 / 卸载时自动调用 |
| `metadata.yaml` | ✅ | name/version/author/tags/repo 元数据 |
| `_conf_schema.json` | ✅ | 自动生成默认配置 + Web 表单 |
| `requirements.txt` | ✅ | 自动安装到公共 venv |
| `Context.llm.*`（具体 LLM 调用） | ✅ | 复用 bot 的 AI Agent 配置 |
| `Context.get_db()` | ❌ | 无对应键值/向量存储抽象 |
| `@register_platform_adapter` | ❌ | 工作量过大（相当于半个 bot） |
| Plugin Pages (WebUI) | ⚠️ | 基础支持（通过 werkzeug.routing 动态路由），JS bridge 待完善 |
| 沙箱隔离 | ❌ | v1 暂不支持 |

### 6.3 架构

插件的消息处理流程在 NoneBot 优先级中的位置：

```text
Message from QQ → NapCat → OneBot V11 WS → NoneBot

  priority=0, block=False → core/command_router.py
    ├── 原生命令 (@command())
    └── AstrBot 命令 (@filter.command) — 由 Loader 注册进来

  priority=1, block=False → core/message_pipeline.py
    └── URL 自动解析 (register_handler)

  priority=2, block=False → astrbot_compat matcher
    ├── @filter.regex 匹配 → dispatch_regex_command()
    └── @filter.on_message → dispatch_on_message()

  更低优先级 → 其他插件（sticker_trigger, voice_trigger, aiagent...）
```

命令执行流程：

```text
用户发送 /trending
  ↓
command_router 匹配 trending 命令
  ↓
Loader 的 _wrapped_handler 桥接
  ↓
_create_astr_event() → 包装为 AstrMessageEvent
  ↓
_run_generator() → 消费 async generator
  ↓
每 yield 一个 MessageEventResult
  ↓
_send_result() → convert_chain_to_onebot()
  → MessageSegment 发送
```

### 6.4 加载插件

**方式一：上传压缩包（Web 面板）**

在 Bot 后台的"加载新插件"区域，选择 `.zip` 文件并上传。上传后自动解压、安装依赖并加载。

**方式二：服务器路径（命令）**

```text
/astrbot load /path/to/plugin.zip
/astrbot load /path/to/plugin_dir
/astrbot load BotData/uploads/my_plugin.zip  my-plugin-name
```

**方式三：自动发现**

重启机器人时，`UserData/astrbot_plugins/` 下有 `main.py` 的目录会被自动加载。

**管理命令（仅超级管理员私聊）：**

| 命令 | 效果 |
|------|------|
| `/astrbot list` | 列出已加载的插件 |
| `/astrbot load <路径> [插件名]` | 从目录或 zip 加载 |
| `/astrbot remove <插件名>` | 卸载插件 |
| `/astrbot reload <插件名>` | 重新加载插件 |
| `/astrbot info <插件名>` | 查看插件详情和配置 |
| `/astrbot rebuild-env` | 重建公共虚拟环境 |

### 6.5 依赖管理

每个插件目录下的 `requirements.txt` 会在加载时被读取。依赖安装到独立的公共虚拟环境（`UserData/astrbot_plugins/.venv/`），与机器人主环境隔离，避免污染 `uv.lock`。

```text
UserData/astrbot_plugins/
├── .venv/                  ← 公共插件 venv
│   └── Lib/site-packages/  ← 依赖安装到这里
├── plugin_A/
│   ├── main.py
│   └── requirements.txt
└── plugin_B/
    ├── main.py
    └── requirements.txt
```

**重建环境：** 移除插件后，残留依赖通过 `/astrbot rebuild-env` 命令一键重建（所有插件依赖从零安装）。

### 6.6 配置

插件通过 `_conf_schema.json` 声明配置结构和默认值。加载后配置保存到 `UserData/astrbot_plugins/<name>/config.json`。

```json
{
  "api_key": { "description": "API 密钥", "type": "string" },
  "max_results": { "description": "最大结果数", "type": "int", "default": 10 },
  "debug": { "description": "调试模式", "type": "bool", "default": false }
}
```

在 Web 面板中，这些配置项会自动渲染为表单（文本/数字/开关/JSON 编辑器），保存后立即生效。

### 6.7 Web 管理

Bot 后台（`:54213`）左侧增加「AstrBot」导航。功能包括：

- **插件列表** — 显示所有已加载和发现的插件，含状态（✅ 已加载 / ⏹️ 未加载）
- **插件详情** — 点击后显示作者、版本、描述、仓库地址、注册命令、依赖
- **配置表单** — 按 `_conf_schema.json` 自动生成（支持 string/int/float/bool/list/object）
- **操作按钮** — 加载、重载、卸载
- **上传插件** — 直接上传 zip 压缩包
- **路径加载** — 输入服务器本地路径
- **环境管理** — 一键重建公共虚拟环境

### 6.8 限制

| 限制 | 说明 |
|------|------|
| `Context.get_db()` | 无对应存储抽象，始终抛 NotImplementedError |
| 平台适配器 | `@register_platform_adapter` 未实现（工作量过大） |
| 插件 WebUI | Plugin Pages 基础支持（`register_web_api()` 接入 bot_admin 路由），JS bridge 待完善 |
| 沙箱隔离 | 插件代码与机器人进程相同权限，加载前请确认来源可信 |
| 超大消息 | 渲染图片超过 ~900 KB 时自动保存到 `sharedFolder/astrbot_temp/` 后引用 |
| LLM 工具注册 | `Context.register_llm_tool()` 仅做日志记录，不影响内置 AI Agent 的工具集 |

插件加载后可通过 `/astrbot list` 确认状态，`/astrbot info <名>` 查看详细信息。若加载失败，检查 Bot 日志中 `AstrBotCompat.*` 的报错。

---

## 七、核心模块

核心模块位于 [`core/`](core/) 目录，提供机器人底层能力。

### 消息处理流程

```
Message from QQ → NapCat → OneBot V11 WS → NoneBot

  priority=0, block=False → core/command_router.py
    - 显式命令路由，@command() 装饰器注册
    - 创建 CommandContext，匹配成功标记已处理

  priority=1, block=False → core/message_pipeline.py
    - URL/自动解析处理器，register_handler() 注册
    - URLHandler 协议 match + handle
    - 被 command_router 处理的消息跳过

  其余插件 (on_message, priority=...)
    - AI Agent 最低优先级兜底
```

### 模块清单

| 模块 | 职责 |
|------|------|
| [`config_loader.py`](core/config_loader.py) | 加载主配置 + 插件配置，深合并默认值，mtime/size 热重载 |
| [`command_router.py`](core/command_router.py) | 显式命令分发，`@command()` 装饰器，priority=0 |
| [`message_pipeline.py`](core/message_pipeline.py) | URL 自动解析注册器，`register_handler()`，priority=1 |
| [`rendering.py`](core/rendering.py) | 图片文字渲染，`load_font()` 从配置读取字体链 |
| [`bot_messages.py`](core/bot_messages.py) | 用户面向回复，`get_message(key)` 从 `bot_messages.json` 读取 |
| [`ai_tool_registry.py`](core/ai_tool_registry.py) | `register_ai_tool()` 暴露插件函数为 AI Agent 工具 |
| [`access_control.py`](core/access_control.py) | QQ/群黑白名单检查 |
| [`error_notifier.py`](core/error_notifier.py) | 用户友好错误提示 + 管理员 traceback 通知 |
| [`lifecycle_logging.py`](core/lifecycle_logging.py) | 启动摘要、插件加载日志、事件描述辅助 |
| [`temp_media_cleaner.py`](core/temp_media_cleaner.py) | 定时清理临时下载媒体 |
| [`activity_tracker.py`](core/activity_tracker.py) | 实时活动跟踪，供 Admin 总览页展示 |
| [`stats_tracker.py`](core/stats_tracker.py) | 会话使用统计 |
| [`bot_identity.py`](core/bot_identity.py) | 机器人名称/身份，从配置读取 |
| [`resources.py`](core/resources.py) | 加载/回填 `BotData/resources/` 下的 JSON 资源 |
| [`runtime_info.py`](core/runtime_info.py) | 运行时长、版本信息（`version.json`） |

---

## 八、可热改资源

**目录：** `BotData/resources/`

首次启动时从 `.example.json` 自动生成真实资源文件。修改后不需要重新构建项目镜像；机器人运行中会按文件修改时间重新读取。

### 生成图片字体

**配置文件：** `BotData/resources/rendering.json`

推荐准备两个字体文件：
- 常规字重：`BotData/fonts/MyFont-Regular.ttf`
- 粗体字重：`BotData/fonts/MyFont-Bold.ttf`

```json
{
  "font_regular": "BotData/fonts/MyFont-Regular.ttf",
  "font_bold": "BotData/fonts/MyFont-Bold.ttf",
  "fallback_fonts_regular": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"],
  "fallback_fonts_bold": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"]
}
```

如果不放自定义字体，运行容器会安装 `fonts-noto-cjk` 做 fallback。

### 机器人固定回复

**配置文件：** `BotData/resources/bot_messages.json`

常见的固定回复已抽到该 JSON（错误提示、JMComic、Pixiv/Cobalt 部分错误、贴纸命令提示等）。修改后下一次发送对应消息时会读取新内容。

---

## 九、NapCat 文件目录

机器人会把图片、视频、贴纸、PDF 等临时文件放到 `/tmp/hikari_bot`。NapCat 必须能读取这个目录，否则会出现"解析成功但发送失败"。

各插件的临时媒体默认 10 分钟后清理（通过 `cache_ttl_seconds` 调整）。

Docker 部署时请挂载共享目录：

```yaml
services:
  napcat:
    volumes:
      - ./runtime/tmp/hikari_bot:/tmp/hikari_bot
```

---

## 十、项目结构

```text
HIKARI_BOT_NEO/
├── bot.py                              # 程序入口
├── pyproject.toml                      # Python 依赖和 NoneBot 配置
│
├── core/                               # 核心模块
│   ├── command_router.py               #   命令路由
│   ├── config_loader.py                #   配置加载（含热重载）
│   ├── message_pipeline.py             #   自动解析管道
│   ├── rendering.py                    #   图片渲染
│   ├── bot_messages.py                 #   固定回复
│   ├── ai_tool_registry.py             #   AI 工具注册
│   ├── access_control.py               #   黑白名单
│   ├── error_notifier.py               #   错误通知
│   ├── activity_tracker.py             #   实时活动跟踪
│   ├── stats_tracker.py                #   会话统计
│   ├── bot_identity.py                 #   机器人身份
│   ├── resources.py                    #   资源加载
│   ├── runtime_info.py                 #   运行时信息
│   ├── lifecycle_logging.py            #   生命周期日志
│   └── temp_media_cleaner.py           #   临时媒体清理
│
├── plugins/                            # 插件目录
│   ├── pixiv_parser/                   #   Pixiv 作品解析
│   ├── media_parser/                   #   聚合媒体解析适配层
│   ├── cobalt_parser/                  #   Instagram / Facebook 解析
│   ├── netease_parser/                 #   网易云音乐解析
│   ├── youtube_downloader/             #   YouTube 视频下载
│   ├── media_detail_web/               #   媒体详情 Web 页面
│   ├── tg_sticker_parser/              #   Telegram 贴纸包导入
│   ├── sticker_trigger/                #   本地贴纸触发
│   ├── sticker_collector/              #   贴纸静默收集
│   ├── voice_trigger/                  #   本地语音触发
│   ├── tts_speaker/                    #   Fish Audio TTS
│   ├── aiagent/                        #   AI Agent 聊天
│   ├── bot_admin/                      #   Web 管理后台
│   ├── bot_help/                       #   帮助 / 关于
│   ├── push_framework/                 #   定时推送框架
│   ├── steam_deals/                    #   Steam 日报
│   ├── ai_news/                        #   AI 资讯日报
│   ├── zhihu_hot/                      #   知乎热搜
│   ├── rss_subscriber/                 #   RSS 订阅
│   ├── osu_info/                       #   osu! 信息查询
│   ├── stardew_wiki/                   #   星露谷物语 Wiki
│   ├── mc_wiki/                        #   Minecraft Wiki
│   ├── sts2_wiki/                      #   杀戮尖塔 2 Wiki
│   ├── jmcomic_api/                    #   JMComic PDF
│   ├── profile_like/                   #   QQ 资料卡点赞
│   ├── mention_reaction/               #   空 @ 表情回应
│   ├── poke_back/                      #   戳一戳回戳
│   ├── media_transcoder/               #   媒体转码服务
│   ├── astrbot_compat/                 #   AstrBot 插件兼容层
│   └── sticker_web/                    #   旧后台兼容占位
│
├── third_party/                        # 上游 vendored 代码
│   └── astrbot_plugin_media_parser/    #   聚合媒体解析器（AGPL）
│
├── BotData/                            # 运行时数据
│   ├── config.json                     #   主配置（不提交）
│   ├── config.example.json             #   配置模板
│   ├── plugin_configs/                 #   插件配置
│   ├── resources/                      #   热改资源（rendering, messages）
│   ├── fonts/                          #   自定义字体
│   ├── Gifs/_library/                  #   贴纸统一文件库
│   ├── Voices/_library/                #   语音统一文件库
│   └── agent_personas/                 #   AI 人格 skill
│
├── UserData/                           # 用户数据（选择性忽略 git）
│   ├── astrbot_plugins/                #   AstrBot 兼容插件
│   ├── stats/                          #   会话统计
│   ├── aiagent_memory/                 #   AI 持久化记忆
│   └── osu_bindings.json              #   osu! 绑定数据
│
├── docker/                             # Docker 相关
│   └── entrypoint.sh                   #   容器启动脚本
│
├── deploy/                             # 部署配置
│   ├── docker-compose.server.yml       #   服务器 Compose 编排
│   └── searxng/                        #   SearXNG 配置
│
├── docs/                               # 文档
│   └── API.md                          #   HTTP API 文档
│
├── scripts/                            # 辅助脚本
│   └── update_media_parser_vendor.ps1  #   更新 vendored 解析器
│
├── deploy.ps1                          # SSH 部署脚本（PowerShell）
├── install.sh                          # 一键安装脚本（Linux）
├── install.ps1                         # 一键安装脚本（PowerShell）
├── LICENSE                             # AGPL v3 许可证
├── USER_AGREEMENT.md                   # 用户协议模板
└── PRIVACY_POLICY.md                   # 隐私政策模板
```

---

## 十一、常见问题

| 症状 | 常见原因 | 处理方式 |
|------|----------|----------|
| 启动后机器人不在线 | NapCat WebSocket 地址或 Token 错误 | 检查 `BotData/config.json` 的 `ws_url` 和 `token` |
| `tg贴纸` 没有反应 | 插件关闭、链接不匹配、NapCat 未连接 | 检查配置和日志 |
| 图片或视频发送失败 | NapCat 读不到临时文件 | 挂载共享目录，检查 `PrivateTmp` |
| Pixiv 403 / Cloudflare | Cookie 失效或不完整 | 更新 Cookie，必要时补 `cf_clearance` |
| Pixiv 连接失败 | 网络无法直连 | 配置 `proxy` |
| Instagram / Facebook 解析失败 | cobalt API 不可用 | 确认 `cobalt_api` 地址正确 |
| 抖音/B站/小红书等解析失败 | 平台风控、Cookie 失效、代理不可用 | 检查配置、代理和日志 |
| Telegram 贴纸解析失败 | Token 未配置或无法访问 Telegram API | 填写 Token，配置代理 |
| Telegram 动态贴纸转换失败 | 缺少转换依赖 | 检查 ffmpeg、lottie 命令 |
| JSON 配置报错 | 格式错误 | 运行 `python -m json.tool <文件>` 检查 |

---

## 十二、开发说明

- **插件目录：** 由 `pyproject.toml` 中的 `plugin_dirs = ["plugins"]` 配置。
- **自动解析：** `core.message_pipeline` 注册全局管道，插件通过 `register_handler()` 接入。
- **热重载：** 插件配置修改 JSON 后下条消息即可生效。
- **不提交：** `BotData/config.json`、`BotData/plugin_configs/*.json`、`UserData/stats`、日志和媒体文件。
- **配置文件：** 定义默认值 → `config_loader.load_plugin_config("name", DEFAULT)` 深合并用户 JSON。
- **命令注册：** `@command()` 装饰器，`CommandContext` 提供解析参数和作用域。
- **AI 工具注册：** `@register_ai_tool()`，默认只读，返回 JSON 序列化数据。
- **图片渲染：** 始终使用 `core.rendering.load_font()`，避免固定宽度布局。
- **用户回复：** 通过 `core.bot_messages.get_message()`，不硬编码文本。

**验证命令：**

```bash
# Python 语法检查
uv run python -m compileall <changed paths>

# 运行全部测试
uv run python -m unittest discover -s tests

# 单个测试
uv run python -m unittest tests.test_<name>

# JSON 校验
python -m json.tool BotData/plugin_configs/<file>.json

# JS 语法检查
node --check plugins/bot_admin/static/<file>.js
```

---

## 十三、许可证与致谢

### 许可证

本项目使用 [GNU Affero General Public License v3.0 or later](LICENSE) 开源。

使用本项目解析、下载或转发第三方平台内容时，请自行确认相关平台服务条款和内容版权要求。

### 参考与致谢

- [NoneBot 2](https://github.com/nonebot/nonebot2) — 机器人框架
- [NapCatQQ](https://github.com/NapNeko/NapCatQQ) — QQ / OneBot V11 接入
- [drdon1234/astrbot_plugin_media_parser](https://github.com/drdon1234/astrbot_plugin_media_parser) — 聚合媒体解析能力（AGPL）
- [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp) — YouTube 等站点视频信息提取与下载
- [imputnet/cobalt](https://github.com/imputnet/cobalt) — Instagram / Facebook 媒体解析 API
- [searxng/searxng](https://github.com/searxng/searxng) — AI Agent 搜索工具的元搜索服务
- [valkey-io/valkey](https://github.com/valkey-io/valkey) — SearXNG 缓存服务
- [NeteaseCloudMusicApiEnhanced/api-enhanced](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced) — 网易云音乐解析 API

---

## 十四、用户协议与隐私政策

仓库提供了面向自部署场景的 [用户协议模板](USER_AGREEMENT.md) 和 [隐私政策模板](PRIVACY_POLICY.md)。实际部署前，请将服务运营者、联系方式、数据保存期限和第三方服务配置补充为你的真实情况。
