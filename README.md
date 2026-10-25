# HIKARI BOT NEO

<div align="center">

基于 [NoneBot 2](https://nonebot.dev/) + [NapCat](https://napneko.github.io/) OneBot V11 的 QQ 机器人

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

</div>

HIKARI BOT NEO 是一个功能丰富的 QQ 机器人，通过 NapCat 的 OneBot V11 WebSocket 接入 QQ。它能自动解析主流媒体平台链接（Pixiv、Bilibili、抖音、小红书、YouTube、网易云音乐等），管理贴纸包和语音包，运行 AI 对话（支持人格技能、持久化记忆、联网搜索与工具调用），提供定时推送能力（Steam 日报、AI 资讯、知乎热搜、RSS 订阅），并自带 Web 管理后台。

> [!IMPORTANT]
> 机器人本体不读取 `.env`。运行配置来自 `BotData/config.json` 和 `BotData/plugin_configs/*.json`；根目录 `.env` 只给 Docker Compose 设置端口、Python 基础镜像和 NapCat 账号。

---

## 🚀 快速开始

```bash
# 一键安装（服务器需 Docker Engine + Compose v2 + Git）
curl -fsSL https://raw.githubusercontent.com/higashitaniyume/HIKARI_BOT_NEO/main/install.sh | sudo sh

# 或本地开发
uv sync
uv run python bot.py
```

首次启动后在 `BotData/` 生成真实配置，至少修改 `bot.superuser_id`、`napcat.token`、后台密码与各平台 Cookie。

---

## 📚 文档

| 文档 | 内容 |
|------|------|
| [docs/overview.md](docs/overview.md) | 项目简介、架构、数据边界、功能一览 |
| [docs/deployment.md](docs/deployment.md) | 部署（Docker Compose / 一键安装 / 服务器 / 本地开发） |
| [docs/plugins.md](docs/plugins.md) | 全部插件功能与命令详解 |
| [docs/astrbot.md](docs/astrbot.md) | AstrBot 插件兼容层 |
| [docs/core.md](docs/core.md) | 核心模块与开发模式 |
| [docs/resources.md](docs/resources.md) | 可热改资源（字体、固定回复） |
| [docs/faq.md](docs/faq.md) | 常见问题与验证命令 |
| [docs/API.md](docs/API.md) | 后台 HTTP API 文档 |

> 机器人自身也参考这些文档：AI Agent 被问到"你会干什么"时，会通过 `bot_help` 工具读取 `docs/` 回答。

---

## ✨ 最近更新

- **群聊主动接话可逐群开关**：后台「AI Agent」页把原来的「一行一个群号」白名单换成群列表——可手动加群号，也可从机器人所在群搜索后批量添加，每个群单独开关；关掉总开关不影响列表，旧配置的纯群号数组自动视为已开启

- **后台「空间」页**：网页上直接改定时说说的任务/可见范围/AI 开关，随时点「用 AI 生成」拿一条草稿、改完再发布；人格提示词统一取 AI 配置里的 persona skill（不再单独维护），生成和发布都用 NapCat 当前登录态，页面不碰 Cookie

- **QQ 空间操作**：新增 `qzone` 插件，用 NapCat 当前登录的 QQ 直接发/删说说、看好友动态与好友说说、点赞和评论；登录态不落盘，每次操作前向 NapCat `get_cookies`（`domain=user.qzone.qq.com`）取一次 Cookie，换号只需换 NapCat 登录；写操作限超级用户，AI 侧只默认开放只读的 `qzone_get_feed`

- **修复 AI 语音后会话卡死**：AI 文字回复、合并转发、跟随贴纸与语音发送统一加 30 秒超时，NapCat/QQ 不回 `send_msg` 时只放弃当次发送并记 warning，不再永久占住会话锁；已重启清掉卡住状态
- **AI 主动发语音（TTS）**：`tts_speaker` 新增 `send_voice` 工具，AI Agent 判断需要语音时可直接用 Fish Audio 合成并发送语音消息，支持指定音色；默认关闭，需在后台「AI Agent → Tools 管理」放行副作用工具并勾选，按会话冷却，群聊自动回复默认不语音，单条语音长度可在 TTS 设置页单独限制
- **AI 双引擎混合搜索与网络搜图**：新增 `web_image_search` 与 `send_images` 工具，模型可根据聊天语境主动搜索网络图片并直接发送（支持 WebP/GIF 转码）；搜索模式新增 `hybrid`（DeepSeek 官方 Search API + 自建 SearXNG 并发检索、URL 规范化去重与摘要合并），兼顾时效与广度；搜图链接经过 SSRF 校验与会话隔离缓存保护
- **AI 会话隔离文件工作区与聊天文件接收**：废除旧版全局 `UserData` 文件读写，改为按 QQ 用户 / 群组严格隔离的沙箱工作区（`UserData/aiagent_files/...`），支持文件读写、删除、移动、zip/unzip 压缩解压（带 Zip-Slip 防护）以及 `send_file` 发送文件；自动接收用户发来的聊天文件（硬上限 100MB）存入工作区供 AI 阅读与处理
- **AI 智能长回复与合并转发**：去除 50 字长度枷锁，日常闲聊精简，教程/代码/长文分析可完整输出；单条回复超过阈值（默认 1200 字）自动按自然段落、换行或标点切分并放入单个合并聊天记录发出（带 URL 原子保护），转发失败优雅降级逐条发送；短期上下文自动截断保留，防止撑爆上下文预算
- **AI 回复表情包跟随**：文字回复成功后可随机抽取 1 张表情包跟随发送，支持限定贴纸包或使用全部贴纸包，自动最近 5 张去重，合并转发时整条回复仅跟发 1 张，失败完全静默
- **AI 缓存命中率优化**：请求前缀改为「人格 → 长期记忆摘要 → 短期历史 → 群公共上下文 → 当前时间 + 用户消息」，时间戳从 system 挪到当前 user 消息并按原样写入历史；记忆只注入会话标记之前的稳定摘要区（当前会话原文不再重复，群聊只注入群记忆）；历史改为「涨到 2 倍窗口才整段裁」；工具集合固定下发并按名字排序。活跃会话的 DeepSeek 前缀命中率预计可到 80% 以上；每次请求会打一行 `[AIAgent] usage caller=... hit=... miss=...` 日志，可按 `chat` / `review` / `summarize` / `ai_news` / `astrbot` 归因
- **AI 引用回复**：AI 的回复现在会以引用回复的形式挂在触发它的消息下（群聊/私聊都生效），多话题并发时更容易看清在回哪条；`chat.quote_reply` 默认开，后台「AI Agent」页可关闭；超长回复改用合并转发时无法携带引用
- **AI 群聊自动回复**：新增全局 `auto_reply` 白名单（只有名单里的群会“没 @ 也回”，名单不设数量上限）；普通群消息逐条触发，同群冷却默认 5 秒，冷却内消息直接跳过、不排队；复用 AI Agent 的人格/记忆/工具，自动回复不占对话配额，失败静默；后台「AI Agent」页可开关与配置
- **AI 搜索升级**：`web_search` 改用 DeepSeek 官方 Search API（`/v1/tools/search`）——此前服务端内置 `web_search` 只在 `deepseek-v4-pro` 生效、flash 下会被忽略；现在 flash 也能搜，并支持 `site:` / `lang:` / `after:` / `before:` / 精确短语等语法、按用户原话补 `ori_question`、可选带回网页正文，408/429/5xx 自动重试；非 DeepSeek 端点自动回退 SearXNG，后台可调搜索条数与是否带回正文
- **AI 看图增强**：AI Agent 图片输入改为默认开启，精度默认 `auto`（保留原图，截图里的文字可读）；模型名改用官方 `deepseek-flash`，主模型是 `deepseek-v4-pro` 等不支持看图的模型时，带图请求会自动切到 Flash，也可在后台「AI Agent」页单独指定「看图模型」；超过 2048px 或 3 MiB 的图片会先压缩再发送，避免超出 API 尺寸/体积限制
- **Steam 解析正式接入**：`store.steampowered.com/app/<appid>` 商店游戏页现在能单独触发自动解析（此前只有同条消息里恰好带了别的受支持平台链接时才会被顺带解析）；`parsers.steam` 默认 `仅视频`，只发预告片、不发截图（游戏信息随合并转发首条一起发出），避免一个游戏页几十个媒体拖慢下载；后台「权限规则」新增「Steam 解析」，媒体详情页平台清单同步；多个媒体一律合并转发（视频也在里面），合并被拒或超时就什么都不发，不再逐条补发导致同一批媒体收到两遍
- **媒体解析升级 v7.0.0**：vendored 解析器跟进上游——B站 Cookie 扫码登录与自动续期、抖音 Web 接口兜底、下载安全加固（SSRF 防护）、小红书 `xhslink.cn` 短链支持；上游新增的 Pixiv 解析被剔除（继续由自有 `pixiv_parser` 插件处理），并适配了 shim 日志与下载会话两处衔接
- **AI 多配置文件**：可配多套 AI 设置（不同模型/Key/人格，上限 20 套），按群聊或私聊绑定，未绑定的会话走全局默认；后台「AI Agent」页可切换/新建/重命名/删除/设为默认并管理绑定，聊天里用 `AI配置列表` / `切换AI配置` / `解绑AI配置`（限超级用户）。旧扁平配置首次启动自动迁移
- **AI 配额系统**：AI 聊天改为对话次数配额（每日/每小时，群扣群额度、私聊扣个人额度），支持用户/群定制限额、豁免名单、`额度` 命令查询，后台新增「AI 配额」页查看每个群用量明细并手动重置
- **黑白名单细化**：白名单/黑名单按用户、群两个维度独立开关（如只开群白名单不影响私聊），后台「AI 配额」页统一管理，旧配置自动兼容
- **bot_help 工具**：AI Agent 新增帮助文档工具，被问到"你会干什么"时自动读取 `docs/` 功能文档作答
- **AI 聊天上下文与安全加固**：群聊短期上下文改为按「群 + 用户」隔离（同一群不同用户不再串线），同一会话的消息串行处理避免乱序；可选开启群聊公共上下文（默认关闭）；图片下载增加 SSRF 防护（拒绝内网/回环地址并逐跳校验重定向）；AI 写 `UserData` 文件默认关闭（`tools.files.allow_writes`）；记忆总结按当前 `api.protocol` 正确分发（Responses / Chat Completions）
- **AI 配额与工具稳定性**：配额改为「先原子预留、失败退回」，并发请求不会一起挤过限额；单个工具调用受 `tools.tool_timeout_seconds` 限制（默认 30 秒，超时只让该工具报错）；失败原因区分提示（超时 / 网络 / 限流 / 上游故障 / Key 无效）
- **上下文预算与记忆提示注入防护**：短期上下文受 `chat.max_context_chars` 字符预算约束（默认 12000，超出时裁到一半，避免每轮滑窗破坏前缀缓存）；持久化记忆按「参考数据而非指令」注入，并转义记忆文本中的 `system:`/`assistant:`/`user:` 角色标记
- **AI 群聊工具与本地聊天记录**：新增三个只读群聊工具——枚举本群成员（`group_members`）、查成员名片资料（`group_member_profile`）、查成员历史发言（`group_user_messages`），一律只作用于当前群（schema 里没有 `group_id`，私聊不下发），命中多人时返回候选让模型反问；配合本地聊天记录（`UserData/aiagent_chatlog/`，只记纯文本、默认保留 7 天 / 200MB 自动清理，全局段 `chatlog.*`，后台可开关与设群白名单）解决 NapCat 不存历史的问题
- 文档拆分为 `docs/` 多文件，按类别维护

---

## 📋 快速功能索引

| 功能 | 触发方式 |
|------|----------|
| 媒体解析（Pixiv/B站/抖音/小红书/YouTube/网易云等） | 直接发送链接 |
| 贴纸 | `tg贴纸 <链接>` / 关键词触发 / `贴纸包` |
| 合并转发表情导出 | 引用合并聊天记录后回复 `导出表情 [包名]`（递归展开嵌套记录） |
| 动图/视频互转 | 引用媒体后回复 `转mp4`/`转视频`、`转gif`/`转动图`/`转贴纸` |
| TTS | `说话 <文本>`；AI 也可通过 `send_voice` 自己发语音 |
| QQ 空间 | `发说说 <内容>` / `空间动态` / `好友说说 <QQ号>` / `说说赞 <序号>` / `空间定时` |
| AI 聊天 | 私聊文本 / 群聊 @机器人；白名单群可开启自动回复；`额度` 查配额；`AI配置列表` / `切换AI配置` 管理多套配置 |
| 定时推送 | `推送 状态`；Steam/AI 资讯/知乎/RSS |
| Wiki 查询 | `星露谷wiki` / `mcwiki` / `塔2wiki` / `osu` |
| 吉星派对观战 | `查询 <观战码>` |
| 帮助 / 关于 | `帮助` / `关于` |
| 管理后台 | `http://IP:54213/`（AI 配额、贴纸、TTS、推送、日志） |

完整命令列表见 [docs/plugins.md](docs/plugins.md)。

---

## 🗂 项目结构

```text
HIKARI_BOT_NEO/
├── bot.py              # 程序入口
├── core/               # 核心模块（命令路由、配置、渲染、黑白名单等）
├── plugins/            # 功能插件（媒体解析、贴纸、AI Agent、后台等）
├── third_party/        # 上游 vendored 代码
├── docs/               # 文档（按类别拆分）
├── deploy/             # 服务器 Compose 编排
├── deploy.ps1          # SSH 部署脚本（PowerShell 7）
└── install.sh          # 一键安装脚本
```

---

## 📄 许可证与致谢

本项目使用 [GNU Affero General Public License v3.0 or later](LICENSE) 开源。使用本项目解析、下载或转发第三方平台内容时，请自行确认相关平台服务条款和内容版权要求。

参考与致谢：NoneBot 2、NapCatQQ、drdon1234/astrbot_plugin_media_parser（AGPL）、Eganchiyu/qzone-sdk（MIT）、yt-dlp、imputnet/cobalt、searxng/searxng、valkey-io/valkey、NeteaseCloudMusicApiEnhanced/api-enhanced。

用户协议与隐私政策模板见 [USER_AGREEMENT.md](USER_AGREEMENT.md) 与 [PRIVACY_POLICY.md](PRIVACY_POLICY.md)。
