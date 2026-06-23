"""机器人帮助信息插件。"""

from nonebot.adapters.onebot.v11 import Message

from core.command_router import CommandContext, command, format_command_help

HELP_TEXT = """HIKARI BOT 帮助

媒体解析：
- 直接发送 Pixiv 作品链接：解析并发送图片
- 直接发送 Instagram / Facebook 链接：解析并发送媒体
- 直接发送 Telegram 贴纸包链接：统一转 GIF 并发送
  可选参数：zip / refresh / nosave / name=关键词

JMComic：
- 私聊发送：jm 123456
- 群聊不解析 JM

贴纸：
- 发送贴纸关键词：随机发送一张贴纸
- 关键词 10：随机发送 10 张
- 随机贴纸：从所有贴纸包随机发送
- 拼图 关键词：生成贴纸包预览图
- 贴纸包统计：查看贴纸库总数
- 贴纸包列表：分页查看贴纸包和关键词
- 贴纸包列表 全部：合并转发完整贴纸包列表
- 统计：查看当前会话统计

贴纸上传页面：
- https://stickers-hikari.vlnc.top/
- 可以新建贴纸包，或上传到已有贴纸包；非 GIF 会先转为 GIF

群聊里查看本帮助：@机器人 帮助
私聊里查看本帮助：帮助"""


@command("帮助", aliases=("/help", "help", "菜单"), description="查看帮助", require_tome=True)
async def handle_help(ctx: CommandContext) -> None:
    command_help = format_command_help()
    suffix = f"\n\n已注册命令：\n{command_help}" if command_help else ""
    await ctx.send(Message(f"{HELP_TEXT}{suffix}"))
