# 可热改资源

**目录：** `BotData/resources/`

首次启动时从 `.example.json` 自动生成真实资源文件。修改后不需要重新构建项目镜像；机器人运行中会按文件修改时间重新读取。

## 生成图片字体

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

## 机器人固定回复

**配置文件：** `BotData/resources/bot_messages.json`

常见的固定回复已抽到该 JSON（错误提示、JMComic、Pixiv/Cobalt 部分错误、贴纸命令提示等）。修改后下一次发送对应消息时会读取新内容。
