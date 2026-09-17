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

- **Steam 解析正式接入**：`store.steampowered.com/app/<appid>` 商店游戏页现在能单独触发自动解析（此前只有同条消息里恰好带了别的受支持平台链接时才会被顺带解析）；`parsers.steam` 默认 `仅视频`，只发预告片、不发截图与文本，避免一个游戏页几十个媒体拖慢下载又撑爆合并转发；后台「权限规则」新增「Steam 解析」，媒体详情页平台清单同步
- **媒体解析升级 v7.0.0**：vendored 解析器跟进上游——B站 Cookie 扫码登录与自动续期、抖音 Web 接口兜底、下载安全加固（SSRF 防护）、小红书 `xhslink.cn` 短链支持；上游新增的 Pixiv 解析被剔除（继续由自有 `pixiv_parser` 插件处理），并适配了 shim 日志与下载会话两处衔接
- **AI 多配置文件**：可配多套 AI 设置（不同模型/Key/人格，上限 20 套），按群聊或私聊绑定，未绑定的会话走全局默认；后台「AI Agent」页可切换/新建/重命名/删除/设为默认并管理绑定，聊天里用 `AI配置列表` / `切换AI配置` / `解绑AI配置`（限超级用户）。旧扁平配置首次启动自动迁移
- **AI 配额系统**：AI 聊天改为对话次数配额（每日/每小时，群扣群额度、私聊扣个人额度），支持用户/群定制限额、豁免名单、`额度` 命令查询，后台新增「AI 配额」页查看每个群用量明细并手动重置
- **黑白名单细化**：白名单/黑名单按用户、群两个维度独立开关（如只开群白名单不影响私聊），后台「AI 配额」页统一管理，旧配置自动兼容
- **bot_help 工具**：AI Agent 新增帮助文档工具，被问到"你会干什么"时自动读取 `docs/` 功能文档作答
- **AI 聊天上下文与安全加固**：群聊短期上下文改为按「群 + 用户」隔离（同一群不同用户不再串线），同一会话的消息串行处理避免乱序；可选开启群聊公共上下文（默认关闭）；图片下载增加 SSRF 防护（拒绝内网/回环地址并逐跳校验重定向）；AI 写 `UserData` 文件默认关闭（`tools.files.allow_writes`）；记忆总结按当前 `api.protocol` 正确分发（Responses / Chat Completions）
- **AI 配额与工具稳定性**：配额改为「先原子预留、失败退回」，并发请求不会一起挤过限额；单个工具调用受 `tools.tool_timeout_seconds` 限制（默认 30 秒，超时只让该工具报错）；失败原因区分提示（超时 / 网络 / 限流 / 上游故障 / Key 无效）
- **上下文预算与记忆提示注入防护**：短期上下文受 `chat.max_context_chars` 字符预算约束（默认 12000，超出从最旧丢起）；持久化记忆按「参考数据而非指令」注入，并转义记忆文本中的 `system:`/`assistant:`/`user:` 角色标记
- **AI 群聊工具与本地聊天记录**：新增三个只读群聊工具——枚举本群成员（`group_members`）、查成员名片资料（`group_member_profile`）、查成员历史发言（`group_user_messages`），一律只作用于当前群（schema 里没有 `group_id`，私聊不下发），命中多人时返回候选让模型反问；配合本地聊天记录（`UserData/aiagent_chatlog/`，只记纯文本、默认保留 7 天 / 200MB 自动清理，全局段 `chatlog.*`，后台可开关与设群白名单）解决 NapCat 不存历史的问题
- 文档拆分为 `docs/` 多文件，按类别维护

---

## 📋 快速功能索引

| 功能 | 触发方式 |
|------|----------|
| 媒体解析（Pixiv/B站/抖音/小红书/YouTube/网易云等） | 直接发送链接 |
| 贴纸 | `tg贴纸 <链接>` / 关键词触发 / `贴纸包` |
| 动图/视频互转 | 引用媒体后回复 `转mp4`/`转视频`、`转gif`/`转动图`/`转贴纸` |
| TTS | `说话 <文本>` |
| AI 聊天 | 私聊文本 / 群聊 @机器人；`额度` 查配额；`AI配置列表` / `切换AI配置` 管理多套配置 |
| 定时推送 | `推送 状态`；Steam/AI 资讯/知乎/RSS |
| Wiki 查询 | `星露谷wiki` / `mcwiki` / `塔2wiki` / `osu` |
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

参考与致谢：NoneBot 2、NapCatQQ、drdon1234/astrbot_plugin_media_parser（AGPL）、yt-dlp、imputnet/cobalt、searxng/searxng、valkey-io/valkey、NeteaseCloudMusicApiEnhanced/api-enhanced。

用户协议与隐私政策模板见 [USER_AGREEMENT.md](USER_AGREEMENT.md) 与 [PRIVACY_POLICY.md](PRIVACY_POLICY.md)。
