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


def _safe_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _short_reason(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text[:160]
