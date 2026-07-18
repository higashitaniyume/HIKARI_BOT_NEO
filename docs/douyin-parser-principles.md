# 从零开始用 Python 解析抖音分享链接

抖音（Douyin）的分享链接通常长这样：`v.douyin.com/xxxxx/`——短链、无意义、点进去才知道是什么。本文从零开始讲怎么用 Python 解析这类链接，拿到视频或图文的真实内容。

> 抖音没有公开的 API，但它的移动端分享页里藏着完整的数据——以 `window._ROUTER_DATA` 的形式直接注入在 HTML 中。

---

## 准备工作

```bash
pip install httpx
```

---

## 第一步：理解抖音链接的类型

抖音分享链接有三种常见形态：

```
# 短链（最常用，QQ/微信分享的）
v.douyin.com/xxxxx/

# 展开后的视频页
www.douyin.com/video/1234567890123456789

# 图文/笔记页
www.douyin.com/note/1234567890123456789

# Slides（多分段视频/图文混排）
www.douyin.com/slides/1234567890123456789
```

它们的共同点是：**内容 ID 是一串 19 位数字**。

解析的第一步：展开短链，获取真实 URL，再判断内容类型。

```python
import re
from urllib.parse import urlparse
import httpx

DOUYIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/116.0.0.0 Mobile Safari/537.36"
    ),
    "Referer": "https://www.douyin.com/",
}

async def get_redirected_url(url: str) -> str:
    """获取抖音短链重定向后的真实 URL。"""
    async with httpx.AsyncClient(
        headers=DOUYIN_HEADERS,
        follow_redirects=True,
    ) as client:
        # 先 HEAD 请求（更快）
        resp = await client.head(url)
        redirected = str(resp.url)

        if redirected != url and resp.status_code < 400:
            return redirected

        # HEAD 没拿到有效跳转，用 GET 回退
        resp = await client.get(url)
        return str(resp.url)


# 识别内容类型
def detect_content_type(url: str) -> tuple[str, str]:
    """
    判断链接类型，返回 (类型, 内容ID)。
    类型: "video", "note", "slides"
    """
    m = re.search(r"/(?:video|note|slides)/(\d+)", url)
    if m:
        type_map = {"video": "video", "note": "note", "slides": "slides"}
        for t, key in type_map.items():
            if f"/{t}/" in url:
                return key, m.group(1)
    
    # 兜底：从 URL 中找 19 位数字作为 ID
    m = re.search(r"(\d{19})", url)
    if m:
        return "video", m.group(1)

    raise RuntimeError(f"无法识别的内容类型: {url}")
```

---

## 第二步：获取页面数据（分享页）

抖音把数据放在移动端分享页的 `window._ROUTER_DATA` 里。我们直接请求分享页，把这段 JSON 提取出来。

```python
import json

async def fetch_share_page(item_id: str, content_type: str = "video") -> dict:
    """获取抖音分享页的 HTML，提取 _ROUTER_DATA。"""
    url_map = {
        "video": f"https://www.iesdouyin.com/share/video/{item_id}/",
        "note": f"https://www.iesdouyin.com/share/note/{item_id}/",
        "slides": f"https://www.iesdouyin.com/share/slides/{item_id}/",
    }
    url = url_map.get(content_type, url_map["video"])

    async with httpx.AsyncClient(headers=DOUYIN_HEADERS) as client:
        resp = await client.get(url)
        html = resp.text

    # 提取 window._ROUTER_DATA
    json_str = extract_router_data(html)
    if not json_str:
        raise RuntimeError("未找到 _ROUTER_DATA")

    # 反转义常见编码
    json_str = json_str.replace("\\u002F", "/").replace("\\/", "/")
    return json.loads(json_str)


def extract_router_data(html: str) -> str | None:
    """从 HTML 中提取 window._ROUTER_DATA 的 JSON 字符串。"""
    marker = "window._ROUTER_DATA = "
    start = html.find(marker)
    if start == -1:
        return None

    brace_start = html.find("{", start)
    if brace_start == -1:
        return None

    # 用花括号匹配找到完整的 JSON
    depth = 0
    for i in range(brace_start, len(html)):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                return html[brace_start:i + 1]
    return None
```

---

## 第三步：从 _ROUTER_DATA 中提取数据

`_ROUTER_DATA` 的结构类似如下（简化）：

```json
{
  "loaderData": {
    "video/{id}/": {
      "videoInfoRes": {
        "item_list": [{ ... 作品数据 ... }]
      }
    },
    "note/{id}/": {
      "noteDetailRes": {
        "item_list": [{ ... 笔记数据 ... }]
      }
    },
    "slides/{id}/": {
      "slidesInfoRes": {
        "aweme_list": [{ ... slides 数据 ... }]
      }
    }
  }
}
```

关键是在 `loaderData` 中找到包含 `videoInfoRes`、`noteDetailRes` 或 `slidesInfoRes` 的 value：

```python
def extract_item_info(router_data: dict) -> dict:
    """从 _ROUTER_DATA 中提取作品信息。"""
    loader_data = router_data.get("loaderData", {})

    for value in loader_data.values():
        if not isinstance(value, dict):
            continue

        # 三种内容类型的 key
        for res_key in ("videoInfoRes", "noteDetailRes", "slidesInfoRes"):
            content = value.get(res_key)
            if not content:
                continue

            # 找到第一个非空的作品列表
            for list_key in ("item_list", "aweme_details", "aweme_list"):
                items = content.get(list_key)
                if isinstance(items, list) and items:
                    return items[0]

            # 兜底
            item = content.get("aweme_detail")
            if isinstance(item, dict) and item:
                return item

    raise RuntimeError("无法从 _ROUTER_DATA 中提取作品信息")
```

---

## 第四步：提取视频信息

抖音的作品数据结构很大（几百个字段），但我们需要的不多。核心字段大致在这几个位置：

```python
def extract_video_info(item_info: dict) -> dict:
    """
    从作品信息中提取视频数据和作者信息。
    
    Returns:
        {
            "title": str,
            "author": str,
            "timestamp": str,
            "video_urls": [str],   # 候选视频 URL 列表
        }
    """
    desc = item_info.get("desc", "")
    author_info = item_info.get("author", {})
    nickname = author_info.get("nickname", "")
    unique_id = author_info.get("unique_id", "")
    author = f"{nickname}(uid:{unique_id})" if unique_id else nickname

    create_time = item_info.get("create_time", 0)
    if create_time:
        from datetime import datetime
        timestamp = datetime.fromtimestamp(int(create_time)).strftime("%Y-%m-%d")
    else:
        timestamp = ""

    # 提取视频播放地址
    video_info = item_info.get("video", {})
    video_urls = extract_play_addr(video_info)

    return {
        "title": desc,
        "author": author,
        "timestamp": timestamp,
        "video_urls": video_urls,
    }
```

### 视频 URL 的提取

抖音的视频地址藏在 `video` 对象的 `play_addr` 或 `playAddr` 字段里：

```python
def extract_play_addr(video_info: dict) -> list[str]:
    """从 video 结构中提取所有候选播放地址。"""
    urls = []

    # 多个可能的 key 名称（B 端和 C 端字段名不一致）
    for key in ("play_addr", "playAddr", "PlayAddr", "download_addr", "downloadAddr"):
        play_addr = video_info.get(key)
        if not play_addr:
            continue

        if isinstance(play_addr, dict):
            # 优先取 url_list
            url_list = play_addr.get("url_list") or play_addr.get("urlList") or []
            for url in url_list:
                if isinstance(url, str) and url.startswith("http"):
                    urls.append(url)

            # 如果只有 uri，可以拼接出播放地址
            video_uri = play_addr.get("uri", "")
            if video_uri and not urls:
                urls.append(f"https://www.douyin.com/aweme/v1/play/?video_id={video_uri}")

    # bit_rate 里也可能有播放地址
    for bitrate in video_info.get("bit_rate") or []:
        if isinstance(bitrate, dict):
            for key in ("play_addr", "playAddr"):
                addr = bitrate.get(key)
                if isinstance(addr, dict):
                    for url in addr.get("url_list", addr.get("urlList", [])):
                        if isinstance(url, str) and url not in urls:
                            urls.append(url)

    return urls
```

---

## 第五步：提取图集信息

对于图文笔记（note）或多分段内容（slides），作品信息里没有 `video`，而是 `images` 数组：

```python
def extract_image_info(item_info: dict) -> tuple[list[list[str]], list[list[str]]]:
    """
    从作品信息中提取图片和视频段。
    
    Returns:
        (video_url_lists, image_url_lists)
        每个元素是一个二维列表，每个内层列表是一个媒体项的候选 URL 列表。
    """
    video_url_lists = []
    image_url_lists = []

    # 先检查是否是视频作品（顶层有 video 字段）
    top_video = item_info.get("video", {})
    if top_video:
        urls = extract_play_addr(top_video)
        if urls:
            video_url_lists.append(urls)
            return video_url_lists, image_url_lists

    # 遍历 images 数组
    for image_item in item_info.get("images") or []:
        if not isinstance(image_item, dict):
            continue

        # 检查这个条目是否内嵌视频段（slides 里常见）
        slide_video = extract_nested_video(image_item)
        if slide_video:
            video_url_lists.append(slide_video)
            continue

        # 否则当作图片处理
        img_urls = extract_image_urls(image_item)
        if img_urls:
            image_url_lists.append(img_urls)

    return video_url_lists, image_url_lists


def extract_nested_video(image_item: dict) -> list[str] | None:
    """检查图片条目中是否包含视频段。"""
    for key in ("video", "video_info", "video_clip", "clip"):
        video_root = image_item.get(key)
        if isinstance(video_root, dict):
            urls = extract_play_addr(video_root)
            if urls:
                return urls

    # 条目本身就有播放地址字段
    if any(k in image_item for k in ("play_addr", "playAddr", "bit_rate")):
        urls = extract_play_addr(image_item)
        if urls:
            return urls

    return None


def extract_image_urls(image_item: dict) -> list[str]:
    """从图片条目中提取图片 URL（排除视频/音频 URL）。"""
    urls = []
    for key in ("url_list", "urlList", "urls", "url", "displayImage", "originImage"):
        value = image_item.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            for u in value:
                if isinstance(u, str) and u.startswith("http") and ".mp4" not in u:
                    urls.append(u)
        elif isinstance(value, str) and value.startswith("http") and ".mp4" not in value:
            urls.append(value)

    return urls
```

> **视频 URL 和图片 URL 的区分很重要。** 抖音的字段里经常视频和图片混在一起。简单的判据：含 `.mp4`、`video_id=`、`video/`、`mime_type=video` 的是视频；不含的是图片。

---

## 第六步：Slides 特殊处理

对于 Slides 类型的内容（多分段视频/图文混排），上面的分享页取数方法不一定总能拿到数据。抖音还提供了一个独立的 API：

```python
async def fetch_slides_info(item_id: str) -> dict | None:
    """通过 slidesinfo API 获取图文/视频混排内容。"""
    headers = {
        **DOUYIN_HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.douyin.com/",
    }

    async with httpx.AsyncClient(headers=headers) as client:
        resp = await client.get(
            "https://www.iesdouyin.com/web/api/v2/aweme/slidesinfo/",
            params={"aweme_ids": f"[{item_id}]", "request_source": "200"},
        )
        if resp.status_code >= 400:
            return None
        data = resp.json()

    # 提取作品信息
    for list_key in ("item_list", "aweme_details", "aweme_list"):
        items = data.get(list_key) or []
        if items:
            return extract_all_info(items[0])

    item = data.get("aweme_detail")
    return extract_all_info(item) if item else None


def extract_all_info(item_info: dict) -> dict:
    """统一提取视频/图集的所有信息。"""
    video_lists, image_lists = extract_image_info(item_info)
    info = extract_video_info(item_info)

    # 从 info 中拿到的 video_urls 是单层列表，整理为二维列表
    if video_lists or image_lists:
        info["video_urls"] = video_lists
        info["image_urls"] = image_lists
    else:
        info["video_urls"] = [info.pop("video_urls", [])] if info.get("video_urls") else []
        info["image_urls"] = []

    return info
```

---

## 第七步：完整的解析流程

把前面的步骤串起来：

```python
async def parse_douyin(url: str) -> dict:
    """
    解析抖音链接，返回标准化的元数据。
    
    Returns:
        {
            "url": str,           # 展示用 URL
            "title": str,         # 作品描述
            "author": str,        # 作者名(uid:xxx)
            "timestamp": str,     # 发布时间
            "video_urls": [[str]],  # 视频候选 URL 列表
            "image_urls": [[str]],  # 图片候选 URL 列表
        }
    """
    # 1. 展开短链
    redirected = await get_redirected_url(url)
    display_url = redirected if "v.douyin.com" not in url else url

    # 2. 判断内容类型和提取 ID
    content_type, item_id = detect_content_type(redirected)

    # 3. 获取数据
    try:
        router_data = await fetch_share_page(item_id, content_type)
        item_info = extract_item_info(router_data)
    except (RuntimeError, KeyError, json.JSONDecodeError):
        # 分享页取数失败，尝试 slides API 兜底
        if content_type == "slides":
            item_info = await fetch_slides_info(item_id)
            if not item_info:
                raise RuntimeError(f"无法获取作品信息: {url}")
        else:
            raise

    # 4. 提取信息
    info = extract_video_info(item_info)
    video_lists, image_lists = extract_image_info(item_info)

    is_gallery = bool(image_lists and not video_lists)

    if is_gallery:
        return {
            "url": display_url,
            "title": info["title"],
            "author": info["author"],
            "timestamp": info["timestamp"],
            "video_urls": [],
            "image_urls": image_lists,
        }

    if not video_lists:
        raise RuntimeError(f"无法获取视频地址: {url}")

    return {
        "url": display_url,
        "title": info["title"],
        "author": info["author"],
        "timestamp": info["timestamp"],
        "video_urls": video_lists,
        "image_urls": image_lists,
    }


# 使用示例
import asyncio
result = asyncio.run(parse_douyin("https://v.douyin.com/xxxxx/"))
print(result["title"])
print(f"作者: {result['author']}")
print(f"视频候选地址: {len(result['video_urls'])} 个")
```

---

## 总结

抖音解析的完整链路：

```
v.douyin.com/xxxxx/
  ↓
HEAD/GET 展开短链
  ↓
判断: video / note / slides
  ↓
请求 iesdouyin.com/share/{type}/{id}/
  ↓
提取 window._ROUTER_DATA（HTML 中的内联 JSON）
  ↓
提取作品信息（loaderData → xxxInfoRes → item_list）
  ↓
判断媒体类型：
  ├─ 有 video.play_addr  → 视频 → 提取播放地址
  ├─ 有 images[]          → 图集 → 提取图片 URL
  └─ slides               → 混排 → 逐项判断视频/图片
```

**核心要点：**

1. **分享页是主要数据源**，`window._ROUTER_DATA` 里包含完整数据
2. **短链展开**用 HEAD 请求优先，不行再 GET
3. **三种内容类型**（video/note/slides）走不同的数据路径
4. **视频地址**可能只给了 uri，需要拼接成完整播放 URL
5. **图片提取**要注意排除视频 URL（按扩展名和 URL 特征过滤）
6. **Slides API** `iesdouyin.com/web/api/v2/aweme/slidesinfo/` 可作为兜底
