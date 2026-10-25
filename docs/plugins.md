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

> **Steam：** 只解析商店游戏页 `store.steampowered.com/app/<appid>`（`/bundle`、`/sub`、`/publisher` 这类页面不解析）。标题、开发商、发行日期、价格和类型来自官方 `appdetails` 接口；一个游戏页会同时解析出封面/胶囊图、全部截图、预告片缩略图和预告片视频，但 `parsers.steam` 默认是 `仅视频`，因此只发送预告片视频、不发送截图（页面没有预告片时回一句「解析成功，但没有可发送的视频。」）。游戏信息（平台/标题/作者/发行时间/媒体数量/原始链接）会作为合并转发的第一条节点一起发出，不额外单发文本消息。想连截图一起发就改成 `全部发送` 或 `仅富媒体`；`max_send` 按「先保留视频、再用剩余额度补图片」的顺序裁剪，媒体多时下载与发送都会明显变慢。下载代理开关在 `proxy.steam`（`parse` 详情接口默认 `false`，`image` 图片 / `video` 视频默认 `true`，三者都只在配置了 `proxy.address` 时才真正生效）；`steam.use_xiaoheihe` 打开后改走小黑盒完整游戏路径，补充评分、在线人数、销量排行等统计（默认关闭）。

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
| `send_strategy.prefer_forward_message` | 媒体多于 1 个时必须合并转发（含视频，游戏信息等文本作为首条节点一起发出），默认 `true`；只有单个媒体才直接发送 |
| `send_strategy.fallback_to_separate_media` / `include_text_in_forward` | 合并转发被 NapCat 拒绝或超时后是否降级逐条发送，默认 `false`（**合并失败就什么都不发**，避免同一批媒体收到两遍；改成 `true` 才逐条补发）／合并转发首条是否带上文本元数据，默认 `true` |
| `send_strategy.forward_timeout_seconds` | 合并转发超时秒数，默认 `300`；含视频的转发要先把文件上传到转发服务（7 个预告片约 2 分钟），超时即按「合并失败」处理 |
| `parsers.<平台>` | 各平台输出模式：`关闭` / `全部发送` / `仅文本` / `仅富媒体` / `仅视频`（`仅视频` 只发送该链接解析出的视频、不发图片，游戏信息随合并转发一起发出，默认仅 Steam） |
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

> **未接入的上游能力：** vendored 的 ZIP 归档（`message.archive`）、LLM 翻译（`translation`）、媒体中转（`media_relay`）和消息聚合（`message.packing`）依赖上游 AstrBot 的消息链。本仓库用自己的发送链（`send_strategy` 控制合并转发与失败策略，文本由本地组装），这些配置键保留在示例配置中但不生效。

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

## QQ 音乐解析

**配置文件：** `BotData/plugin_configs/qqmusic_parser.json`

使用 `yt-dlp` 的 `qqmusic` 提取器下载 QQ 音乐歌曲。自动检测 `y.qq.com` / `i.y.qq.com` 链接与 QQ 音乐分享卡片（`com.tencent.music.lua`）。

**支持链接：**

- `https://i.y.qq.com/v8/playsong.html?...&songmid=003dKInI1dmvj6...`（分享卡片里的 jumpUrl）
- `https://i.y.qq.com/v8/playsong.html?songid=587897337#webchat_redirect`（同页面，但用数字 ID）
- `https://y.qq.com/n/ryqq/songDetail/<songmid>`
- `https://y.qq.com/n/yqq/song/<songmid>.html`（旧版）

> **两套 ID 都会遇到。** `songmid` 是 14 位字母数字（如 `003dKInI1dmvj6`），`songid` 是纯数字（如 `587897337`）。
> yt-dlp 只认 `songmid`；数字 `songid` 会先经 QQ 接口换算，插件已自动处理，用户侧无感知。
> 另外 `i.y.qq.com/v8/playsong.html` 会 302 到 yt-dlp 不支持的 `ryqq_v2` 路径，插件统一归一化为 `y.qq.com/n/ryqq/songDetail/<songmid>` 后再下载。

**音质：**

| 档位 | 格式 ID | 匿名可用 | 登录后 |
|------|---------|----------|--------|
| 无损 FLAC | `flac` | ✗ | ✓（需会员，且该曲有无损版） |
| 320k MP3 | `320mp3` | ✗ | ✓（需会员） |
| 128k MP3 | `128mp3` | ✓ | ✓ |
| 96k AAC | `96aac` | ✓ | ✓ |
| 48k AAC | `48aac` | ✓ | ✓ |

**匿名请求的上限就是 128k MP3（QQ 的「标准音质」档）**，实测频率上限约 16 kHz；即使是免费的 128k 档，`flac`/`320mp3` 的 vkey 也不会下发。想要 320k / FLAC 必须配置登录 cookie。插件不做任何转码，下载到的就是服务端原档。

**触发方式：**

- 私聊：直接发送链接或卡片即自动解析（`auto_parse`）
- 群聊：默认需 `引用` 卡片/链接消息并 `@机器人`；加入 `auto_parse_groups` 白名单的群发链接即自动解析
- 非白名单群未 @bot 的 QQ 音乐卡片：回复一句「引用 + @bot」引导提示（`card_hint` 控制，同群默认冷却 300 秒）

**发送方式：** 默认通过 NapCat 上传文件到聊天（`歌手 - 歌名.flac` / `.mp3`），可切换为语音消息。

### 配置登录 cookie

cookie 文件是**账号凭据**，不进 git（`BotData/cookies/*` 已被 `.gitignore` 忽略），默认路径为 `BotData/cookies/qqmusic.txt`。

1. 在浏览器登录 [y.qq.com](https://y.qq.com)，用导出 cookie 的扩展（如 Get cookies.txt LOCALLY）导出 **Netscape 格式** 文本。
2. 存成 `BotData/cookies/qqmusic.txt`。文件应为 `# Netscape HTTP Cookie File` 开头的 TAB 分隔格式，含 `qqmusic_key`、`uin`、`fqm_pvqid` 等字段——这三者是 yt-dlp 判定「已登录」与换取 320k/FLAC vkey 的关键。
3. 重启容器（cookie 按 mtime 缓存，改文件后重启最稳）：

```bash
docker compose restart hikaribot
```

本地 Docker 直接放在仓库的 `BotData/cookies/qqmusic.txt` 即可（compose 把 `./BotData` 挂到 `/app/BotData`）。

**服务器部署注意：** `deploy.ps1` 只同步 git 可见文件，被忽略的 cookie **不会**被上传，需要在服务器上单独放置：

```powershell
# 从本机推送（服务器数据目录是 /opt/hikaribot-docker/BotData）
scp .\BotData\cookies\qqmusic.txt root@192.168.31.2:/opt/hikaribot-docker/BotData/cookies/qqmusic.txt
```

```bash
# 服务器上重启生效
cd /opt/hikaribot-docker && docker compose restart hikaribot
```

cookie 会过期（`qqmusic_key` 有效期约数月），失效后 320k/FLAC 会重新变成拿不到。

**关键配置：**

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用 |
| `auto_parse` | 是否自动解析（私聊；群聊见 `auto_parse_groups`） |
| `auto_parse_groups` | 群白名单（`enable` + `groups`），名单内发链接即自动解析 |
| `card_hint.enabled` / `card_hint.cooldown_seconds` | 非白名单群引导提示开关与同群冷却秒数 |
| `format_priority` | 音质优先级（yt-dlp 格式 ID），默认 `flac > 320mp3 > 128mp3 > 96aac > 48aac`，按第一个可用的下发 |
| `cookiefile` | Netscape 格式 cookie 路径，相对路径按仓库根目录解析 |
| `send_strategy` | `"upload"` = 上传文件（默认），`"record"` = 语音消息 |
| `max_file_mb` | 单文件大小上限，默认 200 MB |
| `max_links_per_message` | 单条消息最多处理几首，默认 3 |
| `send_link_info` | 是否发送歌名、歌手、专辑、时长、音质、大小 |
| `download_timeout` / `socket_timeout` / `retries` | 下载超时与重试 |
| `api_timeout` | QQ 接口超时（用于 songid 换算与付费标志查询），默认 30s |
| `permissions` | 黑白名单 |

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
| `ai_tool.max_chars` | AI 调用 `send_voice` 时单条语音的长度上限（默认 80） |
| `ai_tool.cooldown_seconds` | 同一会话两次 AI 语音的最小间隔（默认 10 秒） |
| `ai_tool.allow_auto_reply` | 群聊自动回复是否允许发语音（默认 **false**） |

### AI 语音（`send_voice`）

`tts_speaker` 通过 `core.ai_tool_registry` 向 AI Agent 注册了 `send_voice` 工具，模型可以在想发语音时自己调用，不需要用户手动发 `说话`：

- **默认不启用**：它是副作用工具（`readonly=False`）且默认关闭（`enabled_by_default=False`）。在后台「AI Agent」页的 Tools 管理里，先勾选「允许副作用 Tools」，再勾选 `send_voice`，保存后下一次对话生效。
- **参数**：`text`（要说的内容，必填）和 `voice`（音色库名称，可选；省略时用当前会话音色/全局默认音色）。
- **发送方式**：生成后以 QQ 语音消息（`record`）直接发到当前会话；随后模型仍会给一条简短的文字回复，提示词要求它不要重复语音内容。
- **边界**：沿用 `说话` 命令的 Fish Audio 配置、音色、缓存与 `enabled` 开关；每次调用按会话冷却；群聊自动回复默认禁止发语音（`ai_tool.allow_auto_reply=true` 可放开）；TTS 失败只把错误返回给模型，不影响文字回复。
- **超时**：语音生成受 `tools.tool_timeout_seconds`（默认 30 秒）约束；网络慢或文本较长时可在后台调大该值。

---

## AI Agent 聊天

**配置文件：** `BotData/plugin_configs/aiagent.json`

最低优先级兜底插件。默认走 DeepSeek Responses API（无状态，兼容 OpenAI Responses 格式），也可切换为 OpenAI-compatible 的 `chat/completions` 接口；网页搜索由本地 `web_search` 函数工具执行（DeepSeek 官方 Search API 或 SearXNG）。

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
  "chatlog": {},                 // 全局聊天记录
  "auto_reply": {},              // 全局自动回复白名单
  "quota": {},                   // 全局配额
  "permissions": {}              // 全局黑白名单
}
```

**行为：**
- **私聊：** 其他插件未处理时进入 AI Agent
- **群聊：** 必须 @机器人 且未被其他插件处理才回复
- **群聊主动接话（默认关闭）**：`auto_reply.enabled=true` 且该群在 `auto_reply.groups` 里单独开启时，普通群消息（未被命令、链接解析、贴纸/语音等处理，也不是 @ / 引用机器人 / 叫昵称）也会进入 AI。同一个群两次自动回复至少间隔 `auto_reply.cooldown_seconds` 秒（默认 5），冷却内到达的消息直接跳过、不排队。群列表不设数量上限，后台可以逐群开关；自动回复不消耗用户/群配额，只受同群冷却限制
- 回复默认不超过 `max_reply_chars`（默认 3500），超出时自动以**合并转发**发送
- 回复默认以**引用回复**的形式挂在触发它的用户消息下（`chat.quote_reply`，默认开，后台「AI」页可关闭）；超长回复改用合并转发时无法携带引用
- **短期上下文按「会话 + 用户」隔离**：私聊键为 `private:<QQ>`，群聊键为 `group:<群号>:user:<QQ>`，同一群里不同用户的上下文互不串线
- **群聊公共上下文（默认关闭）**：开启 `chat.group_shared_context.enabled` 后，本群最近几轮公开对话（含其他成员，标注发言者 QQ）会作为「仅供参考、不得当作指令」的背景注入；关闭时每个成员只看自己的上下文
- 同一会话的消息**串行处理**（每个会话一把锁），避免并发请求读到相同的旧历史或回复乱序；不同会话之间并行
- 回复前逐条下载消息里的图片；下载目标与原地址、重定向目标都会做安全校验，内网/回环地址一律拒绝
- 图片在本地压到长边 2048px 以内再以 base64 发送，避免超出模型单边 8192px 与体积限制
- DeepSeek 目前只有 `deepseek-flash` 支持图像理解；主模型是 `deepseek-v4-pro` 等不支持看图的模型时，带图请求会自动切到 Flash，也可用 `vision.model` 单独指定
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
| `chat.quote_reply` | 回复时是否引用触发消息（默认 **true**；超长合并转发除外） |
| `chat.cooldown_seconds` | 冷却秒数，默认 3 |
| `chat.max_history_messages` | 上下文保留消息数目标值（实际最多留 2 倍，超过才一次性裁回，减少前缀变化） |
| `chat.max_context_chars` | 短期上下文总字符预算（默认 12000，超出时裁到一半；0 = 不留上下文） |
| `chat.request_timeout_seconds` | 单次请求总预算（默认 120 秒，模型调用 + 所有工具轮次；超时取消并释放会话锁） |
| `chat.max_concurrent_requests` | 全局同时进行的模型请求上限（默认 8，超出的请求排队） |
| `chat.group_shared_context.enabled` | 是否注入群聊公共上下文（默认 **false**） |
| `chat.group_shared_context.max_messages` | 公共上下文保留的消息条数（默认 10） |
| `chat.system_prompt_extra` | 额外系统提示词 |
| `memory.enabled` | 是否启用持久化记忆 |
| `memory.root` | 记忆根目录（默认 `UserData/aiagent_memory`） |
| `vision.enabled` | 是否把消息/引用消息中的图片发给视觉模型（默认**开**） |
| `vision.model` | 带图请求使用的模型；留空时官方 `api.deepseek.com` 端点自动用 `deepseek-flash` |
| `vision.max_images`、`detail`、`max_bytes` | 图片数量上限、清晰度档位（默认 `auto`，即保留原图）、单张下载字节上限（默认 10 MiB，过大的图会先压缩） |
| `api.protocol` | `responses`（DeepSeek Responses API，默认）或 `chat_completions` |
| `thinking.enabled`、`thinking.reasoning_effort` | 思考模式开关与强度（`low`/`medium`/`high`/`max`） |
| `tools.help.enabled` | 是否启用 bot_help 帮助文档工具（默认开） |
| `tools.search.enabled` | 是否启用网页搜索（默认开） |
| `tools.search.mode` | `builtin` 用 DeepSeek 官方 Search API（默认；按成功请求计费，flash 也能搜，非 DeepSeek 端点自动回退 SearXNG）或 `searxng` 只用自建实例 |
| `tools.search.mode` | 搜索引擎模式：`builtin`（DeepSeek 官方 Search API，默认）、`searxng`（自建 SearXNG）、`hybrid`（双引擎并发查询、URL 去重与结果合并） |
| `tools.search.max_results`、`need_content` | 每次搜索返回条数（默认 5，上限 10）、是否带回网页正文（默认 false，更耗上下文） |
| `tools.images.enabled`、`allow_send` | 是否启用网络搜图（`web_image_search`）、是否允许 AI 主动发图（`send_images`，默认 false） |
| `tools.images.max_results`、`max_images_per_send` | 搜图返回条数（默认 5）、单次发图上限（默认 3） |
| `tools.workspace.enabled`、`allow_writes`、`allow_delete`、`allow_send` | 会话隔离文件工作区开关与写/删/发权限（写/删/发默认 false） |
| `tools.workspace.ingest_incoming`、`incoming_max_mb` | 是否自动接收用户发送的聊天文件（默认 true）、单文件下载硬上限（默认 100MB） |
| `chat.single_message_chars` | 单条普通消息字数阈值（默认 1200），超过转为一个合并聊天记录发出 |
| `chat.forward_node_chars`、`forward_max_nodes` | 合并转发单段长度（默认 1500 字，优先自然段落/标点切分）、最大段数（默认 30） |
| `chat.history_reply_chars` | 写入短期对话历史时的回复保留长度（默认 1200 字，避免超长回复挤占上下文预算） |
| `chat.sticker.enabled`、`packs`、`probability` | 回复跟随表情包开关（默认 false）、限定贴纸包列表（留空=全部）、跟随概率（0.0~1.0） |
| `tools.files.enabled` | 是否启用人格资源只读工具（`read_persona_resource`） |
| `tools.plugin_tools.enabled` | 是否启用插件 AI 工具 |
| `tools.plugin_tools.allow_side_effects` | 是否放行副作用插件工具（默认 false；`send_voice` 等需要它） |
| `tools.plugin_tools.enabled_names` | 启用名单；留空时按各工具的 `enabled_by_default` 决定，填了则只启用名单内的工具 |
| `tools.plugin_tools.disabled_names` | 禁用名单，优先级高于启用 |
| `tools.max_tool_rounds` | 单次回复最多工具调用轮数，默认 4，运行时上限 10 |
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
| `auto_reply.enabled` | 是否启用群聊主动接话（全局段，默认 **false**） |
| `auto_reply.groups` | 群列表，每项 `{"id": "...", "enabled": true/false}`；只有开启的群才接话，不设数量上限（旧的纯群号数组视为开启） |
| `auto_reply.cooldown_seconds` | 同一个群两次自动回复的最小间隔（默认 5 秒；0 = 不冷却，仍防并发） |
| `auto_reply.context_messages` | 主动接话时附带的本群近期消息条数（默认 10，0 = 不带上下文） |
| `auto_reply.context_max_chars` | 主动接话上下文字符预算（默认 1200，超出从最旧的消息丢弃） |
| `quota.enabled` | 是否启用对话次数配额（默认关） |
| `quota.default_user` / `default_group` | 默认额度：每日/每小时对话次数（0 = 不限额） |
| `quota.user_overrides` / `group_overrides` | 指定用户/群的独立额度 |
| `quota.exempt_user_ids` / `exempt_group_ids` | 豁免名单（不限额） |
| `quota.count_background` | 记忆总结等后台调用是否计入配额 |
| `permissions` | 黑白名单（用户/群维度独立开关） |

表中除 `enabled`、`quota.*`、`permissions`、`chatlog.*`、`auto_reply.*` 之外的字段都位于 `profiles.<id>` 内，每套配置各有一份；`chatlog.*` 与 `auto_reply.*` 是全局段，所有配置文件共用。

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
| `web_search` | 内置 | 通过 DeepSeek 官方 Search API（默认）、SearXNG 或 `hybrid` 混合模式搜索网页；支持 `site:`/`lang:`/`after:` 等语法 |
| `web_image_search` | 内置 | 通过 SearXNG 搜索网络图片，结果缓存在当前会话（只读） |
| `send_images` | 内置 | 将搜到的网络图片或工作区图片发送至当前会话（支持 WebP/GIF 转码，需 `tools.images.allow_send`） |
| `workspace_list` | 内置 | 查看当前私聊/群聊工作区内的文件与目录结构（只读，严格隔离） |
| `workspace_read` | 内置 | 读取工作区中的纯文本文件内容（只读） |
| `workspace_write` | 内置 | 写入或覆盖工作区文件（需 `tools.workspace.allow_writes`） |
| `workspace_delete` | 内置 | 删除工作区中的文件或目录（需 `tools.workspace.allow_delete`） |
| `workspace_move` | 内置 | 移动或重命名工作区文件/目录（需 `tools.workspace.allow_delete`） |
| `workspace_mkdir` | 内置 | 在工作区内创建新目录（需 `tools.workspace.allow_writes`） |
| `workspace_zip` | 内置 | 将工作区文件打包为 zip 压缩包（需 `tools.workspace.allow_writes`） |
| `workspace_unzip` | 内置 | 解压工作区内的 zip 压缩包，含 Zip-Slip 防护（需 `tools.workspace.allow_writes`） |
| `send_file` | 内置 | 将工作区文件作为群文件或私聊离线文件上传发送（需 `tools.workspace.allow_send`） |
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
| `send_voice` | tts_speaker | 用 Fish Audio 生成并发送语音消息（副作用工具，默认关闭，需在 Tools 管理里放行并勾选） |

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

#### 双引擎混合搜索与网络搜图

- **双引擎混合搜索（`hybrid` 模式）**：并发向 DeepSeek Search API 与自建 SearXNG 发起检索。结果按规范化 URL 自动去重（统一 scheme、去除 `www.` 与末尾斜杠），优先保留 DeepSeek 的相关性摘要与权重，并穿插 SearXNG 的结果。单边失败或未配 Key 时自动降级，不阻断回复。
- **搜图与发图（`web_image_search` & `send_images`）**：
  - AI 可主动调用 `web_image_search` 搜索图片，候选图片链接缓存在当前会话（TTL 30 分钟，最多 50 张）；
  - 需发送图片时，调用 `send_images`（受 `tools.images.allow_send` 开关控制）；只允许发送当前会话搜图缓存内的 URL 或工作区图片；
  - 下载图片经过 SSRF 校验，单张图片体积受限（默认 15MB），自动使用 `media_transcoder` 转码为兼容格式。

#### 会话隔离文件工作区与聊天文件接收

- **工作区会话隔离**：
  - 私聊工作区：`UserData/aiagent_files/private/<QQ号>/`
  - 群聊工作区：`UserData/aiagent_files/group/<群号>/`
  - 根目录完全由服务端事件计算生成，模型参数无法指定目标 QQ 或群号，彻底阻断越权逃逸；
  - 路径严格校验：禁止 `..` 相对路径逃逸、绝对路径逃逸与符号链接逃逸；
  - 单会话存储额度：默认 300MB / 500 个文件，单次写入上限 20MB；压缩解压内置 Zip-Slip 路径逃逸检测；
  - 旧版全局 `read_user_file`/`write_user_file` 已移除，模型无法再窥探其他用户的记忆与配置。
- **聊天文件接收**：
  - 用户向机器人发送文件段（`file`）时，AI Agent 自动接收并下载到当前会话的 `incoming/` 子目录；
  - **100MB 硬上限**：单文件大小超过 100MB 直接拒绝下载；采用四级下载回退（NapCat 本地临时目录 → `get_file` API → `get_*_file_url` → 流式下载校验）；
  - 自动去重与保存：相同文件 10 分钟内不重复下载；`incoming/` 目录默认 7 天自动懒清理；
  - 纯文本文件 AI 可直接调用 `workspace_read` 阅读；Office/PDF/二进制等文件不解析文本，但可通过 `send_file` 原样转发给其他成员。

#### 智能长回复与合并转发

- **去除 50 字限制**：日常闲聊简明扼要；当用户索取代码、方案、教程或长文分析时，AI 可完整输出。
- **智能分段**：
  - 回复长度 ≤ `single_message_chars`（默认 1200 字）时，以普通消息发送并支持引用原消息；
  - 回复长度超过阈值时，自动转为单个合并聊天记录（群聊 / 私聊均支持）；
  - 切分算法优先在自然段落（`\n\n`）、换行（`\n`）、句末标点（`。！？；…`）处切分，受 URL 原子保护（不将 URL 从中间腰斩）；
  - 合并转发失败时，优雅降级为逐条普通消息发送（段间短暂延迟），不丢弃内容；
  - 短期对话记忆中，长回复截断保留至 `history_reply_chars`（默认 1200 字），防止撑爆上下文预算。

#### 回复表情包跟随

- 开启 `chat.sticker.enabled` 后，每次 AI 文字回复成功后，自动从表情包库随机抽取 1 张贴纸跟随发送；
- 支持限定指定贴纸包名称列表，或留空使用全部贴纸包；
- 最近 5 张贴纸会话去重，避免连续复读同一张表情；
- 合并转发长回复时整条回复仅跟发 1 张贴纸；贴纸发送失败完全静默，绝不影响文字交付。

#### 群聊主动接话（自动回复）

默认关闭。开启后，只有群列表里**单独开启**的群会进入「没 @ 也回复」模式：

```jsonc
"auto_reply": {
  "enabled": true,
  "groups": [
    {"id": "123456789", "enabled": true},
    {"id": "987654321", "enabled": false}   // 留在列表里但关掉，不会接话
  ],
  "cooldown_seconds": 5,                  // 同一个群两次回复的最小间隔
  "context_messages": 10,                 // 接话前读多少条本群近期消息
  "context_max_chars": 1200               // 接话上下文字符预算
}
```

- 触发条件是**普通群消息**：命令、链接解析、贴纸/语音触发、空 @ 表情等已被其他插件处理的消息不会触发；@机器人、引用机器人、以机器人昵称开头的消息走原来的直接回复，不受群列表限制。
- 普通消息按到达顺序逐条判断：冷却已过就回复；冷却内到达的消息**直接跳过，不会排队补发**。同一个群同时只允许一笔自动回复在生成，避免一次回复耗时较长时并发触发第二条。
- 群列表不设数量上限；只有 `enabled: true` 的群才会接话，总开关 `enabled` 关掉时整份列表都不生效。没有黑名单。自动回复仍受顶层 `permissions` 黑白名单约束（不在 AI 允许范围内的群不会回复）。
- 后台「AI Agent → 群聊主动接话」用列表管理：可以手动输群号添加，也可以点「从机器人所在群选择」按群名/群号搜索后批量添加；每行一个开关，关掉即暂停该群，删除即移除。保存时自动去重、忽略非数字项。旧配置里写成纯群号数组也能直接读（视为已开启）。
- 启用总开关时至少要有一个群处于开启状态，否则保存会报错。
- **主动接话会带本群近期上下文**：读取最近 `context_messages` 条群消息（字符预算 `context_max_chars`）作为背景，并明确告诉模型这些消息不一定是在对它说。模型判断不需要插话时只输出 `<NO_REPLY>`，机器人不发送任何内容。
- 自动回复**不使用引用回复**，避免看起来像在针对某个人回答；只有 @ / 引用 / 昵称 / 私聊的直接回复才引用原消息。
- 自动回复复用原有 AI Agent 的全套行为：人格 skill、短期上下文、持久化记忆、图片输入、工具、`strip_markdown`、长回复合并转发。
- 自动回复**不消耗**用户/群对话次数配额（`quota.*`）；成本由开启接话的群数量与冷却控制。直接回复（@ / 引用 / 昵称 / 私聊）仍按原规则扣配额。
- 自动回复失败时静默处理（只写日志），不会在群里发「请求失败」之类的提示；直接回复仍会按失败原因提示。
- 隐私提醒：开启自动回复后，开启接话的群里未被 @ 的普通消息也会进入模型请求；请只在接受这一点的群里开启。

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

- 群聊短期历史不会把其他群成员与机器人的对话喂给当前用户；群共享记忆是显式的“群级事实”，读取时只注入群记忆的稳定摘要区（个人记忆是群记忆的子集，不再重复注入）。
- 需要多人接话、跨用户话题连续性时，可显式开启 `chat.group_shared_context`（默认关闭）：开启后本群最近的公开对话会作为**不可信背景**注入，并标注发言者 QQ；后台「AI Agent」页可开关与调整条数。
- 短期历史条数由 `chat.max_history_messages` 控制（默认 10 条，一问一答各占 1 条）：实际最多保留 2 倍，超过才整段裁回目标值；并受 `chat.max_context_chars` 字符预算约束（默认 12000，超出时裁到一半）。
- 请求前缀按「人格 → 记忆摘要 → 短期历史 → 群公共上下文 → 当前时间 + 用户消息」排列：时间戳放在最后一条 user 消息里并原样写入历史，记忆只注入会话标记之前的摘要区，工具集合固定下发并按名字排序。这样连续多轮请求的前缀尽量逐字节一致，DeepSeek 的前缀缓存才能命中；每次请求会打一行 `[AIAgent] usage caller=... hit=... miss=... hit_rate=...` 日志，便于按 caller 归因。
- 持久化记忆按**参考数据**注入：提示里明确它不是指令，记忆文本内的 `system:`/`assistant:`/`user:` 等角色标记与 `<...>` 会被转义，降低「在聊天里喂指令写进记忆」的提示注入风险。
- `重置` / `清空上下文` 只清空**当前用户**的短期上下文与记忆文件。
- 记忆总结（自动或 `总结记忆`）按当前配置的 `api.protocol` 走对应接口，Responses 与 Chat Completions 两种配置都可用。
- 后台任务（记忆总结）默认按 `quota.count_background` 计入配额。
- 配额采用**先预留、失败退回**：请求发出前原子检查并扣费（同一 scope 并发请求不会一起挤过限额），只有成功回复才真正消耗；API 报错、网络异常或消息为空都会退回。
- 失败原因会区分提示：超时 / 网络不通 / 上游限流（429）/ 上游故障（5xx）/ Key 无效（401、403）/ 其他失败，对应 `bot_messages.json` 里的 `aiagent.timeout`、`aiagent.network_error`、`aiagent.rate_limited`、`aiagent.upstream_error`、`aiagent.auth_failed`、`aiagent.failed`。
- 单个工具调用受 `tools.tool_timeout_seconds` 限制（默认 30 秒），超时只让该工具返回错误，不会让整轮回复失败，也不会卡住该会话的锁。
- AI 文字回复、合并转发、跟随贴纸和 `send_voice` 语音发送都带 30 秒发送超时。NapCat/QQ 偶发不回应 `send_msg` 时只放弃当次发送并记录 warning，不会永久占住会话锁、让该会话后续消息一直排队。

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

## 合并转发表情导出

**配置文件：** `BotData/plugin_configs/forward_sticker_export.json`

从一条合并转发（合并聊天记录）里提取表情包并导入贴纸库，支持递归展开**嵌套的合并转发**（记录里又套着记录）。

**用法：** 引用/回复那条合并聊天记录，发送 `导出表情 [贴纸包名]`（别名：`导出表情包` / `提取表情` / `提取表情包`）。不填包名时用配置里的 `default_pack`。

- `导出表情` — 导入到默认贴纸包
- `导出表情 群友精选` — 导入到「群友精选」贴纸包（不存在时自动创建，同名合并）

**工作方式：** 从被引用消息里取出合并转发的 `res_id`，调用 OneBot / NapCat 的 `get_forward_msg` 拉取内容；节点里遇到 `forward` 段（嵌套合并转发）时，优先用内联 `content` 展开，否则按其 `id` 再次调用 `get_forward_msg` 递归拉取（按 `max_depth` 限深、按已见 id 去环）。抠出的图片下载后统一走 `media_transcoder.ensure_sticker_gif` 转 GIF，调用 `sticker_library.save_gifs_to_pack()`（`source="forward_export"`，按 GIF 哈希去重）入库。

**导出范围：** 默认 `animated_only=false`，合并记录里的**所有图片**（动画表情、普通图片表情、截图等）都会被下载导出。设为 `true` 则只导出 NapCat 标记 `summary="[动画表情]"` 的表情（与贴纸定向收集同一套判定）。

**权限：** 默认 `require_admin=true`，仅超级管理员或群 owner/admin 可用（该命令写入共享贴纸库）。设为 `false` 则所有人可用。

**关键配置：**

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用 |
| `require_admin` | 是否仅管理员可用 |
| `max_depth` | 递归展开嵌套合并转发的最大层数 |
| `max_images` | 单次最多导出的图片数量上限 |
| `animated_only` | 只导出动画表情（`summary="[动画表情]"`）；默认 `false` = 导出所有图片 |
| `default_pack` | 未指定包名时的默认贴纸包 |
| `temp_root` | 下载/转码临时目录 |
| `download_timeout_seconds` / `max_download_mb` | 单张下载超时与大小上限 |
| `concurrency` | 下载 + 转码并发数 |
| `forward_api_timeout_seconds` | `get_forward_msg` 单次调用超时 |

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

## 吉星派对观战手牌

**配置文件：** `BotData/plugin_configs/astral_party_spectator.json`

调用外部观战服务 [AstralPartySpectator](https://github.com/higashitaniyume/AstralPartySpectator) 的公开无状态接口 `GET /query?id=<观战码>`，把官方服务器实际下发的四名玩家手牌以文本消息发回。服务地址默认 `http://192.168.31.2:18743`，可在配置里改成任意可达地址。

服务端行为：每次查询都会临时登录官方服务器、拉一次房间快照、再退出，所以单次耗时通常在数秒到数十秒，配置的 `timeout` 默认给到 90 秒。同一时间服务端只允许一个查询，并发命令会排队。

**可用指令：**

| 消息 | 效果 |
|------|------|
| `查询 <观战码>` | 显示四名玩家的手牌与公开属性 |
| `观战 <观战码>` / `手牌 <观战码>` | 同上 |

群聊里必须 @机器人 才会响应（`@机器人 查询 C27ED36`）；私聊直接发送即可。

输出包含每名玩家的名次、昵称、英雄、HP/金币/攻击/防御，以及手牌列表（`[战斗费用] 卡牌名`，只显示卡牌名称、不含卡牌描述）。服务器只给数量、没给牌面时，会明确显示「服务器只提供数量，牌面被遮蔽」，不会用数量推断牌面。

**配置项：**

| 键 | 说明 |
|------|------|
| `enabled` | 是否启用该命令 |
| `endpoint` | 观战服务地址，默认 `http://192.168.31.2:18743` |
| `timeout` | 单次查询超时（秒），默认 90 |
| `verify_ssl` | HTTPS 服务时是否校验证书 |
| `proxy` | 可选的 HTTP 代理 |
| `max_name_chars` | 昵称、卡牌名称单条截断长度 |
| `max_chars` | 整条回复字符上限，默认 3000；超出时从末尾玩家开始截断，并提示「…还有 N 名玩家…」 |

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

## QQ 空间

**配置文件：** `BotData/plugin_configs/qzone.json`（默认值见 `qzone.example.json`）

用 NapCat 当前登录的 QQ 直接操作 QQ 空间：发说说、删说说、看好友动态、看指定好友说说、点赞与评论。

**登录态：** 不保存 Cookie。每次操作前调用 OneBot `get_cookies`（`domain=user.qzone.qq.com`）向 NapCat 取一次 Cookie，
由 vendored 的 [qzone-sdk](https://github.com/Eganchiyu/qzone-sdk)（`third_party/qzone_sdk/`，MIT）计算 `g_tk` 并发起 CGI 请求。
NapCat 登录的号就是空间操作使用的号，换号只需换 NapCat 登录，无需改配置。

**可用指令：**

| 消息 | 效果 | 权限 |
|------|------|------|
| `发说说 [公开\|好友\|私密] <内容>` | 发布纯文本说说，默认可见范围取配置 `default_visible` | 超级用户 |
| `删说说 <序号\|tid>` | 删除说说；序号来自最近一次 `空间动态` | 超级用户 |
| `空间动态 [数量]` | 拉取好友动态（feeds3），默认 8 条，自动过滤广告与空内容 | 按黑白名单 |
| `好友说说 <QQ号> [数量]` | 拉取指定好友（或自己）的说说 | 按黑白名单 |
| `我的说说 [数量]` | 看自己最近发的说说、tid 与评论数 | 按黑白名单 |
| `评论列表 [序号]` | 看自己某条说说下的评论（序号来自「我的说说」） | 按黑白名单 |
| `回复评论 <评论序号> <内容>` | 回复自己说说下的某条评论 | 超级用户 |
| `说说赞 <序号>` | 给 `空间动态` 列表里的某条点赞 | 超级用户 |
| `说说评论 <序号> <内容>` | 评论 `空间动态` 列表里的某条 | 超级用户 |

`空间动态` 的结果按「会话 + 用户」缓存 10 分钟，`说说赞` / `说说评论` / `删说说` 才能按序号取到 tid；
缓存过期后需要重新发送 `空间动态`。

`我的说说` 同样会把结果缓存 10 分钟：先 `我的说说` 看帖子和序号，再 `评论列表 序号` 看这条说说下的评论，
最后 `回复评论 评论序号 内容` 回复。回复会带上 `@{uin:...}` 提及（和空间网页行为一致），
评论下的回复会展开成 `└` 开头的行一起编号，回复时会挂在对应的一级评论下。
评论列表走 `emotion_cgi_msgdetail_v6`，只能读到接口返回的评论（最近说说的前 20 条）。

**关键配置：**

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用，默认开 |
| `default_visible` | 发说说默认可见范围：`public` / `friends` / `private` |
| `max_length` | 单条说说字数上限，默认 1000 |
| `feed_count` | `空间动态` 默认条数，默认 8（最大 20） |
| `moods_count` | `好友说说` 默认条数，默认 5（最大 20） |
| `timeout` | 单次空间请求超时秒数，默认 20 |
| `permissions` | 黑白名单，结构同其它插件 |

**AI 工具：**

| 工具 | 说明 |
|------|------|
| `qzone_get_feed` | 只读，返回好友动态（昵称、正文、图片数、tid、链接），默认可用 |
| `qzone_publish` | 发说说，**有副作用且默认关闭**：需在 `aiagent.json` 的 `tools.plugin_tools` 打开 `allow_side_effects` 并把 `qzone_publish` 加进 `enabled_names`，且调用者必须是超级用户 |

**注意：**

- 写操作（发/删/赞/评论）一律限超级用户，避免群里任何人替机器人发空间。
- 空间接口有频率风控，`-10000` 一般是操作过快，隔几分钟再试；不建议把 `feed_count` 调到 20 以上反复刷新。
- 图片说说暂未支持（需要先把聊天图片落到本地再上传）；当前版本只发纯文本。

### 定时说说

`schedule` 段默认关闭（`enabled: false`），打开后按任务时间自动发人设短文。**人格提示词不在这里配置**，统一取后台「AI」页里当前配置文件的 persona skill（`BotData/agent_personas/…`）：

```json
"schedule": {
  "enabled": true,
  "check_interval_seconds": 60,
  "grace_seconds": 1800,
  "state_path": "UserData/qzone_state.json",
  "ai": {
    "enabled": true,
    "temperature": 1.0,
    "max_tokens": 400,
    "max_chars": 120,
    "extra_prompt": ""
  },
  "fallback_texts": ["哼，今天也在海里摸鱼喵～（摇尾巴）"],
  "jobs": [
    {
      "id": "morning",
      "enabled": true,
      "time": "08:30",
      "days": [],
      "timezone": "Asia/Shanghai",
      "visible": "public",
      "prompt": "刚睡醒，写一句早安或今天想做的小事"
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `enabled` | 定时总开关，默认 `false`（不开就不会自动发） |
| `check_interval_seconds` | 轮询间隔，默认 60 秒 |
| `grace_seconds` | 到点后允许补发的时间窗，默认 1800 秒；机器人当时没在线就跳过当天 |
| `state_path` | 每天去重状态文件，默认 `UserData/qzone_state.json` |
| `ai.enabled` | 用 AI 生成文案；关掉则只用 `fallback_texts` |
| `ai.max_chars` | 单条文案字数上限，默认 120 |
| `ai.extra_prompt` | 额外的写作要求（人格仍然取 AI 配置），比如「再短一点」 |
| `jobs[].days` | 空数组=每天；也可以写 `["mon","wed"]` 或 `["周一","周三"]` |
| `jobs[].visible` | `public` / `friends` / `private` |

### 后台「空间」页

后台侧边栏的「空间」页可以直接改上面所有配置，并随时蹭 AI 生成一条说说：

- **定时说说**：开关、AI 开关、字数上限、补发窗口、额外写作要求、备用文案，以及每条任务的 id / 时间 / 星期 / 可见范围 / 文案方向，都能在页面上改，保存即写回 `qzone.json`（不需要重启，60 秒内定时循环就会读到）。
- **生成并发布**：填一句可选的要求 → 「用 AI 生成」→ 草稿进文本框，可以随便改 → 选可见范围 → 「发布到空间」。生成和发布都走机器人主循环，用的是 NapCat 当前登录态，所以页面本身不保存任何 Cookie。
- 页面顶部会显示当前人格来源（如 `BotData/agent_personas/ds`、字数）和模型，改 AI 人格就换文案风格。

对应的 HTTP 接口见 [API.md](API.md#qq-空间配置)。

行为约定：

- 每条任务每天最多发一次，重启不会重复（状态落盘）；`空间定时` 可查看下次时间与上次 tid。
- `空间定时 run <任务id>` 立即执行一次（仅超管），用于试文案。
- AI 失败会自动用 `fallback_texts` 兜底；连续失败 3 次当天放弃，只写日志不发错误消息。
- 生成文案会带上最近 3 条已发内容做去重提示，避免每天说同一句。

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
