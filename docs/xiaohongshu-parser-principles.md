# 从零开始用 Python 解析小红书笔记

小红书的分享链接以 `xhslink.com` 短链或 `xiaohongshu.com/explore/` 长链的形式出现。和抖音类似，小红书也没有公开 API，但它的 Web 页面里注入了完整的笔记数据——藏在 `window.__INITIAL_STATE__` 中。

本文从零开始，用 Python 解析小红书笔记，支持视频笔记和图文笔记两种形态。

---

## 准备工作

```bash
pip install httpx
```

---

## 第一步：理解笔记链接

```
# 短链（最常见）
xhslink.com/xxxxx

# 移动端笔记
www.xiaohongshu.com/discovery/item/64abc123456789

# PC 端笔记（含 xsec_token 等参数）
www.xiaohongshu.com/explore/64abc123456789?xsec_token=...
```

关键区别：

- **移动端**链接（`/discovery/item/`）：参数简单，页面轻量
- **PC 端**链接（`/explore/`）：包含 `xsec_token` 等安全参数，**不能随意删除**，否则页面可能不返回完整数据

短链需要先展开：

```python
import re
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import httpx

# 移动端 UA（请求移动版页面，数据更完整）
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Mobile Safari/537.36"
)

# PC 端 UA
PC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)


async def expand_short_url(url: str) -> str:
    """展开 xhslink.com 短链，获取真实 URL。"""
    headers = {"User-Agent": MOBILE_UA}

    async with httpx.AsyncClient(headers=headers, allow_redirects=False) as client:
        resp = await client.get(url)
        if resp.status_code == 302:
            from urllib.parse import unquote
            return unquote(resp.headers.get("Location", ""))

    # 没有 302 重定向的话，跟着 redirect 走
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        resp = await client.get(url)
        return str(resp.url)
```

---

## 第二步：清理 URL 参数

移动端分享链接携带的 `source`、`xhsshare` 等参数是追踪用的，删掉不影响数据。但 **PC 端链接的 `xsec_token` 等参数不能删**。

```python
def is_pc_url(url: str) -> bool:
    """判断是否为 PC 端链接。"""
    url_lower = url.lower()
    return "/explore/" in url_lower or "xsec_source=pc" in url_lower


def clean_share_url(url: str) -> str:
    """
    清理分享链接参数。
    移动端去掉 source/xhsshare，PC 端保留原样。
    """
    if is_pc_url(url):
        return url  # PC 端参数不能删

    if "discovery/item" not in url:
        return url

    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)

    # 只删 source 和 xhsshare
    query.pop("source", None)
    query.pop("xhsshare", None)

    # 重建 query
    flat = {k: v[0] for k, v in query.items() if v}
    new_query = urlencode(flat)
    return urlunparse(parsed._replace(query=new_query))
```

---

## 第三步：获取页面和初始化状态

小红书的页面数据在 `window.__INITIAL_STATE__` 里，是一个内联的 JSON 对象：

```python
import json

async def fetch_initial_state(url: str) -> dict:
    """
    获取小红书笔记页的 HTML，提取 window.__INITIAL_STATE__。
    """
    # 选择请求头：PC 端用 PC UA，移动端用移动 UA
    ua = PC_UA if is_pc_url(url) else MOBILE_UA
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        resp = await client.get(url)
        html = resp.text

    # 用正则提取 __INITIAL_STATE__
    # 方法 1：标准 script 标签格式
    pattern = r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>'
    match = re.search(pattern, html, re.DOTALL)
    if match:
        json_str = match.group(1)
        json_str = re.sub(r'\bundefined\b', 'null', json_str)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    # 方法 2：手动花括号匹配（应付格式不标准的情况）
    start_marker = "window.__INITIAL_STATE__"
    start_idx = html.find(start_marker)
    if start_idx == -1:
        raise RuntimeError("未找到 window.__INITIAL_STATE__")

    json_start = html.find("{", start_idx)
    script_end = html.find("</script>", start_idx)
    search_end = script_end if script_end != -1 else len(html)

    # 数花括号找到完整 JSON
    brace_count = 0
    in_string = False
    escape_next = False
    json_end = json_start

    for i in range(json_start, search_end):
        c = html[i]
        if escape_next:
            escape_next = False
            continue
        if c == "\\":
            escape_next = True
            continue
        if c == '"' and not escape_next:
            in_string = not in_string
            continue
        if not in_string:
            if c == "{":
                brace_count += 1
            elif c == "}":
                brace_count -= 1
                if brace_count == 0:
                    json_end = i + 1
                    break

    if brace_count != 0:
        raise RuntimeError("无法找到完整的 JSON 对象")

    json_str = html[json_start:json_end]
    json_str = re.sub(r'\bundefined\b', 'null', json_str)
    return json.loads(json_str)
```

---

## 第四步：从初始化状态中提取笔记数据

移动端和 PC 端的数据路径不同：

```
移动端:  state.noteData.data.noteData
PC 端:   state.note.noteDetailMap[noteId].note
```

```python
def extract_note_data(state: dict) -> dict:
    """
    从 __INITIAL_STATE__ 中提取笔记数据。
    
    Returns:
        {
            "type": "video" | "normal",
            "title": str,
            "desc": str,
            "author_name": str,
            "author_id": str,
            "timestamp": str,   # YYYY-MM-DD
            "video_url": str,   # 视频笔记才有
            "image_urls": [str],  # 图文笔记才有
        }
    """
    from datetime import datetime

    note_data = None
    user_data = {}

    # 优先尝试移动端路径
    try:
        note_data = state["noteData"]["data"]["noteData"]
        user_data = note_data.get("user", {})
    except (KeyError, TypeError):
        pass

    # 再试 PC 端路径
    if not note_data:
        try:
            detail_map = state.get("note", {}).get("noteDetailMap", {})
            for detail in detail_map.values():
                potential = detail.get("note")
                if potential and isinstance(potential, dict):
                    note_data = potential
                    user_data = note_data.get("user", {})
                    break
        except (KeyError, TypeError):
            pass

    if not note_data:
        raise RuntimeError("无法找到笔记数据（移动端和 PC 端路径都失败）")

    # --- 基本信息 ---
    note_type = note_data.get("type", "normal")  # "video" 或 "normal"
    title = note_data.get("title", "")
    desc = note_data.get("desc", "")
    author_name = user_data.get("nickName", user_data.get("nickname", ""))
    author_id = user_data.get("userId", "")

    # --- 时间处理（毫秒时间戳）---
    ts = note_data.get("time", 0)
    if ts:
        publish_time = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
    else:
        publish_time = ""

    # --- 视频媒体 ---
    video_url = ""
    if note_type == "video":
        video_info = note_data.get("video", {})
        media = video_info.get("media", {})
        stream = media.get("stream", {})
        h264_streams = stream.get("h264", [])
        if h264_streams:
            video_url = h264_streams[0].get("masterUrl", "")

        # 统一协议
        if video_url.startswith("//"):
            video_url = "https:" + video_url
        elif video_url.startswith("http://"):
            video_url = video_url.replace("http://", "https://", 1)

    # --- 图片媒体 ---
    image_urls = []
    if note_type != "video":
        for img in note_data.get("imageList", []):
            if not isinstance(img, dict):
                continue

            url = None
            if img.get("urlDefault"):
                url = img["urlDefault"]
            elif img.get("url"):
                url = img["url"]

            # infoList 里的 WB_DFT 场景通常是最大图
            if not url:
                for info in img.get("infoList", []):
                    if isinstance(info, dict) and info.get("imageScene") == "WB_DFT":
                        url = info.get("url")
                        break

            if url:
                if url.startswith("//"):
                    url = "https:" + url
                elif url.startswith("http://"):
                    url = url.replace("http://", "https://", 1)
                image_urls.append(url)

    # --- 清理话题标签 ---
    desc = re.sub(r'#([^#\[]+)\[话题\]#', r'#\1', desc)

    return {
        "type": note_type,
        "title": title,
        "desc": desc,
        "author_name": author_name,
        "author_id": author_id,
        "publish_time": publish_time,
        "video_url": video_url,
        "image_urls": image_urls,
    }
```

**关键点：**

- **视频地址**在 `video.media.stream.h264[0].masterUrl`，需要处理协议前缀（`//` 或 `http://`）
- **图片地址**在 `imageList[].urlDefault`，备选 `infoList[].url`（取 `imageScene == "WB_DFT"` 的最大尺寸）
- 话题标签格式如 `#标签[话题]#`，需要清理成 `#标签`
- **时间戳是毫秒级**（13 位数），需要除以 1000

---

## 第五步：处理不同笔记类型

视频笔记和图集笔记用不同的字段输出：

```python
def build_result(url: str, note_data: dict) -> dict:
    """根据笔记类型构建标准化的结果。"""
    author = ""
    name, uid = note_data["author_name"], note_data["author_id"]
    if name and uid:
        author = f"{name}(主页id:{uid})"
    elif name:
        author = name

    image_headers = {
        "User-Agent": MOBILE_UA,
        "Referer": url,
    }
    video_headers = {
        "User-Agent": MOBILE_UA,
        "Referer": url,
    }

    if note_data["type"] == "video":
        if not note_data["video_url"]:
            raise RuntimeError("视频笔记但没有视频 URL")
        return {
            "url": url,
            "title": note_data["title"],
            "author": author,
            "desc": note_data["desc"],
            "timestamp": note_data["publish_time"],
            "video_urls": [[note_data["video_url"]]],
            "image_urls": [],
            "image_headers": image_headers,
            "video_headers": video_headers,
        }
    else:
        if not note_data["image_urls"]:
            raise RuntimeError("图文笔记但没有图片 URL")
        return {
            "url": url,
            "title": note_data["title"],
            "author": author,
            "desc": note_data["desc"],
            "timestamp": note_data["publish_time"],
            "video_urls": [],
            "image_urls": [[url] for url in note_data["image_urls"]],
            "image_headers": image_headers,
            "video_headers": video_headers,
        }
```

注意 `image_urls` 的格式是 **二维列表**：`[[img1_url], [img2_url]]`，每个内层列表可以包含同一张图片的多个候选地址。

---

## 第六步：完整的解析流程

```python
async def parse_xiaohongshu(url: str) -> dict:
    """
    解析小红书链接。
    
    支持格式：
    - xhslink.com 短链
    - xiaohongshu.com/discovery/item/{id}
    - xiaohongshu.com/explore/{id}
    """
    # 1. 展开短链
    if "xhslink.com" in url.lower():
        url = await expand_short_url(url)

    # 2. 清理参数（仅移动端）
    url = clean_share_url(url)

    # 3. 获取 __INITIAL_STATE__
    state = await fetch_initial_state(url)

    # 4. 提取笔记数据
    note_data = extract_note_data(state)

    # 5. 构建结果
    return build_result(url, note_data)


# 使用示例
import asyncio
result = asyncio.run(parse_xiaohongshu("https://xhslink.com/xxxxx"))
print(f"标题: {result['title']}")
print(f"作者: {result['author']}")
if result['video_urls']:
    print("类型: 视频笔记")
else:
    print(f"类型: 图文笔记 ({len(result['image_urls'])} 张图)")
```

---

## 进阶：提取热门评论

小红书页面状态里通常会带评论数据，可以从 `__INITIAL_STATE__` 中提取：

```python
def extract_hot_comments(state: dict, max_count: int = 5) -> list[dict]:
    """从 __INITIAL_STATE__ 中提取热门评论（按赞数排序）。"""
    if max_count <= 0:
        return []

    comments = []

    # 路径 1：移动端
    try:
        comments = state["noteData"]["data"]["commentData"]["comments"]
    except (KeyError, TypeError):
        pass

    # 路径 2：PC 端
    if not comments:
        try:
            for detail in state.get("note", {}).get("noteDetailMap", {}).values():
                comments_list = detail.get("comments", {}).get("list", [])
                if comments_list:
                    comments = comments_list
                    break
        except (KeyError, TypeError):
            pass

    # 归一化
    result = []
    seen = set()
    for item in comments:
        if not isinstance(item, dict):
            continue
        user = item.get("user", item.get("userInfo", {}))
        uid = user.get("userId", user.get("user_id", ""))
        username = user.get("nickname", user.get("nickName", ""))
        content = item.get("content", item.get("text", ""))
        likes = item.get("likeCount", item.get("like_count", 0))

        if not content:
            continue
        key = (uid, content[:50])
        if key in seen:
            continue
        seen.add(key)

        result.append({
            "username": str(username),
            "uid": str(uid),
            "likes": int(likes or 0),
            "message": str(content).replace("\n", " ").strip(),
        })

    result.sort(key=lambda x: x["likes"], reverse=True)
    return result[:max_count]
```

---

## 总结

小红书解析的完整链路：

```
xhslink.com/xxxxx  或  xiaohongshu.com/explore/{id}
  ↓
展开短链（xhslink.com → 302 → 真实 URL）
  ↓
清理参数（仅移动端去掉 source/xhsshare）
  ↓
选择请求头：PC UA（PC 端） / Mobile UA（移动端）
  ↓
请求页面，提取 window.__INITIAL_STATE__
  ↓
按路径提取笔记数据：
  ├─ 移动端: noteData.data.noteData
  └─ PC 端:   note.noteDetailMap[*].note
  ↓
判断类型：
  ├─ "video" → video.media.stream.h264[].masterUrl
  └─ "normal" → imageList[].urlDefault
  ↓
组装标准化的元数据
```

**核心要点：**

1. **`window.__INITIAL_STATE__`** 是一切数据的来源——标题、作者、时间、媒体地址都在里面
2. **移动端和 PC 端的数据路径不同**，解析器需要兼容两套结构
3. **参数处理要谨慎**：移动端的 `source`/`xhsshare` 可删，PC 端的 `xsec_token` 不能删
4. **视频地址**从 stream.h264 中取 `masterUrl`；**图片地址**从 `imageList` 的 `urlDefault` 或 `infoList` 中取
5. **话题标签**带有 `#xxx[话题]#` 格式，提取后记得清理
6. **评论数据**可能随页面状态下发，无需额外请求
