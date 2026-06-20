# HIKARI BOT NEO

基于 [NoneBot](https://nonebot.dev/) 的 QQ 机器人，通过 NapCat WebSocket 接入 QQ。

---

## 功能

### Pixiv 作品解析

群里发一条 Pixiv 链接，机器人自动下载图片并发送。

**支持：** `artworks` 和 `i` 两种链接格式，带不带 `www`、带不带 `en` 都行。

```
https://www.pixiv.net/artworks/123456789
https://pixiv.net/i/123456789
```

**效果：** 自动回复作品标题、作者、PID、图片数量，然后发送图片。
- 单图作品 → 直接发图
- 多图作品 → 合并转发（所有图片包在一条消息里）
- 合并转发失败 → 自动降级为逐张发送

**不支持：** 纯数字 PID、`pid:123456789`、用户主页、tag、novel 等链接。

**命令:** `/pixiv <URL>` 手动触发。

### Instagram / Facebook 媒体解析

通过 [cobalt](https://github.com/imputnet/cobalt) 实例解析社交媒体链接。

**支持：**
| 平台 | URL 示例 |
|------|---------|
| Instagram | `instagram.com/p/xxx` `instagram.com/reel/xxx` `instagram.com/stories/xxx` |
| Facebook | `facebook.com/xxx` `fb.com/xxx` `fb.watch/xxx` |

**效果：** 自动下载图片/视频并发送。多图/视频同样用合并转发。

### 消息记录

所有消息自动写入 JSONL，一字不漏：
- 私聊 → `UserData/private/<QQ号>.jsonl`
- 群聊 → `UserData/group/<群号>.jsonl`

每条记录含时间、消息内容、发送者信息、完整原始事件。

### 错误通知

任何功能出错时：
- 给触发用户/群里回复简短提示「解析失败，请稍后再试」
- 给超级管理员私发详细错误（时间、来源、QQ、异常堆栈等）

### 配置热重载

修改 `BotData/plugin_configs/` 下的配置文件后无需重启，下次消息自动生效。

---

## 快速开始

### 1. 安装依赖

```bash
uv sync
```

### 2. 填写配置

首次运行会自动创建默认配置文件，修改即可：

- **主配置** `BotData/config.json` — NapCat 地址、Token、管理员 QQ
- **Pixiv** `BotData/plugin_configs/pixiv_parser.json` — Cookie、代理、发送策略
- **Cobalt** `BotData/plugin_configs/cobalt_parser.json` — cobalt API 地址

> 所有配置从 JSON 读取，**不使用 `.env` 文件**。模板参见 `BotData/*.example.json`。

### 3. 启动

```bash
python bot.py
```

或：

```bash
uv run nb run
```

### 4. 测试

在 QQ 里发一条 Pixiv 链接，机器人回复作品信息和图片即成功。

---

## 部署

### systemd

```bash
cp /opt/HIKARI_BOT_NEO/hikari-bot-neo.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hikari-bot-neo
journalctl -u hikari-bot-neo -f
```

### 一键部署

Windows PowerShell（需先配 SSH Key）：

```powershell
.\deploy.ps1
```

脚本自动完成：打包 → scp 上传 → uv sync → 安装 systemd 服务 → 启动。

### SSH Key 配置

```powershell
ssh-keygen -t ed25519
type $env:USERPROFILE\.ssh\id_ed25519.pub   # 复制到服务器 /root/.ssh/authorized_keys
```

### NapCat 媒体目录映射

Bot 下载的图片存在宿主机 `/tmp/hikari_bot/`，NapCat 容器需要能读到：

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
    plugin_configs/       # 各插件独立配置
    logs/                 # 日志（每次启动新建）
  UserData/
    private/              # 私聊消息 JSONL
    group/                # 群聊消息 JSONL
  deploy.ps1              # 一键部署脚本
  hikari-bot-neo.service  # systemd 服务
```

---

## 配置热重载

修改配置文件后**不需要重启**机器人，下一次收到消息时自动读取最新配置。

以下配置支持热重载：
- `BotData/plugin_configs/pixiv_parser.json` — Pixiv Cookie、代理、发送策略等
- `BotData/plugin_configs/cobalt_parser.json` — cobalt API、发送策略等

主配置 `BotData/config.json` 中的 NapCat 连接参数需重启生效。

---

## 常见问题

| 症状 | 原因 | 解决 |
|------|------|------|
| 启动后不发消息 | NapCat 未连接 | 检查 `ws_url`、`token` 和 NapCat 是否启动 |
| 图片发送后 QQ 看不到 | NapCat 容器读不到文件 | Docker Compose 加 `/tmp/hikari_bot:/tmp/hikari_bot` |
| Pixiv 403 | Cookie 失效或 IP 被墙 | 更新 Cookie、配置代理 |
| Cloudflare 拦截 | Cookie 不够完整 | 补充 `cf_clearance` 到 Cookie |
| 合并转发失效 | 私聊可能不支持 | 会自动降级为逐张发送 |
| JSON 配置报错 | JSON 格式错误 | `python -m json.tool BotData/config.json` 检查 |
