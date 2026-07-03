# HIKARI BOT NEO HTTP API

本文档覆盖机器人进程自己托管的 HTTP 接口：

- Bot Admin 管理 API，默认 `http://服务器IP:54213`
- 媒体详情 Web API，默认 `http://服务器IP:53123`

不在本文档范围内：

- NapCat / OneBot V11 本身暴露的接口。
- 机器人调用的第三方上游接口，例如 osu! API、Pixiv、Cobalt、SearXNG、OpenAI-compatible API 等。
- QQ 聊天命令。聊天命令属于机器人消息交互，不是本项目自托管 HTTP API。

## 基础约定

### 端口与配置

| 服务 | 默认地址 | 配置文件 | Docker 端口变量 |
| --- | --- | --- | --- |
| Bot Admin | `http://服务器IP:54213` | `BotData/plugin_configs/bot_admin.json` | `HIKARI_BOT_ADMIN_PORT`，兼容旧变量 `HIKARI_STICKER_WEB_PORT` |
| 媒体详情 Web | `http://服务器IP:53123` | `BotData/plugin_configs/media_detail_web.json` | `HIKARI_MEDIA_DETAIL_WEB_PORT` |

Bot Admin 默认监听容器内 `0.0.0.0:54213`。媒体详情 Web 默认监听容器内 `0.0.0.0:53123`。

### Bot Admin 鉴权

Bot Admin 的 `/api/...` 接口支持两种鉴权方式：

1. 浏览器登录后携带 session cookie。
2. 请求头直接携带 token。token 就是 `BotData/plugin_configs/bot_admin.json` 里的 `password`。

可用请求头：

```http
X-Admin-Token: <后台密码>
X-Hikari-Admin-Token: <后台密码>
Token: <后台密码>
Authorization: Bearer <后台密码>
```

示例：

```bash
curl -H "X-Admin-Token: <后台密码>" http://192.168.31.2:54213/api/aiagent-config
curl -H "Authorization: Bearer <后台密码>" http://192.168.31.2:54213/api/state
```

注意：

- Header token 只对 `/api/...` 路径生效。
- 普通页面 `/`、`/login` 仍使用原来的网页登录 cookie/session。
- 如果 `password` 为空，Bot Admin 鉴权关闭；不建议公网或不可信局域网这样部署。

### 错误响应

JSON API 的错误通常返回：

```json
{
  "error": "错误说明"
}
```

常见状态码：

| 状态码 | 含义 |
| --- | --- |
| `200` | 请求成功 |
| `202` | 后台任务已创建，需轮询任务状态 |
| `206` | 媒体分段响应 |
| `303` | 登录/退出后的页面重定向 |
| `400` | 请求格式或参数错误 |
| `401` | Bot Admin 未登录或 token 错误 |
| `404` | 资源不存在 |
| `409` | 当前状态冲突，例如推送任务正在运行 |
| `413` | 请求或远程媒体超过限制 |
| `416` | Range 请求范围无效 |
| `500` | 服务内部错误，需看日志 |
| `502` | 媒体代理或上游请求失败 |
| `504` | 操作超时 |

### JSON 与文件上传

JSON 请求请使用：

```http
Content-Type: application/json
```

上传接口使用：

```http
Content-Type: multipart/form-data
```

路径参数中的中文、空格、斜杠等字符需要 URL encode。接口会拒绝目录穿越类文件名。

## Bot Admin API

Base URL 示例：

```text
http://192.168.31.2:54213
```

除页面与静态资源外，以下 `/api/...` 接口都需要 Bot Admin 鉴权。

### 页面与登录接口

#### `GET /`

返回 Bot Admin 主页面。未登录时返回登录页面。

#### `GET /index.html`

等同 `GET /`。

#### `GET /login`

返回登录页面。已登录时重定向到 `/`。

#### `POST /login`

表单登录接口。

Content-Type:

```http
application/x-www-form-urlencoded
```

请求字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `password` | string | 是 | `bot_admin.json` 中的后台密码 |

成功时返回 `303` 并设置 session cookie；失败返回登录页面和 `401`。

#### `GET /logout`

清除 session cookie 并重定向到 `/login`。

#### `GET /static/<path>`

返回后台静态文件。只允许读取 Bot Admin static 目录内的文件。

### 贴纸库状态

#### `GET /api/state`

读取贴纸包和触发词总览。

响应：

```json
{
  "packs": [
    {
      "name": "capoo",
      "count": 12,
      "keywords": ["capoo", "猫猫虫"],
      "previews": ["a1b2c3d4e5f6a7b8.gif"]
    }
  ],
  "keywords": [
    {
      "keyword": "猫猫虫",
      "packs": ["capoo"]
    }
  ],
  "total_stickers": 12
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `packs[].name` | 贴纸包名称 |
| `packs[].count` | 包内贴纸数量 |
| `packs[].keywords` | 触发词列表 |
| `packs[].previews` | 最多 6 个预览贴纸 ID |
| `keywords[]` | 触发词到贴纸包的反向索引 |
| `total_stickers` | 贴纸库中去重后的贴纸总数 |

### 贴纸包详情

#### `GET /api/packs/{pack_name}`

读取指定贴纸包详情。

响应：

```json
{
  "pack": {
    "name": "capoo",
    "count": 2,
    "keywords": ["capoo"],
    "stickers": [
      {
        "id": "a1b2c3d4e5f6a7b8.gif",
        "file": "a1b2c3d4e5f6a7b8.gif",
        "original_name": "hello.gif",
        "source": "upload",
        "created_at": 1750000000,
        "size": 12345,
        "missing": false
      }
    ]
  }
}
```

`missing=true` 表示索引中有记录，但文件已经不存在或不可读。

### 下载贴纸包压缩包

#### `GET /api/packs/{pack_name}/download`

生成并下载指定贴纸包的 `.7z` 压缩包。

响应：

- 成功：`application/x-7z-compressed`
- 失败：JSON `{ "error": "..." }`

Content-Disposition 使用 UTF-8 文件名。

### 获取贴纸文件

#### `GET /api/stickers/{sticker_id}`

返回单个贴纸文件，通常为 GIF。

响应头：

```http
Content-Type: image/gif
Cache-Control: private, max-age=86400
```

### 新增贴纸触发词

#### `POST /api/keywords`

给贴纸包新增触发词。

请求：

```json
{
  "pack": "capoo",
  "keyword": "猫猫虫;capoo"
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `pack` | string | 是 | 贴纸包名称 |
| `keyword` | string | 是 | 触发词；多个触发词可用 `;` 或 `；` 分隔 |

响应：同 `GET /api/state`。

### 删除贴纸触发词

#### `DELETE /api/keywords?pack={pack_name}&keyword={keyword}`

从贴纸包移除一个触发词。

响应：

- 成功：同 `GET /api/state`
- 未找到关联：返回 `404`，响应里会带 `error`

### 上传贴纸素材

#### `POST /api/uploads`

异步上传贴纸素材到指定贴纸包。素材会统一转换为 GIF。

Content-Type:

```http
multipart/form-data
```

表单字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | file[] | 是 | 素材文件，可多选，最多 99 个 |
| `existing_pack` | string | 否 | 已有贴纸包名称 |
| `new_pack` | string | 否 | 新贴纸包名称 |
| `keyword` | string | 否 | 附加触发词 |

`existing_pack` 和 `new_pack` 至少提供一个；优先使用 `existing_pack`。

支持后缀来自 `plugins.media_transcoder.STICKER_INPUT_EXTS`，常见包括 `.gif`、`.jpg`、`.jpeg`、`.png`、`.webp`、`.mp4`、`.webm`、`.mov`、`.mkv`、`.tgs`。

成功创建任务时返回 `202`：

```json
{
  "id": "f6f0d0...",
  "status": "queued",
  "pack": "capoo",
  "total": 3,
  "processed": 0,
  "saved": 0,
  "reused": 0,
  "failed": [],
  "current": "",
  "message": "等待处理...",
  "created_at": 1750000000.0,
  "updated_at": 1750000000.0
}
```

### 查询上传任务

#### `GET /api/uploads/{job_id}`

轮询贴纸上传或 Telegram 导入任务。

响应字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 任务 ID |
| `status` | `queued`、`running`、`done`、`failed` |
| `pack` | 目标贴纸包 |
| `total` | 任务总数 |
| `processed` | 已处理数量 |
| `saved` | 新增数量 |
| `reused` | 复用数量 |
| `failed` | 失败项文本数组 |
| `current` | 当前处理项 |
| `message` | 当前进度说明 |
| `created_at` / `updated_at` | Unix 时间戳 |

### 同步上传贴纸素材页面接口

#### `POST /upload`

旧式 HTML 表单入口。行为和 `/api/uploads` 类似，但同步处理并返回 HTML 页面。外部程序应优先使用 `/api/uploads`。

### 导入 Telegram 贴纸包

#### `POST /api/tg-stickers`

异步导入 Telegram 贴纸包。

请求：

```json
{
  "url": "https://t.me/addstickers/pack_name",
  "pack": "目标包名",
  "keyword": "触发词",
  "refresh": false
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `url` | string | 是 | Telegram 贴纸包链接 |
| `pack` | string | 否 | 目标包名；为空时使用贴纸包名 |
| `keyword` | string | 否 | 附加触发词 |
| `refresh` | boolean | 否 | 是否忽略本地缓存重新解析 |

成功创建任务时返回 `202`，响应结构同上传任务。

### 删除贴纸包

#### `DELETE /api/packs?pack={pack_name}`

删除整个贴纸包。

响应：

```json
{
  "packs": [],
  "keywords": [],
  "total_stickers": 0,
  "result": {
    "deleted": true,
    "pack": "capoo",
    "removed_stickers": 12,
    "deleted_files": 12
  }
}
```

如果贴纸文件仍被其他贴纸包引用，不会删除实体文件。

### 删除贴纸包内贴纸

#### `POST /api/pack-stickers/delete`

从一个贴纸包删除指定贴纸。

请求：

```json
{
  "pack": "capoo",
  "stickers": ["a1b2c3d4e5f6a7b8.gif"]
}
```

响应：

```json
{
  "packs": [],
  "keywords": [],
  "total_stickers": 0,
  "result": {
    "pack": "capoo",
    "removed": 1,
    "deleted_files": 1
  },
  "pack_detail": {
    "name": "capoo",
    "count": 0,
    "keywords": [],
    "stickers": []
  }
}
```

### 移动贴纸到其他包

#### `POST /api/pack-stickers/move`

请求：

```json
{
  "source_pack": "capoo",
  "target_pack": "new_pack",
  "stickers": ["a1b2c3d4e5f6a7b8.gif"]
}
```

响应：

```json
{
  "packs": [],
  "keywords": [],
  "total_stickers": 0,
  "result": {
    "source": "capoo",
    "target": "new_pack",
    "moved": 1
  },
  "pack_detail": {}
}
```

`pack_detail` 是移动后来源包详情；如果来源包被清空，仍返回该包当前状态。

### 收集箱列表

#### `GET /api/inbox`

读取机器人静默收集但尚未整理的 GIF。

响应：

```json
{
  "items": [
    {
      "id": "a1b2c3d4e5f6a7b8.gif",
      "file": "a1b2c3d4e5f6a7b8.gif",
      "sha256": "...",
      "source": "qq_message",
      "sender_id": "123456",
      "group_id": "10000",
      "message_id": "abc",
      "created_at": 1750000000,
      "original_name": "a1b2c3d4e5f6a7b8.gif"
    }
  ]
}
```

### 获取收集箱图片

#### `GET /api/inbox/{item_id}/image`

返回收集箱 GIF 文件。

响应头：

```http
Content-Type: image/gif
Cache-Control: private, max-age=86400
```

### 分配收集箱项目到贴纸包

#### `POST /api/inbox/assign`

请求：

```json
{
  "ids": ["a1b2c3d4e5f6a7b8.gif"],
  "pack": "capoo",
  "keyword": "猫猫虫"
}
```

响应：

```json
{
  "result": {
    "assigned": 1,
    "missing": []
  },
  "inbox": {
    "items": []
  },
  "state": {
    "packs": [],
    "keywords": [],
    "total_stickers": 1
  }
}
```

### 删除收集箱项目

#### `POST /api/inbox/delete`

请求：

```json
{
  "ids": ["a1b2c3d4e5f6a7b8.gif"]
}
```

响应：

```json
{
  "removed": 1,
  "inbox": {
    "items": []
  }
}
```

### 语音库状态

#### `GET /api/voice-state`

读取本地语音库和触发词总览。

响应：

```json
{
  "voices": [
    {
      "id": "a1b2c3d4e5f6a7b8.mp3",
      "name": "提示音",
      "file": "a1b2c3d4e5f6a7b8.mp3",
      "original_name": "ding.mp3",
      "keywords": ["叮"],
      "created_at": 1750000000,
      "size": 12345,
      "missing": false
    }
  ],
  "keywords": [
    {
      "keyword": "叮",
      "voices": ["提示音"]
    }
  ],
  "total_voices": 1,
  "total_keywords": 1,
  "total_bytes": 12345,
  "allowed_exts": [".aac", ".amr", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".silk", ".wav"]
}
```

### 获取语音文件

#### `GET /api/voices/{voice_id}/file`

返回语音文件。

响应头：

```http
Content-Type: audio/mpeg
Cache-Control: private, max-age=86400
```

`Content-Type` 会根据后缀猜测；无法识别时默认 `audio/mpeg`。

### 上传语音

#### `POST /api/voices`

Content-Type:

```http
multipart/form-data
```

表单字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `voice_file` | file[] | 是 | 语音文件，最多 20 个 |
| `voice_name` | string | 否 | 单文件上传时的显示名；多文件上传时使用原文件名 stem |
| `voice_keyword` | string | 否 | 触发词，多个可用 `;` 或 `；` 分隔 |

响应：

```json
{
  "status": "done",
  "message": "语音上传完成，新增 1 个",
  "saved": ["a1b2c3d4e5f6a7b8.mp3"],
  "reused": [],
  "failed": [],
  "state": {}
}
```

`state` 结构同 `GET /api/voice-state`。

### 新增语音触发词

#### `POST /api/voice-keywords`

请求：

```json
{
  "voice": "a1b2c3d4e5f6a7b8.mp3",
  "keyword": "叮;提示音"
}
```

响应：同 `GET /api/voice-state`。

### 删除语音触发词

#### `DELETE /api/voice-keywords?voice={voice_id}&keyword={keyword}`

响应：同 `GET /api/voice-state`；未找到关联时返回 `404`，响应里会带 `error`。

### 删除语音

#### `DELETE /api/voices?voice={voice_id}`

响应：

```json
{
  "voices": [],
  "keywords": [],
  "total_voices": 0,
  "total_keywords": 0,
  "total_bytes": 0,
  "allowed_exts": [],
  "result": {
    "deleted": true,
    "voice": "a1b2c3d4e5f6a7b8.mp3",
    "deleted_file": true
  }
}
```

### TTS 配置

#### `GET /api/tts-config`

读取 Fish Audio TTS 配置。响应会隐藏真实 API Key。

响应：

```json
{
  "config": {
    "enabled": true,
    "selected_voice": "永雏塔菲",
    "voices": [
      {
        "name": "永雏塔菲",
        "reference_id": "..."
      }
    ],
    "fish_audio": {
      "api_key": "",
      "api_key_set": true,
      "model": "s2-pro",
      "backup_model": "s2.1-pro-free",
      "retry_count": 3,
      "retry_delay_seconds": 1.0,
      "format": "mp3",
      "latency": "normal",
      "speed": 1.0,
      "volume": 0.0,
      "normalize_loudness": true,
      "pitch_semitones": 0.0,
      "temperature": 0.7,
      "top_p": 0.7,
      "chunk_length": 300,
      "normalize": true,
      "sample_rate": null,
      "mp3_bitrate": 128,
      "repetition_penalty": 1.2,
      "condition_on_previous_chunks": true
    },
    "proxy": "",
    "connect_timeout": 10,
    "receive_timeout": 60,
    "max_chars": 120,
    "cooldown_seconds": 5,
    "cache_dir": "/tmp/hikari_bot/tts",
    "cache_ttl_minutes": 60
  }
}
```

#### `POST /api/tts-config`

保存 TTS 配置。

请求字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `enabled` | boolean | 是否启用 TTS 命令 |
| `selected_voice` | string | 当前使用的音色名，必须存在于 `voices` |
| `voices` | array | 音色数组，元素为 `{ "name": "...", "reference_id": "..." }` |
| `fish_audio` | object | Fish Audio 参数 |
| `proxy` | string | 请求代理 |
| `connect_timeout` | number | 连接超时，1-300 秒 |
| `receive_timeout` | number | 接收超时，1-600 秒 |
| `max_chars` | number | 单次合成字符上限，1-1000 |
| `cooldown_seconds` | number | 用户冷却，0-3600 秒 |
| `cache_dir` | string | TTS 缓存目录 |
| `cache_ttl_minutes` | number | 缓存保留分钟数，1-10080 |

`fish_audio.api_key` 留空时保留现有 Key。

成功响应：

```json
{
  "config": {},
  "message": "TTS 设置已保存。"
}
```

### AI Agent 配置与 Tools 目录

#### `GET /api/aiagent-config`

读取 AI Agent 配置、人格 skill 列表和已注册 plugin tools 目录。响应会隐藏真实模型 API Key。

响应：

```json
{
  "config": {
    "enabled": false,
    "model": {
      "base_url": "https://api.deepseek.com/v1",
      "api_key": "",
      "api_key_set": true,
      "model": "deepseek-chat",
      "temperature": 0.7,
      "top_p": 1.0,
      "max_tokens": 1024,
      "timeout_seconds": 60,
      "proxy": ""
    },
    "persona": {},
    "chat": {},
    "memory": {},
    "tools": {}
  },
  "personas": [
    {
      "path": "BotData/agent_personas/default",
      "title": "默认人格",
      "file": "SKILL.md",
      "kind": "directory"
    }
  ],
  "tools_catalog": [
    {
      "name": "mc_wiki_search",
      "plugin_name": "mc_wiki",
      "description": "Search zh.minecraft.wiki pages.",
      "parameters": {
        "type": "object",
        "properties": {}
      },
      "readonly": true,
      "requires_superuser": false,
      "enabled_by_default": true,
      "selected": true,
      "blocked_reason": "",
      "missing": false
    }
  ]
}
```

`tools_catalog[].selected` 表示按当前 `tools.plugin_tools` 配置计算后是否会提供给模型。`blocked_reason` 描述被拦截原因，例如 `plugin_tools 已关闭`、`已加入禁用名单`、`副作用工具未放行`。

#### `POST /api/aiagent-config`

保存 AI Agent 配置。

请求主要字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `enabled` | boolean | 是否启用聊天 Agent |
| `model.base_url` | string | OpenAI-compatible API base URL，不能为空 |
| `model.api_key` | string | 模型 API Key；留空保留现有 Key |
| `model.model` | string | 模型名称，不能为空 |
| `model.temperature` | number | 0-2 |
| `model.top_p` | number | 0-1 |
| `model.max_tokens` | number | 1-32000 |
| `model.timeout_seconds` | number | 5-600 |
| `model.proxy` | string | 请求代理 |
| `persona.skill_path` | string | 必须位于 `BotData/agent_personas` |
| `persona.max_chars` | number | 1000-80000 |
| `persona.include_references` | boolean | 是否读取人格 skill 显式引用 |
| `persona.reference_max_depth` | number | 0-3 |
| `persona.reference_max_files` | number | 0-32 |
| `persona.reference_max_chars_per_file` | number | 1000-80000 |
| `persona.reference_max_total_chars` | number | 1000-160000 |
| `persona.fallback_prompt` | string | 默认人格提示词 |
| `chat.max_user_chars` | number | 1-20000 |
| `chat.max_reply_chars` | number | 100-12000 |
| `chat.max_history_messages` | number | 0-40 |
| `chat.cooldown_seconds` | number | 0-3600 |
| `chat.system_prompt_extra` | string | 附加系统提示词 |
| `memory.enabled` | boolean | 是否启用记忆 |
| `memory.root` | string | 记忆根目录，默认 `UserData/aiagent_memory` |
| `memory.max_read_chars_per_file` | number | 1000-80000 |
| `memory.max_file_chars` | number | 5000-500000 |
| `tools.search` | object | 搜索工具配置 |
| `tools.files` | object | 文件工具配置 |
| `tools.plugin_tools` | object | 插件工具开关和名单 |
| `tools.max_tool_rounds` | number | 0-5 |

`tools.plugin_tools` 字段：

```json
{
  "enabled": true,
  "allow_side_effects": false,
  "enabled_names": [],
  "disabled_names": []
}
```

说明：

- `enabled=false` 时不会向模型提供插件注册的 tools。
- `enabled_names` 非空时进入白名单模式，只启用名单中的 tool。
- `disabled_names` 用于禁用默认启用的 tool。
- `allow_side_effects=false` 时，`readonly=false` 的 tool 即使被选中也不会给模型。

成功响应同 `GET /api/aiagent-config`，并增加：

```json
{
  "message": "AI Agent 设置已保存。"
}
```

### 推送配置

#### `GET /api/push-config`

读取通用推送框架配置和已注册消息源。

响应：

```json
{
  "config": {
    "enabled": true,
    "startup_delay_seconds": 15,
    "check_interval_seconds": 60,
    "send_retry_attempts": 2,
    "send_retry_delay_seconds": 2.0,
    "jobs": []
  },
  "sources": [
    {
      "name": "rss_feed",
      "description": "RSS/Atom 订阅源",
      "default_options": {}
    }
  ],
  "file": {
    "name": "push_framework.json",
    "size": 1234,
    "mtime": 1750000000.0
  }
}
```

#### `POST /api/push-config`

保存通用推送框架配置。

请求：

```json
{
  "enabled": true,
  "startup_delay_seconds": 15,
  "check_interval_seconds": 60,
  "send_retry_attempts": 2,
  "send_retry_delay_seconds": 2.0,
  "jobs": [
    {
      "id": "daily_news",
      "enabled": true,
      "trigger": "schedule",
      "source": "rss_feed",
      "time": "09:00",
      "times": ["09:00"],
      "timezone": "Asia/Shanghai",
      "days": [],
      "late_grace_seconds": 7200,
      "dedupe": "daily",
      "targets": {
        "group_ids": [10000],
        "private_user_ids": []
      },
      "source_options": {}
    }
  ]
}
```

约束：

- `jobs[].id` 只能包含字母、数字、下划线、短横线和点，最长 80。
- `jobs[].trigger` 只能是 `schedule`、`startup`、`shutdown`、`manual`。
- `jobs[].dedupe` 只能是 `daily` 或 `none`。
- 每个任务最多 200 个推送目标。

成功响应同 `GET /api/push-config`，并增加 `message`。

### 立即执行推送任务

#### `POST /api/push-run`

请求：

```json
{
  "job_id": "daily_news",
  "timeout_seconds": 300
}
```

响应：

```json
{
  "result": {
    "job_id": "daily_news",
    "source": "rss_feed",
    "attempted": 1,
    "sent": 1,
    "skipped": 0,
    "empty": false,
    "failed": 0,
    "errors": []
  },
  "message": "推送任务已执行。"
}
```

可能状态码：

- `404`：任务不存在。
- `409`：任务当前状态不允许执行。
- `504`：执行超时。

### RSS 配置

#### `GET /api/rss-config`

读取 RSS 订阅配置。

响应：

```json
{
  "config": {
    "enabled": true,
    "subscriptions": []
  },
  "file": {
    "name": "rss_subscriber.json",
    "size": 1234,
    "mtime": 1750000000.0
  }
}
```

#### `POST /api/rss-config`

保存 RSS 订阅配置。请求体为 `rss_subscriber` 插件配置对象。

成功响应：

```json
{
  "config": {},
  "file": {},
  "message": "RSS 订阅设置已保存。"
}
```

### 权限规则

#### `GET /api/access-rules`

读取后台支持管理的插件权限规则。

当前支持：

| 配置文件 | 后台显示名 |
| --- | --- |
| `media_parser.json` | 聚合媒体解析 |
| `pixiv_parser.json` | Pixiv 解析 |
| `cobalt_parser.json` | Instagram / Facebook 解析 |
| `youtube_downloader.json` | YouTube 下载 |

响应：

```json
{
  "plugins": [
    {
      "name": "media_parser.json",
      "label": "聚合媒体解析",
      "permissions": {
        "admin_id": "",
        "whitelist": {
          "enable": false,
          "user": [],
          "group": []
        },
        "blacklist": {
          "enable": false,
          "user": [],
          "group": []
        }
      },
      "mtime": 1750000000.0
    }
  ]
}
```

#### `POST /api/access-rules`

保存指定插件的 `permissions` 字段。

请求：

```json
{
  "plugin": "media_parser.json",
  "permissions": {
    "admin_id": "",
    "whitelist": {
      "enable": false,
      "user": [],
      "group": []
    },
    "blacklist": {
      "enable": false,
      "user": [],
      "group": []
    }
  }
}
```

响应同 `GET /api/access-rules`，并增加：

```json
{
  "message": "权限规则已保存。"
}
```

### 插件配置文件列表

#### `GET /api/configs`

列出可在线编辑的插件配置文件。

响应：

```json
{
  "files": [
    {
      "name": "aiagent.json",
      "size": 2048,
      "mtime": 1750000000.0
    }
  ],
  "max_edit_bytes": 2097152
}
```

只列出 `BotData/plugin_configs/*.json` 中非 `.example.json` 的文件。

### 读取插件配置文件

#### `GET /api/configs/{name}`

读取单个插件配置文件内容。

响应：

```json
{
  "file": {
    "name": "aiagent.json",
    "size": 2048,
    "mtime": 1750000000.0
  },
  "content": "{\n  \"enabled\": true\n}"
}
```

限制：

- 文件必须在 `BotData/plugin_configs/` 下。
- 文件名必须是 `.json`。
- 文件大小不能超过 `max_edit_bytes`。
- 内容必须是合法 JSON。

### 保存插件配置文件

#### `POST /api/configs/{name}`

请求：

```json
{
  "content": "{\n  \"enabled\": true\n}"
}
```

要求：

- `content` 必须是合法 JSON。
- JSON 顶层必须是对象。
- 写入时会重新格式化为 `ensure_ascii=false, indent=2`。

响应：

```json
{
  "config": {
    "file": {},
    "content": "{}"
  },
  "message": "配置已保存。"
}
```

### 日志文件列表

#### `GET /api/logs`

列出 `BotData/logs/*.log`。

响应：

```json
{
  "files": [
    {
      "name": "2026-07-03.log",
      "size": 123456,
      "mtime": 1750000000.0
    }
  ],
  "max_tail_bytes": 262144
}
```

### 读取日志尾部

#### `GET /api/logs/{name}?max_bytes={bytes}`

读取日志文件尾部内容。

参数：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `max_bytes` | integer | `262144` | 读取尾部最大字节数；最小 1024，最大 262144 |

响应：

```json
{
  "file": {
    "name": "2026-07-03.log",
    "size": 123456,
    "mtime": 1750000000.0
  },
  "truncated": true,
  "content": "日志尾部文本"
}
```

### 系统探针

#### `GET /api/system-probe`

读取主机、CPU、内存、磁盘和当前进程状态。

响应：

```json
{
  "captured_at": 1750000000.0,
  "host": {
    "hostname": "server",
    "platform": "Linux-...",
    "python": "3.12.0"
  },
  "cpu": {
    "count": 4,
    "load_average": [0.1, 0.2, 0.3],
    "percent": 12.3
  },
  "memory": {
    "available": 1024,
    "percent": 52.1,
    "total": 2048,
    "used": 1024
  },
  "disk": {
    "free": 1024,
    "percent": 40.0,
    "total": 2048,
    "used": 1024
  },
  "uptime_seconds": 123456.7,
  "process": {
    "pid": 123,
    "uptime_seconds": 3600.0,
    "rss_bytes": 12345678,
    "thread_count": 24
  }
}
```

部分字段在非 Linux 或 `/proc` 不可用时可能为 `null`。

## 媒体详情 Web API

Base URL 示例：

```text
http://192.168.31.2:53123
```

媒体详情 Web 目前没有独立鉴权。它适合部署在可信局域网或经过反向代理保护的环境。

### 页面

#### `GET /`

返回媒体详情解析页面。

#### `GET /index.html`

等同 `GET /`。

### 支持平台

#### `GET /api/platforms`

读取支持的平台分组和当前自动下载默认值。

响应：

```json
{
  "platform_groups": [
    {
      "name": "聚合媒体解析",
      "platforms": ["Bilibili", "抖音", "TikTok", "快手"]
    },
    {
      "name": "Pixiv",
      "platforms": ["Pixiv artworks"]
    }
  ],
  "auto_download": true
}
```

完整平台组由服务运行时代码返回，当前包括：

- 聚合媒体解析：Bilibili、抖音、TikTok、快手、微博、小红书、闲鱼、今日头条、小黑盒、Twitter/X。
- Pixiv。
- YouTube。
- Cobalt：Instagram、Facebook。

### 解析媒体链接

#### `POST /api/parse`

解析文本中的媒体链接，并按配置决定是否下载媒体。

请求：

```json
{
  "url": "https://example.com/media/link",
  "download": true
}
```

也可以使用 `text` 字段：

```json
{
  "text": "这里有多个链接 https://example.com/a https://example.com/b",
  "download": false
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `url` | string | 二选一 | 单个或包含链接的文本；代码会读取 `url` 或 `text` |
| `text` | string | 二选一 | 和 `url` 等价，`url` 优先 |
| `download` | boolean | 否 | 是否下载媒体；省略时使用 `media_detail_web.auto_download` |

限制来自 `media_detail_web.json`：

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `max_links_per_request` | `8` | 单次最多解析链接数 |
| `operation_timeout_seconds` | `1800` | 整体解析超时 |
| `request_body_limit_bytes` | `1048576` | 请求体上限 |
| `token_ttl_seconds` | `3600` | 返回的媒体 token 有效期 |
| `max_registry_entries` | `512` | 内存 token registry 最大条目 |
| `max_remote_proxy_mb` | `1024` | 远程媒体代理大小限制 |

响应：

```json
{
  "items": [
    {
      "source": "media_parser",
      "platform": "douyin",
      "source_url": "https://example.com/source",
      "title": "标题",
      "author": "作者",
      "description": "描述",
      "timestamp": "发布时间",
      "tags": ["tag"],
      "flags": [],
      "details": [
        {
          "label": "视频数量",
          "value": "1"
        }
      ],
      "summary": {
        "videos": 1,
        "images": 0,
        "downloaded": 1
      },
      "media": [
        {
          "token": "0123456789abcdef0123456789abcdef",
          "kind": "video",
          "label": "视频 1",
          "filename": "media.mp4",
          "content_type": "video/mp4",
          "size_bytes": 123456,
          "mode": "local",
          "preview_url": "/api/media/0123456789abcdef0123456789abcdef",
          "download_url": "/api/media/0123456789abcdef0123456789abcdef?download=1",
          "source_url": "https://example.com/media.mp4"
        }
      ],
      "warnings": [],
      "error": ""
    }
  ],
  "messages": [],
  "download_enabled": true,
  "platform_groups": []
}
```

顶层字段：

| 字段 | 说明 |
| --- | --- |
| `items` | 解析结果数组，每个元素对应一个候选链接或平台结果 |
| `messages` | 全局提示，例如未找到链接、达到解析上限、部分解析器失败 |
| `download_enabled` | 本次请求实际是否启用下载 |
| `platform_groups` | 支持平台分组 |

`items[]` 字段：

| 字段 | 说明 |
| --- | --- |
| `source` | 使用的内部解析来源：`media_parser`、`pixiv_parser`、`youtube_downloader`、`cobalt_parser` |
| `platform` | 平台名 |
| `source_url` | 原始链接 |
| `title` / `author` / `description` / `timestamp` | 元信息 |
| `tags` | 标签，最多保留一部分 |
| `flags` | 限制、封面模式、访问受限、超过大小等标记 |
| `details` | 展示用详情数组，元素为 `{ "label": "...", "value": "..." }` |
| `summary` | 视频数、图片数、可下载/已下载媒体数 |
| `media` | 可预览/可下载媒体数组 |
| `warnings` | 单条结果的警告 |
| `error` | 单条结果错误；空字符串表示该条成功 |

`media[]` 字段：

| 字段 | 说明 |
| --- | --- |
| `token` | 32 位十六进制媒体 token；跳过项没有 token |
| `kind` | `image`、`video`、`audio` 或其他 |
| `label` | 展示标签，例如 `图片 1`、`视频 1`、`封面` |
| `filename` | 下载文件名 |
| `content_type` | MIME 类型 |
| `size_bytes` | 本地文件大小；远程代理项可能为 `null` |
| `mode` | `local`、`remote` 或 `skip` |
| `preview_url` | 同源预览 URL |
| `download_url` | 同源下载 URL |
| `source_url` | 原始媒体 URL |
| `status` | 跳过项为 `skipped` |
| `skip_reason` | 跳过原因 |

空输入响应：

```json
{
  "items": [],
  "messages": ["请输入要解析的 URL。"],
  "download_enabled": true,
  "platform_groups": []
}
```

没有支持链接时：

```json
{
  "items": [],
  "messages": ["没有找到当前机器人媒体解析插件支持的链接。"],
  "download_enabled": true,
  "platform_groups": []
}
```

### 预览或下载解析出的媒体

#### `GET /api/media/{token}`

根据 `POST /api/parse` 返回的 `preview_url` 读取媒体。

参数：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `download` | boolean-like | 空 | `1`、`true`、`yes` 时使用附件下载；否则 inline 预览 |

示例：

```bash
curl -o media.mp4 http://192.168.31.2:53123/api/media/0123456789abcdef0123456789abcdef?download=1
```

响应：

- 本地媒体支持 `Range`，成功时返回 `200` 或 `206`。
- 远程媒体会由服务端代理请求，成功时返回 `200` 或 `206`。
- `Content-Disposition` 根据 `download` 参数在 `inline` 与 `attachment` 间切换。
- token 过期或不存在时返回 `404` JSON 错误。

#### `HEAD /api/media/{token}`

和 `GET /api/media/{token}` 一样解析 token 与响应头，但不返回 body。适合外部程序预检文件类型、大小和 Range 支持。

### 媒体 token 生命周期

媒体 token 保存在内存 registry 中：

- 默认有效期 `token_ttl_seconds=3600` 秒。
- 机器人进程重启后 token 全部失效。
- 超过 `max_registry_entries` 时会清理旧条目。
- 对于本地下载文件，token 指向实际临时文件；文件如果被清理，也会返回 404 或 502。

## 调用示例

### 读取 AI Agent tools 目录

```bash
curl \
  -H "X-Admin-Token: <后台密码>" \
  http://192.168.31.2:54213/api/aiagent-config
```

### 只启用指定 AI tools

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: <后台密码>" \
  -d '{
    "tools": {
      "plugin_tools": {
        "enabled": true,
        "allow_side_effects": false,
        "enabled_names": ["mc_wiki_search", "rss_latest"],
        "disabled_names": []
      }
    }
  }' \
  http://192.168.31.2:54213/api/aiagent-config
```

注意：`POST /api/aiagent-config` 会校验模型 `base_url` 和 `model`。如果当前配置不存在或为空，应同时提交完整 `model` 字段。

### 上传贴纸并轮询任务

```bash
JOB_ID=$(curl -s \
  -H "X-Admin-Token: <后台密码>" \
  -F "new_pack=测试包" \
  -F "keyword=测试" \
  -F "file=@demo.png" \
  http://192.168.31.2:54213/api/uploads | python -c "import json,sys; print(json.load(sys.stdin)['id'])")

curl \
  -H "X-Admin-Token: <后台密码>" \
  "http://192.168.31.2:54213/api/uploads/$JOB_ID"
```

### 解析媒体并下载第一个文件

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/media","download":true}' \
  http://192.168.31.2:53123/api/parse
```

从响应的 `items[0].media[0].download_url` 取出相对路径后：

```bash
curl -L -o media.bin "http://192.168.31.2:53123/api/media/<token>?download=1"
```

## 安全建议

- Bot Admin 暴露的是管理面，包含配置编辑、日志读取、上传、删除和推送触发能力。不要把 `54213` 暴露到公网。
- 如果必须跨公网访问，建议放到反向代理后面，加 HTTPS、IP allowlist 或额外认证。
- 媒体详情 Web 默认无鉴权，解析时可能触发下载和远程代理请求。不要在不可信网络公开 `53123`。
- API token 与后台密码相同，泄露后等同后台登录权限。修改 `bot_admin.json.password` 后，旧 token 立即失效；旧 cookie 会因签名不匹配而失效。
- `GET /api/configs/{name}` 与 `GET /api/logs/{name}` 可能暴露敏感配置或日志内容，应只给可信调用方使用。
