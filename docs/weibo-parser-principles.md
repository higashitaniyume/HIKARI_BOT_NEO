# 从零开始用 Python 解析微博链接

微博有三套不同的页面和接口——桌面版（weibo.com）、移动版（m.weibo.cn）、视频组件版（video.weibo.com）。每套的数据源都不一样，解析前必须先判断链接类型。

本文从零开始，用 Python 解析这三种微博链接。

---

## 准备工作

```bash
pip install httpx
```

---

## 第一步：判断链接类型

微博的链接主要分三类：

```
# 桌面版详情页
https://weibo.com/1234567890/AbCdEfGhI
https://weibo.cn/status/1234567890

# 移动版详情页
https://m.weibo.cn/detail/1234567890

# 视频组件页
https://video.weibo.com/show?fid=1034:5233218052358208
https://weibo.com/tv/show/1034:5233218052358208
```

```python
import re

def get_url_type(url: str) -> str:
    """判断微博链接类型。"""
    if re.search(r"m\.weibo\.cn/detail/\d+", url):
        return "m_weibo_cn"
    elif re.search(r"video\.weibo\.com", url) or re.search(r"/tv/show/", url):
        return "video_weibo"
    elif re.search(r"weibo\.com/\d+/[A-Za-z0-9]+", url) or re.search(r"weibo\.cn/status/\d+", url):
        return "weibo_com"
    raise ValueError(f"无法识别的微博链接: {url}")


def extract_page_id(url: str) -> str:
    """从 URL 中提取页面 ID。"""
    match = re.search(r"/([A-Za-z0-9]+)$", url.rstrip("/"))
    if match:
        return match.group(1)
    raise ValueError(f"无法提取页面 ID: {url}")


def extract_blog_id(url: str) -> str:
    """从 m.weibo.cn 链接中提取博客 ID。"""
    match = re.search(r"/detail/(\d+)", url)
    if match:
        return match.group(1)
    raise ValueError(f"无法提取博客 ID: {url}")


def extract_video_id(url: str) -> str:
    """从视频链接中提取视频 ID。"""
    from urllib.parse import urlparse, parse_qs

    # 尝试从 query 中提取 fid
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    if "fid" in params:
        return params["fid"][0]

    # 尝试从路径中提取
    match = re.search(r"/(\d+:\d+)", url)
    if match:
        return match.group(1)

    raise ValueError(f"无法提取视频 ID: {url}")
```

---

## 第二步：获取微博访客 Cookie

微博的大部分接口需要 Cookie 才能返回数据。好消息是不需要用户登录——可以通过访客接口自动获取：

```python
import httpx

async def get_visitor_cookies() -> str:
    """
    获取微博访客 Cookie。
    通过 passport 接口生成访客身份，再补全 XSRF-TOKEN。
    """
    async with httpx.AsyncClient() as client:
        # 第一步：生成访客 Cookie
        resp = await client.post(
            "https://visitor.passport.weibo.cn/visitor/genvisitor2",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"cb": "visitor_gray_callback"},
        )

        cookies = []
        for cookie in resp.cookies.values():
            cookies.append(f"{cookie.key}={cookie.value}")

        cookie_str = "; ".join(cookies)

        # 第二步：补充 XSRF-TOKEN（部分接口需要）
        if "XSRF-TOKEN" not in cookie_str:
            resp = await client.get(
                "https://weibo.com",
                headers={"User-Agent": "Mozilla/5.0 ..."},
            )
            for cookie in resp.cookies.values():
                if cookie.key == "XSRF-TOKEN":
                    cookies.append(f"{cookie.key}={cookie.value}")
                    cookie_str = "; ".join(cookies)
                    break

        return cookie_str
```

---

## 第三步：解析桌面版链接（weibo.com）

桌面版的走 Ajax 接口 `weibo.com/ajax/statuses/show`：

```python
async def parse_weibo_com(url: str, cookies: str) -> dict:
    """解析 weibo.com 详情页。"""
    page_id = extract_page_id(url)

    api_url = f"https://weibo.com/ajax/statuses/show?id={page_id}&locale=zh-CN&isGetLongText=true"

    # 从 Cookie 中取 XSRF-Token
    xsrf_token = ""
    for item in cookies.split("; "):
        if item.startswith("XSRF-TOKEN="):
            xsrf_token = item.split("=", 1)[1]
            break

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": url,
        "Cookie": cookies,
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }
    if xsrf_token:
        headers["X-XSRF-Token"] = xsrf_token

    async with httpx.AsyncClient(headers=headers) as client:
        resp = await client.get(api_url)
        data = resp.json()

    if data.get("ok") == 0:
        raise RuntimeError(f"API 返回错误: {data.get('msg')}")

    # 有些返回包裹在 data 字段里
    json_data = data.get("data", data)

    # 提取作者、时间、文本
    user = json_data.get("user", {})
    screen_name = user.get("screen_name", "")
    user_id = str(user.get("id", ""))
    author = f"{screen_name}(uid:{user_id})" if screen_name and user_id else screen_name

    created_at = json_data.get("created_at", "")
    timestamp = format_weibo_time(created_at)

    raw_text = json_data.get("text_raw", "") or json_data.get("text", "")
    clean_text = clean_html_text(raw_text)

    # 提取媒体
    media_urls = extract_media_urls(json_data)
    video_urls, image_urls = separate_media(media_urls)

    result = {
        "url": url,
        "title": "",
        "author": author,
        "desc": clean_text,
        "timestamp": timestamp,
        "video_urls": video_urls,
        "image_urls": image_urls,
    }

    return result
```

### 时间格式化

微博的时间格式是 `Thu Nov 13 21:18:29 +0800 2025`，需要转成标准格式：

```python
from datetime import datetime

def format_weibo_time(created_at: str) -> str:
    """将微博时间格式转为 YYYY-MM-DD。"""
    if not created_at:
        return ""
    try:
        dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return created_at
```

### 清理 HTML 文本

微博正文里混着 HTML 标签，需要清理：

```python
def clean_html_text(html_text: str) -> str:
    """清理 HTML 标签，提取纯文本。"""
    if not html_text:
        return ""

    text = html_text

    # 保留超链接文本（去掉标签保留文字）
    text = re.sub(
        r'<span\s+class=["\']surl-text["\']>(.*?)</span>',
        r"\1",
        text,
        flags=re.DOTALL,
    )

    # 去掉表情图标
    text = re.sub(r'<span\s+class=["\']url-icon["\'][^>]*>.*?</span>', "", text, flags=re.DOTALL)

    # 去掉图片标签
    text = re.sub(r"<img[^>]*>", "", text)

    # 换行
    text = re.sub(r"<br\s*/?>", " ", text)

    # 去掉剩余标签
    text = re.sub(r"<[^>]+>", "", text)

    # 合并空白
    text = re.sub(r"\s+", " ", text).strip()
    return text
```

---

## 第四步：提取媒体资源

微博的媒体数据散落在多个字段里，需要逐个扫描：

```python
def extract_media_urls(json_data: dict) -> list[str]:
    """
    从微博 JSON 中提取所有媒体 URL（图片+视频）。
    因为微博的媒体结构太多了，得把所有可能的地方都扫一遍。
    """
    urls = []

    # 1. 混合媒体列表（mix_media_info）
    mix_items = json_data.get("mix_media_info", {}).get("items", [])
    for item in mix_items:
        data = item.get("data", {})
        if item.get("type") == "pic":
            url = extract_pic_url(data)
            if url:
                urls.append(url)
        elif item.get("type") == "video":
            media_info = data.get("media_info", {})
            video_url = media_info.get("hd_url") or media_info.get("stream_url_hd") or media_info.get("stream_url")
            if video_url:
                urls.append(video_url)

    # 2. 图片信息表（pic_infos）
    for pic_info in json_data.get("pic_infos", {}).values():
        if pic_info.get("type") == "gif" and pic_info.get("video"):
            urls.append(pic_info["video"])
            continue
        url = extract_pic_url(pic_info)
        if url:
            urls.append(url)

    # 3. 普通图片列表（pics）
    for pic in json_data.get("pics", []):
        url = extract_pic_url(pic)
        if url:
            urls.append(url)

    # 4. 页面卡片（page_info）中的视频
    page_info = json_data.get("page_info", {})
    if page_info:
        # urls 字段
        video_url = extract_video_from_dict(page_info.get("urls", {}))
        if video_url:
            urls.append(video_url)
        # media_info 字段
        media_info = page_info.get("media_info", {})
        if media_info:
            urls.append(media_info.get("hd_url") or media_info.get("stream_url"))

    # 5. 视频详情（video_info）
    video_info = json_data.get("video_info", {})
    if video_info:
        details = video_info.get("video_details", {}).get("video_details", {})
        if details:
            best_quality = max(details.keys(), key=lambda x: int(x) if x.isdigit() else 0, default=None)
            if best_quality:
                url = details[best_quality].get("url")
                if url:
                    urls.append(url)

    return urls


def extract_pic_url(pic_data: dict) -> str | None:
    """从图片数据中提取 URL，由大到小优先。"""
    for key in ["largest", "original", "large"]:
        size_info = pic_data.get(key, {})
        if isinstance(size_info, dict):
            url = size_info.get("url")
            if url:
                return url
    return pic_data.get("url")


def extract_video_from_dict(urls: dict) -> str | None:
    """从 URL 字典中取第一个可用 URL。"""
    if not urls:
        return None
    for url in urls.values():
        if url:
            normalized = url if url.startswith("http") else f"https:{url}"
            return normalized
    return None


def separate_media(media_urls: list[str]) -> tuple[list[list[str]], list[list[str]]]:
    """将媒体 URL 列表分离为视频和图片。"""
    video_urls = []
    image_urls = []

    for url in media_urls:
        if not url:
            continue

        # 补全协议
        if url.startswith("//"):
            url = "https:" + url

        url_lower = url.lower()
        if any(k in url_lower for k in ("video", ".mp4", "stream", "playback")):
            video_urls.append([url])
        else:
            image_urls.append([url])

    return video_urls, image_urls
```

---

## 第五步：解析移动版链接（m.weibo.cn）

移动版不走 API，数据直接注入在 HTML 的 `$render_data` 变量里：

```python
import json

async def parse_m_weibo_cn(url: str, cookies: str) -> dict:
    """解析 m.weibo.cn 移动版链接。"""
    blog_id = extract_blog_id(url)
    detail_url = f"https://m.weibo.cn/detail/{blog_id}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://visitor.passport.weibo.cn/",
        "Cookie": cookies,
    }

    async with httpx.AsyncClient(headers=headers) as client:
        resp = await client.get(detail_url)
        html = resp.text

    # 提取 var $render_data = [...][0]
    match = re.search(r"var \$render_data = (\[.*?\])\[0\]", html, re.DOTALL)
    if not match:
        raise RuntimeError("未找到 $render_data 数据")

    render_data = json.loads(match.group(1))
    if not render_data:
        raise RuntimeError("$render_data 为空")

    status_data = render_data[0]
    status = status_data.get("status", {})
    user = status.get("user", {})

    # 作者
    screen_name = user.get("screen_name", "")
    user_id = str(user.get("id", ""))
    author = f"{screen_name}(uid:{user_id})" if screen_name and user_id else screen_name

    # 时间
    created_at = status.get("created_at", "")
    timestamp = format_weibo_time(created_at)

    # 正文
    raw_text = status.get("text_raw", "") or status.get("text", "")
    clean_text = clean_html_text(raw_text)

    # 媒体（移动版结构比桌面版简单）
    media_urls = extract_media_urls_m_weibo(status_data)
    video_urls, image_urls = separate_media(media_urls)

    return {
        "url": url,
        "title": "",
        "author": author,
        "desc": clean_text,
        "timestamp": timestamp,
        "video_urls": video_urls,
        "image_urls": image_urls,
    }


def extract_media_urls_m_weibo(json_data: dict) -> list[str]:
    """从 m.weibo.cn 数据中提取媒体 URL。"""
    urls = []
    status = json_data.get("status", {})

    # 图片
    for pic in status.get("pics", []):
        url = extract_pic_url(pic)
        if url:
            urls.append(url)

    # 视频（page_info 中）
    page_info = status.get("page_info", {})
    if page_info and page_info.get("type") == "video":
        video_url = extract_video_from_dict(page_info.get("urls", {}))
        if video_url:
            urls.append(video_url)

    return urls
```

---

## 第六步：解析视频组件页（video.weibo.com）

视频页走 POST 接口：

```python
async def parse_video_weibo(url: str, cookies: str) -> dict:
    """解析 video.weibo.com 视频组件页。"""
    video_id = extract_video_id(url)
    api_url = f"https://weibo.com/tv/api/component?page=/tv/show/{video_id}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"https://weibo.com/tv/show/{video_id}",
        "Cookie": cookies,
        "Content-Type": "application/x-www-form-urlencoded",
    }

    payload = {"data": json.dumps({"Component_Play_Playinfo": {"oid": video_id}})}

    async with httpx.AsyncClient(headers=headers) as client:
        resp = await client.post(api_url, data=payload)
        data = resp.json()

    playinfo = data.get("data", {}).get("Component_Play_Playinfo", {})

    # 视频 URL
    urls_dict = playinfo.get("urls", {})
    video_url = extract_video_from_dict(urls_dict)

    # 元信息
    desc = playinfo.get("title", "") or playinfo.get("content1", "")
    screen_name = playinfo.get("author", "") or playinfo.get("author_name", "")
    user_id = str(playinfo.get("author_id", ""))

    author = f"{screen_name}(uid:{user_id})" if screen_name and user_id else screen_name

    video_urls = [[video_url]] if video_url else []

    return {
        "url": url,
        "title": "",
        "author": author,
        "desc": desc,
        "timestamp": "",
        "video_urls": video_urls,
        "image_urls": [],
    }
```

---

## 第七步：完整的解析流程

将三种解析方式统一入口：

```python
async def parse_weibo(url: str) -> dict:
    """
    解析微博链接，自动判断类型并选择对应的解析方式。
    """
    # 1. 获取访客 Cookie
    cookies = await get_visitor_cookies()

    # 2. 判断类型并解析
    url_type = get_url_type(url)

    if url_type == "weibo_com":
        result = await parse_weibo_com(url, cookies)
    elif url_type == "m_weibo_cn":
        result = await parse_m_weibo_cn(url, cookies)
    elif url_type == "video_weibo":
        result = await parse_video_weibo(url, cookies)
    else:
        raise ValueError(f"不支持的微博链接: {url}")

    return result


# 使用示例
import asyncio
result = asyncio.run(parse_weibo("https://weibo.com/1234567890/AbCdEfGhI"))
print(f"作者: {result['author']}")
print(f"内容: {result['desc'][:100]}")
if result["video_urls"]:
    print(f"视频: {len(result['video_urls'])} 个")
if result["image_urls"]:
    print(f"图片: {len(result['image_urls'])} 张")
```

---

## 进阶：热门评论

微博的热评可以从 `weibo.com/ajax/statuses/buildComments` 获取，需要 Cookie 和上一步拿到的 `status_id`：

```python
async def fetch_hot_comments(cookies: str, status_id: str, uid: str = "", count: int = 5) -> list[dict]:
    """获取微博热门评论。"""
    params = {
        "id": status_id,
        "flow": 0,
        "is_reload": 1,
        "is_show_bulletin": 2,
        "is_mix": 0,
        "count": max(20, count),
    }
    if uid:
        params["uid"] = uid

    # 取 XSRF-Token
    xsrf_token = ""
    for item in cookies.split("; "):
        if item.startswith("XSRF-TOKEN="):
            xsrf_token = item.split("=", 1)[1]
            break

    headers = {
        "User-Agent": "Mozilla/5.0 ...",
        "Referer": f"https://weibo.com/0/{status_id}",
        "Cookie": cookies,
        "X-Requested-With": "XMLHttpRequest",
    }
    if xsrf_token:
        headers["X-XSRF-Token"] = xsrf_token

    async with httpx.AsyncClient(headers=headers) as client:
        resp = await client.get(
            "https://weibo.com/ajax/statuses/buildComments",
            params=params,
        )
        data = resp.json()

    comments = []
    for item in (data.get("data") or []):
        if not isinstance(item, dict):
            continue
        user = item.get("user", {})
        message = clean_html_text(item.get("text_raw", "") or item.get("text", ""))
        if not message:
            continue
        comments.append({
            "username": user.get("screen_name", ""),
            "uid": str(user.get("id", "")),
            "likes": int(item.get("like_counts", 0) or 0),
            "message": message,
        })

    comments.sort(key=lambda x: x["likes"], reverse=True)
    return comments[:count]
```

---

## 总结

微博解析的完整链路：

```
微博链接
  ↓
获取访客 Cookie（自动，无需登录）
  ↓
判断 URL 类型
  │
  ├─ weibo.com
  │   → weibo.com/ajax/statuses/show?id={page_id}
  │   → 提取 mix_media_info / pic_infos / page_info 等各处的媒体
  │
  ├─ m.weibo.cn
  │   → m.weibo.cn/detail/{blog_id}
  │   → 从 HTML 提取 var $render_data = [...][0]
  │   → 提取 pics / page_info 中的媒体
  │
  └─ video.weibo.com
      → weibo.com/tv/api/component (POST)
      → Component_Play_Playinfo → urls
  ↓
组装标准化的元数据
```

**核心要点：**

1. **先判断链接类型**，再选择对应的数据源——三种类型各有不同的取数方式
2. **访客 Cookie** 可以通过 `genvisitor2` 接口自动获取，不需要用户登录
3. **媒体提取**要检查多个字段：`mix_media_info`、`pic_infos`、`pics`、`page_info`、`video_info`
4. **正文 HTML** 包含大量标签和表情，需要清理成纯文本
5. **视频和图片的分离**靠 URL 关键字（`.mp4`、`video`、`stream` 等）
