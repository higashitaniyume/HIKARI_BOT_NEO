# HIKARI BOT NEO

HIKARI BOT NEO 是一个基于 [NoneBot 2](https://nonebot.dev/) 的 QQ 机器人，通过 NapCat 的 OneBot V11 WebSocket 接入 QQ。它主要用于自动解析 QQ 消息里的媒体链接，并提供贴纸包、错误通知等辅助能力。

> [!IMPORTANT]
> 机器人本体不读取 `.env`。机器人运行配置来自 `BotData/config.json` 和 `BotData/plugin_configs/*.json`；根目录 `.env` 只给 Docker Compose 设置端口、Python 基础镜像和 NapCat 账号。

---

## 功能概览

| 功能 | 触发方式 | 说明 |
|------|----------|------|
| Pixiv 作品解析 | 直接发送 Pixiv 作品链接 | 下载并发送作品图片，支持多图合并转发 |
| 聚合媒体解析 | 直接发送抖音/B站/小红书/小黑盒等链接；或 `媒体解析 <链接>` | 基于 `astrbot_plugin_media_parser` 解析并发送支持平台的文本、图片和视频 |
| Instagram / Facebook 解析 | Instagram / Facebook 链接 | 通过自部署 cobalt API 解析并发送图片/视频 |
| YouTube 视频下载 | 直接发送 YouTube 链接 | 使用 yt-dlp 下载视频并发送，默认限制 1GB |
| 媒体详情 Web | 浏览器打开 `http://服务器IP:53123/` | 输入 URL 后查看机器人支持平台的解析详情、预览和下载媒体 |
| Telegram 贴纸包解析 | `tg贴纸 <https://t.me/addstickers/set>` | 拉取贴纸包，调用统一转码服务转换为 GIF，保存成本地贴纸包 |
| 本地贴纸包 | 关键词、`贴纸包 随机`、`贴纸包 拼图 <关键词>` | 从本地贴纸库随机发送贴纸或生成拼图 |
| 本地语音触发 | 关键词 | 从本地语音库发送指定语音 |
| TTS 说话 | `说话 <文本>`、`音色列表`、`切换音色 <名称>` | 使用 Fish Audio 当前音色合成语音并发送 |
| AI Agent 聊天 | 私聊直接发送文本；群聊 @机器人；其他插件未处理时才回复 | 调用兼容 OpenAI Chat Completions 的模型，并读取 BotData 中的女娲人格 skill 和持久化记忆 |
| Bot 后台 | 浏览器打开 `http://服务器IP:54213/` | 管理贴纸包、语音文件和触发关键词 |
| JMComic PDF | 私聊 `jm <id>` | 下载并转换 PDF 后通过私聊发送，群聊不解析 |
| osu! 信息查询 | `osu` / `osu 绑定` / `osu 谱面` / `osu 下载` 等命令 | 查询用户、看板、成绩、排行榜、谱面，支持官方源谱面下载；查询结果以图片发送 |
| Steam 热门热卖日报 | `steam日报` / `steam免费` / `steam低价` | 查询 Steam 热门热卖、免费、低价和大折扣游戏；可配置每日白名单主动推送 |
| AI 最新资讯日报 | `ai资讯`；推送源 `ai_news` | 聚合 AI 官方、研究、社区和媒体 RSS，去重筛选后渲染成资讯图片 |
| 知乎热搜 | `知乎热搜`；推送源 `zhihu_hot` | 读取知乎热榜问题，按热度和排名渲染成热搜图片 |
| 定时推送框架 | `推送 状态` / `推送 触发 <任务ID>` | 提供可注册消息源的通用定时推送能力 |
| 星露谷物语 Wiki | `星露谷wiki <关键词>` | 搜索中文 Stardew Valley Wiki，以合并转发返回链接、详细描述和主图 |
| Minecraft Wiki | `mcwiki <关键词>` | 搜索中文 Minecraft Wiki，以合并转发返回链接、详细描述和主图 |
| 杀戮尖塔 2 Wiki | `塔2wiki <关键词>` / `sts2 <关键词>` | 搜索 wiki.gg 的 Slay the Spire Wiki，返回《杀戮尖塔 2》相关条目摘要和链接 |
| 帮助信息 | 私聊 `帮助`；群聊 `@机器人 帮助` | 查看可用能力和用法 |
| 关于信息 | 私聊 `关于`；群聊 `@机器人 关于` | 查看机器人描述、当前版本、Git 提交标题、运行时长和贴纸库统计 |
| 错误通知 | 自动 | 用户收到通用失败提示，管理员收到脱敏后的异常 |

本仓库当前包含 Pixiv、抖音、Bilibili、TikTok、快手、微博、小红书、闲鱼、今日头条、小黑盒、Twitter/X、Instagram/Facebook、YouTube、媒体详情 Web、Telegram 贴纸、本地贴纸、本地语音、TTS、AI Agent、JMComic、osu!、Steam 热门热卖日报、AI 最新资讯日报、知乎热搜、定时推送框架、星露谷物语 Wiki、Minecraft Wiki 和杀戮尖塔 2 Wiki 相关实现。聚合媒体解析能力来自 vendored 的 `drdon1234/astrbot_plugin_media_parser`。

---

## 运行环境

- 推荐部署方式：Docker + Docker Compose
- 本地开发：Python `>=3.10`，并使用 [uv](https://docs.astral.sh/uv/) 安装依赖
- QQ 接入：NapCat，并开启 OneBot V11 WebSocket 服务
- 聚合媒体解析：依赖 `third_party/astrbot_plugin_media_parser`，部分平台建议配置代理；B站高画质、m3u8/DASH 合并和封面截帧依赖 `ffmpeg`
- Instagram / Facebook 解析：自部署 [cobalt](https://github.com/imputnet/cobalt)
- YouTube 下载：服务器需要能访问 YouTube；高质量视频合并依赖 `ffmpeg`
- Telegram 贴纸解析：Telegram Bot Token，并保证服务器能访问 Telegram API
- 贴纸素材转换：运行容器首次启动时会安装 `ffmpeg`、Cairo/Pango 等转换依赖，并缓存在容器与 Python 依赖卷中

---

## 快速部署

推荐使用 Docker Compose 部署。本项目不再推荐 systemd 部署。

Compose 默认启动 5 个服务：

| 服务 | 作用 | 默认端口 |
|------|------|----------|
| `hikaribot` | 本项目机器人、Bot 后台和媒体详情 Web | `54213`、`53123` |
| `napcat` | QQ / OneBot 接入 | `3000`、`6099`、`54253` 等 |
| `cobalt` | Instagram / Facebook 媒体解析 API | `54257` |
| `searxng` | AI Agent 搜索服务 | `54261` |
| `searxng-valkey` | SearXNG 缓存 | 内部服务 |

运行数据会保存在 compose 所在目录，主要包括 `BotData/`、`UserData/`、`napcat/`、`searxng/` 与统一的 `runtime/`。其中 `runtime/shared/` 用于跨容器共享文件，`runtime/tmp/hikari_bot/` 存放 NapCat 可读取的临时媒体。删除容器不会删除这些数据。

### 源码挂载部署（推荐）

机器人不再构建或分发项目 Docker 镜像。Compose 直接拉取官方 Python 基础镜像，把项目源码目录只读挂载进容器；依赖安装在名为 `hikaribot_venv` 的 Docker volume 中。更新代码时仅同步源码并重启 `hikaribot`，启动过程会按 `uv.lock` 自动同步 Python 依赖。

服务器需要预先安装 Docker Engine、Docker Compose v2 和 Git。新用户只需运行下面一条命令：

```bash
curl -fsSL https://raw.githubusercontent.com/higashitaniyume/HIKARI_BOT_NEO/main/install.sh | sudo sh
```

PowerShell（Windows 或已安装 PowerShell 的 Linux）可使用：

```powershell
irm https://raw.githubusercontent.com/higashitaniyume/HIKARI_BOT_NEO/main/install.ps1 | iex
```

该脚本会拉取源码到 `/opt/hikaribot-docker/app/`、创建持久化数据目录和 `.env`，然后执行 `docker compose up -d` 启动全部服务。

部署编排会同时启动 SearXNG 搜索服务和 Valkey 缓存，供 AI Agent 的搜索工具使用。默认外部端口为 `54261`，可在 `.env` 中通过 `SEARXNG_HOST`、`SEARXNG_PORT` 和 `SEARXNG_VERSION` 调整；SearXNG 配置目录位于部署根目录的 `searxng/core-config/`。

如果需要使用镜像站、私有仓库或其他目录，可以在执行时覆盖环境变量：

```bash
curl -fsSL https://raw.githubusercontent.com/higashitaniyume/HIKARI_BOT_NEO/main/install.sh | sudo env HIKARI_REPOSITORY_URL=https://example.com/HIKARI_BOT_NEO.git HIKARI_DEPLOY_DIR=/opt/hikari sh
```

脚本会保护 `app/` 中的本地源码改动；发现未提交或未跟踪文件时会停止，而不是强制覆盖。

也可以手动完成同样的步骤：

```bash
git clone <本仓库地址> /opt/hikaribot-docker/app
cd /opt/hikaribot-docker
cp app/deploy/docker-compose.server.yml docker-compose.yml
cp app/.env.example .env
mkdir -p searxng/core-config && cp app/deploy/searxng/core-config/settings.yml searxng/core-config/settings.yml
sed -i "s/__SEARXNG_SECRET__/$(openssl rand -hex 32)/g" searxng/core-config/settings.yml
docker compose up -d
```

首次启动会在 `/opt/hikaribot-docker/BotData/` 中生成真实配置文件。编辑这些配置，至少修改：

| 文件 | 必改项 |
|------|--------|
| `BotData/config.json` | `bot.superuser_id`、`napcat.token` |
| `BotData/plugin_configs/pixiv_parser.json` | Pixiv Cookie 或代理，按需填写 |
| `BotData/plugin_configs/youtube_downloader.json` | 无必填项；如遇 YouTube 登录验证，可配置 `cookiefile` |
| `BotData/plugin_configs/media_detail_web.json` | 无必填项；默认监听 `53123` |
| `BotData/plugin_configs/osu_info.json` | osu! OAuth 客户端 ID 和客户端密钥，按需填写 |
| `BotData/plugin_configs/steam_deals.json` | 无必填项；每日主动推送需开启 `schedule.enabled` 并填写 `push_whitelist` |
| `BotData/plugin_configs/ai_news.json` | 无必填项；AI 资讯推送源默认使用公开 RSS/Atom 源，可按需增删 `sources` |
| `BotData/plugin_configs/zhihu_hot.json` | 无必填项；知乎热搜推送源默认读取公开热榜接口 |
| `BotData/plugin_configs/push_framework.json` | 无必填项；通用推送需启用对应 `jobs` 并填写目标 |
| `BotData/plugin_configs/rss_subscriber.json` | 无必填项；RSS 主动推送需在 `subscriptions` 中添加订阅，并在推送任务里引用 |
| `BotData/plugin_configs/bot_admin.json` | `password` |
| `BotData/plugin_configs/tg_sticker_parser.json` | Telegram Bot Token，按需开启 |
| `BotData/plugin_configs/stardew_wiki.json` | 无必填项，默认使用中文 Wiki |
| `BotData/plugin_configs/mc_wiki.json` | 无必填项，默认使用中文 Minecraft Wiki |
| `BotData/plugin_configs/sts2_wiki.json` | 无必填项，默认使用 wiki.gg 的 Slay the Spire Wiki |
| `BotData/plugin_configs/profile_like.json` | 无必填项；`点赞` 默认点满 QQ 资料卡赞 |
| `BotData/plugin_configs/poke_back.json` | 无必填项；被戳一戳时自动戳回 |

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

编辑 `.env`，按需填写：

```text
NAPCAT_ACCOUNT=你的QQ号
```

### 部署到服务器

仓库内的 `deploy.ps1` 会通过 SSH 将受 Git 管理的源码同步到服务器的 `app/` 目录，默认部署到 `root@192.168.31.2:/opt/hikaribot-docker`。其他服务器可以改参数：

```powershell
.\deploy.ps1 -ServerIP 你的服务器IP -ServerUser root -DeployPath /opt/hikaribot-docker -NapcatAccount 你的QQ号
```

`deploy.ps1` 会用 `7z` 将本地源码打包后上传，运行脚本的机器和服务器都需要能执行 `7z` 命令。Bot 后台下载贴纸包时也会调用容器内的 7-Zip 命令行工具；Docker 启动脚本会自动安装 Debian 的 `7zip` 包。

部署和安装脚本会在启动前刷新根目录的 `version.json`，根据 Git 历史写入 `0.0.x` 递增版本、短 hash 和提交标题。`关于` 命令与 Bot 后台总览页都会读取这个文件显示当前版本；后台还会显示完整版本历史。`version.json` 是部署生成物，不需要提交到 Git。

这个脚本会：

1. 首次使用时把历史目录 `/opt/hikaribot-dockcer` 迁移为正确的 `/opt/hikaribot-docker`，并保留所有运行数据
2. 上传源码到 `/opt/hikaribot-docker/app/`，不会上传 `.env`、真实配置、用户数据或媒体文件
3. 上传服务器 Compose 文件
4. 默认更新并重启 `hikaribot`；当共享目录挂载发生变化时，Compose 会自动重建 `napcat` 以保持同一共享目录，`cobalt` 不受影响

如果是第一次在服务器部署，或者确实想更新所有服务：

```powershell
.\deploy.ps1 -AllServices
```

项目源码、持久化数据和容器内依赖的职责如下：

| 位置 | 用途 | 更新时是否覆盖 |
|------|------|----------------|
| `/opt/hikaribot-docker/app/` | 机器人源码、静态资源与 example 配置 | 是 |
| `/opt/hikaribot-docker/BotData/`、`UserData/` 等 | 真实配置、贴纸、语音、日志与用户数据 | 否 |
| `/opt/hikaribot-docker/runtime/` | `shared/` 跨容器共享文件与 `tmp/hikari_bot/` 临时媒体 | 否 |
| Docker volume `hikaribot_hikaribot_venv` | Python 依赖与启动标记 | 仅在依赖锁文件变化时同步 |

### 访问地址

部署完成后，常用访问地址：

| 服务 | 地址 |
|------|------|
| Bot 后台 | `http://服务器IP:54213/` |
| 媒体详情 Web | `http://服务器IP:53123/` |
| NapCat WebUI | `http://服务器IP:3000/` |
| cobalt API | `http://服务器IP:54257/` |

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

## 本地开发

### 1. 安装依赖

```bash
uv sync
```

### 2. 生成配置

首次启动会自动创建默认配置文件：

```bash
uv run python bot.py
```

也可以复制示例配置：

```bash
cp BotData/config.example.json BotData/config.json
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
cp BotData/plugin_configs/poke_back.example.json BotData/plugin_configs/poke_back.json
```

### 3. 修改主配置

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

### 4. 启动

```bash
uv run python bot.py
```

启动后，在 QQ 里发送一个 Pixiv 作品链接或贴纸关键词进行测试。

---

## 插件配置

### Pixiv

配置文件：`BotData/plugin_configs/pixiv_parser.json`

关键字段：

| 字段 | 说明 |
|------|------|
| `cookie` | Pixiv Cookie。遇到 403 或 Cloudflare 拦截时通常需要补全 |
| `proxy` | Pixiv 请求代理，例如 `http://127.0.0.1:7890` |
| `auto_parse` | 是否自动解析消息中的 Pixiv 链接 |
| `max_send` | 单次最多发送图片数 |
| `allow_r18` | 是否允许 R18 内容 |
| `send_link_info` | 是否发送作品标题、作者、链接等详情；设为 `false` 时只发送图片 |
| `cache_dir` | 下载缓存目录，默认 `/tmp/hikari_bot` |
| `cache_ttl_seconds` | 下载媒体保留时间，默认 `600` 秒 |

支持链接形态：

- `https://www.pixiv.net/artworks/<pid>`
- `https://www.pixiv.net/i/<pid>`

不支持纯数字 PID、`pid:` 格式、用户主页、tag、novel 等链接。

### Instagram / Facebook

配置文件：`BotData/plugin_configs/cobalt_parser.json`

本插件依赖自部署 cobalt API。不要直接使用 `api.cobalt.tools`，官方实例通常有 bot 保护，主要供 cobalt 前端使用。

关键字段：

| 字段 | 说明 |
|------|------|
| `cobalt_api` | 自部署 cobalt API 地址，例如 `http://127.0.0.1:9000/` |
| `api_key` | cobalt API Key，可为空 |
| `api_timeout` | API 超时时间 |
| `max_send` | 单次最多发送媒体数 |
| `send_link_info` | 是否发送来源、数量、链接等详情；设为 `false` 时只发送媒体 |
| `cache_dir` | 下载缓存目录 |
| `cache_ttl_seconds` | 下载媒体保留时间，默认 `600` 秒 |

支持 Instagram 的 `p`、`reel`、`stories`、`tv` 链接，以及 `facebook.com`、`fb.com`、`fb.watch` 链接。

### 聚合媒体解析

配置文件：`BotData/plugin_configs/media_parser.json`

本插件基于 vendored 的 [`drdon1234/astrbot_plugin_media_parser`](third_party/astrbot_plugin_media_parser) 解析平台链接，并用 HIKARI 的 NoneBot/OneBot 发送链发送文本、图片和视频。

支持平台与上游当前主线一致：B站、抖音、TikTok、快手、微博、小红书、闲鱼、今日头条、小黑盒、Twitter/X。YouTube 仍由本仓库独立的 `youtube_downloader` 插件处理。

关键字段：

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用聚合媒体解析 |
| `trigger.auto_parse` | 是否自动解析消息里的支持平台链接 |
| `max_links_per_message` | 单条消息最多处理几个链接 |
| `parse_retry_count` | 解析/下载失败后的重试次数，默认 `2`；每次重试会重新解析链接并重新获取媒体地址 |
| `parse_retry_delay_seconds` | 每次解析/下载重试前等待秒数，默认 `2.0` |
| `parse_queue.enabled` | 是否启用解析队列；启用后链接会进入后台解析/下载 worker |
| `parse_queue.max_size` | 等待解析的最大链接数 |
| `parse_queue.max_concurrent` | 同时解析/下载的链接数；建议保持小并发，避免平台风控和本机 IO 抢占 |
| `parse_queue.delay_seconds` | 每个解析 worker 完成一个任务后的等待秒数 |
| `max_send` | 单条链接最多发送多少个图片/视频节点，默认 80；合并转发每包也按这个数量打包 |
| `send_strategy.forward_timeout_seconds` | 单次合并转发 OneBot 调用的超时时间；超时后按策略回退到逐条发送 |
| `parsers.<平台>` | 每个平台的输出模式：`关闭`、`全部发送`、`仅文本`、`仅富媒体` |
| `permissions.whitelist` / `permissions.blacklist` | 插件自己的 QQ/群黑白名单，可在 Bot 后台“权限”页管理 |
| `download.cache_dir` | 媒体缓存目录；Docker/NapCat 部署时应放在双方都能访问的 `/tmp/hikari_bot` 子目录 |
| `download.cache_ttl_seconds` | 下载媒体保留时间，默认 `600` 秒；只清理解析生成的媒体子目录，不清理 B站 Cookie 等运行时数据 |
| `download.max_video_size_mb` | 单个视频大小上限 |
| `proxy.address` | 代理地址，例如 `http://127.0.0.1:7890` |
| `bilibili_enhanced.cookie` | 可选 B站 Cookie，用于高画质和受限内容解析 |
| `bilibili_enhanced.admin_assist.enable` | B站 Cookie 不可用时是否私聊超级管理员协助扫码登录 |
| `message.media_display.video_cover_only` | 是否把视频改为只发封面 |

开启 `bilibili_enhanced.use_cookie` 且开启 `bilibili_enhanced.admin_assist.enable` 后，B站 Cookie 缺失或失效时，Bot 会私聊 `BotData/config.json` 里的 `bot.superuser_id`。超级管理员回复“确定”后会收到 Bilibili 登录二维码图片和备用登录链接；扫码成功后，新 Cookie 会保存到 `download.cache_dir/runtime_manager/bilibili/cookie.json`，无需手动替换配置文件里的 Cookie。超级管理员也可以发送 `B站登录` / `B站Cookie` 手动触发私聊二维码登录。

显式命令：

```text
媒体解析 <链接>
解析媒体 <链接>
视频解析 <链接>
B站登录
B站Cookie
```

上游更新：

```powershell
.\scripts\update_media_parser_vendor.ps1
uv run python -m compileall plugins\media_parser third_party\astrbot_plugin_media_parser
```

更新脚本会替换 `third_party/astrbot_plugin_media_parser/`，HIKARI 自己的 NoneBot 适配代码仍保留在 `plugins/media_parser/`。

### YouTube 视频下载

配置文件：`BotData/plugin_configs/youtube_downloader.json`

本插件使用 `yt-dlp` 下载 YouTube 视频。直接发送 YouTube 视频链接即可触发解析；默认一条消息最多处理 20 个链接，下载完成后先发送视频信息，再发送视频文件。

关键字段：

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用插件 |
| `auto_parse` | 是否自动解析消息中的 YouTube 链接 |
| `max_links_per_message` | 单条消息最多处理几个 YouTube 链接，默认 `1` |
| `max_file_mb` | 视频文件大小上限，默认 `1024` MB |
| `max_height` | 默认最高下载清晰度，默认 `720` |
| `send_link_info` | 是否发送标题、频道、时长、链接等详情；设为 `false` 时只发送视频 |
| `download_timeout` | 单个视频下载超时时间，单位秒 |
| `cache_dir` | 下载缓存目录，默认 `/tmp/hikari_bot/youtube_downloader` |
| `cache_ttl_seconds` | 下载媒体保留时间，默认 `600` 秒 |
| `cookiefile` | 可选 yt-dlp cookies 文件路径；YouTube 要求登录验证时使用 |
| `format` | 可选 yt-dlp format selector；为空时使用插件默认选择 |

支持常见 `youtube.com/watch`、`youtube.com/shorts`、`youtube.com/live`、`youtu.be` 和 `youtube-nocookie.com/embed` 链接。播放列表不会批量下载，只处理单个视频。

### 媒体详情 Web

配置文件：`BotData/plugin_configs/media_detail_web.json`

本插件会单独启动一个 Web 页面，默认监听：

```text
0.0.0.0:53123
```

打开 `http://服务器IP:53123/` 后，可以粘贴 Pixiv、YouTube、Instagram/Facebook 或聚合媒体解析支持的平台链接，页面会展示标题、作者、描述、标签、媒体数量、跳过原因等详情，并为解析到的图片/视频提供浏览器预览和下载入口。

页面 HTML 文件位于：

```text
plugins/media_detail_web/templates/index.html
```

关键字段：

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用独立页面 |
| `host` / `port` | 监听地址和端口，默认 `0.0.0.0:53123` |
| `max_links_per_request` | 单次最多解析几个链接 |
| `auto_download` | 页面默认是否勾选“自动下载媒体” |
| `token_ttl_seconds` | 页面下载链接的内存 token 有效期 |
| `max_registry_entries` | 最多保留多少个下载 token |
| `max_remote_proxy_mb` | 未自动下载时，远程媒体经页面代理预览/下载的大小上限 |
| `operation_timeout_seconds` | 单次解析/下载请求最长执行时间 |

Docker 部署时，`hikaribot` 服务会把宿主机 `${HIKARI_MEDIA_DETAIL_WEB_PORT:-53123}` 映射到容器内 `53123`。

### 媒体转码

配置文件：`BotData/plugin_configs/media_transcoder.json`

贴纸相关插件统一调用这个转码服务。原则是：只要最终进入本地贴纸包，就必须保存为 GIF；普通 Pixiv、Cobalt、JMComic 等非贴纸媒体不走这里。

关键字段：

| 字段 | 说明 |
|------|------|
| `sticker_gif_fps` | 视频/WebP 动态贴纸转 GIF 的帧率 |
| `sticker_gif_width` | 转 GIF 宽度；`0` 表示尽量保持原尺寸 |
| `sticker_gif_max_colors` | GIF 调色板颜色数，最大 256 |
| `sticker_gif_dither` | GIF 抖动算法 |
| `sticker_ffmpeg_concurrency` | 同时执行的贴纸转码数量 |
| `tgs_converter_cmd` | TGS 转 GIF 的外部命令 |

### osu! 信息查询

配置文件：`BotData/plugin_configs/osu_info.json`

本插件使用 osu!api v2 的 Client Credentials OAuth 流程，只读取公开资料。需要先到 osu! 账号设置里创建 OAuth 应用，然后填写：

| 字段 | 说明 |
|------|------|
| `client_id` | osu! OAuth Application 的 Client ID |
| `client_secret` | osu! OAuth Application 的 Client Secret |
| `default_mode` | 默认模式，支持 `osu`、`taiko`、`fruits`、`mania` |
| `proxy` | 请求 osu! API 的代理，可为空 |
| `cache_dir` | 查询结果图片和头像封面缓存目录，默认 `/tmp/hikari_bot/osu_info` |
| `download_no_video` | 下载谱面时默认使用 osu! 官方无视频下载入口 |
| `download_max_file_mb` | 谱面文件大小上限，默认 `80` MB |
| `session_cookie` | 可选 osu! 登录 Cookie；官方下载入口要求登录时才需要，属于敏感配置，不要提交到 git |

可用指令：

| 消息 | 效果 |
|------|------|
| `osu 帮助` | 查看 osu! 查询命令图 |
| `帮助 osu` | 查看 osu! 子命令详细用法 |
| `osu 绑定 <用户名/ID> [模式]` | 将当前 QQ 绑定到 osu! 账号 |
| `osu 解绑` | 解除当前 QQ 的 osu! 绑定 |
| `osu [模式] [用户名/ID]` | 查询用户信息；不填用户时查询绑定账号 |
| `osu 用户 [模式] [用户名/ID]` | 显式查询用户信息 |
| `osu 看板 [模式] [用户名/ID]` | 查询个人看板和最近成绩 |
| `osu 成绩 [best|recent|firsts] [模式] [用户名/ID]` | 查询最好、最近或第一名成绩 |
| `osu 排名 [模式] [国家代码]` | 查询全球或指定国家排行榜前列，例如 `osu 排名 osu JP` |
| `osu 谱面 <谱面ID|关键词>` | 查询谱面详情或搜索谱面 |
| `osu 下载 <谱面集ID|谱面链接|关键词>` | 优先从 osu! 官方源下载 `.osz`；官方源需要登录或返回页面时，会发送官方下载链接兜底 |

所有 osu! 查询结果都会渲染为图片发送，谱面下载会通过 QQ 文件上传发送 `.osz`。QQ 绑定数据保存在 `UserData/osu_bindings.json`。

### Steam 热门热卖日报

配置文件：`BotData/plugin_configs/steam_deals.json`

本插件调用 Steam Store 的 `featuredcategories` 接口，并用 Steam 搜索热卖榜和特惠结果补充内容；`steam日报` 默认展示热门热卖榜单，`steam低价` 仍然筛选免费、超低价、大折扣、新打折和折扣加深游戏。还会尝试读取 SteamDB Free Promotions，为限时免费领取和免费试玩活动打标；SteamDB 抓取失败时会自动降级为普通 Steam 日报。默认不会主动每日推送；即使开启定时任务，也只会发送到 `push_whitelist` 中列出的群或私聊。手动发送命令查询不受白名单限制。

关键字段：

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用插件 |
| `country` | Steam 商店地区代码，默认 `cn` |
| `language` | Steam 商店语言，默认 `schinese` |
| `max_low_price_cents` | 低价阈值，单位为分；默认 `1000` 即约 `¥10` |
| `min_discount_percent` | 大折扣阈值，默认 `90` |
| `max_items` | 单张日报最多展示多少款游戏，默认 `18` |
| `include_market_results` | 是否在 `steam日报` 中加入 Steam 榜单源 |
| `market_filters` | Steam 榜单过滤器，默认 `topsellers` 热卖榜 |
| `market_pages` | 榜单源最多拉取页数 |
| `market_count_per_page` | 榜单源每页条数 |
| `include_search_results` | 是否用 Steam 搜索特惠结果补充日报内容 |
| `search_pages` | 搜索特惠补充源最多拉取页数 |
| `search_count_per_page` | 搜索特惠补充源每页条数 |
| `search_sort_by` | 搜索特惠排序源，默认同时抓 `Discount_DESC`、`Reviews_DESC`、`Released_DESC` |
| `search_category1` | Steam 搜索分类，默认 `998` 只取游戏，避免原声带/工具占位 |
| `price_watch.enabled` | 是否启用本地价格快照，用于判断“新打折”和“折扣加深” |
| `price_watch.mark_first_seen_as_new` | 快照已初始化后，新进入观察范围的特惠是否标记为“新打折” |
| `price_watch.max_entries` | 本地价格快照最多保留多少个 AppID |
| `daily_filter.enabled` | 是否启用日报筛选，减少老低价和同系列刷屏 |
| `daily_filter.max_per_title_family` | 同一标题系列最多保留多少条，默认 `2` |
| `daily_filter.min_review_count_for_plain_low_price` | 纯低价项目进入日报所需最低评价数 |
| `daily_filter.min_discount_for_plain_low_price` | 纯低价项目进入日报所需最低折扣 |
| `daily_filter.min_discount_for_recent_deal` | 近期发布项目进入日报所需最低折扣，默认 `20` |
| `daily_filter.require_recent_search_results` | 搜索特惠结果是否必须是近期发布，默认开启 |
| `daily_filter.max_search_release_age_days` | 搜索特惠结果最大发布时间跨度，默认 `730` 天 |
| `include_steamdb_free_promotions` | 是否用 SteamDB Free Promotions 辅助标注限免领取/免费试玩 |
| `steamdb_free_url` | SteamDB Free Promotions 页面地址 |
| `render.image_format` | 日报图片格式，默认 `JPEG`，比长 PNG 更适合 QQ/NapCat 发送 |
| `render.jpeg_quality` | JPEG 压缩质量，默认 `82` |
| `send_retry_attempts` | NapCat 发送图片/文本超时时的重试次数 |
| `schedule.enabled` | 是否开启每日主动推送 |
| `schedule.time` | 每日推送时间，格式 `HH:MM` |
| `schedule.timezone` | 推送时区，默认 `Asia/Shanghai` |
| `push_whitelist.group_ids` | 允许主动推送的群号列表 |
| `push_whitelist.private_user_ids` | 允许主动推送的私聊 QQ 号列表 |
| `proxy` | 请求 Steam 商店和封面图的代理，可为空 |
| `cache_dir` | 日报图片和封面缓存目录，默认 `/tmp/hikari_bot/steam_deals` |

`price_watch` 首次启用时只会建立价格基线，不会把所有当前特惠都标成“新打折”；之后再次抓取时，快照中新进入观察范围的特惠会标记为“新打折”，折扣百分比提高或到手价下降的项目会标记为“折扣加深”。

可用指令：

| 消息 | 效果 |
|------|------|
| `steam日报` | 查询免费、低价和大折扣游戏日报 |
| `steam免费` / `steam日报 免费` | 只看免费游戏 |
| `steam低价` / `steam日报 低价` | 查看低价和大折扣游戏 |
| `steam日报 刷新` | 忽略短期缓存重新获取 |

需要主动推送时，将配置改成类似：

```json
{
  "schedule": {
    "enabled": true,
    "time": "10:00",
    "timezone": "Asia/Shanghai"
  },
  "push_whitelist": {
    "group_ids": [123456789],
    "private_user_ids": []
  }
}
```

### AI 最新资讯日报

配置文件：`BotData/plugin_configs/ai_news.json`

本插件注册通用推送源 `ai_news`，从公开 RSS/Atom 源聚合 AI 最新资讯，按来源权重、发布时间和关键词加权筛选，去重后渲染成一张图片。默认源包含 OpenAI News、Google AI、Hugging Face Blog、arXiv AI、Hacker News AI、TechCrunch AI、The Verge AI 和 VentureBeat AI。手动命令 `ai资讯` 只用于预览，不写入推送去重状态。

可选的 AI 摘要/翻译功能会复用 `BotData/plugin_configs/aiagent.json` 中的 `model` 配置，包括 `base_url`、`api_key`、`model` 和 `proxy`；`ai_news.json` 不单独保存模型 Key。AI 摘要默认关闭，避免无意消耗 token。开启后，插件会先筛选资讯，再请求 AI Agent 同款 OpenAI-compatible 接口生成中文总览并翻译标题/摘要；如果请求失败且 `fallback_to_original` 为 `true`，会降级发送原始资讯图片，只在日志里记录失败原因。

关键字段：

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用 AI 资讯源 |
| `sources[].id` | 数据源 ID，可在推送任务的 `source_options.source_ids` 中筛选 |
| `sources[].enabled` | 是否启用该数据源 |
| `sources[].group` | 数据源分组，默认使用 `official`、`research`、`community`、`media` |
| `sources[].url` | RSS/Atom 地址；新增数据源时优先使用官方 RSS |
| `sources[].weight` | 来源权重，官方源建议高于媒体源 |
| `max_items` | 单张图片默认最多展示多少条资讯 |
| `max_per_source` | 单个数据源最多展示多少条，避免 arXiv/HN 这类高频源刷屏 |
| `max_age_hours` | 只展示最近多少小时内的条目；`0` 表示不限制时间 |
| `ai_summary.enabled` | 是否默认开启 AI 总结和翻译 |
| `ai_summary.translate` | 是否翻译标题和摘要 |
| `ai_summary.target_language` | 目标语言，默认 `zh-CN` |
| `ai_summary.max_input_items` | 最多把多少条资讯交给 AI 总结 |
| `ai_summary.max_summary_bullets` | 图片顶部 AI 总览最多几条要点 |
| `ai_summary.fallback_to_original` | AI 请求失败时是否降级为原始资讯图片 |
| `only_new` | 推送时是否只发送未见过的条目 |
| `send_first_run` | 第一次推送时是否发送当前最新条目；关闭后第一次只建立基线 |
| `max_state_entries` | `UserData/ai_news_state.json` 中保留多少去重键 |
| `cache_dir` | 资讯图片缓存目录，默认 `/tmp/hikari_bot/ai_news` |
| `render.image_format` | 图片格式，默认 `PNG` |
| `proxy` | 请求 Feed 的 HTTP 代理，可为空 |

可用指令：

| 消息 | 效果 |
|------|------|
| `ai资讯` | 生成默认条数的 AI 资讯图片 |
| `ai资讯 5` | 生成最多 5 条资讯的图片 |
| `ai资讯 总结 5` | 使用 AI Agent 的模型配置翻译并总结后生成图片 |

中午 12 点推送示例：

```json
{
  "id": "ai_news_noon",
  "enabled": true,
  "trigger": "schedule",
  "source": "ai_news",
  "time": "12:00",
  "timezone": "Asia/Shanghai",
  "targets": {
    "group_ids": [123456789],
    "private_user_ids": []
  },
  "source_options": {
    "max_items": 10,
    "max_per_source": 3,
    "ai_summary": true,
    "translate": true,
    "target_language": "zh-CN",
    "only_new": true,
    "send_first_run": true,
    "include_links": false
  }
}
```

### 知乎热搜

配置文件：`BotData/plugin_configs/zhihu_hot.json`

本插件注册通用推送源 `zhihu_hot`，读取知乎热榜接口并渲染成一张图片。图片会展示榜单排名、问题标题、摘要、回答/关注数和知乎返回的热度文本。手动命令 `知乎热搜` 用于预览；推送任务里默认只发图片，`include_links` 开启后会追加问题链接。

关键字段：

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用知乎热搜源 |
| `api_url` | 热榜接口地址，默认 `https://api.zhihu.com/topstory/hot-list` |
| `max_items` | 单张图片默认最多展示多少条热搜，最多 30 条 |
| `summary_max_chars` | 每条问题摘要最多保留多少字符；`0` 表示不显示摘要 |
| `cache_ttl_minutes` | 接口短期缓存时间，避免手动预览和推送连续请求 |
| `cache_dir` | 热搜图片缓存目录，默认 `/tmp/hikari_bot/zhihu_hot` |
| `render.image_format` | 图片格式，默认 `PNG` |
| `proxy` | 请求知乎接口的 HTTP 代理，可为空 |

可用指令：

| 消息 | 效果 |
|------|------|
| `知乎热搜` | 生成默认条数的知乎热搜图片 |
| `知乎热搜 10` | 生成最多 10 条热搜的图片 |
| `知乎热搜 10 刷新` | 忽略短期缓存重新读取 |
| `知乎热搜 链接` | 图片后额外发送问题链接 |

中午 12 点推送示例：

```json
{
  "id": "zhihu_hot_noon",
  "enabled": true,
  "trigger": "schedule",
  "source": "zhihu_hot",
  "time": "12:00",
  "timezone": "Asia/Shanghai",
  "targets": {
    "group_ids": [123456789],
    "private_user_ids": []
  },
  "source_options": {
    "max_items": 15,
    "include_links": false
  }
}
```

### 定时推送框架

配置文件：`BotData/plugin_configs/push_framework.json`

`push_framework` 是通用推送骨架：它只负责定时、目标发送、失败重试和同一轮去重；具体内容由消息源提供。内置 `static_text` 消息源可用于测试链路，后续插件可以调用 `register_push_source()` 注册自己的消息源。

关键字段：

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用推送框架 |
| `startup_delay_seconds` | 机器人启动后等待多少秒再开始检查任务 |
| `check_interval_seconds` | 定时检查间隔，默认 `60` 秒 |
| `send_retry_attempts` | NapCat 发送失败时的重试次数 |
| `jobs[].id` | 推送任务 ID，手动测试时使用 |
| `jobs[].enabled` | 是否启用该任务 |
| `jobs[].trigger` | 触发器，支持 `schedule`、`startup`、`shutdown`、`manual`；默认 `schedule` |
| `jobs[].source` | 消息源名称，例如内置 `static_text` |
| `jobs[].time` / `jobs[].times` | 推送时间，格式 `HH:MM`；`times` 可配置多个时间点 |
| `jobs[].timezone` | 推送时区，默认 `Asia/Shanghai` |
| `jobs[].days` | 可选星期限制；为空表示每天，可写 `mon`、`周一`、`二` 等 |
| `jobs[].late_grace_seconds` | 错过计划时间后仍允许补发的秒数，默认 `7200`；`0` 表示当天过点后都可补发 |
| `jobs[].dedupe` | 去重方式，默认 `daily`；`none` 表示不做定时去重 |
| `jobs[].targets.group_ids` | 需要主动推送的群号列表 |
| `jobs[].targets.private_user_ids` | 需要主动推送的私聊 QQ 号列表 |
| `jobs[].source_options` | 传给消息源的自定义参数 |

可用指令仅超级管理员可用：

| 消息 | 效果 |
|------|------|
| `推送 状态` | 查看框架、任务和消息源状态 |
| `推送 源` | 查看已注册消息源 |
| `推送 触发 <任务ID>` | 立即按该任务的目标试发一次，不写入定时去重状态 |

内置消息源：

| source | 说明 | 常用 `source_options` |
|--------|------|------------------------|
| `static_text` | 发送固定文本，用于测试链路 | `text` |
| `steam_deals` | 发送 Steam 热门热卖、免费和低价游戏日报图片 | `mode`: `all`/`free`/`low`；`include_links`: `true`/`false`；`force_refresh`: `true`/`false` |
| `ai_news` | 发送 AI 最新资讯图片 | `max_items`: 条数；`max_per_source`: 单源上限；`groups`: 分组；`source_ids`: 指定源；`only_new`: `true`/`false`；`include_links`: `true`/`false`；`ai_summary`: `true`/`false`；`translate`: `true`/`false`；`target_language`: 目标语言 |
| `zhihu_hot` | 发送知乎热搜图片 | `max_items`: 条数；`include_links`: `true`/`false`；`force_refresh`: `true`/`false` |
| `rss_feed` | 发送 RSS/Atom 订阅更新 | `subscription_id`: 订阅 ID；`url`: 临时 Feed URL；`max_items`: 条数；`only_new`: `true`/`false`；`mark_seen`: 显式写入去重状态 |

Steam 原插件自己的 `BotData/plugin_configs/steam_deals.json` 定时白名单仍然保留兼容；新建推送任务时推荐走 `push_framework.json`，也就是 source 写 `steam_deals`。

Bot 后台的“推送”页面可以编辑同一份配置；在“任务设置”里点击“立即推送当前任务”会先保存当前表单，再按该任务目标手动执行一次，方便测试消息源和目标是否可用。`manual` 触发器不会被定时循环自动执行，只会被后台按钮或 `推送 触发 <任务ID>` 这类手动入口执行。

定时推送最小配置示例：

```json
{
  "enabled": true,
  "jobs": [
    {
      "id": "daily_text",
      "enabled": true,
      "trigger": "schedule",
      "source": "static_text",
      "time": "09:00",
      "timezone": "Asia/Shanghai",
      "targets": {
        "group_ids": [123456789],
        "private_user_ids": []
      },
      "source_options": {
        "text": "早上好，今日推送测试。"
      }
    }
  ]
}
```

Steam 日报推送示例：

```json
{
  "id": "steam_daily",
  "enabled": true,
  "trigger": "schedule",
  "source": "steam_deals",
  "time": "10:00",
  "timezone": "Asia/Shanghai",
  "targets": {
    "group_ids": [123456789],
    "private_user_ids": []
  },
  "source_options": {
    "mode": "all",
    "include_links": true,
    "force_refresh": false
  }
}
```

生命周期推送示例：

```json
{
  "id": "bot_started_notice",
  "enabled": true,
  "trigger": "startup",
  "source": "static_text",
  "targets": {
    "group_ids": [123456789],
    "private_user_ids": []
  },
  "source_options": {
    "text": "HIKARI Bot 已启动。"
  }
}
```

自定义消息源建议新建插件目录，例如 `plugins/my_push_source/__init__.py`：

```python
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from plugins.push_framework import PushContext, PushMessage, register_push_source


@register_push_source("my_source", description="我的自定义推送源")
async def build_message(ctx: PushContext):
    keyword = ctx.options.get("keyword", "默认主题")
    return f"今日主题：{keyword}"
```

消息源函数可以返回字符串、`Message`、`PushMessage`，或这些值组成的列表。需要发送图片时可返回：

```python
return [
    PushMessage(Message(MessageSegment.image(path.resolve().as_uri())), "图片"),
    PushMessage(Message("附加说明文本"), "说明"),
]
```

然后在 `push_framework.json` 的任务中写 `"source": "my_source"`，并通过 `source_options` 传入该源需要的参数。

### RSS 订阅

配置文件：`BotData/plugin_configs/rss_subscriber.json`

后台“RSS”页面可以维护同一份订阅配置；命令和推送任务都可以通过订阅 ID 复用这些 Feed。支持常见 RSS 2.0 和 Atom，不需要额外账号。

关键字段：

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用 RSS 插件 |
| `timeout_seconds` | 拉取 Feed 的请求超时 |
| `proxy` | 可选 HTTP 代理 |
| `max_items` | 默认读取条目数 |
| `summary_max_chars` | 单条摘要截断长度，`0` 表示不显示摘要 |
| `max_message_chars` | 单条机器人消息最大长度 |
| `max_state_entries` | 每个订阅保留多少去重状态 |
| `subscriptions[].id` | 订阅 ID，命令和推送任务引用它 |
| `subscriptions[].url` | RSS/Atom Feed URL |
| `subscriptions[].only_new` | 推送时是否只发送状态中未见过的条目 |
| `subscriptions[].send_first_run` | 第一次推送时是否发送最新条目；关闭后第一次只建立基线 |

可用指令：

| 消息 | 效果 |
|------|------|
| `rss 列表` | 查看已配置订阅 |
| `rss 看 <订阅ID|URL> [数量]` | 读取最新条目，不写入去重状态 |
| `rss 测试 <订阅ID|URL> [数量]` | 超级管理员试读订阅 |
| `rss 添加 <订阅ID> <URL> [标题]` | 超级管理员新增订阅 |
| `rss 删除 <订阅ID>` | 超级管理员删除订阅 |
| `rss 开启 <订阅ID>` / `rss 关闭 <订阅ID>` | 超级管理员启停订阅 |

RSS 推送任务示例：

```json
{
  "id": "rss_news_daily",
  "enabled": true,
  "trigger": "schedule",
  "source": "rss_feed",
  "time": "09:30",
  "timezone": "Asia/Shanghai",
  "targets": {
    "group_ids": [123456789],
    "private_user_ids": []
  },
  "source_options": {
    "subscription_id": "example_news",
    "max_items": 3,
    "include_summary": true,
    "only_new": true,
    "send_first_run": true
  }
}
```

### 星露谷物语 Wiki

配置文件：`BotData/plugin_configs/stardew_wiki.json`

本插件调用 Stardew Valley Wiki 的 MediaWiki API，不需要账号或密钥。默认查询中文 Wiki。命令会以合并转发发送结果：第一条为页面链接，第二条为详细描述，第三条为页面主图（如果 Wiki 提供）。

关键字段：

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用插件 |
| `api_url` | MediaWiki API 地址，默认中文站 `https://zh.stardewvalleywiki.com/mediawiki/api.php` |
| `timeout` | 请求超时时间，单位秒 |
| `search_limit` | 搜索候选数量，插件会取最佳结果 |
| `summary_max_chars` | 简介字段的最大字符数，供 AI 工具等短结果使用 |
| `detail_max_chars` | 合并转发中详细描述的最大字符数 |
| `image_size` | 请求 Wiki 主图缩略图的目标尺寸 |
| `proxy` | 请求 Wiki API 的代理，可为空 |

可用指令：

| 消息 | 效果 |
|------|------|
| `星露谷wiki <关键词>` | 搜索中文 Wiki，以合并转发返回链接、详细描述和主图 |
| `svwiki <关键词>` | 同上 |
| `stardewwiki <关键词>` | 同上 |

### Minecraft Wiki

配置文件：`BotData/plugin_configs/mc_wiki.json`

本插件调用 Minecraft Wiki 的 MediaWiki API，不需要账号或密钥。默认查询中文 Minecraft Wiki。命令会以合并转发发送结果：第一条为页面链接，第二条为详细描述，第三条为页面主图（如果 Wiki 提供）。

关键字段：

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用插件 |
| `api_url` | MediaWiki API 地址，默认中文站 `https://zh.minecraft.wiki/api.php` |
| `timeout` | 请求超时时间，单位秒 |
| `search_limit` | 搜索候选数量，插件会取最佳结果 |
| `summary_max_chars` | 简介字段的最大字符数，供 AI 工具等短结果使用 |
| `detail_max_chars` | 合并转发中详细描述的最大字符数 |
| `image_size` | 请求 Wiki 主图缩略图的目标尺寸 |
| `proxy` | 请求 Wiki API 的代理，可为空 |
| `user_agent` | 请求 Wiki API 时使用的 User-Agent |

可用指令：

| 消息 | 效果 |
|------|------|
| `mcwiki <关键词>` | 搜索中文 Wiki，以合并转发返回链接、详细描述和主图 |
| `我的世界wiki <关键词>` | 同上 |
| `mc百科 <关键词>` | 同上 |

### 杀戮尖塔 2 Wiki

配置文件：`BotData/plugin_configs/sts2_wiki.json`

本插件调用 wiki.gg 的 Slay the Spire Wiki MediaWiki API，不需要账号或密钥。命令会优先读取 `UserData/sts2_wiki_cache.json` 本地缓存；缓存未命中或过期时才请求外站。默认缓存有效期为 24 小时。

关键字段：

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用插件 |
| `api_url` | MediaWiki API 地址，默认 `https://slaythespire.wiki.gg/api.php` |
| `cache_ttl_seconds` | 本地缓存有效期，默认 86400 秒 |
| `timeout` | 请求超时时间，单位秒 |
| `search_limit` | 搜索候选数量，插件会取最相关的第一个结果 |
| `summary_max_chars` | 返回摘要最大字符数 |
| `query_max_chars` | 用户关键词最大字符数 |
| `max_cache_entries` | 本地缓存最多保留的查询条目数 |
| `proxy` | 请求 Wiki API 的代理，可为空 |
| `user_agent` | 请求 Wiki API 时使用的 User-Agent |
| `query_aliases` | 查询别名映射，用于把中文译名转成英文 wiki.gg 搜索词，例如 `打击` -> `Strike` |

可用指令：

| 消息 | 效果 |
|------|------|
| `塔2wiki <关键词>` | 搜索 Wiki 条目，返回标题、摘要和链接 |
| `塔2 <关键词>` | 同上 |
| `sts2 <关键词>` | 同上 |

AI Agent 工具：`sts2_wiki_search`。这是只读插件工具，返回结构化 JSON，不会直接发消息、写配置或触发推送。

### QQ 互动插件

#### 资料卡点赞

配置文件：`BotData/plugin_configs/profile_like.json`

本插件调用 NapCat/OneBot 的 `send_like` API。默认 `点赞` 会给发命令的人点满 10 次；群聊里不需要 @ 机器人。点赞会静默执行，成功或失败都不会在聊天里发送文字消息，失败细节只写入日志。

关键字段：

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用插件 |
| `default_times` | 未指定次数时默认点赞次数，默认 10 |
| `max_times` | 单次命令允许的最大点赞次数，最高 10 |

可用指令：

| 消息 | 效果 |
|------|------|
| `点赞` | 静默给自己点满赞 |
| `点赞 @用户` | 静默给被 @ 的用户点赞 |
| `点赞 QQ号` | 静默给指定 QQ 号点赞 |
| `点赞 QQ号 5` | 静默给指定 QQ 号点赞 5 次 |

#### 戳一戳回戳

配置文件：`BotData/plugin_configs/poke_back.json`

本插件监听 OneBot V11 的戳一戳通知。如果有人戳到机器人，机器人会立刻调用 NapCat `send_poke` 戳回对方；不会发送文字提示。戳一戳发送依赖 NapCat 当前 packetBackend/QQ 协议支持，如果 NapCat 返回失败，机器人只记录日志。

关键字段：

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用插件 |
| `group_enabled` | 是否在群聊里戳回 |
| `private_enabled` | 是否在私聊里戳回 |

### Telegram 贴纸包

配置文件：`BotData/plugin_configs/tg_sticker_parser.json`

首次加载插件会自动创建配置文件。至少需要填写：

```json
{
  "enabled": true,
  "auto_parse": false,
  "bot_token": "你的 Telegram Bot Token",
  "proxy": ""
}
```

发送 `tg贴纸 https://t.me/addstickers/<set_name>` 后，机器人会：

1. 优先复用本地贴纸库中已保存的同名贴纸包。
2. 无本地缓存或带 `refresh` 参数时，调用 Telegram Bot API 获取贴纸包。
3. 调用 `media_transcoder` 将贴纸统一转换为 GIF。
4. 默认保存到统一贴纸文件夹 `BotData/Gifs/_library/`，并写入贴纸库索引。
5. 自动更新贴纸包关键词，让贴纸包名称成为触发词。
6. 发送转换后的 GIF。

可选参数直接跟在链接后：

| 参数 | 效果 |
|------|------|
| `zip` | 打包为 ZIP 文件发送 |
| `refresh` | 忽略本地缓存，重新获取并转换 |
| `nosave` | 只发送本次结果，不保存成本地贴纸包 |
| `name=关键词` / `keyword=关键词` / `kw=关键词` | 额外注册一个触发词 |

示例：

```text
tg贴纸 https://t.me/addstickers/StickerSetName zip refresh name=猫猫虫
```

### 本地贴纸包

配置文件：`BotData/plugin_configs/sticker_library.json`

贴纸文件统一放在：

```text
BotData/Gifs/_library/
```

配置示例：

```json
{
  "version": 1,
  "storage_root": "BotData/Gifs/_library",
  "stickers": {
    "abc123.gif": {
      "file": "abc123.gif",
      "sha256": "abc123...",
      "source": "upload",
      "original_name": "cute.gif",
      "created_at": 1782144000
    }
  },
  "packs": {
    "capoo_gif": {
      "keywords": ["capoo", "猫猫虫"],
      "stickers": ["abc123.gif"]
    }
  }
}
```

旧版 `BotData/Gifs/<贴纸包目录>/` 会在首次使用贴纸库时自动迁移到 `_library` 并生成索引。`BotData/plugin_configs/sticker_trigger.json` 仍会被同步写出作为兼容文件，但新的管理入口以 `sticker_library.json` 为准。

一个关键词可以关联多个贴纸包，一个贴纸也可以属于多个贴纸包。触发、拼图和批量发送时，如果关键词命中多个贴纸包，会自动合并这些贴纸包里的贴纸并去重。

可用指令：

| 消息 | 效果 |
|------|------|
| `猫猫虫` | 随机发送一张匹配贴纸 |
| `猫猫虫 10` | 随机发送 10 张，不重复 |
| `贴纸包 随机` | 从所有贴纸包随机发送 |
| `贴纸包 拼图 猫猫虫` | 将贴纸包第一帧拼成预览图 |
| `贴纸包 统计` | 查看唯一贴纸数、贴纸包数和关键词数 |
| `贴纸包 列表` | 分页查看已配置贴纸包和关键词，每页 5 个 |
| `贴纸包 列表 2` | 查看第 2 页贴纸包 |
| `贴纸包 列表 全部` | 通过合并转发查看完整贴纸包列表 |
| `贴纸包 预览` | 生成包含所有贴纸包名称、关键词和 6 张预览图的长图 |
| `贴纸包 帮助` / `帮助 贴纸包` | 查看贴纸包子命令 |
| `统计` | 查看当前会话统计 |

本地贴纸包最终只识别 `.gif`。如果素材是 `.jpg`、`.png`、`.webp`、`.mp4` 等，请通过 Bot 后台或 `media_transcoder` 先转换为 GIF。

### 本地语音触发

配置文件：`BotData/plugin_configs/voice_trigger.json`

语音文件统一放在：

```text
BotData/Voices/_library/
```

配置示例：

```json
{
  "version": 1,
  "storage_root": "BotData/Voices/_library",
  "voices": {
    "abc123.silk": {
      "file": "abc123.silk",
      "sha256": "abc123...",
      "display_name": "晚安语音",
      "original_name": "goodnight.silk",
      "keywords": ["晚安", "睡觉"],
      "created_at": 1782144000
    }
  }
}
```

用户发送纯文本关键词并完全匹配时，机器人会随机发送关联语音。一个关键词可以关联多条语音，一条语音也可以有多个关键词。推荐使用 `.silk` 或 `.amr`；后台也允许上传 `.mp3`、`.wav`、`.ogg`、`.m4a`、`.aac`、`.flac`、`.opus`，实际能否作为 QQ 语音发送取决于 NapCat/QQ 的支持。

### TTS 说话

配置文件：`BotData/plugin_configs/tts_speaker.json`

本插件仅使用 Fish Audio 合成语音。`说话 <文本>` 使用当前选中的 Fish 音色；生成的音频会放到 `/tmp/hikari_bot/tts`，再通过 OneBot 语音消息发送。音色库预置永雏塔菲、蒋介石和电棍，也可在 Bot 后台新增或编辑。

关键字段：

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用 `说话` 命令 |
| `selected_voice` | 当前使用的音色名称 |
| `voices` | 音色库，每项包含 `name` 和 Fish 模型 `reference_id` |
| `fish_audio.api_key` | Fish Audio API Key |
| `fish_audio.model` | Fish Audio 模型，默认 `s2-pro` |
| `fish_audio.retry_count` | 主模型在临时错误时的重试次数，默认 `3` |
| `fish_audio.retry_delay_seconds` | 每次主模型重试前的等待秒数，默认 `1.0` |
| `fish_audio.backup_model` | 主模型最终失败后调用一次的备用模型，默认 `s2.1-pro-free`；留空则关闭备用模型 |
| `fish_audio.speed`、`fish_audio.volume` | Fish 原生语速倍率和响度（dB） |
| `fish_audio.pitch_semitones` | 音高半音，使用本机 FFmpeg 后处理 |
| `fish_audio.temperature`、`fish_audio.top_p` | 表现力和多样性参数 |
| `fish_audio.normalize_loudness` | Fish 输出响度归一化（S2-Pro） |
| `proxy` | 访问 TTS 服务的代理，可为空 |
| `max_chars` | 单次合成文本长度上限 |
| `cooldown_seconds` | 同一用户命令冷却秒数 |
| `cache_dir` | 临时语音缓存目录，默认 `/tmp/hikari_bot/tts` |

可用指令：

| 消息 | 效果 |
|------|------|
| `说话 你好哇` | 合成“你好哇”并发送为语音 |
| `tts 你好哇` | 同上 |
| `音色列表` | 显示当前可用音色和正在使用的音色 |
| `切换音色 蒋介石` | 切换当前 Fish Audio 音色 |

### AI Agent 聊天

配置文件：`BotData/plugin_configs/aiagent.json`

本插件目前只实现聊天。它直接调用 OpenAI-compatible `chat/completions` 接口，因此可配置 OpenAI、DeepSeek 或其他兼容服务。

开启后，AI Agent 作为最低优先级兜底：私聊里发送文本会在其他插件未处理时进入 AI Agent；群聊里必须 @机器人 且没有被其他插件处理才会回复。关闭后不会响应。

下列媒体平台链接默认不会被 AI Agent 兜底回复：抖音、Bilibili、小红书、小黑盒、Twitter/X、今日头条、快手、微博和 TikTok。

关键字段：

| 字段 | 说明 |
|------|------|
| `enabled` | AI Agent 总开关 |
| `model.base_url` | 兼容 OpenAI 的 API 根地址，例如 `https://api.deepseek.com/v1` |
| `model.api_key` | API Key，属于敏感配置，不要提交到 git |
| `model.model` | 模型名称，例如 `deepseek-chat` |
| `model.temperature`、`model.top_p`、`model.max_tokens` | 聊天生成参数 |
| `model.proxy` | 请求模型 API 的代理，可为空 |
| `persona.skill_path` | 人格 skill 路径，必须位于 `BotData/agent_personas/` 下 |
| `persona.max_chars` | 最多读取多少字符的人格 skill 内容 |
| `persona.include_references` | 是否读取人格 skill 中显式引用的本地 `.md`、`.txt`、`.json` 补充资源 |
| `persona.reference_max_depth` | 引用展开深度，默认只读取入口文件直接引用的资源 |
| `persona.reference_max_files` | 最多读取多少个补充资源文件 |
| `persona.reference_max_chars_per_file` | 每个补充资源最多读取多少字符 |
| `persona.reference_max_total_chars` | 所有补充资源合计最多读取多少字符 |
| `chat.max_history_messages` | 每个会话保留的上下文消息数；设为 `0` 即无历史 |
| `chat.system_prompt_extra` | 追加在人格 skill 后的额外系统提示词 |
| `chat.blocked_url_domains` | 默认不交给 AI 回复的媒体链接域名 |
| `memory.root` | 持久化记忆根目录，默认 `UserData/aiagent_memory` |
| `memory.max_read_chars_per_file` | 每个 memory.md 注入提示词的最大字符数 |
| `memory.max_file_chars` | 单个 memory.md 保留的最大字符数 |
| `tools.search.enabled` | 是否向模型提供 `web_search` 搜索工具 |
| `tools.search.base_url` | SearXNG 地址；Docker 部署内默认 `http://searxng-core:8080` |
| `tools.search.max_results` | 每次搜索最多返回多少条结果 |
| `tools.search.safesearch` | SearXNG 安全搜索等级，`0` 关闭、`1` 中等、`2` 严格 |
| `tools.files.enabled` | 是否向模型提供文件工具 |
| `tools.files.max_read_chars` | 单次读取文件最多返回多少字符 |
| `tools.files.max_write_chars` | 单次写入 UserData 文件最多允许多少字符 |
| `tools.plugin_tools.enabled` | 是否向模型提供插件显式声明的 AI tools |
| `tools.plugin_tools.allow_side_effects` | 是否允许非只读插件工具；默认 `false` |
| `tools.plugin_tools.enabled_names` | 可选工具白名单；为空时使用所有默认启用且未禁用的插件工具 |
| `tools.plugin_tools.disabled_names` | 插件工具黑名单 |
| `tools.max_tool_rounds` | 单次回复最多允许多少轮工具调用 |

女娲人格 skill 放在：

```text
BotData/agent_personas/
```

推荐每个人格一个目录，例如：

```text
BotData/agent_personas/nuwa_hikari/SKILL.md
```

插件会优先读取目录中的 `SKILL.md`、`skill.md`、`PERSONA.md`、`persona.md` 或 `README.md`，也支持直接把 `persona.skill_path` 指向 `.md`、`.txt`、`.json` 文件。默认还会读取入口 skill 中通过 Markdown 链接或裸相对路径显式引用的本地补充资源，例如 `[语气细则](tone.md)` 或 `references/style.md`；引用必须仍位于 `BotData/agent_personas/` 下，且受 `persona.reference_*` 限制。后台管理页面的 “AI Agent” 页会扫描 `BotData/agent_personas/` 下可用的人格 skill，并支持配置 API 地址、模型、Key、代理、上下文长度和人格路径。

搜索工具使用 OpenAI-compatible Chat Completions 的 `tools` / function calling。模型需要支持工具调用才会主动搜索；如果 SearXNG 使用自定义配置，请确保 `search.formats` 包含 `json`，否则 JSON 搜索接口不可用。

文件工具同样使用 function calling。模型只能读取 `BotData/agent_personas/` 下的 `.md`、`.txt`、`.json` 人格 skill 资源；不能读取 `BotData/config.json`、`BotData/plugin_configs/` 或其他可能含有密钥的配置。`UserData/` 是唯一允许读写的用户数据目录，写入工具会拒绝绝对路径和任何逃出 `UserData/` 的相对路径。

插件工具由各插件显式注册，不会自动暴露所有命令。默认只提供只读查询类能力，包括 `mc_wiki_search`、`stardew_wiki_search`、`sts2_wiki_search`、`zhihu_hot_list`、`steam_deals_list`、`ai_news_list`、`rss_latest`、`osu_user_lookup`、`osu_scores_lookup`、`osu_beatmap_lookup` 和 `osu_ranking_lookup`。这些工具返回结构化 JSON 供模型组织回答；不会直接发送图片、上传文件、触发推送、修改绑定或写入配置。媒体解析、后台管理、TTS、点赞/戳一戳等有明显副作用的能力默认不作为 AI tool 暴露。

可用指令：

| 消息 | 效果 |
|------|------|
| 私聊 `你好` | 其他插件未处理时，使用当前模型和人格 skill 回复 |
| 群聊 `@机器人 你好` | 其他插件未处理时，使用当前模型和人格 skill 回复 |
| 私聊 `重置` / 群聊 `@机器人 重置` | 清空当前会话上下文和对应持久化记忆 |

持久化记忆按下面的文件组织：

```text
UserData/aiagent_memory/private/<QQ>/memory.md
UserData/aiagent_memory/groups/<群号>/memory.md
UserData/aiagent_memory/groups/<群号>/users/<QQ>/memory.md
```

### 可热改资源

资源目录：`BotData/resources/`

首次启动时会从 `.example.json` 自动生成真实资源文件。修改真实 `.json` 后不需要重新构建项目镜像；机器人运行中会按文件修改时间重新读取。

`deploy.ps1` 会同步 `BotData/resources/` 和 `BotData/fonts/` 到服务器数据目录。

#### 生成图片字体

配置文件：`BotData/resources/rendering.json`

推荐准备两个字体文件：

- 常规字重：例如 `BotData/fonts/MyFont-Regular.ttf`
- 粗体字重：例如 `BotData/fonts/MyFont-Bold.ttf`

示例：

```json
{
  "font_regular": "BotData/fonts/MyFont-Regular.ttf",
  "font_bold": "BotData/fonts/MyFont-Bold.ttf",
  "fallback_fonts_regular": [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
  ],
  "fallback_fonts_bold": [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
  ]
}
```

如果没有放自定义字体，会按 `fallback_fonts_*` 查找。运行容器首次启动会安装 `fonts-noto-cjk`，通常会 fallback 到 Noto Sans CJK；如果所有字体都找不到，则退回 Pillow 默认字体，中文可能显示为方块。

#### 机器人固定回复

配置文件：`BotData/resources/bot_messages.json`

常见固定回复已经抽到这个 JSON，例如错误提示、JMComic、Pixiv/Cobalt 部分错误、贴纸命令提示等。修改后不需要重新构建项目镜像；下一次发送对应消息时会读取新内容。

### Bot 后台

配置文件：`BotData/plugin_configs/bot_admin.json`

机器人启动后会用 Python 托管一个管理后台，默认监听：

```text
0.0.0.0:54213
```

> TCP 端口最大为 `65535`，因此不能使用 `542123`。如需修改端口，请改 `bot_admin.json` 中的 `port`。如果服务器上已有旧的 `sticker_web.json`，首次启动会自动迁移。

后台 API 也可以直接用请求头鉴权，token 就是 `bot_admin.json` 里的 `password`；该方式只对 `/api/...` 路径生效，不改变浏览器登录页的 cookie/session 登录。

```bash
curl -H "X-Admin-Token: <后台密码>" http://服务器IP:54213/api/aiagent-config
curl -H "Authorization: Bearer <后台密码>" http://服务器IP:54213/api/state
```

完整 HTTP API 文档见 [`docs/API.md`](docs/API.md)。

打开 `http://服务器IP:54213/` 后，可以：

- 上传贴纸素材到已有贴纸包，保存前会统一转换为 GIF。
- 输入新贴纸包名称并上传，自动创建贴纸包。
- 填写额外触发词，自动写入贴纸库索引。
- 整理机器人静默收集到的待整理表情，并批量加入贴纸包或删除。
- 上传语音文件，管理语音触发关键词，并在浏览器里预览播放。
- 管理 Fish Audio 音色库、API Key、模型、语速、响度、音高、表现力、代理、长度限制、冷却时间和缓存目录。
- 配置 AI Agent 的 OpenAI-compatible API、模型参数、API Key、人格 skill 路径和聊天限制。
- 管理 Pixiv、聚合媒体解析、Instagram/Facebook 和 YouTube 插件自己的 QQ/群黑名单、白名单和启用状态。
- 管理通用定时推送框架的任务、消息源参数、群号和私聊目标，并可在任务设置中立即推送当前任务做测试。
- 在线编辑 `BotData/plugin_configs/*.json` 插件配置，保存前会校验 JSON。
- 查看 `BotData/logs/*.log` 运行日志的尾部内容。

支持上传素材后缀：`.gif`、`.jpg`、`.jpeg`、`.png`、`.webp`、`.mp4`、`.webm`、`.mov`、`.mkv`、`.tgs`。最终保存为 `.gif`。上传内容会用 SHA256 哈希命名；同一份素材重复上传会复用已有 GIF，不会再生成副本。

支持上传语音后缀：`.silk`、`.amr`、`.mp3`、`.wav`、`.ogg`、`.m4a`、`.aac`、`.flac`、`.opus`。语音内容同样会用 SHA256 哈希命名；同一份语音重复上传会复用已有文件。

### 贴纸静默收集

配置文件：`BotData/plugin_configs/sticker_collector.json`

机器人会静默收集群聊和私聊消息中的图片表情，统一转为 GIF 后放入待整理收集箱：

```text
BotData/Gifs/_inbox/
BotData/plugin_configs/sticker_inbox.json
```

待整理表情不会自动进入正式贴纸包，需要在 Bot 后台中手动分配或删除。收集箱按 GIF 哈希去重；如果超过配置的 `max_pending`，会自动移除最旧的待整理项。

关键字段：

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用静默收集 |
| `collect_group` | 是否收集群聊图片 |
| `collect_private` | 是否收集私聊图片 |
| `allowed_groups` | 指定允许收集的群号；为空表示所有群 |
| `ignored_users` | 忽略指定 QQ 用户 |
| `max_pending` | 收集箱最多保留多少个待整理项 |
| `temp_root` | 下载和转码临时目录 |

### JMComic

配置文件：

- `BotData/jmcomic/option.yml`：JMComic 下载配置
- `BotData/plugin_configs/jmcomic_api.json`：机器人触发配置

默认仅私聊可用，所有用户都可以触发：

```text
jm 123456
```

如果需要允许群聊识别，把 `BotData/plugin_configs/jmcomic_api.json` 改为：

```json
{
  "allow_group": true
}
```

机器人会下载漫画、导出 PDF，并通过 NapCat 上传到当前私聊或群聊。下载和 PDF 临时目录默认位于 `/tmp/hikari_bot/jmcomic`。

---

## NapCat 文件目录

机器人会把图片、视频、贴纸、PDF 等临时文件放到 `/tmp/hikari_bot`。NapCat 必须能读取这个目录，否则会出现“解析成功但发送失败”。Pixiv、Cobalt、聚合媒体解析和 YouTube 下载的临时媒体默认登记为 10 分钟后清理，可通过各插件的 `cache_ttl_seconds` 调整。

如果 NapCat 运行在 Docker 容器中，请挂载同一个目录：

```yaml
services:
  napcat:
    volumes:
      - ./runtime/tmp/hikari_bot:/tmp/hikari_bot
```

---


## 项目结构

```text
HIKARI_BOT_NEO/
  bot.py                         # 程序入口
  core/
    command_router.py            # 明确命令路由
    config_loader.py             # 主配置和插件配置加载
    error_notifier.py            # 错误通知
    logger_setup.py              # 日志初始化
    message_pipeline.py          # 自动解析管道
    stats_tracker.py             # 会话统计
  plugins/
    media_parser/                # 抖音/B站/小红书/小黑盒等聚合媒体解析适配
    pixiv_parser/                # Pixiv 解析
    cobalt_parser/               # Instagram / Facebook 解析
    media_detail_web/            # 独立媒体详情解析与下载页面
    tg_sticker_parser/           # Telegram 贴纸包解析
    sticker_collector/           # 群聊/私聊表情静默收集
    sticker_trigger/             # 本地贴纸关键词触发
    voice_trigger/               # 本地语音关键词触发
    tts_speaker/                 # Fish Audio 语音合成命令
    bot_admin/                   # Bot 后台页面
    sticker_web/                 # 旧后台插件兼容占位
    bot_help/                    # 帮助信息
    jmcomic_api/                 # JMComic PDF 下载上传
    steam_deals/                 # Steam 免费和低价游戏日报
    ai_news/                     # AI 最新资讯图片和推送源
    zhihu_hot/                   # 知乎热搜图片和推送源
    push_framework/              # 通用定时推送框架
    rss_subscriber/              # RSS/Atom 订阅命令和推送消息源
    stardew_wiki/                # 星露谷物语 Wiki 查询
    mc_wiki/                     # Minecraft Wiki 查询
    sts2_wiki/                   # 杀戮尖塔 2 Wiki 查询
    profile_like/                # QQ 资料卡点赞命令
    poke_back/                   # 被戳一戳时自动戳回
  BotData/
    config.example.json          # 主配置模板
    config.json                  # 主配置，含敏感信息，不提交
    plugin_configs/              # 插件配置
    Gifs/_library/               # 本地贴纸统一文件库
    Voices/_library/             # 本地语音统一文件库
    jmcomic/option.yml           # JMComic 配置
  third_party/
    astrbot_plugin_media_parser/ # 上游 AGPL 聚合解析器源码
  UserData/
    stats/                       # 会话统计数据
  pyproject.toml                 # Python 依赖和 NoneBot 配置
  docker-compose.yml             # 源码挂载 Docker 编排
  deploy/
    docker-compose.server.yml    # 服务器源码挂载部署编排
  docker/
    entrypoint.sh                # 容器首次启动时初始化配置模板
```

---

## 常见问题

| 症状 | 常见原因 | 处理方式 |
|------|----------|----------|
| 启动后机器人不在线 | NapCat WebSocket 地址或 Token 错误 | 检查 `BotData/config.json` 的 `napcat.ws_url` 和 `napcat.token` |
| `tg贴纸` 没有反应 | 插件关闭、链接不匹配、NapCat 未连接 | 检查 `tg_sticker_parser.json` 的 `enabled` 和 `bot_token`，再看日志 |
| 图片或视频发送失败 | NapCat 读不到临时文件 | 挂载 `./runtime/tmp/hikari_bot:/tmp/hikari_bot`，并确认 systemd `PrivateTmp=no` |
| Pixiv 403 / Cloudflare | Cookie 失效或不完整 | 更新 `pixiv_parser.json` 的 `cookie`，必要时补 `cf_clearance` |
| Pixiv 连接失败 | 网络无法直连 Pixiv | 配置 `proxy` |
| Instagram / Facebook 解析失败 | cobalt API 不可用或地址写错 | 确认 `cobalt_api` 指向自部署实例 |
| 抖音/B站/小红书等解析失败 | 平台风控、Cookie 失效、代理不可用或媒体过大 | 检查 `media_parser.json` 的代理、B站 Cookie、大小限制和日志 |
| Telegram 贴纸解析失败 | `bot_token` 未配置或无法访问 Telegram | 填写 Token，必要时配置 `proxy` |
| Telegram 动态贴纸转换失败 | 缺少转换依赖 | 检查 `ffmpeg`、lottie 转换命令和日志 |
| JSON 配置报错 | JSON 格式错误 | 运行 `python -m json.tool <配置文件>` 检查 |

---

## 开发说明

- 插件目录由 `pyproject.toml` 中的 `plugin_dirs = ["plugins"]` 配置。
- `core.message_pipeline` 会先注册全局消息管道，自动解析类插件通过 `register_handler()` 接入。
- 插件配置大多支持热重载，修改 JSON 后下条消息即可生效。
- `BotData/config.json`、`BotData/plugin_configs/*.json`、`UserData/stats`、日志和实际贴纸媒体文件默认不提交。

---

## 许可证

本项目使用 [GNU Affero General Public License v3.0 or later](LICENSE) 开源。

使用本项目解析、下载或转发第三方平台内容时，请自行确认相关平台服务条款和内容版权要求。

## 参考与致谢

本项目开发和实现过程中参考或使用了这些开源项目：

- [NoneBot 2](https://github.com/nonebot/nonebot2)：机器人框架。
- [NapCatQQ](https://github.com/NapNeko/NapCatQQ)：QQ / OneBot V11 接入。
- [drdon1234/astrbot_plugin_media_parser](https://github.com/drdon1234/astrbot_plugin_media_parser)：抖音、B站、小红书、小黑盒等聚合媒体解析能力。
- [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp)：YouTube 等站点的视频信息提取与下载能力。
- [imputnet/cobalt](https://github.com/imputnet/cobalt)：Instagram / Facebook 等媒体解析 API。
- [searxng/searxng](https://github.com/searxng/searxng)：AI Agent 搜索工具的元搜索服务。
- [valkey-io/valkey](https://github.com/valkey-io/valkey)：SearXNG 缓存服务。

## 用户协议与隐私政策

仓库提供了面向自部署场景的 [用户协议模板](USER_AGREEMENT.md) 和 [隐私政策模板](PRIVACY_POLICY.md)。实际部署前，请将服务运营者、联系方式、数据保存期限和第三方服务配置补充为你的真实情况。
