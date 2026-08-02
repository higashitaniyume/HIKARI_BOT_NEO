# 项目概述

HIKARI BOT NEO 是一个基于 [NoneBot 2](https://nonebot.dev/) 的 QQ 机器人，通过 NapCat 的 OneBot V11 WebSocket 接入 QQ。它能自动解析主流媒体平台链接（Pixiv、Bilibili、抖音、小红书、YouTube、网易云音乐等），管理贴纸包和语音包，运行 AI 对话（支持人格技能、持久化记忆、联网搜索与工具调用），提供定时推送能力（Steam 日报、AI 资讯、知乎热搜、RSS 订阅），并自带 Web 管理后台。

> [!IMPORTANT]
> 机器人本体不读取 `.env`。运行配置来自 `BotData/config.json` 和 `BotData/plugin_configs/*.json`；根目录 `.env` 只给 Docker Compose 设置端口、Python 基础镜像和 NapCat 账号。

## 架构概览

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

## 5 个 Docker Compose 服务

| 服务 | 镜像 | 作用 |
|------|------|------|
| `hikaribot` | `python:3.12-slim-bookworm` | 机器人本体 + Bot 后台 + 媒体详情 Web |
| `napcat` | `mlikiowa/napcat-docker` | QQ / OneBot V11 网关 |
| `cobalt` | `ghcr.io/imputnet/cobalt:11` | Instagram / Facebook 媒体 API |
| `searxng` | `searxng/searxng` | AI Agent 网页搜索 |
| `searxng-valkey` | `valkey/valkey:9-alpine` | SearXNG 缓存 |

## 数据边界

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

## 功能一览

| 功能 | 触发方式 | 详见 |
|------|----------|------|
| Pixiv 作品解析 | 直接发送 Pixiv 链接 | [plugins.md](plugins.md#pixiv-作品解析) |
| 聚合媒体解析（B站/抖音/小红书等） | 直接发送链接 / `媒体解析 <链接>` | [plugins.md](plugins.md#聚合媒体解析) |
| Instagram / Facebook 解析 | 直接发送 IG/FB 链接 | [plugins.md](plugins.md#instagram--facebook-解析) |
| YouTube 视频下载 | 直接发送 YouTube 链接 | [plugins.md](plugins.md#youtube-视频下载) |
| 网易云音乐解析 | 发送网易云链接或 QQ 分享卡片 | [plugins.md](plugins.md#网易云音乐解析) |
| SoundCloud 音频下载 | 直接发送 SoundCloud 链接 | [plugins.md](plugins.md#soundcloud-音频下载) |
| 媒体详情 Web | 浏览器打开 `http://IP:53123/` | [plugins.md](plugins.md#媒体详情-web) |
| Telegram 贴纸包解析 | `tg贴纸 <链接>` | [plugins.md](plugins.md#telegram-贴纸包解析) |
| 本地贴纸包 | 关键词触发 / `贴纸包` 命令 | [plugins.md](plugins.md#本地贴纸包) |
| 本地语音触发 | 关键词匹配 | [plugins.md](plugins.md#本地语音触发) |
| TTS 语音合成 | `说话 <文本>` / `音色列表` / `切换音色` | [plugins.md](plugins.md#tts-语音合成) |
| AI Agent 聊天 | 私聊文本 / 群聊 @机器人 | [plugins.md](plugins.md#ai-agent-聊天) |
| AI 配额管理 | 后台「AI 配额」页 / `额度` 命令 | [plugins.md](plugins.md#ai-agent-聊天) |
| Bot 后台 | 浏览器打开 `http://IP:54213/` | [plugins.md](plugins.md#bot-后台) |
| 贴纸静默收集 | 自动 | [plugins.md](plugins.md#贴纸静默收集) |
| 定时推送框架 | `推送 状态` / `推送 触发 <任务ID>` | [plugins.md](plugins.md#定时推送框架) |
| Steam 日报 | `steam日报` / `steam免费` / `steam低价` | [plugins.md](plugins.md#steam-热门热卖日报) |
| AI 最新资讯日报 | `ai资讯` | [plugins.md](plugins.md#ai-最新资讯日报) |
| 知乎热搜 | `知乎热搜` | [plugins.md](plugins.md#知乎热搜) |
| RSS 订阅 | `rss` 系列命令 | [plugins.md](plugins.md#rss-订阅) |
| 星露谷物语 Wiki | `星露谷wiki <关键词>` | [plugins.md](plugins.md#星露谷物语-wiki) |
| Minecraft Wiki | `mcwiki <关键词>` | [plugins.md](plugins.md#minecraft-wiki) |
| 杀戮尖塔 2 Wiki | `塔2wiki <关键词>` / `sts2 <关键词>` | [plugins.md](plugins.md#杀戮尖塔-2-wiki) |
| osu! 信息查询 | `osu` 系列命令 | [plugins.md](plugins.md#osu-信息查询) |
| QQ 资料卡点赞 | `点赞` | [plugins.md](plugins.md#qq-资料卡点赞) |
| 空 @ 表情回应 | 群聊只 @机器人 | [plugins.md](plugins.md#空--表情回应) |
| 戳一戳回戳 | 自动 | [plugins.md](plugins.md#戳一戳回戳) |
| 媒体转码 | 自动（贴纸转换） | [plugins.md](plugins.md#媒体转码) |
| JMComic PDF | `jm <id>` | [plugins.md](plugins.md#jmcomic-pdf-下载) |
| 好友管理 | 自动 | [plugins.md](plugins.md#好友管理) |
| 帮助信息 | `帮助` | [plugins.md](plugins.md#帮助与关于) |
| 关于信息 | `关于` | [plugins.md](plugins.md#帮助与关于) |
| 错误通知 | 自动 | [plugins.md](plugins.md#错误通知) |
| AstrBot 插件兼容 | 上传 / `astrbot load` / Web 面板 | [astrbot.md](astrbot.md) |

> 功能命令的完整说明见 [plugins.md](plugins.md)；部署见 [deployment.md](deployment.md)；核心模块见 [core.md](core.md)；后台 HTTP API 见 [API.md](API.md)。
