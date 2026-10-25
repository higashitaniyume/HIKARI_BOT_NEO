"""说说/动态数据解析。"""
from __future__ import annotations

import re
from typing import Any, Dict

from ..constants import APP_TYPES


def hex_decode(s: str) -> str:
    """解码 \\xNN 转义序列。"""
    return re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), s)


def parse_feed_item(item_raw: str) -> dict:
    """从单条 feed 的原始文本中提取元数据。"""
    def get(field: str) -> str:
        m = re.search(rf"{field}:\s*'([^']*)'", item_raw)
        if not m:
            m = re.search(rf"{field}:\s*(\d+)", item_raw)
        return m.group(1) if m else ""

    feed: Dict[str, Any] = {
        "key": get("key"),
        "appid": get("appid"),
        "appid_name": APP_TYPES.get(get("appid"), f"未知({get('appid')})"),
        "typeid": get("typeid"),
        "opuin": get("opuin"),
        "uin": get("uin"),
        "nickname": get("nickname"),
        "remark": get("remark"),
        "abstime": get("abstime"),
        "feedstime": get("feedstime").strip(),
        "scope": get("scope"),
        "cmtnum": 0,
        "likecount": 0,
        "content": "",
        "images": [],
    }

    cmt_m = re.search(r"cmtnum:'?(\d+)'?", item_raw)
    if cmt_m:
        feed["cmtnum"] = int(cmt_m.group(1))

    like_m = re.search(r"likecount:'?(\d+)'?", item_raw)
    if like_m:
        feed["likecount"] = int(like_m.group(1))

    html_m = re.search(r"html:'(.*?)'(?:,|\s*\})", item_raw, re.DOTALL)
    if html_m:
        html_content = hex_decode(html_m.group(1))
        html_content = html_content.replace('\\"', '"').replace("\\/", "/")

        infos = re.findall(
            r'class="f-info[^"]*"[^>]*>(.*?)</div>', html_content, re.DOTALL
        )
        texts = []
        for info in infos:
            clean = re.sub(r"<[^>]+>", "", info).strip()
            if clean:
                texts.append(clean)
        feed["content"] = "\n".join(texts)

        imgs = re.findall(r'data-originurl="([^"]*)"', html_content)
        if not imgs:
            imgs = re.findall(
                r'src="(https?://[^"]*(?:photo|pic|qlogo)[^"]*)"', html_content
            )
        feed["images"] = [u for u in imgs if "qlogo" not in u and u.strip()]

    return feed


def parse_mood(msg: dict) -> dict:
    """将单条说说原始数据整理为结构化字段。"""
    item = {
        "tid": msg.get("tid", ""),
        "content": msg.get("content", ""),
        "created_time": msg.get("created_time", 0),
        "createTime": msg.get("createTime", ""),
        "cmtnum": msg.get("cmtnum", 0),
        "fwdnum": msg.get("fwdnum", 0),
        "name": msg.get("name", ""),
        "uin": msg.get("uin", ""),
        "lbs": msg.get("lbs", {}),
        "pic": [],
        "rt_tid": "",
        "rt_uin": "",
        "rt_content": "",
    }
    for p in msg.get("pic", []):
        item["pic"].append({
            "url": p.get("url1", ""),
            "smallurl": p.get("smallurl", ""),
            "width": p.get("width", 0),
            "height": p.get("height", 0),
        })
    if msg.get("rt_con"):
        item["rt_tid"] = msg.get("rt_tid", "")
        item["rt_uin"] = msg.get("rt_uin", "")
        item["rt_content"] = msg.get("rt_con", {}).get("content", "")
    return item
