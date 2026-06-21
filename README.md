# HIKARI BOT NEO

QQ 媒体解析机器人，基于 [NoneBot](https://nonebot.dev/)，通过 NapCat WebSocket 接入 QQ。

收到消息中的媒体链接时自动解析下载，发送到当前会话。

**支持平台：** Pixiv、X/Twitter（本仓库） + 抖音、Bilibili、Instagram、Facebook、TikTok、小黑盒（AstrBot 插件）

---

## 功能

### Pixiv 作品解析

**支持：** `artworks` 和 `i` 两种链接格式。

```
https://www.pixiv.net/artworks/123456789
https://pixiv.net/i/123456789
```

**效果：** 自动回复作品信息（标题、作者、PID、图片数量），然后发送图片。
- 单图 → 直接发图
- 多图 → 合并转发，所有图片包在一条消息里
- 转发失败 → 自动降级为逐张发送

**不支持：** 纯数字 PID、`pid:123456789`、用户主页/tag/novel 等链接。

**命令：** `/pixiv <URL>` 手动触发。

### Instagram / Facebook 解析

通过 [cobalt](https://github.com/imputnet/cobalt) 实例解析。部署在 `192.168.31.2:54257`。

**支持：** `instagram.com/p/xxx` `instagram.com/reel/xxx` `facebook.com/xxx` `fb.watch/xxx` 等。

**效果：** 自动下载图片/视频，多图/视频合并转发。

### X / Twitter 链接解析

> 待实现

---

## 架构

本仓库是 HIKARI BOT NEO 的**部分解析源码**，实现了四个平台的链接解析：

| 平台 | 模块 |
|------|------|
| Pixiv | `plugins/pixiv_parser/` |
| X / Twitter | 待实现 |
| Instagram | `plugins/cobalt_parser/`（基于 [cobalt](https://github.com/imputnet/cobalt)） |
| Facebook | `plugins/cobalt_parser/`（基于 [cobalt](https://github.com/imputnet/cobalt)） |

其余平台通过 AstrBot 生态完成：

| 平台 | 解析方案 |
|------|---------|
| 抖音 | [astrbot_plugin_media_parser](https://github.com/drdon1234/astrbot_plugin_media_parser) |
| Bilibili | [astrbot_plugin_media_parser](https://github.com/drdon1234/astrbot_plugin_media_parser) |
| TikTok | [astrbot_plugin_media_parser](https://github.com/drdon1234/astrbot_plugin_media_parser) |
| 小黑盒 | [astrbot_plugin_media_parser](https://github.com/drdon1234/astrbot_plugin_media_parser) |

两部分并行部署，共同构成完整的媒体解析能力。

---

## 基础能力

- **消息记录** — 所有消息写入 JSONL（私聊 `UserData/private/<QQ>.jsonl`，群聊 `UserData/group/<群号>.jsonl`），含完整原始事件
- **错误通知** — 解析失败时给用户简短提示，同时私发管理员详细错误（含堆栈）
- **配置热重载** — 修改插件配置无需重启，下一条消息自动生效

---

## 快速开始

### 1. 安装

```bash
uv sync
```

### 2. 配置

首次运行自动创建默认配置，修改即可：

| 文件 | 内容 |
|------|------|
| `BotData/config.json` | NapCat 地址、Token、管理员 QQ |
| `BotData/plugin_configs/pixiv_parser.json` | Pixiv Cookie、代理、发送策略 |
| `BotData/plugin_configs/cobalt_parser.json` | cobalt API 地址、发送策略 |

> 所有配置从 JSON 读取，**不使用 `.env`**。模板参见 `BotData/*.example.json`。

### 3. 启动

```bash
python bot.py
```

### 4. 测试

在 QQ 里发一条 Pixiv 链接，机器人回复作品信息即成功。

---

## 部署

### systemd

```bash
cp /opt/HIKARI_BOT_NEO/hikari-bot-neo.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hikari-bot-neo
journalctl -u hikari-bot-neo -f
```

### 一键部署（deploy.ps1）

Windows PowerShell，需先配 SSH Key：

```powershell
.\deploy.ps1
```

脚本流程：打包 → scp 上传 → `uv sync` → 安装 systemd 服务 → 启动。

### SSH Key

```powershell
ssh-keygen -t ed25519
type $env:USERPROFILE\.ssh\id_ed25519.pub   # 复制到服务器 /root/.ssh/authorized_keys
```

### NapCat 媒体目录映射

Bot 下载的图片存在宿主机 `/tmp/hikari_bot/`，NapCat 容器必须能读取：

```yaml
services:
  napcat:
    volumes:
      - /tmp/hikari_bot:/tmp/hikari_bot
```

---

## 项目结构

```
HIKARI_BOT_NEO/
  bot.py                  # 入口
  core/                   # 配置加载、日志、消息管道、错误通知
  plugins/
    pixiv_parser/         # Pixiv 解析
    cobalt_parser/        # Instagram/Facebook 解析
  BotData/
    config.json           # 主配置
    plugin_configs/       # 插件独立配置
    logs/                 # 日志（每次启动新建）
  UserData/
    private/              # 私聊消息 JSONL
    group/                # 群聊消息 JSONL
  deploy.ps1              # 一键部署
  hikari-bot-neo.service  # systemd 服务
```

---

## 常见问题

| 症状 | 原因 | 解决 |
|------|------|------|
| 启动后没反应 | NapCat 未连接 | 检查 `ws_url`、`token`、NapCat 是否启动 |
| 图片发不出去 | NapCat 容器读不到文件 | Docker Compose 挂载 `/tmp/hikari_bot:/tmp/hikari_bot` |
| Pixiv 403 / ConnectError | Cookie 失效或直连被墙 | 更新 Cookie，**配置代理** |
| Cloudflare 拦截 | Cookie 不够完整 | 补全 `cf_clearance` 等字段 |
| 合并转发失效 | 私聊可能不支持 | 自动降级逐张发送 |
| JSON 配置报错 | 格式错误 | `python -m json.tool BotData/config.json` 检查 |
