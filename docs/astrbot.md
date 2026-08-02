# AstrBot 插件兼容层

**插件目录：** [`plugins/astrbot_compat/`](../plugins/astrbot_compat/)

HIKARI BOT NEO 提供了一层 AstrBot 插件兼容适配器，让社区开发的 AstrBot 插件可以直接在机器人上运行。适配器通过 Shim（胶水层）模拟 AstrBot 的核心 API，将插件注册的命令、正则表达式和消息处理器桥接到机器人的 `command_router` 和 NoneBot 事件系统。

## 概述

```text
AstrBot 插件 (main.py)
  ↓ 调用 AstrBot API
Shim 层 (astrbot.api.*)
  ↓ 转为内部调用
兼容层 (plugins/astrbot_compat/)
  ├─ Loader  — 动态导入插件、扫描 Star 子类、注册处理器
  ├─ Manager — /astrbot 命令管理、生命周期
  ├─ Config  — _conf_schema.json → 配置持久化
  └─ Venv    — 公共虚拟环境隔离依赖
  ↓ 注册到
command_router / NoneBot matcher
```

插件上传到 `UserData/astrbot_plugins/` 后被自动发现，或通过 `/astrbot load` 命令 / Web 面板手动加载。

## 支持的 AstrBot API

| API | 支持情况 | 备注 |
|-----|----------|------|
| `Star` 基类 + `PluginKVStoreMixin` | ✅ | 文件 JSON 持久化 KV 存储 |
| `@register(name, author, desc, version)` | ✅ | 设置插件元数据 |
| `@filter.command(name, alias)` | ✅ | 含参数自动解析（int/float/bool/GreedyStr） |
| `@filter.regex(pattern)` | ✅ | 匹配组注入 handler **kwargs |
| `@filter.on_message()` | ✅ | 所有消息处理器 |
| `@filter.command_group()` | ✅ | 子命令分组 |
| `@filter.permission()` / `@filter.event_message_type()` | ✅ | 作用域和权限过滤 |
| `AstrMessageEvent` | ✅ | 包装 OneBot V11 MessageEvent |
| `event.plain_result()` / `image_result()` / `chain_result()` | ✅ | 回复构建 |
| `MessageChain` / `MessageEventResult` | ✅ | 链式构建 + 传播控制 |
| 消息组件（Plain, Image, At, Reply, Share, Record, Video 等） | ✅ | 自动转为 OneBot MessageSegment |
| `AstrBotConfig` | ✅ | 字典式 JSON 配置 + 自动写盘 |
| `text_to_image()` / `html_render()` | ✅ | 委托 `core.rendering` |
| `Context.get_config()` | ✅ | 返回当前插件配置 |
| `Context.send_message()` | ✅ | 按 session 发送消息 |
| `Context.llm_generate()` / `tool_loop_agent()` | ✅ | 桥接到内置 AI Agent |
| `Context.get_all_stars()` / `get_registered_star()` | ✅ | 插件信息查询 |
| `initialize()` / `terminate()` 生命周期 | ✅ | 加载 / 卸载时自动调用 |
| `metadata.yaml` | ✅ | name/version/author/tags/repo 元数据 |
| `_conf_schema.json` | ✅ | 自动生成默认配置 + Web 表单 |
| `requirements.txt` | ✅ | 自动安装到公共 venv |
| `Context.llm.*`（具体 LLM 调用） | ✅ | 复用 bot 的 AI Agent 配置 |
| `Context.get_db()` | ❌ | 无对应键值/向量存储抽象 |
| `@register_platform_adapter` | ❌ | 工作量过大（相当于半个 bot） |
| Plugin Pages (WebUI) | ⚠️ | 基础支持（通过 werkzeug.routing 动态路由），JS bridge 待完善 |
| 沙箱隔离 | ❌ | v1 暂不支持 |

## 架构

插件的消息处理流程在 NoneBot 优先级中的位置：

```text
Message from QQ → NapCat → OneBot V11 WS → NoneBot

  priority=0, block=False → core/command_router.py
    ├── 原生命令 (@command())
    └── AstrBot 命令 (@filter.command) — 由 Loader 注册进来

  priority=1, block=False → core/message_pipeline.py
    └── URL 自动解析 (register_handler)

  priority=2, block=False → astrbot_compat matcher
    ├── @filter.regex 匹配 → dispatch_regex_command()
    └── @filter.on_message → dispatch_on_message()

  更低优先级 → 其他插件（sticker_trigger, voice_trigger, aiagent...）
```

命令执行流程：

```text
用户发送 /trending
  ↓
command_router 匹配 trending 命令
  ↓
Loader 的 _wrapped_handler 桥接
  ↓
_create_astr_event() → 包装为 AstrMessageEvent
  ↓
_run_generator() → 消费 async generator
  ↓
每 yield 一个 MessageEventResult
  ↓
_send_result() → convert_chain_to_onebot()
  → MessageSegment 发送
```

## 加载插件

**方式一：上传压缩包（Web 面板）**

在 Bot 后台的"加载新插件"区域，选择 `.zip` 文件并上传。上传后自动解压、安装依赖并加载。

**方式二：服务器路径（命令）**

```text
/astrbot load /path/to/plugin.zip
/astrbot load /path/to/plugin_dir
/astrbot load BotData/uploads/my_plugin.zip  my-plugin-name
```

**方式三：自动发现**

重启机器人时，`UserData/astrbot_plugins/` 下有 `main.py` 的目录会被自动加载。

**管理命令（仅超级管理员私聊）：**

| 命令 | 效果 |
|------|------|
| `/astrbot list` | 列出已加载的插件 |
| `/astrbot load <路径> [插件名]` | 从目录或 zip 加载 |
| `/astrbot remove <插件名>` | 卸载插件 |
| `/astrbot reload <插件名>` | 重新加载插件 |
| `/astrbot info <插件名>` | 查看插件详情和配置 |
| `/astrbot rebuild-env` | 重建公共虚拟环境 |

## 依赖管理

每个插件目录下的 `requirements.txt` 会在加载时被读取。依赖安装到独立的公共虚拟环境（`UserData/astrbot_plugins/.venv/`），与机器人主环境隔离，避免污染 `uv.lock`。

```text
UserData/astrbot_plugins/
├── .venv/                  ← 公共插件 venv
│   └── Lib/site-packages/  ← 依赖安装到这里
├── plugin_A/
│   ├── main.py
│   └── requirements.txt
└── plugin_B/
    ├── main.py
    └── requirements.txt
```

**重建环境：** 移除插件后，残留依赖通过 `/astrbot rebuild-env` 命令一键重建（所有插件依赖从零安装）。

## 配置

插件通过 `_conf_schema.json` 声明配置结构和默认值。加载后配置保存到 `UserData/astrbot_plugins/<name>/config.json`。

```json
{
  "api_key": { "description": "API 密钥", "type": "string" },
  "max_results": { "description": "最大结果数", "type": "int", "default": 10 },
  "debug": { "description": "调试模式", "type": "bool", "default": false }
}
```

在 Web 面板中，这些配置项会自动渲染为表单（文本/数字/开关/JSON 编辑器），保存后立即生效。

## Web 管理

Bot 后台（`:54213`）左侧增加「AstrBot」导航。功能包括：

- **插件列表** — 显示所有已加载和发现的插件，含状态（✅ 已加载 / ⏹️ 未加载）
- **插件详情** — 点击后显示作者、版本、描述、仓库地址、注册命令、依赖
- **配置表单** — 按 `_conf_schema.json` 自动生成（支持 string/int/float/bool/list/object）
- **操作按钮** — 加载、重载、卸载
- **上传插件** — 直接上传 zip 压缩包
- **路径加载** — 输入服务器本地路径
- **环境管理** — 一键重建公共虚拟环境

## 限制

| 限制 | 说明 |
|------|------|
| `Context.get_db()` | 无对应存储抽象，始终抛 NotImplementedError |
| 平台适配器 | `@register_platform_adapter` 未实现（工作量过大） |
| 插件 WebUI | Plugin Pages 基础支持（`register_web_api()` 接入 bot_admin 路由），JS bridge 待完善 |
| 沙箱隔离 | 插件代码与机器人进程相同权限，加载前请确认来源可信 |
| 超大消息 | 渲染图片超过 ~900 KB 时自动保存到 `sharedFolder/astrbot_temp/` 后引用 |
| LLM 工具注册 | `Context.register_llm_tool()` 仅做日志记录，不影响内置 AI Agent 的工具集 |

插件加载后可通过 `/astrbot list` 确认状态，`/astrbot info <名>` 查看详细信息。若加载失败，检查 Bot 日志中 `AstrBotCompat.*` 的报错。
