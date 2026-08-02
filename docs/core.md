# 核心模块

核心模块位于 [`core/`](../core/) 目录，提供机器人底层能力。

## 消息处理流程

```
Message from QQ → NapCat → OneBot V11 WS → NoneBot

  priority=0, block=False → core/command_router.py
    - 显式命令路由，@command() 装饰器注册
    - 创建 CommandContext，匹配成功标记已处理

  priority=1, block=False → core/message_pipeline.py
    - URL/自动解析处理器，register_handler() 注册
    - URLHandler 协议 match + handle
    - 被 command_router 处理的消息跳过

  其余插件 (on_message, priority=...)
    - AI Agent 最低优先级兜底
```

## 模块清单

| 模块 | 职责 |
|------|------|
| [`config_loader.py`](../core/config_loader.py) | 加载主配置 + 插件配置，深合并默认值，mtime/size 热重载 |
| [`command_router.py`](../core/command_router.py) | 显式命令分发，`@command()` 装饰器，priority=0 |
| [`message_pipeline.py`](../core/message_pipeline.py) | URL 自动解析注册器，`register_handler()`，priority=1 |
| [`rendering.py`](../core/rendering.py) | 图片文字渲染，`load_font()` 从配置读取字体链 |
| [`bot_messages.py`](../core/bot_messages.py) | 用户面向回复，`get_message(key)` 从 `bot_messages.json` 读取 |
| [`ai_tool_registry.py`](../core/ai_tool_registry.py) | `register_ai_tool()` 暴露插件函数为 AI Agent 工具 |
| [`access_control.py`](../core/access_control.py) | QQ/群黑白名单检查（用户/群维度独立开关） |
| [`error_notifier.py`](../core/error_notifier.py) | 用户友好错误提示 + 管理员 traceback 通知 |
| [`lifecycle_logging.py`](../core/lifecycle_logging.py) | 启动摘要、插件加载日志、事件描述辅助 |
| [`temp_media_cleaner.py`](../core/temp_media_cleaner.py) | 定时清理临时下载媒体 |
| [`activity_tracker.py`](../core/activity_tracker.py) | 实时活动跟踪，供 Admin 总览页展示 |
| [`stats_tracker.py`](../core/stats_tracker.py) | 会话使用统计 |
| [`bot_identity.py`](../core/bot_identity.py) | 机器人名称/身份，从配置读取 |
| [`resources.py`](../core/resources.py) | 加载/回填 `BotData/resources/` 下的 JSON 资源 |
| [`runtime_info.py`](../core/runtime_info.py) | 运行时长、版本信息（`version.json`） |

## 开发说明

- **插件目录：** 由 `pyproject.toml` 中的 `plugin_dirs = ["plugins"]` 配置。
- **自动解析：** `core.message_pipeline` 注册全局管道，插件通过 `register_handler()` 接入。
- **热重载：** 插件配置修改 JSON 后下条消息即可生效。
- **不提交：** `BotData/config.json`、`BotData/plugin_configs/*.json`、`UserData/stats`、日志和媒体文件。
- **配置文件：** 定义默认值 → `config_loader.load_plugin_config("name", DEFAULT)` 深合并用户 JSON。
- **命令注册：** `@command()` 装饰器，`CommandContext` 提供解析参数和作用域。
- **AI 工具注册：** `@register_ai_tool()`，默认只读，返回 JSON 序列化数据。
- **图片渲染：** 始终使用 `core.rendering.load_font()`，避免固定宽度布局。
- **用户回复：** 通过 `core.bot_messages.get_message()`，不硬编码文本。

## 可热改资源

**目录：** `BotData/resources/`

首次启动时从 `.example.json` 自动生成真实资源文件。修改后不需要重新构建项目镜像；机器人运行中会按文件修改时间重新读取。

### 生成图片字体

**配置文件：** `BotData/resources/rendering.json`

推荐准备两个字体文件：
- 常规字重：`BotData/fonts/MyFont-Regular.ttf`
- 粗体字重：`BotData/fonts/MyFont-Bold.ttf`

```json
{
  "font_regular": "BotData/fonts/MyFont-Regular.ttf",
  "font_bold": "BotData/fonts/MyFont-Bold.ttf",
  "fallback_fonts_regular": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"],
  "fallback_fonts_bold": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"]
}
```

如果不放自定义字体，运行容器会安装 `fonts-noto-cjk` 做 fallback。

### 机器人固定回复

**配置文件：** `BotData/resources/bot_messages.json`

常见的固定回复已抽到该 JSON（错误提示、JMComic、Pixiv/Cobalt 部分错误、贴纸命令提示等）。修改后下一次发送对应消息时会读取新内容。
