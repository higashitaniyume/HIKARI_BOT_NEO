from __future__ import annotations

from typing import Any

from core.resources import load_json_resource

DEFAULT_MESSAGES: dict[str, Any] = {
    "error": {
        "user": "处理失败，请稍后再试。",
    },
    "common": {
        "none_stats": "暂无统计数据。",
    },
    "sticker": {
        "empty_library": "贴纸包都是空的，请先添加一些表情包。",
        "collage_usage": "用法：拼图 <关键词>",
        "empty_pack": "贴纸包 {pack} 是空的。",
        "collage_progress": "正在拼图 {pack}（{count} 张）...",
        "collage_failed": "拼图失败，请稍后再试。",
        "collage_send_failed": "拼图已经生成，但发送图片超时了。可以稍后重试，或检查 NapCat/QQ 是否卡住。",
        "pack_list_forward_failed": "完整列表发送失败，请使用「贴纸包列表 <页码>」分页查看。",
        "pack_list_usage": "用法：贴纸包列表、贴纸包列表 <页码>、贴纸包列表 全部",
        "pack_list_page_out_of_range": "页码超出范围，目前共有 {total_pages} 页。",
        "no_packs": "暂无贴纸包。",
        "pack_preview_progress": "正在生成贴纸包预览图...",
        "pack_preview_failed": "贴纸包预览生成失败，请稍后再试。",
        "count_min": "数量至少为 1。",
    },
    "tg_sticker": {
        "no_gif": "没有成功转换出可发送的 GIF。",
        "empty_pack": "这个贴纸包里没有可处理的贴纸。",
        "detected": "检测到 Telegram 贴纸包：{title}\n共 {count} 个贴纸，开始处理……",
    },
    "jmcomic": {
        "start": "开始下载并转换 PDF：JM{jm_id}",
        "done": "完成：JM{album_id}",
        "upload_failed": "JM解析完成，但 PDF 上传失败，请稍后再试。",
        "failed": "下载/转换 PDF 失败，请稍后再试。",
    },
    "bot_help": {
        "not_found": "没有找到命令：{command}\n发送「帮助 命令」查看命令列表。",
    },
    "pixiv": {
        "r18_blocked": "Pixiv 作品 {illust_id} 被标记为 R-18/R-18G，当前配置不允许发送。",
        "no_images": "Pixiv 作品 {illust_id} 没有可发送的图片。",
        "download_failed": "Pixiv 作品 {illust_id} 下载失败，没有可发送图片。",
    },
    "cobalt": {
        "download_failed": "媒体下载失败，请稍后再试。",
    },
    "tts": {
        "usage": "用法：说话 <文本>",
        "disabled": "语音合成功能当前已关闭。",
        "too_long": "文本太长啦，最多 {max_chars} 个字符。",
        "cooldown": "稍等 {seconds} 秒再让我说话吧。",
        "failed": "语音生成失败，请稍后再试。",
    },
}


def _lookup(data: dict[str, Any], key: str) -> Any:
    current: Any = data
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def get_message(key: str, **kwargs: Any) -> str:
    data = load_json_resource("bot_messages.json", DEFAULT_MESSAGES)
    value = _lookup(data, key)
    if value is None:
        value = _lookup(DEFAULT_MESSAGES, key)
    if value is None:
        value = key

    text = str(value)
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except Exception:
        return text
