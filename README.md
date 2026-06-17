# HIKARI BOT NEO

基于 [NoneBot](https://nonebot.dev/) 框架的 QQ 机器人，通过 NapCat WebSocket 接入 QQ。

## 目录结构

```
project_root/
  bot.py                   # 入口文件
  pyproject.toml           # uv 项目配置
  README.md
  deploy.ps1               # Windows PowerShell 一键部署脚本
  hikari-bot-neo.service   # systemd 服务文件

  core/
    __init__.py
    config_loader.py       # 配置加载（从 JSON 读取）
    logger_setup.py        # 日志初始化
    message_pipeline.py    # 消息处理中心
    message_collector.py   # 消息收集（写入 JSONL）
    error_notifier.py      # 错误通知

  plugins/
    pixiv_parser/          # Pixiv 解析插件
      __init__.py          # 插件入口 / 注册
      config.py            # Pixiv 配置加载
      parser.py            # URL 匹配 / API 获取
      downloader.py        # 图片下载
      sender.py            # 作品发送

  BotData/
    config.json            # 主配置文件
    plugin_configs/
      pixiv_parser.json    # Pixiv 插件独立配置
    logs/                  # 日志文件（每启动一次新建一个）

  UserData/
    private/               # 私聊消息 JSONL
      123456789.jsonl
    group/                 # 群聊消息 JSONL
      987654321.jsonl

  sample_src/              # 原始示例代码（参考用）
    pixiv_parser.py
```

## 为什么不使用 `.env`

1. `.env` 文件容易意外提交到 Git，泄露敏感信息。
2. 多个环境（`.env` / `.env.prod` / `.env.dev`）造成配置碎片化。
3. JSON 配置文件有结构，支持嵌套，IDE 有语法高亮和校验。
4. 本项目所有配置从 `BotData/config.json` 及 `BotData/plugin_configs/` 中的 JSON 文件读取。
5. 程序启动时若 JSON 文件不存在，会自动创建带默认值的配置文件。
6. 删除 `.env` 后不会对 NoneBot 产生任何影响——所有配置均以程序方式传入 `nonebot.init()`。

## 配置文件

### 主配置：`BotData/config.json`

```json
{
  "bot": {
    "name": "HikariBotNeo",
    "superuser_id": "3433559280",
    "log_level": "INFO"
  },
  "napcat": {
    "ws_url": "ws://192.168.31.2:54253/",
    "token": "MH4NBIRN7ICP46wL",
    "protocol": "websocket"
  },
  "paths": {
    "bot_data": "BotData",
    "user_data": "UserData",
    "logs": "BotData/logs",
    "plugin_configs": "BotData/plugin_configs",
    "temp_media": "/tmp/hikari_bot"
  },
  "features": {
    "message_collector": true,
    "pixiv_parser": true
  },
  "media": {
    "send_path_prefix": "file://"
  }
}
```

| 字段 | 说明 |
|------|------|
| `bot.name` | 机器人名称 |
| `bot.superuser_id` | 超级管理员 QQ 号（接收错误通知） |
| `bot.log_level` | 日志级别：DEBUG / INFO / WARNING / ERROR |
| `napcat.ws_url` | NapCat WebSocket 地址（Bot 主动连接 NapCat） |
| `napcat.token` | NapCat 访问 token |
| `paths.temp_media` | 媒体临时目录，图片下载到此目录 |
| `features.message_collector` | 是否启用消息记录 |
| `features.pixiv_parser` | 是否启用 Pixiv 解析 |
| `media.send_path_prefix` | 发送图片时的路径前缀（`file://` 表示本地文件） |

### Pixiv 插件配置：`BotData/plugin_configs/pixiv_parser.json`

```json
{
  "cookie": "",
  "auto_parse": true,
  "max_send": 6,
  "max_file_mb": 25,
  "allow_r18": false,
  "cache_dir": "/tmp/hikari_bot",
  "cache_ttl_hours": 24,
  "proxy": "",
  "send_strategy": {
    "prefer_forward_message": true,
    "fallback_to_separate_images": true
  }
}
```

| 字段 | 说明 |
|------|------|
| `cookie` | Pixiv Cookie（至少需要 PHPSESSID） |
| `auto_parse` | 是否自动解析聊天消息中的 Pixiv 链接 |
| `max_send` | 单次最多发送几张图片 |
| `max_file_mb` | 图片最大大小（MB），超过则尝试 regular 图 |
| `allow_r18` | 是否允许发送 R-18 / R-18G 作品 |
| `cache_dir` | 图片缓存目录 |
| `cache_ttl_hours` | 缓存保留时间（小时） |
| `proxy` | HTTP 代理地址（如 `http://127.0.0.1:7890`） |
| `send_strategy.prefer_forward_message` | 多图作品是否优先使用合并转发 |
| `send_strategy.fallback_to_separate_images` | 合并转发失败后是否降级为逐张发送 |

## 本地运行

```bash
# 安装依赖
uv sync

# 启动机器人
uv run nb run
```

首次运行会自动创建 `BotData/config.json` 和默认目录结构。

## systemd 部署

### 服务文件：`hikari-bot-neo.service`

已放置于项目根目录。部署到 `/opt/HIKARI_BOT_NEO` 后：

```bash
# 复制服务文件
cp /opt/HIKARI_BOT_NEO/hikari-bot-neo.service /etc/systemd/system/

# 重载 systemd
systemctl daemon-reload

# 启用开机自启
systemctl enable hikari-bot-neo

# 启动服务
systemctl restart hikari-bot-neo

# 查看状态
systemctl status hikari-bot-neo

# 查看实时日志
journalctl -u hikari-bot-neo -f
```

## 一键部署（deploy.ps1）

在 Windows PowerShell 中运行：

```powershell
.\deploy.ps1
```

脚本会：
1. 打包项目文件（排除 `.git` / `.venv` / `__pycache__` 等）
2. 通过 `scp` 上传到 `root@192.168.31.2:/opt/HIKARI_BOT_NEO`
3. 在服务器上执行 `uv sync` 安装依赖
4. 安装/更新 systemd 服务
5. 启用并重启 `hikari-bot-neo` 服务
6. 显示服务状态

### 前置条件：配置 SSH Key

在 Windows PowerShell 中生成 SSH key（如果还没有）：

```powershell
ssh-keygen -t ed25519
```

查看公钥：

```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub
```

将公钥内容添加到服务器的 `/root/.ssh/authorized_keys`：

```bash
# 在服务器上执行
mkdir -p /root/.ssh
echo "你的公钥内容" >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
```

验证免密登录：

```powershell
ssh root@192.168.31.2
```

## NapCat 配置

### NapCat WebSocket 服务端

本 Bot 作为客户端主动连接 NapCat。NapCat 需要配置为 WebSocket 服务端模式。

NapCat WebSocket 地址：`ws://192.168.31.2:54253/`
NapCat Token：`MH4NBIRN7ICP46wL`

### NapCat Docker Compose 媒体目录映射

Bot 下载的 Pixiv 图片存放在宿主机 `/tmp/hikari_bot/`，NapCat 需要能读取此目录。

在 NapCat 的 `docker-compose.yml` 中，**必须**添加以下 volume 映射：

```yaml
services:
  napcat:
    # ... 其他配置 ...
    volumes:
      - /tmp/hikari_bot:/tmp/hikari_bot
```

**原理说明：**

1. Bot 下载图片到宿主机 `/tmp/hikari_bot/xxx.jpg`
2. Bot 通过 OneBot 协议发送 `file:///tmp/hikari_bot/xxx.jpg`
3. NapCat 容器读取容器内的 `/tmp/hikari_bot/xxx.jpg`（通过 volume 映射）
4. NapCat 将图片发送到 QQ

**如果 Bot 未来也容器化：** Bot 容器和 NapCat 容器都必须映射同一个宿主机目录：

```yaml
services:
  napcat:
    volumes:
      - /tmp/hikari_bot:/tmp/hikari_bot

  hikari_bot:
    volumes:
      - /tmp/hikari_bot:/tmp/hikari_bot
```

## Pixiv 解析功能

### 支持的 URL 格式

```
https://www.pixiv.net/artworks/123456789
https://www.pixiv.net/en/artworks/123456789
https://pixiv.net/artworks/123456789
https://www.pixiv.net/i/123456789
https://pixiv.net/i/123456789
```

### 不支持

- 纯数字 PID（如 `123456789`）
- `pid:123456789` 格式
- 用户主页链接
- tag / novel / search 等其他 Pixiv 链接

### 行为

1. 自动检测消息中的 Pixiv 作品 URL
2. 下载图片到 `/tmp/hikari_bot/`
3. 发送作品信息（标题、作者、PID、链接、图片数量）
4. 多图作品优先使用合并转发
5. 合并转发失败则降级为逐张发送
6. 不对 R18 / R18G 做限制（`allow_r18` 配置为 `true` 时允许发送）

### 命令

```
/pixiv <Pixiv作品URL>
```

## 消息记录

所有收到的消息自动写入 JSONL 文件：

- 私聊：`UserData/private/<user_id>.jsonl`
- 群聊：`UserData/group/<group_id>.jsonl`

每条消息一行 JSON，包含完整 `raw_event`。

## 查看日志

```bash
# systemd 部署后的实时日志
journalctl -u hikari-bot-neo -f

# 文件日志
ls BotData/logs/
cat BotData/logs/2026-06-16_15-30-00.log
```

每次启动会创建新的日志文件（文件名包含启动时间）。

## 测试

### 测试 NapCat 连接

启动机器人后，观察日志：

```bash
journalctl -u hikari-bot-neo -f
```

正常连接时应看到类似日志：

```
[INFO] nonebot: Succeeded to connect to WebSocket server ws://192.168.31.2:54253/
```

或发送任意消息给机器人，如果收到回复即为连接正常。

### 测试 Pixiv 解析

在 QQ 中发送 Pixiv 作品链接：

```
https://www.pixiv.net/artworks/123456789
```

Bot 应自动回复作品信息和图片。

或使用命令：

```
/pixiv https://www.pixiv.net/artworks/123456789
```

## 常见问题排查

### WebSocket 连接失败

检查 NapCat 是否启动且 WebSocket 端口可访问：

```bash
# 在服务器上测试
curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" http://192.168.31.2:54253/
```

检查 `BotData/config.json` 中的 `napcat.ws_url` 是否正确。

### Token 错误

检查 `BotData/config.json` 中的 `napcat.token` 是否与 NapCat 配置的 token 一致。

### NapCat 读不到本地图片

**症状：** Bot 日志显示发送成功，但 QQ 看不到图片。

**原因：** NapCat 容器无法访问宿主机文件。

**解决：** 确保 Docker Compose 中配置了 volume 映射：

```yaml
volumes:
  - /tmp/hikari_bot:/tmp/hikari_bot
```

### Pixiv 403

**症状：** `Pixiv 返回 403`

**原因：**
1. Cookie 失效或未配置
2. IP 被限制
3. 缺少 Referer 请求头

**解决：**
1. 在浏览器中登录 Pixiv，复制 Cookie 填入 `BotData/plugin_configs/pixiv_parser.json` 的 `cookie` 字段
2. 如果使用代理，在 `proxy` 字段配置代理地址

### Pixiv 被 Cloudflare 拦截

**症状：** `Pixiv Web Ajax 被 Cloudflare 拦截`

**原因：** Pixiv 开启了 Cloudflare 防护，当前 Cookie 方案不可用。

**解决：**
1. 更新更完整的 Cookie（包括 `cf_clearance`）
2. 降低请求频率

### 合并转发失败

**症状：** `合并转发失败`，然后降级为逐张发送

**原因：** 可能是私聊不支持合并转发，或 NapCat 版本不兼容。

**解决：** 降级后逐张发送是正常行为。可在配置中设置 `send_strategy.prefer_forward_message: false` 直接使用逐张发送。

### systemd 启动失败

```bash
# 查看详细错误
journalctl -u hikari-bot-neo -n 50 --no-pager

# 检查 uv 路径
which uv

# 如果 uv 路径不是 /root/.cargo/bin/uv，修改 hikari-bot-neo.service 中的 ExecStart
```

### 配置文件 JSON 格式错误

启动时如果 JSON 格式错误，Bot 会输出明确错误并拒绝启动。用 JSON 校验工具检查语法：

```bash
python -m json.tool BotData/config.json
```
