# HIKARI BOT NEO

HIKARI BOT NEO 是一个基于 [NoneBot 2](https://nonebot.dev/) 的 QQ 机器人，通过 NapCat 的 OneBot V11 WebSocket 接入 QQ。它主要用于自动解析 QQ 消息里的媒体链接，并提供贴纸包、错误通知等辅助能力。

> [!IMPORTANT]
> 机器人本体不读取 `.env`。机器人运行配置来自 `BotData/config.json` 和 `BotData/plugin_configs/*.json`；根目录 `.env` 只给 Docker Compose 设置端口、Python 基础镜像和 NapCat 账号。

---

## 功能概览

| 功能 | 触发方式 | 说明 |
|------|----------|------|
| Pixiv 作品解析 | 直接发送 Pixiv 作品链接 | 下载并发送作品图片，支持多图合并转发 |
| Instagram / Facebook 解析 | Instagram / Facebook 链接 | 通过自部署 cobalt API 解析并发送图片/视频 |
| Telegram 贴纸包解析 | `https://t.me/addstickers/<set>` | 拉取贴纸包，调用统一转码服务转换为 GIF，保存成本地贴纸包 |
| 本地贴纸包 | 关键词、`随机贴纸`、`拼图 <关键词>` | 从本地贴纸库随机发送贴纸或生成拼图 |
| 本地语音触发 | 关键词 | 从本地语音库发送指定语音 |
| TTS 说话 | `说话 <文本>`、`音色列表`、`切换音色 <名称>` | 使用 Fish Audio 当前音色合成语音并发送 |
| AI Agent 聊天 | `ai <内容>`、`aiagent <内容>`、`聊天 <内容>` | 调用兼容 OpenAI Chat Completions 的模型，并读取 BotData 中的女娲人格 skill |
| Bot 后台 | 浏览器打开 `http://服务器IP:54213/` | 管理贴纸包、语音文件和触发关键词 |
| JMComic PDF | 私聊 `jm <id>` | 下载并转换 PDF 后通过私聊发送，群聊不解析 |
| osu! 信息查询 | `osu` / `osu绑定` / `osu谱面` / `osu下载` 等命令 | 查询用户、看板、成绩、排行榜、谱面，支持官方源谱面下载；查询结果以图片发送 |
| 星露谷物语 Wiki | `星露谷wiki <关键词>` | 搜索中文 Stardew Valley Wiki，返回标题、简介和 URL |
| 帮助信息 | 私聊 `帮助`；群聊 `@机器人 帮助` | 查看可用能力和用法 |
| 错误通知 | 自动 | 用户收到通用失败提示，管理员收到脱敏后的异常 |

本仓库当前包含 Pixiv、Instagram/Facebook、Telegram 贴纸、本地贴纸、本地语音、TTS、AI Agent、JMComic、osu! 和星露谷物语 Wiki 相关实现。X/Twitter、抖音、Bilibili、TikTok、小黑盒等其他平台解析由独立 AstrBot 插件并行部署，不在本仓库内实现。

---

## 运行环境

- 推荐部署方式：Docker + Docker Compose
- 本地开发：Python `>=3.10`，并使用 [uv](https://docs.astral.sh/uv/) 安装依赖
- QQ 接入：NapCat，并开启 OneBot V11 WebSocket 服务
- Instagram / Facebook 解析：自部署 [cobalt](https://github.com/imputnet/cobalt)
- Telegram 贴纸解析：Telegram Bot Token，并保证服务器能访问 Telegram API
- 贴纸素材转换：运行容器首次启动时会安装 `ffmpeg`、Cairo/Pango 等转换依赖，并缓存在容器与 Python 依赖卷中

---

## 快速部署

推荐使用 Docker Compose 部署。本项目不再推荐 systemd 部署。

Compose 默认启动 4 个服务：

| 服务 | 作用 | 默认端口 |
|------|------|----------|
| `hikaribot` | 本项目机器人和 Bot 后台 | `54213` |
| `napcat` | QQ / OneBot 接入 | `3000`、`6099`、`54253` 等 |
| `cobalt` | Instagram / Facebook 媒体解析 API | `54257` |
| `astrbot` | 其他平台解析机器人 | `6185`、`6199` |

运行数据会保存在 compose 所在目录，主要包括 `BotData/`、`UserData/`、`napcat/`、`astrbot/` 与统一的 `runtime/`。其中 `runtime/shared/` 用于跨容器共享文件，`runtime/tmp/hikari_bot/` 存放 NapCat 可读取的临时媒体。删除容器不会删除这些数据。

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
docker compose up -d
```

首次启动会在 `/opt/hikaribot-docker/BotData/` 中生成真实配置文件。编辑这些配置，至少修改：

| 文件 | 必改项 |
|------|--------|
| `BotData/config.json` | `bot.superuser_id`、`napcat.token` |
| `BotData/plugin_configs/pixiv_parser.json` | Pixiv Cookie 或代理，按需填写 |
| `BotData/plugin_configs/osu_info.json` | osu! OAuth 客户端 ID 和客户端密钥，按需填写 |
| `BotData/plugin_configs/bot_admin.json` | `password` |
| `BotData/plugin_configs/tg_sticker_parser.json` | Telegram Bot Token，按需开启 |
| `BotData/plugin_configs/stardew_wiki.json` | 无必填项，默认使用中文 Wiki |

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

这个脚本会：

1. 首次使用时把历史目录 `/opt/hikaribot-dockcer` 迁移为正确的 `/opt/hikaribot-docker`，并保留所有运行数据
2. 上传源码到 `/opt/hikaribot-docker/app/`，不会上传 `.env`、真实配置、用户数据或媒体文件
3. 上传服务器 Compose 文件
4. 默认更新并重启 `hikaribot`；当共享目录挂载发生变化时，Compose 会自动重建 `napcat` 与 `astrbot` 以保持同一共享目录，`cobalt` 不受影响

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
| NapCat WebUI | `http://服务器IP:3000/` |
| AstrBot WebUI | `http://服务器IP:6185/` |
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
cp BotData/plugin_configs/cobalt_parser.example.json BotData/plugin_configs/cobalt_parser.json
cp BotData/plugin_configs/bot_admin.example.json BotData/plugin_configs/bot_admin.json
cp BotData/plugin_configs/media_transcoder.example.json BotData/plugin_configs/media_transcoder.json
cp BotData/plugin_configs/osu_info.example.json BotData/plugin_configs/osu_info.json
cp BotData/plugin_configs/voice_trigger.example.json BotData/plugin_configs/voice_trigger.json
cp BotData/plugin_configs/tts_speaker.example.json BotData/plugin_configs/tts_speaker.json
cp BotData/plugin_configs/aiagent.example.json BotData/plugin_configs/aiagent.json
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
| `cache_dir` | 下载缓存目录，默认 `/tmp/hikari_bot` |

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
| `cache_dir` | 下载缓存目录 |

支持 Instagram 的 `p`、`reel`、`stories`、`tv` 链接，以及 `facebook.com`、`fb.com`、`fb.watch` 链接。

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
| `osu帮助` | 查看 osu! 查询命令图 |
| `osu绑定 <用户名/ID> [模式]` | 将当前 QQ 绑定到 osu! 账号 |
| `osu解绑` | 解除当前 QQ 的 osu! 绑定 |
| `osu [模式] [用户名/ID]` | 查询用户信息；不填用户时查询绑定账号 |
| `osu看板 [模式] [用户名/ID]` | 查询个人看板和最近成绩 |
| `osu成绩 [best|recent|firsts] [模式] [用户名/ID]` | 查询最好、最近或第一名成绩 |
| `osu排名 [模式] [国家代码]` | 查询全球或指定国家排行榜前列，例如 `osu排名 osu JP` |
| `osu谱面 <谱面ID|关键词>` | 查询谱面详情或搜索谱面 |
| `osu下载 <谱面集ID|谱面链接|关键词>` | 优先从 osu! 官方源下载 `.osz`；官方源需要登录或返回页面时，会发送官方下载链接兜底 |

所有 osu! 查询结果都会渲染为图片发送，谱面下载会通过 QQ 文件上传发送 `.osz`。QQ 绑定数据保存在 `UserData/osu_bindings.json`。

### 星露谷物语 Wiki

配置文件：`BotData/plugin_configs/stardew_wiki.json`

本插件调用 Stardew Valley Wiki 的 MediaWiki API，不需要账号或密钥。默认查询中文 Wiki。

关键字段：

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用插件 |
| `api_url` | MediaWiki API 地址，默认中文站 `https://zh.stardewvalleywiki.com/mediawiki/api.php` |
| `timeout` | 请求超时时间，单位秒 |
| `search_limit` | 搜索候选数量，插件会取最佳结果 |
| `summary_max_chars` | 返回简介的最大字符数 |
| `proxy` | 请求 Wiki API 的代理，可为空 |

可用指令：

| 消息 | 效果 |
|------|------|
| `星露谷wiki <关键词>` | 搜索中文 Wiki，返回标题、简介和 URL |
| `svwiki <关键词>` | 同上 |
| `stardewwiki <关键词>` | 同上 |

### Telegram 贴纸包

配置文件：`BotData/plugin_configs/tg_sticker_parser.json`

首次加载插件会自动创建配置文件。至少需要填写：

```json
{
  "enabled": true,
  "auto_parse": true,
  "bot_token": "你的 Telegram Bot Token",
  "proxy": ""
}
```

发送 `https://t.me/addstickers/<set_name>` 后，机器人会：

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
| `随机贴纸` | 从所有贴纸包随机发送 |
| `拼图 猫猫虫` | 将贴纸包第一帧拼成预览图 |
| `贴纸包统计` | 查看唯一贴纸数、贴纸包数和关键词数 |
| `贴纸包列表` | 分页查看已配置贴纸包和关键词，每页 5 个 |
| `贴纸包列表 2` | 查看第 2 页贴纸包 |
| `贴纸包列表 全部` | 通过合并转发查看完整贴纸包列表 |
| `贴纸包预览` | 生成包含所有贴纸包名称、关键词和 6 张预览图的长图 |
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

关键字段：

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用 `ai` / `aiagent` / `聊天` 命令 |
| `model.base_url` | 兼容 OpenAI 的 API 根地址，例如 `https://api.deepseek.com/v1` |
| `model.api_key` | API Key，属于敏感配置，不要提交到 git |
| `model.model` | 模型名称，例如 `deepseek-chat` |
| `model.temperature`、`model.top_p`、`model.max_tokens` | 聊天生成参数 |
| `model.proxy` | 请求模型 API 的代理，可为空 |
| `persona.skill_path` | 人格 skill 路径，必须位于 `BotData/agent_personas/` 下 |
| `persona.max_chars` | 最多读取多少字符的人格 skill 内容 |
| `chat.max_history_messages` | 每个会话保留的上下文消息数；设为 `0` 即无历史 |
| `chat.system_prompt_extra` | 追加在人格 skill 后的额外系统提示词 |

女娲人格 skill 放在：

```text
BotData/agent_personas/
```

推荐每个人格一个目录，例如：

```text
BotData/agent_personas/nuwa_hikari/SKILL.md
```

插件会优先读取目录中的 `SKILL.md`、`skill.md`、`PERSONA.md`、`persona.md` 或 `README.md`，也支持直接把 `persona.skill_path` 指向 `.md`、`.txt`、`.json` 文件。后台管理页面的 “AI Agent” 页会扫描 `BotData/agent_personas/` 下可用的人格 skill，并支持配置 API 地址、模型、Key、代理、上下文长度和人格路径。

可用指令：

| 消息 | 效果 |
|------|------|
| `ai 你好` | 使用当前模型和人格 skill 回复 |
| `aiagent 介绍一下你自己` | 同上 |
| `聊天 今天适合写什么代码` | 同上 |
| `ai 重置` / `ai重置` | 清空当前私聊或群聊会话上下文 |

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

打开 `http://服务器IP:54213/` 后，可以：

- 上传贴纸素材到已有贴纸包，保存前会统一转换为 GIF。
- 输入新贴纸包名称并上传，自动创建贴纸包。
- 填写额外触发词，自动写入贴纸库索引。
- 整理机器人静默收集到的待整理表情，并批量加入贴纸包或删除。
- 上传语音文件，管理语音触发关键词，并在浏览器里预览播放。
- 管理 Fish Audio 音色库、API Key、模型、语速、响度、音高、表现力、代理、长度限制、冷却时间和缓存目录。
- 配置 AI Agent 的 OpenAI-compatible API、模型参数、API Key、人格 skill 路径和聊天限制。
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

机器人会把图片、视频、贴纸、PDF 等临时文件放到 `/tmp/hikari_bot`。NapCat 必须能读取这个目录，否则会出现“解析成功但发送失败”。

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
    pixiv_parser/                # Pixiv 解析
    cobalt_parser/               # Instagram / Facebook 解析
    tg_sticker_parser/           # Telegram 贴纸包解析
    sticker_collector/           # 群聊/私聊表情静默收集
    sticker_trigger/             # 本地贴纸关键词触发
    voice_trigger/               # 本地语音关键词触发
    tts_speaker/                 # Fish Audio 语音合成命令
    bot_admin/                   # Bot 后台页面
    sticker_web/                 # 旧后台插件兼容占位
    bot_help/                    # 帮助信息
    jmcomic_api/                 # JMComic PDF 下载上传
    stardew_wiki/                # 星露谷物语 Wiki 查询
  BotData/
    config.example.json          # 主配置模板
    config.json                  # 主配置，含敏感信息，不提交
    plugin_configs/              # 插件配置
    Gifs/_library/               # 本地贴纸统一文件库
    Voices/_library/             # 本地语音统一文件库
    jmcomic/option.yml           # JMComic 配置
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
| 发链接没有反应 | 插件关闭、链接不匹配、NapCat 未连接 | 检查插件配置里的 `auto_parse`，再看日志 |
| 图片或视频发送失败 | NapCat 读不到临时文件 | 挂载 `./runtime/tmp/hikari_bot:/tmp/hikari_bot`，并确认 systemd `PrivateTmp=no` |
| Pixiv 403 / Cloudflare | Cookie 失效或不完整 | 更新 `pixiv_parser.json` 的 `cookie`，必要时补 `cf_clearance` |
| Pixiv 连接失败 | 网络无法直连 Pixiv | 配置 `proxy` |
| Instagram / Facebook 解析失败 | cobalt API 不可用或地址写错 | 确认 `cobalt_api` 指向自部署实例 |
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

本项目使用 [MIT License](LICENSE) 开源。

使用本项目解析、下载或转发第三方平台内容时，请自行确认相关平台服务条款和内容版权要求。

## 用户协议与隐私政策

仓库提供了面向自部署场景的 [用户协议模板](USER_AGREEMENT.md) 和 [隐私政策模板](PRIVACY_POLICY.md)。实际部署前，请将服务运营者、联系方式、数据保存期限和第三方服务配置补充为你的真实情况。
