# 插件功能详解

## Pixiv 作品解析

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

## 聚合媒体解析

**配置文件：** `BotData/plugin_configs/media_parser.json`

基于 vendored 的 [`drdon1234/astrbot_plugin_media_parser`](../third_party/astrbot_plugin_media_parser) 解析多个平台链接，使用 HIKARI 的 OneBot 发送链发送文本、图片和视频。

**支持平台：** B站、抖音、TikTok、快手、微博、小红书、闲鱼、今日头条、小黑盒、Steam、Twitter/X（各平台短链均可识别，如 `b23.tv`、`v.douyin.com`、`xhslink.com` / `xhslink.cn`、`vm.tiktok.com`）

**QQ 分享卡片：** 直接分享成 QQ 小程序 / 新闻 / 音乐卡片（json / xml 消息段）时，会从卡片元数据里取回真实链接再解析——除 `meta.detail_1.qqdocurl`、`meta.news.jumpUrl` 外，也会扫描 `detail_1.url` 等其他字段，并还原 `\/` 与 HTML 转义；只有命中受支持平台域名的链接才会进入解析。

> YouTube 由独立的 `youtube_downloader` 插件处理；上游 v6.4.0 起自带的 Pixiv 解析在 vendored 副本中已剔除，Pixiv 链接继续由独立的 `pixiv_parser` 插件处理，避免重复解析。

> **Steam：** 只解析商店游戏页 `store.steampowered.com/app/<appid>`（`/bundle`、`/sub`、`/publisher` 这类页面不解析）。标题、开发商、发行日期、价格和类型来自官方 `appdetails` 接口；一个游戏页会同时解析出封面/胶囊图、全部截图、预告片缩略图和预告片视频，但 `parsers.steam` 默认是 `仅视频`，因此只发送预告片视频，图片和文本信息都不发送（页面没有预告片时这条链接不发送任何内容）。想连截图一起发就改成 `全部发送` 或 `仅富媒体`；`max_send` 按「先保留视频、再用剩余额度补图片」的顺序裁剪，媒体多时下载与发送都会明显变慢，合并转发还可能因为单个资源上传失败而整包失败并降级为逐条发送，必要时调低 `max_send`。下载代理开关在 `proxy.steam`（`parse` 详情接口默认 `false`，`image` 图片 / `video` 视频默认 `true`，三者都只在配置了 `proxy.address` 时才真正生效）；`steam.use_xiaoheihe` 打开后改走小黑盒完整游戏路径，补充评分、在线人数、销量排行等统计（默认关闭）。

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
| `parsers.<平台>` | 各平台输出模式：`关闭` / `全部发送` / `仅文本` / `仅富媒体` / `仅视频`（`仅视频` 只发送该链接解析出的视频，图片和文本都不发送，默认仅 Steam） |
| `message.text_metadata.show_url` | 是否在解析结果里附上"原始链接：…"，默认 `true` |
| `message.text_metadata.max_desc_chars` | 简介/正文最多显示多少字符，默认 600 |
| `permissions` | QQ/群黑白名单 |
| `proxy.address` | 代理地址，例如 `http://127.0.0.1:7890`（TikTok / 小黑盒视频 / Twitter 等分平台开关在 `proxy` 下） |
| `bilibili_enhanced.cookie` | B站 Cookie（高画质和受限内容） |
| `bilibili_enhanced.max_quality` | Cookie 模式下的画质上限（`不限制` / `4K` / `1080P60` / `1080P+` / …） |
| `bilibili_enhanced.admin_assist.enable` | Cookie 失效时私聊管理员协助扫码登录 |
| `download.max_video_size_mb` | 单个视频大小上限 |
| `download.cache_dir` | 媒体缓存目录（需与 NapCat 容器共享，默认 `/tmp/hikari_bot/media_parser`） |
| `parse_rate_limit.same_link` / `same_user` | 解析频控：时间窗内同链接 / 同用户最多解析次数，`0` 为不限制（记录持久化在缓存目录） |

**B站 Cookie 辅助登录：** 开启 `bilibili_enhanced.use_cookie` 和 `admin_assist.enable` 后，Cookie 缺失或失效时 Bot 会私聊超级管理员。回复"确定"后会收到 Bilibili 登录二维码图片和备用链接；扫码成功后新 Cookie 自动保存，无需手动替换。超级管理员也可发送 `B站登录` / `B站Cookie` 手动触发。

> **未接入的上游能力：** vendored 的 ZIP 归档（`message.archive`）、LLM 翻译（`translation`）、媒体中转（`media_relay`）和消息聚合（`message.packing`）依赖上游 AstrBot 的消息链。本仓库用自己的发送链（`send_strategy` 控制合并转发与回退，文本由本地组装），这些配置键保留在示例配置中但不生效。

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

脚本克隆上游 `main` 全量替换 vendored 目录后，会**自动剔除上游的 Pixiv 解析**并校验零残留；若上游重构导致剔除正则失配，脚本会报错并列出残留位置，需手动清理后再提交。

---

## Instagram / Facebook 解析

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

## YouTube 视频下载

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

## 网易云音乐解析

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

**触发方式：**
- 私聊：直接发送链接即自动解析（`auto_parse`）
- 群聊：默认需 `引用` 分享卡片或链接消息并 `@机器人`；加入 `auto_parse_groups` 白名单的群发链接即自动解析
- 非白名单群里未 @bot 的网易云链接/卡片：回复一句「引用 + @bot」引导提示（`card_hint` 控制，同群默认冷却 300 秒，避免刷屏）

**切换音质：** 回复 Bot 刚发送的网易云文件消息，内容包含 `mp3` 或 `flac`，Bot 会按目标格式重新发送，并把这个格式记为你的默认偏好（按用户持久化，无偏好默认 FLAC，之后解析链接直接按偏好请求；`quality_switch` 可关闭）。@bot、普通消息或非引用来源不会触发。

**关键配置：**

| 字段 | 说明 |
|------|------|
| `auto_parse` | 是否自动解析网易云链接（私聊；群聊见 `auto_parse_groups`） |
| `api_base_url` | api-enhanced 服务地址 |
| `api_timeout` | API 超时，默认 30s |
| `high_quality` | 默认请求最高可用音质（FLAC > 320k > 192k），关闭则固定 320kbps MP3 |
| `quality_switch` | 是否启用「回复换音质」，默认开 |
| `auto_parse_groups` | 群白名单（`enable` + `groups`），名单内发链接即自动解析 |
| `card_hint.enabled` / `card_hint.cooldown_seconds` | 非白名单群引导提示开关与同群冷却秒数 |
| `cookie` | 网易云登录 Cookie（VIP 歌曲完整播放） |
| `real_ip` | 国内 IP（海外服务器绕过地区限制） |
| `max_file_mb` | 单文件大小上限，默认 200 MB |

---

## SoundCloud 音频下载

**配置文件：** `BotData/plugin_configs/soundcloud_parser.json`

使用 `yt-dlp` 下载 SoundCloud 音频。直接发送 `on.soundcloud.com` 短链接即可触发解析。

**支持链接：**
- `https://on.soundcloud.com/<shortcode>`（分享短链接，推荐）
- `https://soundcloud.com/<艺人>/<曲目>`（完整链接）
- `https://m.soundcloud.com/<艺人>/<曲目>`（移动端）

> 单级路径的短链接（如 `soundcloud.com/xxxxx`）会被视为用户主页，不会被自动解析。

**发送方式：** 默认通过 NapCat 上传文件到聊天（`艺人 - 曲目.flac` 等），可切换为语音消息。

**关键配置：**

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用 |
| `auto_parse` | 是否自动解析消息中的链接 |
| `send_strategy` | `"upload"` = 上传文件，`"record"` = 语音消息 |
| `preferred_codec` | `"best"` = 原始格式，或 `m4a` / `mp3` / `opus` / `flac` |
| `max_file_mb` | 文件大小上限，默认 1024 MB |
| `send_link_info` | 是否发送标题、作者、时长等详情 |
| `download_timeout` | 下载超时（秒） |
| `cookiefile` | yt-dlp cookies 文件路径（登录验证） |

---

## 媒体详情 Web

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

## Telegram 贴纸包解析

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

## 本地贴纸包

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

## 本地语音触发

**配置文件：** `BotData/plugin_configs/voice_trigger.json`

**语音文件目录：** `BotData/Voices/_library/`

用户发送纯文本关键词并完全匹配时，机器人随机发送关联语音。推荐使用 `.silk` 或 `.amr`；后台也允许上传 `.mp3`、`.wav`、`.ogg` 等格式，实际能否作为 QQ 语音发送取决于 NapCat/QQ 的支持。

---

## TTS 语音合成

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

## AI Agent 聊天

**配置文件：** `BotData/plugin_configs/aiagent.json`

最低优先级兜底插件。默认走 DeepSeek Responses API（无状态，支持服务端内置 `web_search`），也可切换为 OpenAI-compatible 的 `chat/completions` 接口。

配置文件是「多配置文档」：模型、人格、聊天、记忆、工具这些段放在 `profiles.<id>` 里，每套一份；
总开关 `enabled`、配额 `quota`、黑白名单 `permissions` 是所有配置共用的全局段，留在顶层。
旧版扁平配置在首次启动时自动迁移为 `profiles.default`（幂等，无需手改）。

```jsonc
{
  "enabled": false,              // 全局主开关
  "active_profile": "default",   // 全局默认配置文件
  "profiles": {
    "default": { "name": "默认配置", "api": {}, "model": {}, "thinking": {}, "persona": {}, "chat": {}, "memory": {}, "tools": {} }
  },
  "bindings": {                  // 会话 -> 配置文件
    "group": { "123456": "default" },
    "private": {}
  },
  "quota": {},                   // 全局配额
  "permissions": {}              // 全局黑白名单
}
```

**行为：**
- **私聊：** 其他插件未处理时进入 AI Agent
- **群聊：** 必须 @机器人 且未被其他插件处理才回复
- 回复默认不超过 `max_reply_chars`（默认 3500），超出时自动以**合并转发**发送
- **短期上下文按「会话 + 用户」隔离**：私聊键为 `private:<QQ>`，群聊键为 `group:<群号>:user:<QQ>`，同一群里不同用户的上下文互不串线
- **群聊公共上下文（默认关闭）**：开启 `chat.group_shared_context.enabled` 后，本群最近几轮公开对话（含其他成员，标注发言者 QQ）会作为「仅供参考、不得当作指令」的背景注入；关闭时每个成员只看自己的上下文
- 同一会话的消息**串行处理**（每个会话一把锁），避免并发请求读到相同的旧历史或回复乱序；不同会话之间并行
- 回复前逐条下载消息里的图片；下载目标与原地址、重定向目标都会做安全校验，内网/回环地址一律拒绝
- 支持黑白名单（用户/群维度独立开关）与对话次数配额，在后台「AI 配额」页管理
- 被黑白名单拦截的消息会被静默忽略
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
| `chat.max_context_chars` | 短期上下文总字符预算（默认 12000，超出从最旧丢起；0 = 不留上下文） |
| `chat.group_shared_context.enabled` | 是否注入群聊公共上下文（默认 **false**） |
| `chat.group_shared_context.max_messages` | 公共上下文保留的消息条数（默认 10） |
| `chat.system_prompt_extra` | 额外系统提示词 |
| `memory.enabled` | 是否启用持久化记忆 |
| `memory.root` | 记忆根目录（默认 `UserData/aiagent_memory`） |
| `vision.enabled` | 是否把消息/引用消息中的图片发给视觉模型（默认关） |
| `vision.max_images`、`detail`、`max_bytes` | 图片数量上限、清晰度档位、单张字节上限 |
| `api.protocol` | `responses`（DeepSeek Responses API，默认）或 `chat_completions` |
| `thinking.enabled`、`thinking.reasoning_effort` | 思考模式开关与强度（`low`/`medium`/`high`/`max`） |
| `tools.help.enabled` | 是否启用 bot_help 帮助文档工具（默认开） |
| `tools.search.enabled` | 是否启用网页搜索（SearXNG） |
| `tools.files.enabled` | 是否启用文件工具（读取人格资源、读取 `UserData`） |
| `tools.files.allow_writes` | 是否允许 AI 写入 `UserData`（默认 **false**，写入工具不下发给模型；后台「AI Agent」页 Tools 管理里对应「允许 AI 写 UserData 文件」） |
| `tools.plugin_tools.enabled` | 是否启用插件 AI 工具 |
| `tools.max_tool_rounds` | 单次回复最多工具调用轮数，默认 4 |
| `tools.wiki_prefetch.enabled` | 是否启用 wiki 优先预取（默认开，命中 wiki 别名时先跑一次 wiki） |
| `tools.wiki_prefetch.web_search` | 预取 wiki 时是否顺带强制一次 `web_search`（默认开，关闭可省一次外部搜索） |
| `tools.tool_timeout_seconds` | 单个工具调用超时（默认 30 秒；超时按工具报错返回，不拖住整轮回复） |
| `tools.group_members.enabled` | 是否允许 AI 枚举本群成员（默认开；只读，仅当前群，私聊不下发） |
| `tools.group_members.max_members` | 一次最多返回的成员数（默认 100） |
| `tools.member_profile.enabled` | 是否允许 AI 查询群成员名片与资料（默认开；只读，仅当前群） |
| `tools.user_messages.enabled` | 是否允许 AI 查询群成员历史发言（默认开；只读，仅当前群） |
| `tools.user_messages.max_messages` | 历史发言条数上限（默认 50） |
| `tools.user_messages.max_chars` | 历史发言总字符预算（默认 4000，超出从最旧丢起） |
| `tools.user_messages.allow_live_history` | 本地记录不足时是否用 NapCat 实时历史窗口补齐（默认开） |
| `chatlog.enabled` | 是否把群消息记录到本地（全局段，默认 **true**；只记纯文本，详见下文） |
| `chatlog.groups` | 只记录这些群号，留空 = 全部群 |
| `chatlog.retention_days` | 聊天记录保留天数（默认 7） |
| `chatlog.max_total_mb` | 聊天记录总量上限 MB（默认 200，超出从最旧删） |
| `chatlog.record_bot` | 是否连机器人自己的发言也记录（默认 false） |
| `quota.enabled` | 是否启用对话次数配额（默认关） |
| `quota.default_user` / `default_group` | 默认额度：每日/每小时对话次数（0 = 不限额） |
| `quota.user_overrides` / `group_overrides` | 指定用户/群的独立额度 |
| `quota.exempt_user_ids` / `exempt_group_ids` | 豁免名单（不限额） |
| `quota.count_background` | 记忆总结等后台调用是否计入配额 |
| `permissions` | 黑白名单（用户/群维度独立开关） |

表中除 `enabled`、`quota.*`、`permissions`、`chatlog.*` 之外的字段都位于 `profiles.<id>` 内，每套配置各有一份；`chatlog.*` 是全局段（一个部署一份记录策略），所有配置文件共用。

### 多配置文件与会话绑定

一个部署可以配多套 AI 设置（不同模型、不同 Key、不同人格），上限 20 套，然后把具体的群或私聊绑定到某一套。

- 配置文件 ID 是自动生成的 slug（`[A-Za-z0-9_-]`，最长 32 字符），创建后不可改；名称可随时改。
- 未绑定的会话使用 `active_profile` 指向的全局默认配置。
- 删除配置文件时会连带清掉它的会话绑定；默认配置和最后一套配置不允许删除。
- 所有配置共用同一个记忆目录（`memory.root`），切换配置不会丢记忆；配额账本按 `group:` / `user:` 记账，与配置文件无关。

后台「AI Agent」页顶部可以切换正在编辑的配置、新建/重命名/删除/设为默认，下方的「会话绑定」表按群聊 / 私聊两个 tab 管理绑定。

聊天命令（均限超级用户，因为切配置会直接改变调用的模型与花费）：

| 命令 | 效果 |
|------|------|
| `AI配置列表` / `配置列表` | 列出全部配置，标出全局默认和当前会话生效的那套 |
| `切换AI配置 <名称\|ID>` | 把当前群/私聊绑定到该配置 |
| `解绑AI配置` | 清除当前会话绑定，回落全局默认 |

### 对话次数配额

一条用户消息 = 1 次对话（内部工具调用多轮只算 1 次）。群聊扣群共享额度，私聊扣用户个人额度，每日 / 每小时各一个固定窗口（整点/零点滚动）。

- 超限时回复「今日/本小时 AI 对话次数已用完，X 点恢复」
- 发送 `额度` 可查询本群/个人剩余次数与重置时间
- 后台「AI 配额」页可配置默认额度、定制限额、豁免名单、黑白名单，并查看每个群的用量明细（进度条、剩余、重置），支持手动重置
- 黑白名单优先于配额：命中黑名单或不在白名单的会话直接不回复

### 黑白名单（用户/群维度独立开关）

| 开关 | 语义 |
|------|------|
| 启用用户白名单 | 仅名单内 QQ 号可用（私聊与群聊均生效） |
| 启用群白名单 | 仅名单内群可用（只约束群聊，不影响私聊） |
| 启用用户黑名单 | 名单内 QQ 号禁止 |
| 启用群黑名单 | 名单内群禁止（只约束群聊） |

白名单维度任一启用时，未命中任何启用的白名单维度即拒绝；旧版整体 `enable` 配置自动兼容。

### AI Agent 工具

插件工具由各插件显式注册，默认只提供只读查询：

| 工具 | 来源 | 说明 |
|------|------|------|
| `bot_help` | 内置 | 查询 `docs/` 功能文档（被问到"你会干什么"时自动使用） |
| `web_search` | 内置 | 通过 SearXNG 搜索网页 |
| `group_members` | 内置 | 枚举当前群成员（只读，仅当前群） |
| `group_member_profile` | 内置 | 查询当前群某成员的名片与资料（只读，仅当前群） |
| `group_user_messages` | 内置 | 查询当前群某成员最近的历史发言（只读，仅当前群） |
| `mc_wiki_search` | mc_wiki | Minecraft Wiki 查询 |
| `stardew_wiki_search` | stardew_wiki | 星露谷 Wiki 查询 |
| `sts2_wiki_search` | sts2_wiki | 杀戮尖塔 2 Wiki 查询 |
| `zhihu_hot_list` | zhihu_hot | 知乎热搜列表 |
| `steam_deals_list` | steam_deals | Steam 游戏列表 |
| `ai_news_list` | ai_news | AI 资讯列表 |
| `rss_latest` | rss_subscriber | RSS 订阅最新 |
| `osu_user_lookup`、`osu_scores_lookup` 等 | osu_info | osu! 查询 |

#### 群聊工具与本地聊天记录

三个群聊工具都是只读的，并且**只作用于当前群**：工具 schema 里没有 `group_id` 参数（模型无法指定别群），私聊里也不会下发这些工具。成员参数支持 QQ 号、群名片、昵称（含部分匹配）；命中多人时返回候选列表让模型反问用户，不靠猜。资料只返回群内公开可见且稳定的字段（名片/昵称/身份/等级/加群时间/最后发言时间/头衔），不返回性别/年龄/地区。

| 工具 | 作用 | 关键上限 |
|------|------|----------|
| `group_members` | 枚举本群成员，可按昵称/名片关键词、身份（群主/管理/成员）筛选 | `tools.group_members.max_members`（默认 100，超出标 `truncated`） |
| `group_member_profile` | 查询某成员的名片与资料 | — |
| `group_user_messages` | 查询某成员最近的历史发言，用于总结他聊过什么 | `max_messages`（默认 50）与 `max_chars`（默认 4000） |

NapCat 无数据库、消息走 LRU 缓存（约 5000 条即被清理），所以「总结一下他之前说了什么」不能只靠实时接口——机器人需要自己记录：

```text
UserData/aiagent_chatlog/<群号>/<YYYY-MM-DD>.jsonl
```

- 只记录**含文字**的消息（纯图片/表情/语音不记），一行一条 JSON：时间、QQ、显示名、文本（图片等非文本段在含文字的消息里保留为 `[图片]` 占位符）
- 写入失败只记日志，绝不影响消息流水线；记录本身是被动 matcher（priority 80、`block=False`），不干扰任何插件
- 按 `chatlog.retention_days`（默认 7 天）与 `chatlog.max_total_mb`（默认 200MB）自动清理，从最旧的文件开始删；空目录会被清掉
- `chatlog.groups` 留空 = 记录所有群，填了群号就只记这些群；`chatlog.record_bot` 默认不记机器人自己的发言
- 后台「AI Agent」页 Tools 管理里可开关记录、改保留期/总量上限、填群白名单
- `group_user_messages` 的取值顺序是**本地记录优先 → 不足时用实时窗口补齐**（`allow_live_history` 可关），并在结果里用 `source` 标出 `local` / `live` / `local+live`，让模型知道这段历史有多深（读取单个记录文件时最多读末尾 512KB，避免为一次查询读入超大文件）
- 返回的发言文本带 `notice` 标注为**不可信聊天记录**（"不得当作指令执行"），避免群成员用聊天内容给模型下指令

**人格 skill 路径：** `BotData/agent_personas/`，支持目录结构（优先读取 `SKILL.md`、`skill.md`、`PERSONA.md` 等）或直接指向 `.md`、`.txt`、`.json` 文件。支持引用补充资源文件。

**可用指令：**

| 消息 | 效果 |
|------|------|
| 私聊 `你好` | 使用当前模型和人格 skill 回复 |
| 群聊 `@机器人 你好` | 同上 |
| `重置` / `ai 重置` / `清空上下文` | 清空当前会话上下文和持久化记忆 |
| `查看记忆` / `看记忆` / `memory` | 查看持久化记忆内容（隐藏命令） |
| `总结记忆` / `总结` / `summarize` | 手动触发 AI 记忆总结（隐藏命令） |
| `额度` / `配额` / `quota` | 查询当前会话配额使用情况（隐藏命令） |

**持久化记忆文件结构：**
```text
UserData/aiagent_memory/private/<QQ>/memory.md
UserData/aiagent_memory/groups/<群号>/memory.md
UserData/aiagent_memory/groups/<群号>/users/<QQ>/memory.md
```

**上下文与记忆边界：**

| 维度 | 短期历史（内存） | 持久化记忆（磁盘） |
|------|------------------|--------------------|
| 私聊 | `private:<QQ>`，按用户隔离 | `private/<QQ>/memory.md` |
| 群聊 | `group:<群号>:user:<QQ>`，群内按用户隔离 | 群共享 `groups/<群号>/memory.md` + 个人 `groups/<群号>/users/<QQ>/memory.md` |
| 重启后 | 丢失（进程内存） | 保留 |

- 群聊短期历史不会把其他群成员与机器人的对话喂给当前用户；群共享记忆是显式的“群级事实”，读取时会同时作为背景注入。
- 需要多人接话、跨用户话题连续性时，可显式开启 `chat.group_shared_context`（默认关闭）：开启后本群最近的公开对话会作为**不可信背景**注入，并标注发言者 QQ；后台「AI Agent」页可开关与调整条数。
- 短期历史条数由 `chat.max_history_messages` 控制（默认 10 条，一问一答各占 1 条），并受 `chat.max_context_chars` 字符预算约束（默认 12000，超出从最旧的对话丢起）。
- 持久化记忆按**参考数据**注入：提示里明确它不是指令，记忆文本内的 `system:`/`assistant:`/`user:` 等角色标记与 `<...>` 会被转义，降低「在聊天里喂指令写进记忆」的提示注入风险。
- `重置` / `清空上下文` 只清空**当前用户**的短期上下文与记忆文件。
- 记忆总结（自动或 `总结记忆`）按当前配置的 `api.protocol` 走对应接口，Responses 与 Chat Completions 两种配置都可用。
- 后台任务（记忆总结）默认按 `quota.count_background` 计入配额。
- 配额采用**先预留、失败退回**：请求发出前原子检查并扣费（同一 scope 并发请求不会一起挤过限额），只有成功回复才真正消耗；API 报错、网络异常或消息为空都会退回。
- 失败原因会区分提示：超时 / 网络不通 / 上游限流（429）/ 上游故障（5xx）/ Key 无效（401、403）/ 其他失败，对应 `bot_messages.json` 里的 `aiagent.timeout`、`aiagent.network_error`、`aiagent.rate_limited`、`aiagent.upstream_error`、`aiagent.auth_failed`、`aiagent.failed`。
- 单个工具调用受 `tools.tool_timeout_seconds` 限制（默认 30 秒），超时只让该工具返回错误，不会让整轮回复失败，也不会卡住该会话的锁。

**文件工具边界：**
- 读取：`read_persona_resource`（仅 `BotData/agent_personas` 下的 `.md`/`.txt`/`.json`）、`read_user_file`（仅 `UserData`）
- 写入：`write_user_file` 仅在 `tools.files.allow_writes=true` 时才下发给模型，且只能写 `UserData` 内部路径；跨目录逃逸会被拒绝

---

## Bot 后台

**配置文件：** `BotData/plugin_configs/bot_admin.json`

Python 托管的 Web 管理后台，默认监听 `0.0.0.0:54213`。

**功能总览：**
- **总览页：** 机器人实时运行状态，包括各插件当前进行的解析、下载和回复活动
- **贴纸管理：** 上传贴纸素材到已有贴纸包或创建新包，保存前统一转换为 GIF；填写额外触发词
- **表情收集箱：** 整理机器人静默收集的待整理表情，批量加入贴纸包或删除
- **语音管理：** 上传语音文件，管理触发关键词，浏览器预览播放
- **TTS 管理：** 管理 Fish Audio 音色库、API Key、模型、语速、响度等参数
- **AI Agent 配置：** 多套配置文件（新建/重命名/删除/设为默认）、API 地址、模型参数、Key、人格 skill 路径、聊天限制，以及群聊/私聊的配置绑定
- **AI 配额：** 配置对话次数配额（默认额度/定制限额/豁免）、黑白名单（用户/群维度开关），查看每个群的用量明细并手动重置
- **记忆管理：** 查看 AI 持久化记忆文件，手动触发总结
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

完整 HTTP API 文档见 [API.md](API.md)。

---

## 贴纸静默收集

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
| `target_packs` | 定向收集目标：QQ 号 → `{pack, name, groups, enabled}` |

### 定向收集

配置 `target_packs` 可指定收集某个群友的表情包，直接进入正式贴纸库的新贴纸包（自动建包、按 GIF 哈希去重），不再进收件箱：

```json
"target_packs": {
  "123456789": {
    "pack": "某人的表情包",
    "name": "昵称",
    "groups": [],
    "enabled": true
  }
}
```

| 字段 | 说明 |
|------|------|
| `pack` | 目标贴纸包名（不存在时自动创建，同名时合并） |
| `name` | 昵称，仅用于列表展示 |
| `groups` | 限定收集的群号，空 = 全部群 |
| `enabled` | 是否启用该目标 |

也可以直接用群内命令管理（仅超级管理员或群 owner/admin 可用）：

- `收集 @某人 [包名]` — 开始收集；包名不填默认用群内昵称
- `停止收集 @某人` — 停止收集，已收集内容保留
- `收集列表` — 查看当前收集目标及已收数量

定向收集命中时直接调用 `sticker_library.save_gifs_to_pack()`（`source="qq_collect"`），与静默收集共用下载、转 GIF、大小限制等逻辑。定向收集是显式指定的目标，不受 `collect_group` / `collect_private` / `allowed_groups` 开关限制（但仍遵守全局 `enabled` 与 `ignored_users`）。

**只收动画表情：** 定向收集只收 QQ 表情面板的动画表情。NapCat 对表情消息带 `summary="[动画表情]"` 标记，普通图片与静态表情包无此标记（summary 为空）且无法从协议层区分——所以定向收集按 `summary` 标记过滤：非 `[动画表情]` 的消息在下载前直接跳过，不下载不存储；标记命中的消息下载后转 GIF 入库（部分标记为动画表情的表情内容实际为静态 JPEG，仍按标记收集）。静默收集（收件箱）行为不变，仍收所有图片。

**公开页面（无需登录）：**

任意贴纸包有公开页面（挂载在 Bot 后台原端口，`auth=False`）。`<key>` 可以是定向目标的 QQ 号（显示目标昵称）或任意贴纸包名：

- `GET /collect/<key>` — 该贴纸包的全部表情包网格预览
- `GET /collect/<key>/sticker/<贴纸ID>` — 单张 GIF 图片（仅限该贴纸包内的贴纸）

规则：先按 QQ 号查定向目标（命中且启用时显示目标包，包未创建时显示空状态），查不到再按贴纸包名查找；两者都找不到返回 404。图片端点校验贴纸归属，不会泄露其他贴纸包内容。

---

## 定时推送框架

**配置文件：** `BotData/plugin_configs/push_framework.json`

通用推送骨架：负责定时、目标发送、失败重试和同一轮去重；具体内容由消息源提供。插件可以调用 `register_push_source()` 注册自己的消息源。

**关键配置：**

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用 |
| `startup_delay_seconds` | 启动后等待秒数再开始检查 |
| `check_interval_seconds` | 检查间隔，默认 60 秒 |
| `jobs[].id` | 任务 ID，手动测试时使用 |
| `jobs[].trigger` | 触发器：`schedule` / `startup` / `shutdown` / `manual` / `event`（由插件事件触发，如好友添加通知） |
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
| `friend_add` | 新好友添加通知（需 `trigger: event`，内容来自事件数据） |

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

**事件触发示例（好友添加通知）：** `trigger: "event"` 的任务不会参与定时轮询，由对应插件在事件发生时触发（如 `friend_manager` 在收到好友添加通知时触发 `friend_add` 消息源），`targets` 建议填超级管理员的私聊 QQ：
```json
{
  "jobs": [{
    "id": "notify_friend_add",
    "enabled": true,
    "trigger": "event",
    "source": "friend_add",
    "targets": {"group_ids": [], "private_user_ids": [3433559280]}
  }]
}
```
若未配置任何 `friend_add` 任务，`friend_manager` 会直接私聊超级管理员作为兜底。

**自定义消息源：**
```python
from plugins.push_framework import register_push_source, PushContext

@register_push_source("my_source", description="我的自定义推送源")
async def build_message(ctx: PushContext):
    keyword = ctx.options.get("keyword", "默认主题")
    return f"今日主题：{keyword}"
```

---

## Steam 热门热卖日报

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

## AI 最新资讯日报

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

## 知乎热搜

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

## RSS 订阅

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

## osu! 信息查询

**配置文件：** `BotData/plugin_configs/osu_info.json`

通过 osu! API v2 查询用户、成绩、排行榜与谱面信息，结果渲染为图片。使用前需在 osu! 开发者中心创建 OAuth 客户端，并填入 `client_id` 和 `client_secret`。

**可用指令（需 @机器人）：**

| 消息 | 效果 |
|------|------|
| `osu [模式] [用户名/ID]` | 查询用户信息；不填用户时使用绑定账号 |
| `osu 用户 [模式] [用户名/ID]` | 显式查询用户信息 |
| `osu 绑定 <用户名/ID> [模式]` / `osu 解绑` | 绑定或解绑当前 QQ |
| `osu 看板 [模式] [用户名/ID]` | 用户信息 + 最近成绩看板 |
| `osu 成绩 [best\|recent\|firsts] [模式] [用户名/ID]` | 查询成绩列表 |
| `osu 排名 [模式] [国家代码]` | 查询全球或国家排行榜前列 |
| `osu 谱面 <谱面ID\|关键词>` | 查询谱面详情或搜索谱面 |
| `osu 下载 <谱面集ID\|谱面链接\|关键词>` | 优先从 osu! 官方源下载 .osz |

模式支持：`osu`/`std`、`taiko`、`fruits`/`ctb`、`mania`。

---

## 星露谷物语 Wiki

**配置文件：** `BotData/plugin_configs/stardew_wiki.json`

调用 Stardew Valley Wiki 的 MediaWiki API（默认中文站），不需要账号或密钥。以合并转发发送结果：链接 → 详细描述 → 主图。

**可用指令：**

| 消息 | 效果 |
|------|------|
| `星露谷wiki <关键词>` | 搜索中文 Wiki |
| `svwiki <关键词>` | 同上 |
| `stardewwiki <关键词>` | 同上 |

---

## Minecraft Wiki

**配置文件：** `BotData/plugin_configs/mc_wiki.json`

调用 Minecraft Wiki 的 MediaWiki API（默认中文站），不需要账号或密钥。

**可用指令：**

| 消息 | 效果 |
|------|------|
| `mcwiki <关键词>` | 搜索中文 Wiki |
| `我的世界wiki <关键词>` | 同上 |
| `mc百科 <关键词>` | 同上 |

---

## 杀戮尖塔 2 Wiki

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

## QQ 资料卡点赞

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

## QQ 个人名片

**配置文件：** `BotData/plugin_configs/contact_card.json`

发送 OneBot V11 的 `contact` 消息段（`{"type": "contact", "data": {"type": "qq", "id": "<QQ号>"}}`），由 NapCat 渲染成 QQ 客户端里的原生联系人卡片，点击可直接打开对方资料页。不是链接、图片或 Ark 卡片模拟。

发送目标按事件自动判定：群聊发到当前群，私聊发给触发者。

**可用指令：**

| 消息 | 效果 |
|------|------|
| `名片 QQ号` | 发送该 QQ 的原生个人名片 |
| `个人名片 QQ号` / `qq名片 QQ号` / `card QQ号` | 同上 |

**提示文案：**

| 情况 | 回复 |
|------|------|
| 没有参数 | `用法：名片 QQ号` |
| 参数含非数字 | `QQ号必须是纯数字。` |
| 位数超出范围 | `QQ号位数不正确，请检查后重试。` |
| NapCat 拒绝或连接异常 | `名片发送失败，请检查 NapCat 是否正常连接。`（完整 traceback 只记日志） |

**关键配置：**

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用 |
| `min_digits` | QQ 号最少位数，默认 5 |
| `max_digits` | QQ 号最多位数，默认 11 |

---

## 空 @ 表情回应

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

## 戳一戳回戳

**配置文件：** `BotData/plugin_configs/poke_back.json`

监听 OneBot V11 的戳一戳通知。被戳到时立刻戳回对方，不发送文字提示。

**关键配置：**

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用 |
| `group_enabled` | 群聊戳回 |
| `private_enabled` | 私聊戳回 |

---

## 媒体转码

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

## 动图/视频互转

**配置文件：** `BotData/plugin_configs/media_convert.json`

通过「引用媒体消息 + 回复命令」把动图和视频互转，私聊、群聊均可用，群聊无需 @机器人。不支持同消息直接带图；非引用方式回复用法提示。

**指令：**

| 命令 | 别名 | 引用的媒体 | 行为 |
|------|------|-----------|------|
| `转mp4` | `转MP4`、`转视频` | 动图（GIF / 动态WebP / APNG 等 PIL 判定为动画的格式） | ffmpeg 转 MP4 发回 |
| `转gif` | `转GIF`、`转动图`、`转贴纸` | 视频（MP4 等，≤30MB） | 转 GIF（调色板优化）发回 |

别名与主命令行为完全一致，只是换个说法。「转贴纸」归到 GIF 方向，因为本项目的贴纸包一律以 GIF 存储（见[媒体转码](#媒体转码)）。

**边界行为：**

| 情况 | 回复 |
|------|------|
| 未引用 / 引用的消息没有媒体 | 用法提示 |
| `转mp4` 引用静态图片 | 提示静态图片转换不了 |
| `转mp4` 引用视频 | 提示改用 `转gif` |
| `转gif` 引用动图 | 提示已经是动图，不用转 |
| `转gif` 视频超过大小上限 | 提示视频超过 {max_mb}MB |
| 媒体文件拿不到（无 url 且本地文件不存在） | 提示重新发送媒体后再试 |
| 下载/转码异常 | 日志留详情，回复通用失败提示 |

**关键配置：**

| 字段 | 说明 |
|------|------|
| `enabled` | 插件总开关 |
| `max_video_mb` | 视频 → GIF 方向的输入大小上限（MB） |
| `max_image_mb` | 动图 → MP4 方向的下载大小上限（MB） |
| `download_timeout_seconds` | 媒体下载超时秒数 |
| `ffmpeg_timeout_seconds` | 动图 → MP4 的 ffmpeg 超时秒数（视频 → GIF 复用媒体转码服务，内部固定 180s） |
| `output_ttl_seconds` | 输出文件保留时间，超时自动清理 |
| `gif_fps` | 视频 → GIF 帧率 |
| `gif_width` | GIF 宽度（0 = 保持原宽） |
| `gif_max_colors` | GIF 调色板颜色数 |
| `temp_root` | 转换临时目录（需与 NapCat 容器共享，默认 `/tmp/hikari_bot/media_convert`） |

---

## JMComic PDF 下载

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

## 帮助与关于

**配置文件：** `plugins/bot_help/`（无独立配置）

| 消息 | 效果 |
|------|------|
| 私聊 `帮助` | 查看帮助分区列表（只显示分区名） |
| 群聊 `@机器人 帮助` | 同上 |
| 私聊 `帮助 分区名` | 查看该分区下的命令列表（如 `帮助 贴纸`、`帮助 百科`） |
| 私聊 `帮助 命令名` | 查看单个命令的详细用法 |
| 私聊 `帮助 全部` | 查看所有分区的完整帮助 |
| 私聊 `关于` | 查看机器人描述、版本、Git 提交、运行时长、贴纸库统计 |
| 群聊 `@机器人 关于` | 同上 |

命令按分区组织，分区由命令注册时的 `category` 决定（`core/command_router.py` 的 `@command(..., category="…")`）；媒体分区在分区列表中会额外标注支持的平台。

---

## 错误通知

自动错误处理。用户收到通用失败提示，管理员（超级管理员）收到脱敏后的异常 traceback 通知。

---

## 好友管理

**配置文件：** `BotData/plugin_configs/friend_manager.json`

监听好友请求通知，自动通过好友申请并向新好友发送欢迎消息。支持白名单/黑名单控制；好友添加成功后会通知超级管理员。

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用自动通过好友请求 |
| `auto_approve` | 是否自动通过好友请求 |
| `comment_keyword` | 验证消息关键词：留空不检查，填关键词则要求验证消息包含该词 |
| `allowed_users` | 白名单用户列表（非空时只接受白名单用户） |
| `blocked_users` | 黑名单用户列表（自动拒绝） |
| `welcome_enabled` | 通过后是否向新好友发送欢迎消息 |
| `notify_superuser` | 添加好友后是否通知超级管理员（默认开启） |

**新好友通知：** `notify_superuser` 开启时，优先通过 push_framework 的 `friend_add` 消息源发送（需在 `push_framework.json` 配置一个 `trigger: "event"` 的任务，见上文示例）；若未配置任何有效任务，则直接私聊超级管理员兜底。`notify_superuser: false` 可完全关闭通知。
