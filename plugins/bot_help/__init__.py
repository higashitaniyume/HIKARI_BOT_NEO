"""机器人帮助信息插件。"""

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent

HELP_TEXT = """HIKARI BOT NEO 帮助

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
- 贴纸包列表：查看贴纸包和关键词
- 统计：查看当前会话统计

贴纸上传页面：
- http://服务器IP:54213/
- 可以新建贴纸包，或上传到已有贴纸包；非 GIF 会先转为 GIF

群聊里查看本帮助：@机器人 帮助
私聊里查看本帮助：帮助"""

help_matcher = on_message(priority=3, block=False)


@help_matcher.handle()
async def handle_help(event: MessageEvent):
    text = event.get_plaintext().strip().lower()
    if text not in {"帮助", "help", "菜单"}:
        return

    if isinstance(event, GroupMessageEvent) and not event.is_tome():
        return

    await help_matcher.finish(Message(HELP_TEXT))
