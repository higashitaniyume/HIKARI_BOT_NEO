# HIKARI BOT NEO

HIKARI BOT NEO 是一个基于 [NoneBot 2](https://nonebot.dev/) 的 QQ 机器人，通过 NapCat 的 OneBot V11 WebSocket 接入 QQ。它主要用于自动解析 QQ 消息里的媒体链接，并提供贴纸包、错误通知等辅助能力。

> [!IMPORTANT]
> 本项目不使用 `.env`。所有运行配置都从 `BotData/config.json` 和 `BotData/plugin_configs/*.json` 读取。

---

## 功能概览

| 功能 | 触发方式 | 说明 |
|------|----------|------|
| Pixiv 作品解析 | Pixiv 作品链接，或 `/pixiv <URL>` | 下载并发送作品图片，支持多图合并转发 |
| Instagram / Facebook 解析 | Instagram / Facebook 链接 | 通过自部署 cobalt API 解析并发送图片/视频 |
| Telegram 贴纸包解析 | `https://t.me/addstickers/<set>` | 拉取贴纸包，转换为 GIF，保存成本地贴纸包 |
| 本地贴纸包 | 关键词、`随机贴纸`、`拼图 <关键词>` | 从 `BotData/Gifs` 随机发送贴纸或生成拼图 |
| JMComic PDF | 私聊 `jm <id>`、`/jm <id>`、`/jmpdf <id>` | 下载并转换 PDF 后通过私聊发送，群聊不解析 |
| 错误通知 | 自动 | 用户收到通用失败提示，管理员收到脱敏后的异常 |

本仓库当前包含 Pixiv、Instagram/Facebook、Telegram 贴纸、本地贴纸和 JMComic 相关实现。X/Twitter、抖音、Bilibili、TikTok、小黑盒等其他平台解析由独立 AstrBot 插件并行部署，不在本仓库内实现。

---

## 运行环境

- Python `>=3.10`
- [uv](https://docs.astral.sh/uv/) 用于依赖安装
- NapCat，并开启 OneBot V11 WebSocket 服务
- 如果使用 Instagram / Facebook 解析，需要自部署 [cobalt](https://github.com/imputnet/cobalt)
- 如果使用 Telegram 贴纸解析，需要 Telegram Bot Token，并保证服务器能访问 Telegram API
- 如果需要转换 Telegram 动态贴纸/视频贴纸，需要系统可用的转换依赖，例如 `ffmpeg`、lottie 相关工具链

---

## 快速开始

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

1. 优先复用 `BotData/Gifs/<set_name>` 中已保存的 GIF。
2. 无本地缓存或带 `refresh` 参数时，调用 Telegram Bot API 获取贴纸包。
3. 将贴纸转换为 GIF。
4. 默认保存到 `BotData/Gifs/<set_name>`。
5. 自动写入 `BotData/plugin_configs/sticker_trigger.json`，让贴纸包名称成为触发词。
6. 发送转换后的 GIF。

可选参数直接跟在链接后：

| 参数 | 效果 |
|------|------|
| `zip` | 打包为 ZIP 文件发送 |
| `refresh` | 忽略本地缓存，重新获取并转换 |
| `nosave` | 只发送本次结果，不保存成本地贴纸包 |
| `name=关键词` / `keyword=关键词` / `kw=关键词` | 额外注册一个触发词 |

### 本地贴纸包

配置文件：`BotData/plugin_configs/sticker_trigger.json`

贴纸文件放在：

```text
BotData/Gifs/<贴纸包目录>/
```

配置示例：

```json
{
  "triggers": {
    "capoo_gif": ["capoo", "猫猫虫"]
  }
}
```

可用指令：

| 消息 | 效果 |
|------|------|
| `猫猫虫` | 随机发送一张匹配贴纸 |
| `猫猫虫 10` | 随机发送 10 张，不重复 |
| `随机贴纸` | 从所有贴纸包随机发送 |
| `拼图 猫猫虫` | 将贴纸包第一帧拼成预览图 |
| `贴纸包列表` | 查看已配置贴纸包和关键词 |
| `统计` | 查看当前会话统计 |

支持后缀：`.gif`、`.jpg`、`.jpeg`、`.png`、`.webp`、`.mp4`。

### JMComic

配置文件：`BotData/jmcomic/option.yml`

仅私聊可用，所有用户都可以触发，群聊中不会解析：

```text
jm 123456
/jm 123456
/jmpdf 123456
```

机器人会下载漫画、导出 PDF，并通过 NapCat 上传到当前私聊。下载和 PDF 临时目录默认位于 `/tmp/hikari_bot/jmcomic`。

---

## NapCat 文件目录

机器人会把图片、视频、贴纸、PDF 等临时文件放到 `/tmp/hikari_bot`。NapCat 必须能读取这个目录，否则会出现“解析成功但发送失败”。

如果 NapCat 运行在 Docker 容器中，请挂载同一个目录：

```yaml
services:
  napcat:
    volumes:
      - /tmp/hikari_bot:/tmp/hikari_bot
```

systemd 服务里已经设置 `PrivateTmp=no`，用于避免服务进程看到隔离后的 `/tmp`。

---

## 部署

### systemd

服务文件默认工作目录为 `/opt/HIKARI_BOT_NEO`，启动命令为：

```text
/opt/HIKARI_BOT_NEO/.venv/bin/python /opt/HIKARI_BOT_NEO/bot.py
```

部署命令：

```bash
cp /opt/HIKARI_BOT_NEO/hikari-bot-neo.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hikari-bot-neo
journalctl -u hikari-bot-neo -f
```

### PowerShell 一键部署

Windows 本地可运行：

```powershell
.\deploy.ps1
```

默认部署到：

- 服务器：`root@192.168.31.2`
- 路径：`/opt/HIKARI_BOT_NEO`
- 服务名：`hikari-bot-neo`

可以覆盖参数：

```powershell
.\deploy.ps1 -ServerIP "1.2.3.4" -ServerUser "root" -DeployPath "/opt/HIKARI_BOT_NEO"
```

只同步 `BotData/Gifs` 贴纸文件：

```powershell
.\deploy.ps1 -u
```

首次部署前需要配置 SSH Key：

```powershell
ssh-keygen -t ed25519
type $env:USERPROFILE\.ssh\id_ed25519.pub
```

将公钥加入服务器 `/root/.ssh/authorized_keys`。

---

## 项目结构

```text
HIKARI_BOT_NEO/
  bot.py                         # 程序入口
  core/
    config_loader.py             # 主配置和插件配置加载
    error_notifier.py            # 错误通知
    logger_setup.py              # 日志初始化
    message_pipeline.py          # 自动解析管道
    stats_tracker.py             # 会话统计
  plugins/
    pixiv_parser/                # Pixiv 解析
    cobalt_parser/               # Instagram / Facebook 解析
    tg_sticker_parser/           # Telegram 贴纸包解析
    sticker_trigger/             # 本地贴纸关键词触发
    jmcomic_api/                 # JMComic PDF 下载上传
  BotData/
    config.example.json          # 主配置模板
    config.json                  # 主配置，含敏感信息，不提交
    plugin_configs/              # 插件配置
    Gifs/                        # 本地贴纸包
    jmcomic/option.yml           # JMComic 配置
  UserData/
    stats/                       # 会话统计数据
  deploy.ps1                     # PowerShell 部署脚本
  hikari-bot-neo.service         # systemd 服务文件
  pyproject.toml                 # Python 依赖和 NoneBot 配置
```

---

## 常见问题

| 症状 | 常见原因 | 处理方式 |
|------|----------|----------|
| 启动后机器人不在线 | NapCat WebSocket 地址或 Token 错误 | 检查 `BotData/config.json` 的 `napcat.ws_url` 和 `napcat.token` |
| 发链接没有反应 | 插件关闭、链接不匹配、NapCat 未连接 | 检查插件配置里的 `auto_parse`，再看日志 |
| 图片或视频发送失败 | NapCat 读不到临时文件 | 挂载 `/tmp/hikari_bot:/tmp/hikari_bot`，并确认 systemd `PrivateTmp=no` |
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

