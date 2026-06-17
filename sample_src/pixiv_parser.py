import asyncio
import os
import re
import time
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from nonebot import logger, on_command, on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.params import CommandArg
from pydantic import BaseModel


# =========================
# Pixiv 配置
# =========================


class PixivConfig(BaseModel):
    pixiv_cookie: str = ""
    pixiv_auto_parse: bool = True
    pixiv_max_send: int = 6
    pixiv_max_file_mb: int = 25
    pixiv_allow_r18: bool = False
    pixiv_cache_dir: str = "data/pixiv_cache"
    pixiv_cache_ttl_hours: int = 24
    pixiv_proxy: str = ""


config = PixivConfig(
    # Pixiv Cookie，建议至少包含 PHPSESSID
    pixiv_cookie="first_visit_datetime_pc=2026-02-17%2021%3A46%3A46; p_ab_id=2; p_ab_id_2=4; p_ab_d_id=69864781; _ga=GA1.1.1120467697.1771332407; yuid_b=UCOBVSA; privacy_policy_agreement=7; _ga_MZ1NL4PHH0=GS2.1.s1776838678$o3$g1$t1776838841$j60$l0$h0; PHPSESSID=118333068_NkGzpwZp7QaEDU42dpApK8ftvATZS0Cy; privacy_policy_notification=0; a_type=0; b_type=2; c_type=21; cf_clearance=Ky8OdcLw6atE4yCM2DXQ4Y0HgTWH1n5pX.mCHR85MXk-1781572750-1.2.1.1-tjDpEMzjND9cRqJJMG6WhjbCAOBPa6xDU_dJ2kT2vO30SN77s.2VXHjg2JbK.6x05xMUbZUOB1Fv02NdGz9j5NXlb.DYAfxZlGdSRaWH0uYqvCQL2pCnAfqH7sU1ogdPP.tISceqpWruRcLujTqg14oRbp6nv2Rhp1Bwukh3XhmIpOUkSIuJxXo1PvzskfmsLr30ox7W_gatEhHBy5bkNVZAHcJNZHhnXBCDuvyVs5H2p9XLjGlXtjZvmjJRVJDBVg3hELYr9WDnoIRjuxNUwznV7idij4x1NFZMGAYD3xv9tWH3AEzk0eQQ1GWomJwTe90fH.9PqEMHo0YYO4OvpA; _ga_75BBYNYN9J=GS2.1.s1781572752$o8$g0$t1781572752$j60$l0$h0; _gcl_au=1.1.1327994243.1781572753; _cfuvid=4bYCyZT0f28A4x7.F72_i3ngqIH58L_PTz5G8JnH_tc-1781572764.9502788-1.0.1.1-Zkd.v3i62inSjMhcTku.aLntkQaOQaspME1R1QvwIic; __cf_bm=9hn.mnRJzufxu0Lba9rRSwoExE4j6TlKvBfVfZ4pJNI-1781572765.0050876-1.0.1.1-a2ozYRLIbOrSb6whMQgYTqmXlYqrBWKjbCHIr8U0xBXszyf3xx2_MGCPugKeh_G2qa9huZfsthfdEvC6xQisydhfW2aTMsuCvYRm1g5bZxGfrJWPwi1qYsbNAQxmr51QwOkXdmELsSiYw7s6i.EsUg",
    # 是否自动解析聊天里的 Pixiv 链接
    pixiv_auto_parse=True,

    # 单次最多发送几张，防止多图作品炸群
    pixiv_max_send=6,

    # 图片最大大小，单位 MB，超过后会尝试 regular 图
    pixiv_max_file_mb=25,

    # 是否允许发送 R-18 / R-18G
    pixiv_allow_r18=True,

    # 缓存目录
    pixiv_cache_dir="data/pixiv_cache",

    # 缓存保留时间，单位小时
    pixiv_cache_ttl_hours=24,

    # 代理，不需要就留空
    # 例如 "http://127.0.0.1:7890"
    pixiv_proxy="",
)


# =========================
# 正则
# =========================

PIXIV_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?pixiv\.net/"
    r"(?:"
    r"(?:artworks|i)/(?P<id1>\d{5,12})"
    r"|member_illust\.php\?(?:[^\s#]*?)illust_id=(?P<id2>\d{5,12})"
    r")"
    r"(?:[^\s]*)?",
    re.IGNORECASE,
)

PID_RE = re.compile(r"^\s*(?:pid[:：]?\s*)?(\d{5,12})(?:\s+(.*))?$", re.IGNORECASE)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.7103.48 Safari/537.36"
)

PIXIV_ACCEPT_LANGUAGE = (
    "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7,ja-JP;q=0.6,ja;q=0.5"
)


# =========================
# 数据结构
# =========================

@dataclass
class PixivPage:
    index: int
    original_url: str
    regular_url: str
    thumb_url: str
    width: int
    height: int


@dataclass
class PixivArtwork:
    illust_id: str
    title: str
    user_name: str
    user_id: str
    page_count: int
    x_restrict: int
    ai_type: int
    sanity_level: int
    tags: list[str]
    pages: list[PixivPage]

    @property
    def is_r18(self) -> bool:
        # xRestrict: 0 普通，1 R-18，2 R-18G
        return self.x_restrict in (1, 2)


# =========================
# 工具函数
# =========================

def ensure_cache_dir() -> Path:
    cache_dir = Path(config.pixiv_cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def cleanup_cache() -> None:
    cache_dir = ensure_cache_dir()
    ttl = max(config.pixiv_cache_ttl_hours, 1) * 3600
    now = time.time()

    deleted_count = 0
    kept_count = 0
    error_count = 0
    total_size_freed = 0

    for item in cache_dir.glob("*"):
        try:
            if item.is_file() and now - item.stat().st_mtime > ttl:
                file_size = item.stat().st_size
                item.unlink(missing_ok=True)
                deleted_count += 1
                total_size_freed += file_size
            else:
                kept_count += 1
        except Exception as e:
            error_count += 1
            logger.warning(f"[Pixiv] 清理缓存失败: {item} — {e}")

    if deleted_count > 0 or error_count > 0:
        logger.info(
            f"[Pixiv] 缓存清理完成 → "
            f"删除 {deleted_count} 个文件 ({total_size_freed / 1024 / 1024:.1f} MB), "
            f"保留 {kept_count} 个, 错误 {error_count} 个, TTL={config.pixiv_cache_ttl_hours}h"
        )
    else:
        logger.debug(
            f"[Pixiv] 缓存清理 → 无过期文件 (保留 {kept_count} 个, TTL={config.pixiv_cache_ttl_hours}h)"
        )


def extract_pixiv_ids(text: str) -> list[str]:
    ids: list[str] = []
    for match in PIXIV_URL_RE.finditer(text):
        illust_id = match.group("id1") or match.group("id2")
        if illust_id and illust_id not in ids:
            ids.append(illust_id)
    return ids


def parse_page_selector(raw: Optional[str]) -> list[int]:
    """
    复刻 SomeACG-Bot 的 indexes 语义：
    - 默认 [0]：只取第一张
    - [-1]：全部页
    - [0, 1, 2]：指定页
    """
    if not raw:
        return [0]

    raw = raw.strip().lower()
    if raw in {"all", "全部", "*", "-1"}:
        return [-1]

    raw = raw.replace("，", ",")
    raw = raw.replace("p", "")

    result: set[int] = set()

    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue

        if "-" in part:
            left, right = part.split("-", 1)
            if not left.isdigit() or not right.isdigit():
                continue
            a, b = int(left), int(right)
            if a > b:
                a, b = b, a
            for i in range(a, b + 1):
                result.add(i)
        elif part.isdigit():
            result.add(int(part))

    return sorted(result) if result else [0]


def pixiv_headers(illust_id: Optional[str] = None) -> dict[str, str]:
    """
    请求头按 SomeACG-Bot 的 pixivInstance 逻辑：
    cookie + accept-language + user-agent。

    下载 pximg 图片时保留 Referer，避免防盗链 403。
    """
    headers = {
        "accept-language": PIXIV_ACCEPT_LANGUAGE,
        "user-agent": USER_AGENT,
    }

    if config.pixiv_cookie:
        headers["cookie"] = config.pixiv_cookie.strip()

    if illust_id:
        headers["referer"] = f"https://www.pixiv.net/artworks/{illust_id}"
    else:
        headers["referer"] = "https://www.pixiv.net/"

    return headers


def get_http_client(illust_id: Optional[str] = None) -> httpx.AsyncClient:
    proxy = config.pixiv_proxy.strip() or None
    return httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=20.0),
        headers=pixiv_headers(illust_id),
        proxy=proxy,
        follow_redirects=True,
    )


def get_suffix_from_url(url: str) -> str:
    path = url.split("?", 1)[0]
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        return suffix
    return ".jpg"


def cache_path_for_url(url: str) -> Path:
    cache_dir = ensure_cache_dir()
    suffix = get_suffix_from_url(url)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}{suffix}"


def file_as_uri(path: Path) -> str:
    return path.resolve().as_uri()


# =========================
# Pixiv API
# =========================


def ensure_pixiv_ajax_json(resp: httpx.Response, stage: str, illust_id: str) -> dict:
    """
    Pixiv Web Ajax 正常应该返回 application/json。
    如果返回 HTML，通常是 Cloudflare / 登录 / 风控页面。
    """
    content_type = resp.headers.get("content-type", "")
    body_preview = resp.text[:300]

    logger.debug(
        f"[Pixiv] {stage} 响应 → pid={illust_id}, "
        f"status={resp.status_code}, content-type={content_type}"
    )

    if "text/html" in content_type.lower() or resp.text.lstrip().lower().startswith("<!doctype html"):
        if "Just a moment" in resp.text or "just a moment" in resp.text:
            raise RuntimeError("Pixiv Web Ajax 被 Cloudflare 拦截，当前 Cookie 方案不可用")
        raise RuntimeError(f"Pixiv Web Ajax 返回 HTML，无法按 JSON 解析：{body_preview!r}")

    try:
        return resp.json()
    except Exception as e:
        raise RuntimeError(f"Pixiv Web Ajax JSON 解析失败：{e} body={body_preview!r}") from e

async def fetch_artwork(illust_id: str) -> PixivArtwork:
    """
    获取作品元信息 + pages 图片信息。
    """
    if not config.pixiv_cookie:
        raise RuntimeError("未配置 PIXIV_COOKIE，Pixiv 解析可能失败。请在 .env.prod 里配置 PIXIV_COOKIE。")

    logger.info(f"[Pixiv] 开始获取作品信息 → pid={illust_id}")
    t_start = time.time()

    async with get_http_client(illust_id) as client:
        # —— 获取作品元信息 ——
        info_url = f"https://www.pixiv.net/ajax/illust/{illust_id}"
        logger.debug(f"[Pixiv] 请求作品元信息 → {info_url}")

        info_resp = await client.get(info_url)

        if info_resp.status_code != 200:
            logger.warning(
                f"[Pixiv] 元信息请求失败 → pid={illust_id}, "
                f"status={info_resp.status_code}, "
                f"content-type={info_resp.headers.get('content-type')}, "
                f"body={info_resp.text[:300]!r}"
            )

        info_resp.raise_for_status()
        info_json = ensure_pixiv_ajax_json(info_resp, "元信息", illust_id)
        logger.debug(f"[Pixiv] 元信息响应 → status={info_resp.status_code}, error={info_json.get('error')}")

        if info_json.get("error"):
            err_msg = info_json.get('message') or 'unknown error'
            logger.error(f"[Pixiv] API 返回错误 pid={illust_id}: {err_msg}")
            raise RuntimeError(f"Pixiv 返回错误：{err_msg}")

        body = info_json.get("body") or {}
        logger.debug(
            f"[Pixiv] 作品基本信息 → title={body.get('illustTitle') or body.get('title')}, "
            f"author={body.get('userName')}, pageCount={body.get('pageCount')}, "
            f"xRestrict={body.get('xRestrict')}, aiType={body.get('aiType')}, sl={body.get('sl')}"
        )

        # —— 获取 pages 图片列表 ——
        pages_url = f"https://www.pixiv.net/ajax/illust/{illust_id}/pages?lang=zh"
        logger.debug(f"[Pixiv] 请求作品 pages → {pages_url}")
        pages_resp = await client.get(pages_url)
        if pages_resp.status_code != 200:
            logger.warning(
                f"[Pixiv] pages 请求失败 → pid={illust_id}, "
                f"status={pages_resp.status_code}, "
                f"content-type={pages_resp.headers.get('content-type')}, "
                f"body={pages_resp.text[:300]!r}"
            )
        pages_resp.raise_for_status()
        pages_json = ensure_pixiv_ajax_json(pages_resp, "pages", illust_id)
        logger.debug(f"[Pixiv] pages 响应 → status={pages_resp.status_code}, count={len(pages_json.get('body') or [])}")

        if pages_json.get("error"):
            err_msg = pages_json.get('message') or 'unknown error'
            logger.error(f"[Pixiv] pages API 返回错误 pid={illust_id}: {err_msg}")
            raise RuntimeError(f"Pixiv pages 返回错误：{err_msg}")

    raw_pages = pages_json.get("body") or []
    pages: list[PixivPage] = []

    for index, item in enumerate(raw_pages):
        urls = item.get("urls") or {}
        original = urls.get("original")
        regular = urls.get("regular") or urls.get("small") or original
        thumb = urls.get("thumb_mini") or urls.get("small") or regular

        if not original:
            logger.warning(f"[Pixiv] pid={illust_id} page#{index} 缺少 original URL，跳过")
            continue

        w, h = int(item.get("width") or 0), int(item.get("height") or 0)
        logger.debug(
            f"[Pixiv] pid={illust_id} page#{index} → "
            f"original={original[:80]}..., regular={regular[:80]}..., "
            f"size={w}x{h}"
        )

        pages.append(
            PixivPage(
                index=index,
                original_url=original,
                regular_url=regular,
                thumb_url=thumb,
                width=w,
                height=h,
            )
        )

    # 标签处理复刻 SomeACG-Bot：
    # R-18 -> R18；优先使用 translation.en；空格替换为下划线。
    tags_raw = body.get("tags", {}).get("tags", [])
    tags = []
    for item in tags_raw:
        tag_name = item.get("tag")
        if not tag_name:
            continue

        if tag_name == "R-18":
            tag_name = "R18"

        translation = item.get("translation") or {}
        if translation.get("en"):
            tag_name = translation["en"]

        tags.append(str(tag_name).replace(" ", "_"))

    artwork = PixivArtwork(
        illust_id=illust_id,
        title=str(body.get("illustTitle") or body.get("title") or "Untitled"),
        user_name=str(body.get("userName") or "Unknown"),
        user_id=str(body.get("userId") or ""),
        page_count=int(body.get("pageCount") or len(pages) or 1),
        x_restrict=int(body.get("xRestrict") or 0),
        ai_type=int(body.get("aiType") or 0),
        sanity_level=int(body.get("sl") or 0),
        tags=tags,
        pages=pages,
    )

    elapsed = time.time() - t_start
    r18_label = ""
    if artwork.x_restrict == 1:
        r18_label = " [R-18]"
    elif artwork.x_restrict == 2:
        r18_label = " [R-18G]"
    ai_label = " [AI]" if artwork.ai_type == 2 else ""

    logger.info(
        f"[Pixiv] 作品信息获取完成 → pid={illust_id} "
        f"「{artwork.title}」by {artwork.user_name} "
        f"共 {artwork.page_count} 页 / {len(pages)} 张图片{r18_label}{ai_label} "
        f"tags={tags[:6]}{'...' if len(tags) > 6 else ''} "
        f"({elapsed:.2f}s)"
    )

    return artwork


async def download_image(url: str, illust_id: str) -> Path:
    """
    下载图片到本地缓存。
    Pixiv 图片必须带 Referer。
    """
    path = cache_path_for_url(url)

    # 缓存命中
    if path.exists() and path.stat().st_size > 0:
        size_kb = path.stat().st_size / 1024
        logger.debug(
            f"[Pixiv] 缓存命中 pid={illust_id} → {path.name} ({size_kb:.1f} KB)"
        )
        return path

    # 缓存未命中，开始下载
    logger.info(f"[Pixiv] 下载图片 pid={illust_id} → {url[:100]}...")
    t_start = time.time()

    async with get_http_client(illust_id) as client:
        resp = await client.get(url)
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        content_length = resp.headers.get("content-length")
        size_info = f"{int(content_length) / 1024:.1f} KB" if content_length else "unknown size"

        logger.debug(
            f"[Pixiv] 图片响应 pid={illust_id} → "
            f"status={resp.status_code}, content-type={content_type}, size={size_info}"
        )

        if not content_type.startswith("image/"):
            logger.error(
                f"[Pixiv] 非图片响应 pid={illust_id} → "
                f"content-type={content_type}, url={url[:100]}..."
            )
            raise RuntimeError(f"下载到的不是图片：{content_type}")

        path.write_bytes(resp.content)

    elapsed = time.time() - t_start
    file_size_kb = path.stat().st_size / 1024
    logger.info(
        f"[Pixiv] 下载完成 pid={illust_id} → {path.name} "
        f"({file_size_kb:.1f} KB, {elapsed:.2f}s)"
    )

    return path


async def download_with_fallback(page: PixivPage, illust_id: str) -> tuple[Path, bool]:
    """
    优先下载 original。
    如果 original 超过大小限制，尝试 regular。
    返回：文件路径、是否为原图。
    """
    max_bytes = max(config.pixiv_max_file_mb, 1) * 1024 * 1024
    max_mb = max_bytes / 1024 / 1024
    logger.debug(
        f"[Pixiv] 下载页面 pid={illust_id} p={page.index} → "
        f"尺寸={page.width}x{page.height}, 大小限制={max_mb:.0f}MB"
    )

    # 先尝试 original
    original_path = await download_image(page.original_url, illust_id)
    original_size_mb = original_path.stat().st_size / 1024 / 1024

    if original_path.stat().st_size <= max_bytes:
        logger.debug(
            f"[Pixiv] 使用原图 pid={illust_id} p={page.index} → {original_size_mb:.2f}MB"
        )
        return original_path, True

    # original 过大，尝试 regular
    logger.warning(
        f"[Pixiv] 原图过大，尝试 regular → pid={illust_id} p={page.index} "
        f"original={original_size_mb:.2f}MB > {max_mb:.0f}MB"
    )

    regular_path = await download_image(page.regular_url, illust_id)
    regular_size_mb = regular_path.stat().st_size / 1024 / 1024

    if regular_path.stat().st_size <= max_bytes:
        logger.info(
            f"[Pixiv] 降级为 regular 图 pid={illust_id} p={page.index} → "
            f"regular={regular_size_mb:.2f}MB (原图 {original_size_mb:.2f}MB)"
        )
        return regular_path, False

    # 两者都过大
    logger.error(
        f"[Pixiv] 图片过大，original 和 regular 均超限 → "
        f"pid={illust_id} p={page.index} "
        f"original={original_size_mb:.2f}MB, regular={regular_size_mb:.2f}MB, "
        f"limit={max_mb:.0f}MB"
    )
    raise RuntimeError(
        f"图片过大，original={original_size_mb:.1f}MB，"
        f"regular={regular_size_mb:.1f}MB"
    )


def select_pages(artwork: PixivArtwork, selector: Optional[list[int]]) -> list[PixivPage]:
    """
    复刻 SomeACG-Bot 的 indexes 选择逻辑：
    - 默认 [0]
    - [-1] 表示全部
    - 最大页码超过 pageCount - 1 时抛 Picture index out of range
    """
    if not artwork.pages:
        logger.warning(f"[Pixiv] 作品无图片 → pid={artwork.illust_id}")
        return []

    indexes = selector if selector is not None else [0]

    if len(indexes) == 1 and indexes[0] == -1:
        indexes = list(range(artwork.page_count))

    if indexes and max(indexes) > artwork.page_count - 1:
        logger.warning(
            f"[Pixiv] 页码越界 → pid={artwork.illust_id}, "
            f"indexes={indexes}, pageCount={artwork.page_count}"
        )
        raise RuntimeError("Picture index out of range")

    selected = [p for p in artwork.pages if p.index in indexes]

    if not selected:
        logger.warning(
            f"[Pixiv] 页码选择无匹配 → pid={artwork.illust_id}, "
            f"indexes={indexes}, available={[p.index for p in artwork.pages]}"
        )
        return []

    max_send = max(config.pixiv_max_send, 1)
    if len(selected) > max_send:
        trimmed = selected[max_send:]
        selected = selected[:max_send]
        logger.info(
            f"[Pixiv] 图片数量超限裁剪 → pid={artwork.illust_id} "
            f"selected={len(selected) + len(trimmed)} → {len(selected)}, "
            f"max_send={max_send}, trimmed_indices={[p.index for p in trimmed]}"
        )

    logger.debug(
        f"[Pixiv] 页面选择完成 → pid={artwork.illust_id}, "
        f"indexes={indexes}, selected={[p.index for p in selected]}"
    )

    return selected


def build_info_text(artwork: PixivArtwork, selected_count: int, original_count: int) -> str:
    r18_text = ""
    if artwork.x_restrict == 1:
        r18_text = " / R-18"
    elif artwork.x_restrict == 2:
        r18_text = " / R-18G"

    ai_text = " / AI" if artwork.ai_type == 2 else ""

    tags = " ".join(f"#{t}" for t in artwork.tags[:8])
    if tags:
        tags = "\n" + tags

    return (
        f"Pixiv: {artwork.title}\n"
        f"作者: {artwork.user_name}\n"
        f"PID: {artwork.illust_id} / 共 {artwork.page_count} 页 / 本次发送 {selected_count} 张"
        f"{r18_text}{ai_text}\n"
        f"原图: {original_count}/{selected_count}"
        f"{tags}"
    )


async def send_pixiv_artwork(bot: Bot, event: Event, illust_id: str, selector: Optional[list[int]] = None) -> None:
    t_start = time.time()
    logger.info(
        f"[Pixiv] 开始处理作品请求 → pid={illust_id}, "
        f"selector={selector if selector is not None else [0]}"
    )

    cleanup_cache()

    # —— 获取作品信息 ——
    artwork = await fetch_artwork(illust_id)

    # —— R-18 检查 ——
    if artwork.is_r18 and not config.pixiv_allow_r18:
        logger.info(
            f"[Pixiv] R-18 作品被拦截 → pid={illust_id} "
            f"x_restrict={artwork.x_restrict}, allow_r18={config.pixiv_allow_r18}"
        )
        await bot.send(
            event,
            Message(
                f"Pixiv 作品 {illust_id} 被标记为 R-18/R-18G，当前配置不允许发送。"
            ),
        )
        return

    if artwork.is_r18:
        logger.info(
            f"[Pixiv] R-18 作品允许发送 → pid={illust_id} x_restrict={artwork.x_restrict}"
        )

    # —— 选择页面 ——
    selected_pages = select_pages(artwork, selector)
    logger.info(
        f"[Pixiv] 页面选择 → pid={illust_id} "
        f"total={artwork.page_count} pages, selected={len(selected_pages)}, "
        f"max_send={config.pixiv_max_send}"
    )

    if not selected_pages:
        logger.warning(
            f"[Pixiv] 无可发送页面 → pid={illust_id} "
            f"total_pages={len(artwork.pages)}, selector={selector}"
        )
        await bot.send(event, Message(f"Pixiv 作品 {illust_id} 没有可发送的图片，或页码选择无效。"))
        return

    # —— 下载 & 发送每张图片 ——
    original_count = 0
    image_segments: list[MessageSegment] = []
    download_errors = 0

    for i, page in enumerate(selected_pages):
        try:
            logger.debug(
                f"[Pixiv] 处理第 {i+1}/{len(selected_pages)} 页 → "
                f"pid={illust_id} p={page.index}"
            )
            path, is_original = await download_with_fallback(page, illust_id)
            if is_original:
                original_count += 1

            image_segments.append(MessageSegment.image(file_as_uri(path)))
            logger.debug(
                f"[Pixiv] 图片已加入发送队列 → pid={illust_id} p={page.index} "
                f"({'原图' if is_original else 'regular'})"
            )

            # 稍微让一下，避免连续下载/发送过快
            await asyncio.sleep(0.2)

        except Exception as e:
            download_errors += 1
            logger.exception(
                f"[Pixiv] 图片下载失败 → pid={illust_id} p={page.index}: {e}"
            )
            await bot.send(event, Message(f"PID {illust_id} 第 {page.index} 页下载失败：{e}"))

    if not image_segments:
        logger.error(
            f"[Pixiv] 所有图片下载失败 → pid={illust_id} "
            f"errors={download_errors}/{len(selected_pages)}"
        )
        await bot.send(event, Message(f"Pixiv 作品 {illust_id} 下载失败，没有可发送图片。"))
        return

    if download_errors > 0:
        logger.warning(
            f"[Pixiv] 部分图片下载失败 → pid={illust_id} "
            f"success={len(image_segments)}/{len(selected_pages)}, errors={download_errors}"
        )

    # —— 发送信息文本 ——
    info_text = build_info_text(artwork, len(image_segments), original_count)
    logger.debug(f"[Pixiv] 发送作品信息 → pid={illust_id}\n{info_text}")
    await bot.send(event, Message(info_text))

    # —— 分开发送图片 ——
    # 分开发送比拼成一条更稳，尤其是 QQ / NapCat 对多图消息有时会抽风。
    for i, seg in enumerate(image_segments):
        logger.debug(f"[Pixiv] 发送图片 {i+1}/{len(image_segments)} → pid={illust_id}")
        await bot.send(event, Message(seg))
        await asyncio.sleep(0.5)

    total_elapsed = time.time() - t_start
    logger.info(
        f"[Pixiv] 作品发送完成 → pid={illust_id} "
        f"发送 {len(image_segments)} 张 (原图 {original_count}), "
        f"失败 {download_errors} 张, "
        f"总耗时 {total_elapsed:.2f}s"
    )


# =========================
# Matcher
# =========================

pixiv_cmd = on_command(
    "pixiv",
    aliases={"pid", "p站", "Pixiv", "PID"},
    priority=5,
    block=True,
)

auto_pixiv = on_message(priority=20, block=False)


@pixiv_cmd.handle()
async def handle_pixiv_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    raw = args.extract_plain_text().strip()
    user_id = event.get_user_id()
    logger.info(f"[Pixiv] 收到命令 → user={user_id}, raw_input={raw[:100]}")

    if not raw:
        logger.debug(f"[Pixiv] 空命令 → user={user_id}，返回帮助信息")
        await pixiv_cmd.finish(
            "用法：\n"
            "/pixiv 作品ID\n"
            "/pixiv 作品ID all\n"
            "/pixiv 作品ID 0\n"
            "/pixiv 作品ID 0,1,2\n"
            "/pixiv 作品ID 1-3"
        )

    # 支持直接传 URL
    ids = extract_pixiv_ids(raw)
    if ids:
        illust_id = ids[0]
        selector = None
        logger.debug(f"[Pixiv] 从 URL 提取 ID → user={user_id}, illust_id={illust_id}")
    else:
        match = PID_RE.match(raw)
        if not match:
            logger.info(f"[Pixiv] 未识别到作品 ID → user={user_id}, input={raw[:50]}")
            await pixiv_cmd.finish("没有识别到 Pixiv 作品 ID。")

        illust_id = match.group(1)
        selector = parse_page_selector(match.group(2))
        logger.debug(
            f"[Pixiv] 解析命令 → user={user_id}, illust_id={illust_id}, "
            f"selector={match.group(2) or '0'}"
        )

    try:
        await send_pixiv_artwork(bot, event, illust_id, selector)
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        logger.error(
            f"[Pixiv] HTTP 错误 → pid={illust_id}, "
            f"status={code}, url={e.request.url if e.request else 'N/A'}"
        )
        if code == 403:
            await pixiv_cmd.finish("Pixiv 返回 403，通常是 Cookie 失效、Referer 问题或 IP 被限制。")
        elif code == 404:
            await pixiv_cmd.finish("Pixiv 返回 404，作品可能不存在、被删除，或没有权限查看。")
        else:
            await pixiv_cmd.finish(f"Pixiv 请求失败：HTTP {code}")
    except Exception as e:
        logger.exception(f"[Pixiv] 解析失败 → pid={illust_id}: {e}")
        await pixiv_cmd.finish(f"Pixiv 解析失败：{e}")


@auto_pixiv.handle()
async def handle_auto_pixiv(bot: Bot, event: MessageEvent):
    if not config.pixiv_auto_parse:
        return

    text = str(event.get_message())
    ids = extract_pixiv_ids(text)

    if not ids:
        return

    user_id = event.get_user_id()
    # 一条消息里多个 Pixiv 链接时，限制只处理前 2 个，防止刷屏
    total_found = len(ids)
    ids = ids[:2]

    logger.info(
        f"[Pixiv] 自动解析触发 → user={user_id}, "
        f"发现 {total_found} 个链接, 处理 {len(ids)} 个, "
        f"ids={ids}"
    )
    if total_found > 2:
        logger.debug(f"[Pixiv] 自动解析截断 → 仅处理前 2 个，丢弃 {total_found - 2} 个链接")

    for i, illust_id in enumerate(ids):
        logger.debug(
            f"[Pixiv] 自动解析第 {i+1}/{len(ids)} 个 → pid={illust_id}"
        )
        try:
            await send_pixiv_artwork(bot, event, illust_id, selector=None)
            await asyncio.sleep(1.0)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            logger.warning(
                f"[Pixiv] 自动解析 HTTP 错误 → pid={illust_id}, "
                f"status={code}"
            )
            await bot.send(event, Message(f"Pixiv 自动解析失败：PID {illust_id} HTTP {code}"))
        except Exception as e:
            logger.exception(f"[Pixiv] 自动解析失败 → pid={illust_id}: {e}")
            await bot.send(event, Message(f"Pixiv 自动解析失败：PID {illust_id} {e}"))