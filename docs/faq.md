# 常见问题

| 症状 | 常见原因 | 处理方式 |
|------|----------|----------|
| 启动后机器人不在线 | NapCat WebSocket 地址或 Token 错误 | 检查 `BotData/config.json` 的 `ws_url` 和 `token` |
| `tg贴纸` 没有反应 | 插件关闭、链接不匹配、NapCat 未连接 | 检查配置和日志 |
| 图片或视频发送失败 | NapCat 读不到临时文件 | 挂载共享目录，检查 `PrivateTmp` |
| Pixiv 403 / Cloudflare | Cookie 失效或不完整 | 更新 Cookie，必要时补 `cf_clearance` |
| Pixiv 连接失败 | 网络无法直连 | 配置 `proxy` |
| Instagram / Facebook 解析失败 | cobalt API 不可用 | 确认 `cobalt_api` 地址正确 |
| 抖音/B站/小红书等解析失败 | 平台风控、Cookie 失效、代理不可用 | 检查配置、代理和日志 |
| Telegram 贴纸解析失败 | Token 未配置或无法访问 Telegram API | 填写 Token，配置代理 |
| Telegram 动态贴纸转换失败 | 缺少转换依赖 | 检查 ffmpeg、lottie 命令 |
| JSON 配置报错 | 格式错误 | 运行 `python -m json.tool <文件>` 检查 |
| 私聊 AI 不回话 | 用户/群黑白名单、配额用尽或 AI 未启用 | 检查后台「AI 配额」页开关与日志 |
| 群聊 AI 只对部分群回复 | 群白名单/群黑名单生效 | 后台「AI 配额」页调整群维度开关 |

## NapCat 文件目录

机器人会把图片、视频、贴纸、PDF 等临时文件放到 `/tmp/hikari_bot`。NapCat 必须能读取这个目录，否则会出现"解析成功但发送失败"。

各插件的临时媒体默认 10 分钟后清理（通过 `cache_ttl_seconds` 调整）。

Docker 部署时请挂载共享目录：

```yaml
services:
  napcat:
    volumes:
      - ./runtime/tmp/hikari_bot:/tmp/hikari_bot
```

## 验证命令

```bash
# Python 语法检查
uv run python -m compileall <changed paths>

# 运行全部测试
uv run python -m unittest discover -s tests

# 单个测试
uv run python -m unittest tests.test_<name>

# JSON 校验
python -m json.tool BotData/plugin_configs/<file>.json

# JS 语法检查
node --check plugins/bot_admin/static/<file>.js
```
