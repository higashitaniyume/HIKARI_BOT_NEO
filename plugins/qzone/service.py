"""QQ 空间服务层。

登录态不落盘：每次操作前用 OneBot `get_cookies`（domain=user.qzone.qq.com）
向 NapCat 取一次当前 QQ 的 Cookie，再由 qzone_sdk 计算 g_tk 并发起 CGI 请求。

读写分工：
- 发布 / 删除 / 好友动态 / 指定好友说说 走 `third_party/qzone_sdk`；
- 点赞 / 评论 需要的 unikey、curkey、abstime 等参数 SDK 没有暴露，这里按
  社区已验证的参数直接 POST `internal_dolike_app` / `emotion_cgi_re_feeds`。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from third_party.qzone_sdk import ManualCookieProvider, QZoneClient, QZoneConfig
from third_party.qzone_sdk.auth.base import auth_from_cookies
from third_party.qzone_sdk.constants import UA
from third_party.qzone_sdk.errors import QZoneApiError, QZoneAuthError, QZoneNetworkError

logger = logging.getLogger("HikariBot.QZone")

COOKIE_DOMAIN = "user.qzone.qq.com"
DEFAULT_APPID = 311

_WHITESPACE_RE = re.compile(r"\s+")
# feeds3 的 HTML 里换行/制表符是二次转义的（字面量 "\t"），直接发出去会很难看
_ESCAPED_WHITESPACE_RE = re.compile(r"\\[trn]")
# 空间自己的表情标记：[em]e400867[/em]
_QZONE_EMOJI_RE = re.compile(r"\[em\]e\d+\[/em\]")
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff"), None)

_LIKE_URL = "https://user.qzone.qq.com/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app"
_COMMENT_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
_MOOD_DETAIL_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msgdetail_v6"
_MENTION_RE = re.compile(r"@\{uin:\d+,[^}]*\}")


class QzoneLoginError(RuntimeError):
    """拿不到可用登录态（NapCat 未登录 / Cookie 缺 p_skey）。"""


class QzoneActionError(RuntimeError):
    """空间接口拒绝了这次操作。"""

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


@dataclass(frozen=True, slots=True)
class QzoneAuth:
    cookies: str
    uin: str
    g_tk: int
    p_skey: str


@dataclass(frozen=True, slots=True)
class FeedItem:
    """一条好友动态（feeds3）。"""

    tid: str
    uin: str
    nickname: str
    content: str
    image_count: int
    abstime: int
    appid: int
    typeid: int
    unikey: str
    curkey: str

    @property
    def mood_url(self) -> str:
        return f"https://user.qzone.qq.com/{self.uin}/mood/{self.tid}"


@dataclass(frozen=True, slots=True)
class MoodItem:
    """指定好友的一条说说。"""

    tid: str
    uin: str
    name: str
    content: str
    created_at: str
    comment_count: int


@dataclass(frozen=True, slots=True)
class CommentItem:
    """一条说说下的评论。"""

    comment_id: str
    uin: str
    nickname: str
    content: str


# ── 登录态 ────────────────────────────────────────────────


async def fetch_auth(bot: Any) -> QzoneAuth:
    """从 NapCat 取当前登录 QQ 的空间 Cookie。"""
    uin = str(getattr(bot, "self_id", "") or "").strip()
    if not uin.isdigit():
        raise QzoneLoginError("无法确定机器人当前登录的 QQ 号")

    try:
        data = await bot.call_api("get_cookies", domain=COOKIE_DOMAIN)
    except Exception as exc:  # ActionFailed / 网络异常
        raise QzoneLoginError(f"NapCat 未返回空间 Cookie：{_short_reason(exc)}") from exc

    cookies = _extract_cookies(data)
    if not cookies:
        raise QzoneLoginError("NapCat 返回的空间 Cookie 为空，请确认 QQ 已登录并打开过空间")

    try:
        info = auth_from_cookies(cookies, uin)
    except QZoneAuthError as exc:
        raise QzoneLoginError(f"空间 Cookie 不可用：{_short_reason(exc)}") from exc

    return QzoneAuth(cookies=cookies, uin=uin, g_tk=info.g_tk, p_skey=info.p_skey)


def _extract_cookies(data: Any) -> str:
    if isinstance(data, dict):
        value = data.get("cookies")
        return value.strip() if isinstance(value, str) else ""
    if isinstance(data, str):
        return data.strip()
    return ""


# ── SDK 调用（同步 HTTP 放线程里跑） ────────────────────────


def _build_client(auth: QzoneAuth, timeout: int) -> QZoneClient:
    return QZoneClient(
        QZoneConfig(
            auth_provider=ManualCookieProvider(auth.cookies, auth.uin),
            timeout=timeout,
        )
    )


async def _run_sdk(factory: Any) -> Any:
    """在独立线程里跑 SDK 的异步方法，避免 urllib 阻塞事件循环。"""

    def runner() -> Any:
        return asyncio.run(factory())

    try:
        return await asyncio.to_thread(runner)
    except (QZoneAuthError, QZoneNetworkError, QZoneApiError) as exc:
        raise QzoneActionError(_short_reason(exc)) from exc
    except OSError as exc:
        raise QzoneActionError(f"网络请求失败：{_short_reason(exc)}") from exc


# ── 说说 ──────────────────────────────────────────────────


async def publish(
    bot: Any,
    content: str,
    *,
    visible: int = 1,
    timeout: int = 20,
) -> dict[str, Any]:
    """发布一条纯文本说说，返回 {"tid": ..., "curkey": ...}。"""
    auth = await fetch_auth(bot)
    client = _build_client(auth, timeout)
    result = await _run_sdk(lambda: client.publish(content, visible=visible))
    if not isinstance(result, dict) or not result.get("success"):
        message = ""
        if isinstance(result, dict):
            message = str(result.get("message") or result.get("code") or "")
        raise QzoneActionError(message or "发布说说失败")
    return {
        "tid": str(result.get("tid") or ""),
        "curkey": str(result.get("curkey") or ""),
    }


async def delete(bot: Any, tid: str, *, curkey: str = "", timeout: int = 20) -> None:
    """删除一条说说。"""
    auth = await fetch_auth(bot)
    client = _build_client(auth, timeout)
    result = await _run_sdk(lambda: client.delete(tid, curkey))
    if not isinstance(result, dict) or not result.get("success"):
        message = ""
        if isinstance(result, dict):
            message = str(result.get("message") or result.get("code") or "")
        raise QzoneActionError(message or "删除说说失败")


async def fetch_feed(bot: Any, count: int = 8, *, timeout: int = 20) -> list[FeedItem]:
    """拉取好友动态（feeds3），自动过滤广告与空内容。"""
    auth = await fetch_auth(bot)
    client = _build_client(auth, timeout)
    raw_items = await _run_sdk(lambda: client.get_feed_list(1, count))
    if not isinstance(raw_items, list):
        return []
    items: list[FeedItem] = []
    for raw in raw_items:
        item = _feed_item_from_sdk(raw)
        if item is not None:
            items.append(item)
    return items


def _feed_item_from_sdk(raw: Any) -> FeedItem | None:
    if not isinstance(raw, dict):
        return None
    tid = str(raw.get("key") or "").strip()
    uin = str(raw.get("uin") or raw.get("opuin") or "").strip()
    if not tid or not uin or not uin.isdigit():
        return None
    if tid.startswith("advertisement"):
        return None
    appid = _safe_int(raw.get("appid"))
    if appid == 6600:  # 广告
        return None
    content = _clean_text(raw.get("content"))
    images = raw.get("images") if isinstance(raw.get("images"), list) else []
    if not content and not images:
        return None
    unikey = f"http://user.qzone.qq.com/{uin}/mood/{tid}"
    return FeedItem(
        tid=tid,
        uin=uin,
        nickname=_clean_text(raw.get("nickname")) or uin,
        content=content,
        image_count=len(images),
        abstime=_safe_int(raw.get("abstime")),
        appid=appid or DEFAULT_APPID,
        typeid=_safe_int(raw.get("typeid")),
        unikey=unikey,
        curkey=unikey,
    )


async def fetch_moods(
    bot: Any,
    target_qq: str,
    count: int = 5,
    *,
    timeout: int = 20,
) -> list[MoodItem]:
    """拉取指定好友（或自己）的说说列表。"""
    auth = await fetch_auth(bot)
    client = _build_client(auth, timeout)
    raw_items = await _run_sdk(lambda: client.get_friend_moods(target_qq, count))
    if not isinstance(raw_items, list):
        return []
    items: list[MoodItem] = []
    for raw in raw_items:
        item = _mood_item_from_sdk(raw)
        if item is not None:
            items.append(item)
    return items


def _mood_item_from_sdk(raw: Any) -> MoodItem | None:
    if not isinstance(raw, dict):
        return None
    tid = str(raw.get("tid") or "").strip()
    if not tid:
        return None
    uin = str(raw.get("uin") or "").strip()
    nickname = _clean_text(raw.get("name")) or uin
    content = _clean_text(raw.get("content"))
    if not content:
        forwarded = _clean_text(raw.get("rt_content"))
        content = f"转发：{forwarded}" if forwarded else "[无正文]"
    return MoodItem(
        tid=tid,
        uin=uin,
        name=nickname,
        content=content,
        created_at=_clean_text(raw.get("createTime")),
        comment_count=_safe_int(raw.get("cmtnum")),
    )


# ── 互动（点赞 / 评论） ────────────────────────────────────


async def like(bot: Any, item: FeedItem, *, timeout: int = 20) -> None:
    """给一条好友动态点赞。"""
    auth = await fetch_auth(bot)
    payload = await _post_cgi(
        _LIKE_URL,
        {
            "qzreferrer": f"https://user.qzone.qq.com/{auth.uin}",
            "opuin": auth.uin,
            "unikey": item.unikey,
            "curkey": item.curkey,
            "appid": str(item.appid),
            "typeid": str(item.typeid),
            "fid": item.tid,
            "from": "1",
            "active": "0",
            "fupdate": "1",
            "abstime": str(item.abstime),
            "format": "json",
        },
        auth,
        timeout=timeout,
    )
    _ensure_ok(payload, "点赞失败")


async def comment(bot: Any, item: FeedItem, content: str, *, timeout: int = 20) -> None:
    """评论一条好友动态。"""
    auth = await fetch_auth(bot)
    payload = await _post_cgi(
        _COMMENT_URL,
        {
            "hostUin": item.uin,
            "topicId": f"{item.uin}_{item.tid}",
            "content": content,
            "format": "json",
            "qzreferrer": f"https://user.qzone.qq.com/{auth.uin}",
        },
        auth,
        timeout=timeout,
    )
    _ensure_ok(payload, "评论失败")


async def fetch_comments(
    bot: Any,
    post_uin: str,
    tid: str,
    *,
    num: int = 20,
    timeout: int = 20,
) -> list[CommentItem]:
    """读取某条说说（自己的或好友的）下的评论。"""
    auth = await fetch_auth(bot)
    params = {
        "uin": str(post_uin),
        "tid": str(tid),
        "num": str(max(1, min(int(num), 50))),
        "pos": "0",
        "g_tk": str(auth.g_tk),
        "format": "jsonp",
        "not_trunc_con": "1",
        "need_private_comment": "1",
        "callback": "_preloadCallback",
    }
    headers = {
        "Cookie": auth.cookies,
        "User-Agent": UA,
        "Referer": f"https://user.qzone.qq.com/{auth.uin}",
    }
    payload = await _get_payload(_MOOD_DETAIL_URL, params, headers, timeout=timeout)
    code = payload.get("code")
    if str(code).strip() != "0":
        message = str(payload.get("message") or "").strip()
        raise QzoneActionError(message or f"读取评论失败（code={code}）")

    raw_items = payload.get("commentlist")
    items: list[CommentItem] = []
    for raw in raw_items if isinstance(raw_items, list) else []:
        item = _comment_item_from_raw(raw)
        if item is not None:
            items.append(item)
    return items


def _comment_item_from_raw(raw: Any) -> CommentItem | None:
    if not isinstance(raw, dict):
        return None
    comment_id = str(raw.get("tid") or raw.get("commentid") or "").strip()
    uin = str(raw.get("uin") or "").strip()
    if not comment_id or not uin.isdigit():
        return None
    return CommentItem(
        comment_id=comment_id,
        uin=uin,
        nickname=_clean_text(raw.get("name")) or uin,
        content=_comment_text(raw.get("content")),
    )


async def reply_comment(
    bot: Any,
    post_uin: str,
    tid: str,
    comment_item: CommentItem,
    content: str,
    *,
    timeout: int = 20,
) -> None:
    """回复某条说说下的一条评论（带 @ 提及，与空间网页行为一致）。"""
    auth = await fetch_auth(bot)
    text = str(content or "").strip()
    if not text:
        raise QzoneActionError("回复内容不能为空。")

    nickname = re.sub(r"[{},]", "", comment_item.nickname or comment_item.uin)[:40]
    mention = f"@{{uin:{comment_item.uin},nick:{nickname},auto:1}} "
    final_content = text if text.startswith("@{") else mention + text

    payload = await _post_cgi(
        _COMMENT_URL,
        {
            "hostUin": str(post_uin),
            "topicId": f"{post_uin}_{tid}",
            "content": final_content,
            "format": "json",
            "qzreferrer": f"https://user.qzone.qq.com/{auth.uin}",
            "commentId": comment_item.comment_id,
            "replyUin": comment_item.uin,
            "commentUin": comment_item.uin,
        },
        auth,
        timeout=timeout,
    )
    _ensure_ok(payload, "回复评论失败")


async def _post_cgi(
    url: str,
    data: dict[str, str],
    auth: QzoneAuth,
    *,
    timeout: int,
) -> dict[str, Any]:
    headers = {
        "Cookie": auth.cookies,
        "User-Agent": UA,
        "Referer": f"https://user.qzone.qq.com/{auth.uin}",
        "Origin": "https://user.qzone.qq.com",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.post(f"{url}?g_tk={auth.g_tk}", data=data, headers=headers)
    except httpx.HTTPError as exc:
        raise QzoneActionError(f"网络请求失败：{_short_reason(exc)}") from exc

    payload = _parse_payload(response.text)
    if payload is None:
        raise QzoneActionError("空间接口返回了无法解析的内容", detail=response.text[:200])
    return payload


async def _get_payload(
    url: str,
    params: dict[str, str],
    headers: dict[str, str],
    *,
    timeout: int,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url, params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise QzoneActionError(f"网络请求失败：{_short_reason(exc)}") from exc

    payload = _parse_payload(response.text)
    if payload is None:
        raise QzoneActionError("空间返回了无法解析的数据", detail=response.text[:200])
    return payload


def _parse_payload(text: str) -> dict[str, Any] | None:
    body = (text or "").strip()
    if not body:
        return None
    try:
        parsed = json.loads(body)
    except ValueError:
        start, end = body.find("{"), body.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(body[start : end + 1])
        except ValueError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _ensure_ok(payload: dict[str, Any], fallback: str) -> None:
    code = payload.get("code", payload.get("ret"))
    if str(code).strip() == "0":
        return
    message = str(payload.get("message") or payload.get("msg") or "").strip()
    if not message:
        message = f"{fallback}（code={code}）" if code is not None else fallback
    raise QzoneActionError(message)


# ── 工具函数 ──────────────────────────────────────────────


def _clean_text(value: Any) -> str:
    text = str(value or "").translate(_ZERO_WIDTH)
    text = _ESCAPED_WHITESPACE_RE.sub(" ", text)
    text = _QZONE_EMOJI_RE.sub("[表情]", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _comment_text(value: Any) -> str:
    """评论正文：去掉回复里的 @{uin:...} 提及，再统一清洗。"""
    return _clean_text(_MENTION_RE.sub("", str(value or "")))


def _safe_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _short_reason(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text[:160]
