# Pixiv 作品解析原理

## 概述

Pixiv 是一个日本插画、漫画和小说创作平台。要自动化获取 Pixiv 的作品信息与图片，核心思路是**模拟浏览器访问 Pixiv Web 端，调用其 Ajax 内部 API**。这套接口返回 JSON 数据，结构清晰，无需维护独立的 OAuth 认证流程，仅需一个有效的登录 Cookie（`PHPSESSID`）即可工作。

整个解析流程分三个阶段：

1. **URL 匹配** — 从文本中识别 Pixiv 作品链接，提取作品 ID
2. **API 调用** — 通过作品 ID 请求 Web Ajax 接口，获取元数据和图片地址
3. **下载与降级** — 优先下载原图（original），超限时自动降级到常规尺寸（regular）

---

## 一、URL 匹配

Pixiv 的作品页面有几种典型 URL 格式：

```
https://www.pixiv.net/artworks/12345678
https://www.pixiv.net/en/artworks/12345678
https://www.pixiv.net/i/12345678
https://pixiv.net/artworks/12345678
```

作品 ID 是 5～12 位纯数字。下面是一个 Python 正则匹配示例：

```python
import re

PIXIV_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?pixiv\.net"
    r"/(?:en/)?"
    r"(?:artworks|i)/(?P<id>\d{5,12})",
    re.IGNORECASE,
)

def extract_pixiv_ids(text: str) -> list[str]:
    """
    从文本中提取所有 Pixiv 作品 ID（去重，保持顺序）。
    只匹配 artworks 和 i 格式，不匹配用户主页、tag、novel 等链接。
    """
    ids: list[str] = []
    seen: set[str] = set()
    for match in PIXIV_URL_RE.finditer(text):
        illust_id = match.group("id")
        if illust_id and illust_id not in seen:
            seen.add(illust_id)
            ids.append(illust_id)
    return ids
```

**关键点：**

- 只匹配 `/artworks/` 和 `/i/` 路径——这是作品详情页的标准路径
- 可选的前缀 `en/` 用于英文版 Pixiv
- 不匹配纯数字（如用户直接发 `12345678`），避免误伤
- 去重保留顺序，同一条消息中的同一个链接只处理一次

---

## 二、Work Info API：获取作品元信息

Pixiv Web 端使用 Ajax 接口 `/ajax/illust/{illust_id}` 返回作品元数据。

### 请求示例

```python
import httpx

async def fetch_artwork_info(illust_id: str, cookie: str) -> dict:
    """
    调用 Pixiv Ajax 接口获取作品元信息。
    cookie 至少需要 PHPSESSID 字段。
    """
    headers = {
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.7103.48 Safari/537.36"
        ),
        "accept-language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7,ja-JP;q=0.6,ja;q=0.5",
        "referer": f"https://www.pixiv.net/artworks/{illust_id}",
        "cookie": cookie.strip(),
    }

    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(60.0, connect=20.0),
        follow_redirects=True,
    ) as client:
        resp = await client.get(f"https://www.pixiv.net/ajax/illust/{illust_id}")

        # 检查是否返回了 HTML（被 Cloudflare 拦截等异常情况）
        content_type = resp.headers.get("content-type", "")
        if "text/html" in content_type.lower():
            raise RuntimeError("Pixiv 返回了 HTML，Cookie 可能已失效或被 Cloudflare 拦截")

        data = resp.json()

    if data.get("error"):
        raise RuntimeError(f"API 返回错误: {data.get('message')}")

    return data["body"]
```

### 返回数据结构

该接口返回的 `body` 字段结构如下（仅列出关键字段）：

```json
{
  "illustId": "12345678",
  "illustTitle": "作品标题",
  "illustComment": "作品描述...",
  "userId": "87654321",
  "userName": "作者名",
  "createDate": "2025-03-15T12:00:00+09:00",
  "pageCount": 4,
  "xRestrict": 0,
  "aiType": 0,
  "sl": 6,
  "tags": {
    "tags": [
      {
        "tag": "オリジナル",
        "translation": { "en": "Original" }
      },
      {
        "tag": "風景",
        "translation": { "en": "Landscape" }
      }
    ]
  },
  "width": 1200,
  "height": 800
}
```

**字段说明：**

| 字段 | 含义 |
|------|------|
| `illustId` | 作品唯一 ID |
| `illustTitle` | 作品标题 |
| `userName` | 作者昵称 |
| `userId` | 作者用户 ID |
| `pageCount` | 总页数（多图作品的图片数量） |
| `xRestrict` | 分级标志：`0`=全年龄，`1`=R-18，`2`=R-18G |
| `aiType` | AI 作品标志：`0`=非 AI，`2`=AI 生成 |
| `sl` | 安全等级（sanity level），用于额外过滤 |
| `tags.tags[].tag` | 标签名（日文原始名） |
| `tags.tags[].translation.en` | 标签的英文译名（如果有） |

### 标签处理技巧

标签是嵌套结构，每个标签项除了原始日文名，还可能包含多语言翻译。实践中常优先使用英文译名：

```python
tags: list[str] = []
for item in tags_raw:
    tag_name = item.get("tag", "")
    if not tag_name:
        continue
    # 统一 R-18 标签名
    if tag_name == "R-18":
        tag_name = "R18"
    # 优先使用英文翻译
    translation = item.get("translation") or {}
    if translation.get("en"):
        tag_name = translation["en"]
    tags.append(tag_name.replace(" ", "_"))
```

---

## 三、Pages API：获取多页图片 URL

对于多图作品（漫画、系列插图等），需要额外调用 `ajax/illust/{illust_id}/pages` 接口获取每一页的图片地址。

```python
async def fetch_pages(illust_id: str, cookie: str, referer: str) -> list[dict]:
    headers = {
        "user-agent": "...",
        "referer": referer,
        "cookie": cookie.strip(),
    }
    async with httpx.AsyncClient(headers=headers) as client:
        resp = await client.get(
            f"https://www.pixiv.net/ajax/illust/{illust_id}/pages?lang=zh"
        )
        data = resp.json()

    if data.get("error"):
        raise RuntimeError(f"Pages API 返回错误: {data.get('message')}")

    return data["body"]
```

返回的 `body` 是一个数组，每个元素对应一页：

```json
[
  {
    "urls": {
      "thumb_mini": "https://i.pximg.net/c/250x250_80_a2/img-master/img/..._master1200.jpg",
      "small": "https://i.pximg.net/c/540x540_70/img-master/img/..._master1200.jpg",
      "regular": "https://i.pximg.net/c/600x600_80_a2/img-master/img/..._master1200.jpg",
      "original": "https://i.pximg.net/img-original/img/..._p0.png"
    },
    "width": 1200,
    "height": 800
  },
  { "urls": { ... }, "width": 1200, "height": 900 }
]
```

**关键 URL 字段解读：**

| 字段 | 说明 | 用途 |
|------|------|------|
| `thumb_mini` | 250×250 微缩图 | 预览列表 |
| `small` | 540×540 小图 | 列表显示 |
| `regular` | 600×600 中等图 | 默认显示，降级方案 |
| `original` | **原图**（无尺寸裁剪） | 优先下载目标 |

**两个 API 调用的关系：**

- `ajax/illust/{id}` — 获取作品元信息一次
- `ajax/illust/{id}/pages` — 获取所有页的图片 URL 数组

这两个调用可**复用同一个 HTTP 客户端**，减少连接开销。

---

## 四、数据结构设计

用数据类组织解析结果，使代码清晰可维护：

```python
from dataclasses import dataclass, field

@dataclass
class PixivPage:
    """Pixiv 作品的单页图片信息。"""
    index: int
    original_url: str
    regular_url: str
    thumb_url: str
    width: int
    height: int

@dataclass
class PixivArtwork:
    """Pixiv 作品完整信息。"""
    illust_id: str
    title: str
    user_name: str
    user_id: str
    page_count: int
    x_restrict: int
    ai_type: int
    tags: list[str] = field(default_factory=list)
    pages: list[PixivPage] = field(default_factory=list)

    @property
    def is_r18(self) -> bool:
        """x_restrict: 0 普通，1 R-18，2 R-18G"""
        return self.x_restrict in (1, 2)
```

将两个 API 的返回合并为一个 `PixivArtwork` 对象，后续的下载和展示逻辑都基于这个统一的模型操作。

---

## 五、图片下载与降级策略

Pixiv 的图片托管在 `i.pximg.net` 域名下，请求时必须携带正确的 `Referer` 头（`https://www.pixiv.net/artworks/{id}`），否则返回 403。

### 下载单张图片

```python
import hashlib
from pathlib import Path
import httpx

def cache_path_for_url(url: str, cache_dir: str) -> Path:
    """用 SHA256 哈希 URL 生成缓存路径，避免文件名冲突。"""
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        suffix = ".jpg"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return Path(cache_dir) / f"{digest}{suffix}"

async def download_image(
    url: str,
    illust_id: str,
    cookie: str,
    cache_dir: str = "/tmp/pixiv_cache",
    max_bytes: int | None = None,
) -> Path:
    """下载单张图片，支持缓存和流式写入。"""
    path = cache_path_for_url(url, cache_dir)

    # 缓存命中
    if path.exists() and path.stat().st_size > 0:
        return path

    headers = {
        "referer": f"https://www.pixiv.net/artworks/{illust_id}",
        "cookie": cookie.strip(),
    }

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".part")

        try:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()

                # 验证响应是图片
                if not resp.headers.get("content-type", "").startswith("image/"):
                    raise RuntimeError("下载到的不是图片")

                # 流式写入
                written = 0
                with tmp_path.open("wb") as f:
                    async for chunk in resp.aiter_bytes():
                        written += len(chunk)
                        if max_bytes is not None and written > max_bytes:
                            raise RuntimeError(f"图片超过大小限制")
                        f.write(chunk)

            tmp_path.replace(path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    return path
```

### 原图优先，超限降级

这是 Pixiv 下载的核心策略——**优先争取最高质量的图片，但设置安全网**：

```python
async def download_with_fallback(
    page: PixivPage,
    illust_id: str,
    cookie: str,
    cache_dir: str,
    max_file_mb: int = 25,
) -> tuple[Path, bool]:
    """
    下载图片，优先 original，超限则降级到 regular。
    
    Returns:
        (文件路径, 是否为原图)
    """
    max_bytes = max(max_file_mb, 1) * 1024 * 1024

    # 第一轮：尝试下载原图
    original_path = None
    try:
        original_path = await download_image(
            page.original_url, illust_id, cookie, cache_dir,
            max_bytes=max_bytes,
        )
    except RuntimeError as e:
        # 原图超限或失败，记录日志后尝试 regular
        pass

    if original_path is not None and original_path.stat().st_size <= max_bytes:
        return original_path, True  # 原图下载成功

    # 第二轮：降级到 regular 尺寸
    regular_path = await download_image(
        page.regular_url, illust_id, cookie, cache_dir,
        max_bytes=max_bytes,
    )

    return regular_path, False
```

**为何需要降级？** Pixiv 的原图（original）可能非常大——部分高分辨率插画单张可达 50MB 以上。对于即时通讯场景或存储受限环境，自动降级到 regular 尺寸（通常 600×600 以内，文件在 1～3MB 左右）是务实的取舍。

### 大小限制检查

检查发生在两个层面：

1. **Content-Length 预检**：如果服务器返回了 `content-length` 头且超限，直接拒绝，避免浪费流量
2. **流式累计检查**：下载过程中逐 chunk 累加，防止服务器虚报 Content-Length

---

## 六、整体流程串联

将以上各步骤串联成一个完整的解析流程：

```python
async def process_pixiv_artwork(illust_id: str, cookie: str, proxy: str = ""):
    """
    完整的 Pixiv 作品处理流程：
    1. 获取元信息 → 2. 获取图片 URL → 3. 下载图片（原图→降级）
    """
    # 步骤 1+2：调用两个 API（复用 HTTP 客户端）
    # （实现同前文 fetch_artwork_info + fetch_pages）

    # 步骤 3：构建数据模型
    artwork = PixivArtwork(
        illust_id=illust_id,
        title=body.get("illustTitle") or "Untitled",
        user_name=body.get("userName") or "Unknown",
        user_id=body.get("userId") or "",
        page_count=int(body.get("pageCount", 1)),
        x_restrict=int(body.get("xRestrict", 0)),
        ai_type=int(body.get("aiType", 0)),
        tags=tags,
        pages=[
            PixivPage(
                index=i,
                original_url=item["urls"]["original"],
                regular_url=item["urls"].get("regular", item["urls"]["original"]),
                thumb_url=item["urls"].get("thumb_mini", item["urls"]["small"]),
                width=int(item.get("width", 0)),
                height=int(item.get("height", 0)),
            )
            for i, item in enumerate(pages_data)
        ],
    )

    # 步骤 4：下载图片（逐页，原图优先→降级）
    for page in artwork.pages:
        path, is_original = await download_with_fallback(
            page, illust_id, cookie, cache_dir="/tmp/pixiv_cache"
        )
        print(f"Page {page.index}: {'原图' if is_original else '降级'} → {path}")
```

---

## 七、Cookie 的获取与维护

Pixiv Web Ajax API 需要认证 Cookie。最简单的方案是用户在浏览器登录 Pixiv 后，从开发者工具中复制 Cookie 字符串。

**从浏览器获取 Cookie 的步骤：**

1. 在浏览器中登录 `https://www.pixiv.net`
2. 打开开发者工具（F12）→ Network 标签
3. 刷新页面，任意选择一个请求
4. 在 Request Headers 中找到 `Cookie` 字段
5. 复制完整的 Cookie 字符串

**最小必要字段：** `PHPSESSID` 是 Pixiv 会话的核心 token，仅需这一项即可调用 Ajax 接口。但实际使用中，额外携带 `device_token` 等字段可以提高稳定性。

**注意事项：**

- Cookie 有有效期，过期后 API 会返回 403 或重定向到登录页
- 短时间内大量请求可能触发 Cloudflare 防护，表现为接口返回 HTML 页面（含 "Just a moment" 字样），而非 JSON
- 建议搭配代理使用，分散请求频率

---

## 八、异常处理要点

Pixiv 解析过程中可能遇到的典型异常及处理方式：

| 异常情况 | 表现 | 处理 |
|---------|------|------|
| Cookie 过期/无效 | API 返回 `{"error": true, "message": "..."}` 或 403 | 提示重新配置 Cookie |
| Cloudflare 防护 | 返回 HTML 页面，含 "Just a moment" | 报告被拦截，建议降低频率或更换 IP |
| 作品已删除/隐藏 | API 返回 `{"error": true}` | 提示作品不可用 |
| 图片下载超限 | `DownloadTooLargeError` | 自动降级到 regular 尺寸 |
| 图片下载 403 | HTTP 403 错误 | 检查 Referer 头是否设置正确 |

**检测 Cloudflare 拦截的实用方法：**

```python
def ensure_json_response(resp: httpx.Response):
    """检查响应是否为 JSON，防止被 Cloudflare 拦截。"""
    content_type = resp.headers.get("content-type", "")
    if "text/html" in content_type.lower():
        if "Just a moment" in resp.text:
            raise RuntimeError("Pixiv 被 Cloudflare 拦截，Cookie 方案暂不可用")
        raise RuntimeError("Pixiv 返回了 HTML 而非 JSON")
    try:
        return resp.json()
    except Exception as e:
        raise RuntimeError(f"JSON 解析失败: {e}") from e
```

---

## 总结

Pixiv 作品解析的核心技术路线并不复杂：

1. 用**正则**从文本中提取作品 ID
2. 用 **Web Ajax API**（`/ajax/illust/{id}` 和 `/ajax/illust/{id}/pages`）获取结构化的 JSON 数据
3. 用 **原图优先、超限降级** 的策略下载图片
4. 注意 **Cookie 认证**、**Referer 头**、**Cloudflare 防护** 等工程细节

这套方案的优势在于**无需维护 OAuth 流程**，只需要一个浏览器 Cookie 即可工作，实现成本低，适合个人项目和小规模自动化工具。
