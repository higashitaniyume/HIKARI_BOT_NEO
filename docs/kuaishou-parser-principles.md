# 从零开始用 Python 解析快手分享链接

快手（Kuaishou）的分享链接以 `v.kuaishou.com` 短链为主，真实页面可能落在 `kuaishou.com`、`gifshow.com` 或 `chenzhongtech.com` 这几个域名下。和抖音/小红书类似，快手也没有公开 API，数据藏在页面的 SSR 状态里。

本文从零开始，用 Python 解析快手的视频和图集。

---

## 准备工作

```bash
pip install httpx
```

---

## 第一步：理解快手的链接和域名

```
# 短链（最常见）
v.kuaishou.com/xxxxx

# 长链
www.kuaishou.com/fw/photo/xxxxx

# gifshow（数据最完整）
m.gifshow.com/fw/photo/xxxxx

# chenzhongtech（数据稀疏，需要改写）
chenzhongtech.com/fw/photo/xxxxx
```

关键区别：**`m.gifshow.com` 的 SSR 数据最完整**，`chenzhongtech.com` 的 SSR 数据极其稀疏。所以收到 `chenzhongtech.com` 的链接后，要把它改写为 `m.gifshow.com`。

```python
import re
from urllib.parse import urlparse
import httpx

MOBILE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def to_gifshow_url(url: str) -> str:
    """将 chenzhongtech/gifshow 等域名转成 m.gifshow.com（SSR 数据最完整）。"""
    parsed = urlparse(url)
    path = parsed.path

    # /fw/photo/{id} 格式保持不变，只换域名
    photo_match = re.search(r"/fw/photo/([^/?]+)", path)
    if photo_match:
        photo_id = photo_match.group(1)
        return f"https://m.gifshow.com/fw/photo/{photo_id}"

    return f"https://m.gifshow.com{path}"


def is_kuaishou_domain(url: str) -> bool:
    """检查是否为快手系域名。"""
    domains = ("kuaishou.com", "gifshow.com", "chenzhongtech.com")
    return any(d in url.lower() for d in domains)
```

---

## 第二步：获取页面 HTML

快手的数据获取流程：短链 → 302 重定向 → 目标域名 → 改写（如果需要） → 请求 HTML。

```python
async def fetch_html(url: str) -> str:
    """
    获取快手页面的 HTML 内容。
    自动处理短链展开和域名改写。
    """
    async with httpx.AsyncClient(headers=MOBILE_HEADERS) as client:
        # 短链处理
        if "v.kuaishou.com" in urlparse(url).netloc:
            resp = await client.get(url, allow_redirects=False)
            if resp.status_code != 302:
                raise RuntimeError(f"短链展开失败: {url}")

            location = resp.headers.get("Location", "")
            if not location:
                raise RuntimeError(f"短链没有 Location 头: {url}")

            # 如果跳转到了非快手域名，改写为 gifshow
            if "kuaishou.com" not in urlparse(location).netloc.lower():
                location = to_gifshow_url(location)
        else:
            location = url

        # 改写 chenzhongtech → gifshow
        if "chenzhongtech.com" in urlparse(location).netloc.lower():
            location = to_gifshow_url(location)

        # 请求页面
        resp = await client.get(location)
        if resp.status_code != 200:
            raise RuntimeError(f"获取页面失败: {location}, status={resp.status_code}")

        return resp.text
```

---

## 第三步：从 SSR 数据中提取结构

快手新版分享页在 SSR 时会将数据注入到 `INIT_STATE` 或 `__APOLLO_STATE__` 中。数据结构大致如下：

```python
import json

def extract_ssr_state(html: str) -> dict | None:
    """从 HTML 中提取 INIT_STATE 或 __APOLLO_STATE__。"""
    patterns = [
        r'<script>\s*window\.INIT_STATE\s*=\s*(.*?)\s*</script>',
        r'<script>\s*window\.__APOLLO_STATE__\s*=\s*(.*?)\s*</script>',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.DOTALL)
        if match:
            raw = match.group(1).rstrip(";").strip()
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                continue
    return None
```

SSR 状态的结构类似这样（简化）：

```json
{
  "key1": {
    "photo": {
      "mainMvUrls": [
        {"url": "https://.../video.mp4", ...}
      ],
      "coverUrls": [
        {"url": "https://.../cover.jpg", ...}
      ],
      "type": 0,
      "userName": "作者名",
      "userId": 123456789,
      "caption": "作品标题",
      "timestamp": 1700000000,
      "ext_params": "{\"atlas\": {...}}"
    },
    "single": {
      "cdnList": [{"cdn": "p3.a.yximgs.com"}],
      "music": "/ufile/atlas/xxx.m4a"
    }
  }
}
```

关键字段：

| 字段 | 含义 |
|------|------|
| `photo.mainMvUrls` | 视频播放地址列表 |
| `photo.coverUrls` | 封面地址列表 |
| `photo.type` | `1`=图集，其他=视频 |
| `photo.ext_params.atlas` | 图集的图片列表和 CDN |
| `single.cdnList` | 图集的 CDN 服务器列表 |
| `single.music` | 背景音乐路径 |
| `photo.userName` / `photo.userId` | 作者信息 |
| `photo.caption` | 作品标题/描述 |
| `photo.timestamp` | 发布时间 |

```python
def parse_ssr_data(state: dict) -> dict | None:
    """
    从 INIT_STATE SSR 中提取视频/图集信息。
    
    Returns:
        {
            "type": "video" | "album",
            "video_url": str,        # 视频类型
            "video_cover_urls": [str],
            "image_url_lists": [[str]],  # 图集类型，每个元素是一个候选 URL 列表
            "bgm": str | None,
            "photo": dict,           # 原始 photo 数据（用于提取作者等）
        }
    """
    # 找到第一个包含 photo 的 value
    photo_data = None
    single_data = None

    for val in state.values():
        if not isinstance(val, dict) or "photo" not in val:
            continue
        photo_raw = val["photo"]
        if isinstance(photo_raw, str):
            try:
                photo_raw = json.loads(photo_raw)
            except (json.JSONDecodeError, ValueError):
                continue
        if isinstance(photo_raw, dict):
            photo_data = photo_raw
            single_raw = val.get("single")
            if isinstance(single_raw, str):
                try:
                    single_data = json.loads(single_raw)
                except (json.JSONDecodeError, ValueError):
                    single_data = None
            elif isinstance(single_raw, dict):
                single_data = single_raw
            break

    if not photo_data:
        return None

    # ----- 视频 -----
    mv_urls = photo_data.get("mainMvUrls") or []
    video_urls = [
        item["url"] for item in mv_urls
        if isinstance(item, dict) and item.get("url") and ".mp4" in item["url"]
    ]
    if video_urls:
        return {
            "type": "video",
            "video_url": minify_mp4(video_urls[0]),  # 清理 URL
            "video_cover_urls": [
                item["url"] for item in (photo_data.get("coverUrls") or [])
                if isinstance(item, dict) and item.get("url")
            ],
            "photo": photo_data,
        }

    # ----- 图集 -----
    # 从 ext_params.atlas 或 single 中提取
    ext_params = photo_data.get("ext_params")
    if isinstance(ext_params, str):
        ext_params = json.loads(ext_params)

    atlas_data = ext_params.get("atlas") if ext_params else None
    if isinstance(atlas_data, str):
        atlas_data = json.loads(atlas_data)

    if not atlas_data and single_data:
        # 从 single 中提取 CDN 列表
        cdns = [
            item["cdn"] for item in (single_data.get("cdnList") or [])
            if isinstance(item, dict) and item.get("cdn")
        ]
        music_path = single_data.get("music")

        # 检查 photo 里是否有图集路径
        img_paths = extract_atlas_paths(photo_data)
        if img_paths:
            album = build_album(cdns, music_path, img_paths)
            if album:
                album["photo"] = photo_data
                return album

    if photo_data.get("type") == 1 and isinstance(atlas_data, dict):
        atlas_cdns = [
            item["cdn"] for item in (atlas_data.get("cdnList") or [])
            if isinstance(item, dict) and item.get("cdn")
        ]
        atlas_list = atlas_data.get("list", [])
        if isinstance(atlas_list, str):
            atlas_list = [atlas_list]

        album = build_album(atlas_cdns, atlas_data.get("music"), atlas_list)
        if album:
            album["photo"] = photo_data
            return album

    return None
```

### URL 清理

快手的 MP4 URL 里有很多追踪参数，可以用一个最小化函数清理：

```python
def minify_mp4(url: str) -> str:
    """清理 MP4 URL，去掉不必要的参数。"""
    parsed = urlparse(url)
    domain = parsed.netloc
    filename = parsed.path.split("/")[-1].split("?")[0]
    path_wo_file = "/".join(parsed.path.split("/")[1:-1])
    return f"https://{domain}/{path_wo_file}/{filename}"
```

### 图集构建

快手的图集图片通常不是完整 URL，而是 CDN 前缀 + 图片路径的组合：

```python
def build_album(cdns: list[str], music_path: str | None, img_paths: list[str]) -> dict | None:
    """
    构建图集数据。CDN 和图片路径组合成完整 URL。
    """
    # 清理 CDN（去掉 https:// 前缀）
    cdns = [re.sub(r"https?://", "", cdn) for cdn in cdns if cdn]
    if not cdns or not img_paths:
        return None

    # 清理路径
    img_paths = [p.strip('"') for p in img_paths if p.strip('"')]
    if not img_paths:
        return None

    # 每个图片构建候选 URL 列表（多个 CDN 作为备选）
    image_url_lists = []
    for img_path in img_paths:
        url_list = [f"https://{cdn}{img_path}" for cdn in cdns]
        if url_list:
            image_url_lists.append(url_list)

    # 去重
    seen = set()
    uniq_lists = []
    for url_list in image_url_lists:
        if url_list[0] not in seen:
            seen.add(url_list[0])
            uniq_lists.append(url_list)

    bgm = None
    if music_path and cdns:
        bgm = f"https://{cdns[0]}{music_path}"

    return {"type": "album", "image_url_lists": uniq_lists, "bgm": bgm}


def extract_atlas_paths(photo_data: dict) -> list[str]:
    """从 photo 数据中提取图集图片路径。"""
    # 尝试从 ext_params 中提取
    ext_params = photo_data.get("ext_params")
    if isinstance(ext_params, str):
        ext_params = json.loads(ext_params)

    atlas = (ext_params or {}).get("atlas") if ext_params else None
    if isinstance(atlas, str):
        atlas = json.loads(atlas)

    if isinstance(atlas, dict):
        atlas_list = atlas.get("list", [])
        if isinstance(atlas_list, str):
            atlas_list = [atlas_list]
        return atlas_list

    return []
```

---

## 第四步：提取元数据

除了 SSR 中的 `photo` 数据，还需要从 HTML 中用正则兜底提取作者和标题：

```python
def extract_metadata(html: str) -> dict:
    """
    提取作者名、用户 ID 和标题。
    优先从 SSR 中取，取不到再用正则从 HTML 扫。
    """
    metadata = {"userName": None, "userId": None, "caption": None}

    ssr_state = extract_ssr_state(html)
    if ssr_state:
        for val in ssr_state.values():
            if not isinstance(val, dict):
                continue
            photo = val.get("photo", {})
            if isinstance(photo, str):
                try:
                    photo = json.loads(photo)
                except (json.JSONDecodeError, ValueError):
                    photo = {}
            if isinstance(photo, dict):
                metadata["userName"] = metadata["userName"] or photo.get("userName")
                metadata["caption"] = metadata["caption"] or photo.get("caption")
                uid = photo.get("userId")
                if uid is not None:
                    metadata["userId"] = str(uid)

    # SSR 没拿到的话，用正则兜底
    if not metadata["userName"]:
        m = re.search(r'"userName"\s*:\s*"([^"]+)"', html)
        if m:
            metadata["userName"] = m.group(1)

    if not metadata["userId"]:
        m = re.search(r'"userId"\s*:\s*["\']?(\d+)["\']?', html)
        if m:
            metadata["userId"] = m.group(1)

    if not metadata["caption"]:
        m = re.search(r'"caption"\s*:\s*"([^"]+)"', html)
        if m:
            metadata["caption"] = m.group(1)
        else:
            # 从 title 标签取
            m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE)
            if m:
                title = m.group(1).strip()
                if title and title not in ("快手", "快手视频"):
                    metadata["caption"] = title

    return metadata
```

---

## 第五步：旧版页面和有选择地兜底

不是所有快手链接都会提供 SSR 状态。历史链接和一些非标准分享页可能只有旧版字段。需要多层兜底：

```python
async def parse_video_html(html: str) -> str | None:
    """从 HTML 中提取视频 URL（正则兜底）。"""
    # 多个可能的字段名
    patterns = [
        r'"(?:url|srcNoMark|photoUrl|videoUrl)"\s*:\s*"(https?://[^"]+?\.mp4[^"]*)"',
        r'"url"\s*:\s*"(https?://[^"]+?\.mp4[^"]*)"',
    ]
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            return minify_mp4(m.group(1))
    return None


async def parse_album_html(html: str) -> dict | None:
    """从 HTML 中提取图集（正则兜底）。"""
    # 提取 CDN 列表
    cdn_matches = re.findall(r'"cdnList"\s*:\s*\[.*?"cdn"\s*:\s*"([^"]+)"', html, re.DOTALL)
    if not cdn_matches:
        cdn_matches = re.findall(r'"cdn"\s*:\s*"([^"]+)"', html)
    if not cdn_matches:
        return None

    cdns = list(set(cdn_matches))

    # 提取图片路径
    img_paths = re.findall(r'"/ufile/atlas/[^"]+?\.jpg"', html)
    if not img_paths:
        return None

    # 背景音乐
    m = re.search(r'"music"\s*:\s*"(/ufile/atlas/[^"]+?\.m4a)"', html)
    music_path = m.group(1) if m else None

    return build_album(cdns, music_path, img_paths)
```

---

## 第六步：完整的解析流程

```python
from datetime import datetime


def build_author(metadata: dict) -> str:
    """构建作者显示名。"""
    name = metadata.get("userName", "")
    uid = metadata.get("userId", "")
    if name and uid:
        return f"{name}(uid:{uid})"
    return name or uid or ""


def extract_timestamp_from_photo(photo: dict | None, fallback_url: str | None = None) -> str:
    """从 photo 数据或 URL 中提取时间。"""
    if photo:
        ts = photo.get("timestamp")
        if ts and isinstance(ts, (int, float)):
            if ts > 1e12:
                ts //= 1000
            return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")

    if fallback_url:
        # 从 URL 路径中提取时间（如 /2024/01/15/）
        m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", fallback_url)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    return ""


async def parse_kuaishou(url: str) -> dict:
    """
    解析快手链接，支持视频和图集。
    """
    # 1. 获取页面 HTML（含短链展开和域名改写）
    html = await fetch_html(url)

    # 2. 提取元数据（作者、标题）
    metadata = extract_metadata(html)
    author = build_author(metadata)
    title = (metadata.get("caption") or "快手视频")[:100]

    # 3. 优先从 SSR 结构化数据中提取
    ssr_state = extract_ssr_state(html)
    ssr_result = parse_ssr_data(ssr_state) if ssr_state else None

    image_headers = {
        "User-Agent": MOBILE_HEADERS["User-Agent"],
        "Referer": "https://www.kuaishou.com/",
    }
    video_headers = {
        "User-Agent": MOBILE_HEADERS["User-Agent"],
        "Referer": "https://www.kuaishou.com/",
    }

    if ssr_result:
        photo = ssr_result.get("photo", {})

        if ssr_result["type"] == "video":
            timestamp = extract_timestamp_from_photo(photo, ssr_result["video_url"])
            return {
                "url": url,
                "title": title,
                "author": author,
                "desc": "",
                "timestamp": timestamp,
                "video_urls": [[ssr_result["video_url"]]],
                "image_urls": [],
                "image_headers": image_headers,
                "video_headers": video_headers,
            }

        if ssr_result["type"] == "album":
            image_url_lists = ssr_result.get("image_url_lists", [])
            first_url = image_url_lists[0][0] if image_url_lists and image_url_lists[0] else None
            timestamp = extract_timestamp_from_photo(photo, first_url)
            return {
                "url": url,
                "title": title or "快手图集",
                "author": author,
                "desc": "",
                "timestamp": timestamp,
                "video_urls": [],
                "image_urls": image_url_lists,
                "image_headers": image_headers,
                "video_headers": video_headers,
            }

    # 4. SSR 拿不到，用正则兜底
    video_url = await parse_video_html(html)
    if video_url:
        return {
            "url": url,
            "title": title,
            "author": author,
            "desc": "",
            "timestamp": extract_timestamp_from_photo(None, video_url),
            "video_urls": [[video_url]],
            "image_urls": [],
            "image_headers": image_headers,
            "video_headers": video_headers,
        }

    album = await parse_album_html(html)
    if album:
        image_url_lists = album.get("image_url_lists", [])
        timestamp = extract_timestamp_from_photo(None, image_url_lists[0][0]) if image_url_lists else ""
        return {
            "url": url,
            "title": title or "快手图集",
            "author": author,
            "desc": "",
            "timestamp": timestamp,
            "video_urls": [],
            "image_urls": image_url_lists,
            "image_headers": image_headers,
            "video_headers": video_headers,
        }

    raise RuntimeError(f"无法解析快手链接: {url}")


# 使用示例
import asyncio
result = asyncio.run(parse_kuaishou("https://v.kuaishou.com/xxxxx"))
print(f"标题: {result['title']}")
print(f"作者: {result['author']}")
if result["video_urls"]:
    print("类型: 视频")
else:
    print(f"类型: 图集 ({len(result['image_urls'])} 张图)")
```

---

## 总结

快手解析的完整链路：

```
v.kuaishou.com/xxxxx
  ↓
302 重定向到真实链接
  ↓
域名改写：chenzhongtech → m.gifshow.com（SSR 数据更完整）
  ↓
请求 HTML → 提取 SSR 数据（INIT_STATE / __APOLLO_STATE__）
  ↓
判断视频还是图集：
  ├─ 视频：photo.mainMvUrls → minify MP4 URL
  └─ 图集：ext_params.atlas.list + single.cdnList → 构建候选 URL
  ↓
SSR 失败？→ 正则兜底（旧页面格式）
  ↓
组装标准化元数据
```

**核心要点：**

1. **域名改写很关键**——`m.gifshow.com` 的 SSR 数据最完整，遇到 `chenzhongtech.com` 要改写过去
2. **SSR 状态优先**——`INIT_STATE` 或 `__APOLLO_STATE__` 里包含完整的结构化数据
3. **图集图片需要组合**——CDN 前缀 + 图片路径才能得到可访问的完整 URL
4. **多层兜底**——SSR 取不到用正则，正则取不到用 rawData
5. **视频和图集不同**——`type=1` 是图集，其他是视频
6. **URL 清理**——MP4 URL 含大量追踪参数，`minify_mp4()` 可以精简到最简形式
