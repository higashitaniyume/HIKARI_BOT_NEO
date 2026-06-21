# HIKARI BOT NEO

QQ 媒体解析机器人，基于 [NoneBot](https://nonebot.dev/)，通过 NapCat WebSocket 接入 QQ。

**原理：** 在 QQ 里发送一条媒体链接，机器人收到后自动解析、下载媒体内容，发回当前会话。

---

## 支持平台

| 平台 | 解析方案 |
|------|---------|
| Pixiv | 本仓库 `plugins/pixiv_parser/` |
| Instagram | 本仓库 `plugins/cobalt_parser/`（基于 [cobalt](https://github.com/imputnet/cobalt)） |
| Facebook | 本仓库 `plugins/cobalt_parser/`（基于 [cobalt](https://github.com/imputnet/cobalt)） |
| X / Twitter | [astrbot_plugin_media_parser](https://github.com/drdon1234/astrbot_plugin_media_parser) |
| 抖音 | [astrbot_plugin_media_parser](https://github.com/drdon1234/astrbot_plugin_media_parser) |
| Bilibili | [astrbot_plugin_media_parser](https://github.com/drdon1234/astrbot_plugin_media_parser) |
| TikTok | [astrbot_plugin_media_parser](https://github.com/drdon1234/astrbot_plugin_media_parser) |
| 小黑盒 | [astrbot_plugin_media_parser](https://github.com/drdon1234/astrbot_plugin_media_parser) |

本仓库包含 Pixiv、Instagram、Facebook 三个平台的解析实现，其余由 AstrBot 插件完成。两部分并行部署。

---

## 功能详情

### Pixiv

检测到 `pixiv.net/artworks/` 或 `pixiv.net/i/` 链接时自动解析。

发送链接后，机器人回复作品信息（标题、作者、PID、图片数量），然后发送图片：
- 单图 → 直接发图
- 多图 → 合并转发
- 转发失败 → 降级逐张发送

不支持纯数字 PID、`pid:` 格式、用户主页/tag/novel 等链接。可用 `/pixiv <URL>` 手动触发。

### Instagram / Facebook

通过 cobalt 实例（`192.168.31.2:54257`）解析。支持 `instagram.com/p/`、`reel`、`stories` 及 `facebook.com`、`fb.watch` 等。

发送链接后自动下载图片/视频，多图/视频合并转发。

---

## 基础能力

- **消息记录** — 所有消息写入 JSONL（私聊 `UserData/private/<QQ>.jsonl`，群聊 `UserData/group/<群号>.jsonl`）
- **错误通知** — 解析失败时回复「解析失败，请稍后再试」，同时私发管理员详细堆栈
- **配置热重载** — 修改插件 JSON 配置无需重启，下条消息生效

---

## 快速开始

### 1. 安装

```bash
uv sync
```

### 2. 配置

首次运行自动创建默认配置：

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

发一条 Pixiv 链接到 QQ，机器人回复即成功。

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

```powershell
.\deploy.ps1
```

需先配 SSH Key：

```powershell
ssh-keygen -t ed25519
type $env:USERPROFILE\.ssh\id_ed25519.pub   # 复制到服务器 /root/.ssh/authorized_keys
```

### NapCat 媒体目录映射

Bot 下载的媒体存在宿主机 `/tmp/hikari_bot/`，NapCat 容器必须能读取：

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
    cobalt_parser/        # Instagram/Facebook 解析（cobalt）
  BotData/
    config.json           # 主配置
    plugin_configs/       # 插件独立配置
    logs/                 # 日志
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
| 发链接没反应 | NapCat 未连接 | 检查 `ws_url`、`token` |
| 图片发不出去 | NapCat 容器读不到文件 | 挂载 `/tmp/hikari_bot:/tmp/hikari_bot` |
| Pixiv 403/ConnectError | Cookie 失效或直连被墙 | 更新 Cookie，**配置代理** |
| Cloudflare 拦截 | Cookie 不够完整 | 补全 `cf_clearance` |
| 合并转发失效 | 私聊可能不支持 | 自动降级逐张发送 |
| JSON 配置报错 | 格式错误 | `python -m json.tool BotData/config.json` |
