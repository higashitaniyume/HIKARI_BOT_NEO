# 从零开始用 Python 解析 Pixiv 作品

本文以**教程**的形式，从零开始讲解如何用 Python 从 Pixiv 获取作品信息与图片。每步都配有可运行的代码，读完就能写一个自己的 Pixiv 下载工具。

> 如果读完某一步觉得"就这？"——恭喜，你已经上手了。

---

## 准备工作

安装依赖，只需要一个库：

```bash
pip install httpx
```

[`httpx`](https://www.python-httpx.org/) 是一个现代化的 Python HTTP 客户端，支持异步、流式下载，比 `requests` 更适合文件下载场景。

> 想用 `requests` 也行，把 `async with` 换成同步调用即可，原理一样。

---

## 第一步：从 URL 中提取作品 ID

Pixiv 作品的 URL 长这样：

```
https://www.pixiv.net/artworks/12345678
https://www.pixiv.net/en/artworks/12345678
https://www.pixiv.net/i/12345678
```

不管哪种格式，最后那串数字就是**作品 ID**（也叫 illust ID）。我们要做的就是把 ID 从 URL 里抠出来。

```python
import re

# 正则匹配 Pixiv 作品 URL，提取 ID
PIXIV_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?pixiv\.net"
    r"/(?:en/)?"
    r"(?:artworks|i)/(?P<id>\d{5,12})",
    re.IGNORECASE,
)

def extract_illust_id(text: str) -> list[str]:
    """从文本中提取所有 Pixiv 作品 ID，去重保序。"""
    ids: list[str] = []
    seen: set[str] = set()
    for match in PIXIV_URL_RE.finditer(text):
        illust_id = match.group("id")
        if illust_id and illust_id not in seen:
            seen.add(illust_id)
            ids.append(illust_id)
    return ids


# 试试看
print(extract_illust_id("看这个 https://www.pixiv.net/artworks/12345678 还有这个 https://pixiv.net/i/87654321"))
# 输出: ['12345678', '87654321']
```

**为什么只匹配 `artworks` 和 `i`？** 因为 Pixiv 上还有用户主页（`/users/`）、Tag 页（`/tags/`）、小说（`/novels/`）等链接，它们也带数字 ID，但不代表作品。只匹配作品页的路径可以避免误伤。

---

## 第二步：获取 Cookie

Pixiv 的 Web Ajax API 需要登录态才能访问。最简单的方式就是从浏览器里复制 Cookie。

**操作步骤：**

1. 浏览器打开 `https://www.pixiv.net` 并登录
2. 按 F12 打开开发者工具 → Network 标签
3. 刷新页面，点任意一个请求
4. 在 Request Headers 里找到 `Cookie` 字段
5. 复制完整的 Cookie 字符串

其中最重要的字段是 **`PHPSESSID`** —— Pixiv 的会话 token。把 Cookie 存到一个变量里，后面所有请求都要带上它：

```python
COOKIE = "PHPSESSID=abc123...; device_token=xyz..."
```

> 你的 Cookie 不要提交到 GitHub。建议存在环境变量或本地配置文件里，然后用 `os.getenv()` 读取。

---

## 第三步：调用 API 获取作品信息（试试水）

Pixiv 网站本身是用 Ajax 加载数据的，它的内部 API 接口长这样：

```
GET https://www.pixiv.net/ajax/illust/{illust_id}
```

这个接口返回 JSON，包含作品的标题、作者、标签等信息。我们来调一下试试：

```python
import httpx

async def fetch_artwork_info(illust_id: str, cookie: str) -> dict:
    """获取 Pixiv 作品的元信息。"""
    headers = {
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.7103.48 Safari/537.36",
        "accept-language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7,ja-JP;q=0.6,ja;q=0.5",
        "referer": f"https://www.pixiv.net/artworks/{illust_id}",
        "cookie": cookie.strip(),
    }

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        resp = await client.get(f"https://www.pixiv.net/ajax/illust/{illust_id}")
        resp.raise_for_status()
        return resp.json()


# 跑一下看看（假设你有 cookie）
import asyncio
result = asyncio.run(fetch_artwork_info("12345678", COOKIE))
print(result)
```

**关于请求头：**

- `user-agent`：模拟 Chrome 浏览器，别让服务器认出你是脚本
- `referer`：**必填**。Pixiv 的 CDN 和 API 都会检查这个头，值设为对应的作品页
- `cookie`：登录凭证，前面刚拿到的

运行成功后，你会看到类似这样的返回结构：

```json
{
  "error": false,
  "body": {
    "illustId": "12345678",
    "illustTitle": "夏日海滩",
    "illustComment": "暑假画的一张图...",
    "userId": "87654321",
    "userName": "Pixiv作者名",
    "pageCount": 4,
    "xRestrict": 0,
    "aiType": 0,
    "sl": 6,
    "tags": {
      "tags": [
        { "tag": "オリジナル", "translation": { "en": "Original" } },
        { "tag": "風景", "translation": { "en": "Landscape" } }
      ]
    },
    "width": 1200,
    "height": 800
  }
}
```

关键字段一览：

| 字段 | 含义 |
|------|------|
| `illustTitle` | 作品标题 |
| `userName` | 作者昵称 |
| `pageCount` | 总页数（多图时有效） |
| `xRestrict` | `0` 全年龄 / `1` R-18 / `2` R-18G |
| `aiType` | `0` 非 AI / `2` AI 生成 |
| `tags.tags[]` | 标签列表（每个有 tag + translation） |

---

## 第四步：获取图片 URL（Pages API）

上面那个接口不直接返回图片地址。要拿到图片链接，还得调另一个接口：

```
GET https://www.pixiv.net/ajax/illust/{illust_id}/pages?lang=zh
```

```python
async def fetch_pages(illust_id: str, cookie: str) -> list[dict]:
    """获取 Pixiv 作品的每页图片 URL。"""
    headers = {
        "user-agent": "Mozilla/5.0 ...",
        "referer": f"https://www.pixiv.net/artworks/{illust_id}",
        "cookie": cookie.strip(),
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        resp = await client.get(f"https://www.pixiv.net/ajax/illust/{illust_id}/pages?lang=zh")
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise RuntimeError(f"API 错误: {data.get('message')}")
        return data["body"]
```

返回的 `body` 是一个数组，有几个元素就代表有几张图：

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
  }
]
```

注意这里每个元素包含 **4 种尺寸的图片 URL**：

| 字段 | 尺寸约 | 用途 |
| --- | --- | --- |
| `thumb_mini` | 250×250 | 微缩预览 |
| `small` | 540×540 | 列表小图 |
| `regular` | 600×600 | 中等尺寸，降级方案 |
| `original` | 原图尺寸 | **首选下载目标** |

**两个 API 的关系：**

```
ajax/illust/{id}       → 作品信息（标题、作者、标签……）
ajax/illust/{id}/pages → 图片列表（每页的 4 种尺寸 URL）
```

可以**复用同一个 HTTP 客户端**来调这两个接口，节省连接开销：

```python
async with httpx.AsyncClient(headers=headers) as client:
    info_resp = await client.get(f"https://www.pixiv.net/ajax/illust/{illust_id}")
    pages_resp = await client.get(f"https://www.pixiv.net/ajax/illust/{illust_id}/pages?lang=zh")
```

---

## 第五步：用数据类组织信息

到目前为止我们拿到的都是原始字典，用起来容易写错 key。用 `dataclass` 整理一下，后续代码会清晰很多：

```python
from dataclasses import dataclass, field

@dataclass
class PixivPage:
    """单页图片信息。"""
    index: int
    original_url: str
    regular_url: str
    thumb_url: str
    width: int
    height: int

@dataclass
class PixivArtwork:
    """作品完整信息。"""
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
        return self.x_restrict in (1, 2)
```

把前面两步的数据合并到这里：

```python
async def fetch_artwork(illust_id: str, cookie: str) -> PixivArtwork:
    """获取完整作品信息（元信息 + 图片 URL）。"""
    headers = {
        "user-agent": "Mozilla/5.0 ...",
        "referer": f"https://www.pixiv.net/artworks/{illust_id}",
        "cookie": cookie.strip(),
    }

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        # 1. 获取元信息
        info_resp = await client.get(f"https://www.pixiv.net/ajax/illust/{illust_id}")
        info_resp.raise_for_status()
        body = info_resp.json()["body"]

        # 2. 获取图片 URL
        pages_resp = await client.get(f"https://www.pixiv.net/ajax/illust/{illust_id}/pages?lang=zh")
        pages_resp.raise_for_status()
        raw_pages = pages_resp.json()["body"]

    # 3. 解析图片列表
    pages = []
    for i, item in enumerate(raw_pages):
        urls = item.get("urls", {})
        pages.append(PixivPage(
            index=i,
            original_url=urls.get("original", ""),
            regular_url=urls.get("regular", urls.get("small", "")),
            thumb_url=urls.get("thumb_mini", urls.get("small", "")),
            width=int(item.get("width", 0)),
            height=int(item.get("height", 0)),
        ))

    # 4. 解析标签（可能有英文翻译）
    tags_raw = body.get("tags", {}).get("tags", [])
    tags = []
    for item in tags_raw:
        tag_name = item.get("tag", "")
        if not tag_name:
            continue
        if tag_name == "R-18":
            tag_name = "R18"
        translation = item.get("translation") or {}
        if translation.get("en"):
            tag_name = translation["en"]
        tags.append(tag_name.replace(" ", "_"))

    return PixivArtwork(
        illust_id=illust_id,
        title=body.get("illustTitle", "Untitled"),
        user_name=body.get("userName", "Unknown"),
        user_id=body.get("userId", ""),
        page_count=int(body.get("pageCount", len(pages))),
        x_restrict=int(body.get("xRestrict", 0)),
        ai_type=int(body.get("aiType", 0)),
        tags=tags,
        pages=pages,
    )
```

---

## 第六步：下载第一张图片

图片托管在 `i.pximg.net` 上。下载时有两个关键点：

1. **必须带 `Referer` 头**，否则返回 403
2. **用流式下载**，一边接收一边写磁盘，避免大图片撑爆内存

```python
import hashlib
from pathlib import Path

async def download_image(
    url: str,
    illust_id: str,
    cookie: str,
    save_dir: str = "./pixiv_downloads",
) -> Path:
    """下载单张 Pixiv 图片到本地。"""
    # 从 URL 推断后缀名
    suffix = Path(url.split("?")[0]).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        suffix = ".jpg"

    # 用 URL 的 SHA256 哈希做文件名（避免特殊字符导致路径问题）
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    save_path = Path(save_dir) / f"{digest}{suffix}"

    # 缓存命中就不再下载
    if save_path.exists() and save_path.stat().st_size > 0:
        return save_path

    # 请求头（referer 是关键！）
    headers = {
        "referer": f"https://www.pixiv.net/artworks/{illust_id}",
        "cookie": cookie.strip(),
    }

    save_path.parent.mkdir(parents=True, exist_ok=True)

    # 用 .part 临时文件，下载完再重命名，防止中途中断留下残片
    tmp_path = save_path.with_suffix(save_path.suffix + ".part")
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with tmp_path.open("wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)

    tmp_path.rename(save_path)
    return save_path


# 实战：下载一个作品的第一页原图
async def main():
    artwork = await fetch_artwork("12345678", COOKIE)
    first_page = artwork.pages[0]
    path = await download_image(first_page.original_url, artwork.illust_id, COOKIE)
    print(f"下载完成: {path}")

asyncio.run(main())
```

> **为什么用流式下载？**
>
> 假设一张原图 30MB，`resp.content` 一次性读完会让内存占用瞬间飙升。用 `resp.aiter_bytes()` 边收边写，不管多大的文件内存占用都很稳定。

---

## 第七步：原图太大怎么办？—— 降级策略

实操中会发现，部分 Pixiv 原图大得离谱——动辄 30~50MB。对于大多数用途来说，regular 尺寸（600×600 裁剪）完全够用。

所以最佳策略是：**优先尝试原图，如果超过大小限制就自动降级到 regular。**

先给下载函数加上大小限制：

```python
class DownloadTooLargeError(RuntimeError):
    """图片超过大小限制。"""

async def download_image_with_limit(
    url: str,
    illust_id: str,
    cookie: str,
    save_dir: str = "./pixiv_downloads",
    max_bytes: int | None = None,
) -> Path:
    """下载图片，支持大小限制。"""
    # ...（同上，但在写入时检查大小）
    headers = {
        "referer": f"https://www.pixiv.net/artworks/{illust_id}",
        "cookie": cookie.strip(),
    }
    
    suffix = Path(url.split("?")[0]).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        suffix = ".jpg"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    save_path = Path(save_dir) / f"{digest}{suffix}"
    
    if save_path.exists() and save_path.stat().st_size > 0:
        return save_path

    save_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = save_path.with_suffix(save_path.suffix + ".part")

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()

            # 检查 Content-Length（服务器可能不返回这个头）
            content_length = resp.headers.get("content-length")
            if content_length and content_length.isdigit():
                if max_bytes is not None and int(content_length) > max_bytes:
                    raise DownloadTooLargeError(f"图片超过大小限制")

            # 流式写入，边写边检查
            written = 0
            with tmp_path.open("wb") as f:
                async for chunk in resp.aiter_bytes():
                    written += len(chunk)
                    if max_bytes is not None and written > max_bytes:
                        tmp_path.unlink(missing_ok=True)
                        raise DownloadTooLargeError(f"图片超过大小限制")
                    f.write(chunk)

    tmp_path.rename(save_path)
    return save_path
```

然后写一个带降级的下载函数：

```python
async def download_with_fallback(
    page: PixivPage,
    illust_id: str,
    cookie: str,
    save_dir: str = "./pixiv_downloads",
    max_file_mb: int = 25,
) -> tuple[Path, bool]:
    """
    下载图片，原图优先→超限降级。
    
    Returns:
        (文件路径, 是否原图)
    """
    max_bytes = max_file_mb * 1024 * 1024

    # 第一轮：尝试原图
    original_path = None
    try:
        original_path = await download_image_with_limit(
            page.original_url, illust_id, cookie, save_dir, max_bytes
        )
    except DownloadTooLargeError:
        pass  # 原图超限，准备降级

    if original_path and original_path.stat().st_size <= max_bytes:
        return original_path, True  # 原图下载成功！

    # 第二轮：降级到 regular
    regular_path = await download_image_with_limit(
        page.regular_url, illust_id, cookie, save_dir, max_bytes
    )
    return regular_path, False


# 试试看
async def main():
    artwork = await fetch_artwork("12345678", COOKIE)
    for page in artwork.pages:
        path, is_original = await download_with_fallback(page, artwork.illust_id, COOKIE)
        tag = "原图" if is_original else "降级"
        print(f"第{page.index+1}页 ({tag}): {path}")

asyncio.run(main())
```

---

## 第八步：完整的下载脚本

把上面所有的代码拼在一起，就是一个完整的 Pixiv 下载工具：

```python
"""
Pixiv 作品下载工具

用法：
    python pixiv_downloader.py "https://www.pixiv.net/artworks/12345678"
    
前置条件：
    设置环境变量 PIXIV_COOKIE（浏览器登录后复制的 Cookie 字符串）
"""

import asyncio
import hashlib
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx

# ===== 配置 =====
COOKIE = os.environ["PIXIV_COOKIE"]  # 从环境变量读取
DOWNLOAD_DIR = "./pixiv_downloads"
MAX_FILE_MB = 25

# ===== 第一步：URL 匹配 =====
PIXIV_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?pixiv\.net"
    r"/(?:en/)?"
    r"(?:artworks|i)/(?P<id>\d{5,12})",
    re.IGNORECASE,
)

def extract_illust_id(text: str) -> list[str]:
    ids, seen = [], set()
    for m in PIXIV_URL_RE.finditer(text):
        iid = m.group("id")
        if iid and iid not in seen:
            seen.add(iid)
            ids.append(iid)
    return ids

# ===== 第五步：数据结构 =====
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
    tags: list[str] = field(default_factory=list)
    pages: list[PixivPage] = field(default_factory=list)

# ===== 第三、四步：API 调用 =====
async def fetch_artwork(illust_id: str, cookie: str) -> PixivArtwork:
    headers = {
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "referer": f"https://www.pixiv.net/artworks/{illust_id}",
        "cookie": cookie.strip(),
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        info_resp = await client.get(f"https://www.pixiv.net/ajax/illust/{illust_id}")
        info_resp.raise_for_status()
        body = info_resp.json()["body"]

        pages_resp = await client.get(f"https://www.pixiv.net/ajax/illust/{illust_id}/pages?lang=zh")
        pages_resp.raise_for_status()
        raw_pages = pages_resp.json()["body"]

    pages = []
    for i, item in enumerate(raw_pages):
        urls = item.get("urls", {})
        pages.append(PixivPage(i, urls.get("original", ""),
                               urls.get("regular", ""),
                               urls.get("thumb_mini", ""),
                               int(item.get("width", 0)),
                               int(item.get("height", 0))))

    tags = []
    for item in body.get("tags", {}).get("tags", []):
        tag = item.get("tag", "")
        if not tag:
            continue
        if item.get("translation", {}).get("en"):
            tag = item["translation"]["en"]
        tags.append(tag.replace(" ", "_"))

    return PixivArtwork(illust_id, body.get("illustTitle", "Untitled"),
                        body.get("userName", "Unknown"), body.get("userId", ""),
                        int(body.get("pageCount", len(pages))),
                        int(body.get("xRestrict", 0)),
                        int(body.get("aiType", 0)), tags, pages)

# ===== 第六、七步：下载 =====
class DownloadTooLargeError(RuntimeError):
    pass

async def download_image(url: str, illust_id: str, cookie: str,
                         save_dir: str, max_bytes: int | None = None) -> Path:
    suffix = Path(url.split("?")[0]).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        suffix = ".jpg"
    digest = hashlib.sha256(url.encode()).hexdigest()
    save_path = Path(save_dir) / f"{digest}{suffix}"
    if save_path.exists() and save_path.stat().st_size > 0:
        return save_path

    headers = {"referer": f"https://www.pixiv.net/artworks/{illust_id}", "cookie": cookie.strip()}
    save_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = save_path.with_suffix(suffix + ".part")

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            written = 0
            with tmp_path.open("wb") as f:
                async for chunk in resp.aiter_bytes():
                    written += len(chunk)
                    if max_bytes and written > max_bytes:
                        tmp_path.unlink(missing_ok=True)
                        raise DownloadTooLargeError(f"超过大小限制 {max_bytes/1024/1024:.0f}MB")
                    f.write(chunk)
    tmp_path.rename(save_path)
    return save_path

async def download_with_fallback(page: PixivPage, illust_id: str, cookie: str,
                                 save_dir: str = DOWNLOAD_DIR,
                                 max_file_mb: int = MAX_FILE_MB) -> tuple[Path, bool]:
    max_bytes = max(max_file_mb, 1) * 1024 * 1024
    try:
        path = await download_image(page.original_url, illust_id, cookie, save_dir, max_bytes)
        return path, True
    except DownloadTooLargeError:
        pass
    path = await download_image(page.regular_url, illust_id, cookie, save_dir, max_bytes)
    return path, False

# ===== 主函数 =====
async def main(url: str):
    ids = extract_illust_id(url)
    if not ids:
        print("未找到有效的 Pixiv 作品链接")
        return

    illust_id = ids[0]
    print(f"正在获取作品 {illust_id} 的信息……")
    artwork = await fetch_artwork(illust_id, COOKIE)
    print(f"标题: {artwork.title}")
    print(f"作者: {artwork.user_name}")
    print(f"页数: {artwork.page_count}")
    if artwork.is_r18:
        print("⚠ R-18 作品")

    for page in artwork.pages:
        path, is_original = await download_with_fallback(page, illust_id, COOKIE)
        tag = "原图" if is_original else "降级"
        print(f"  第{page.index+1}页 ({tag}): {path.name}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python pixiv_downloader.py <Pixiv URL>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
```

---

## 常见问题与异常处理

实际运行中会遇到各种状况，这里列了最常见的几个：

### Cookie 无效

API 返回 `{"error": true, "message": "..."}` 或 HTTP 403。

**原因：** Cookie 过期了。Pixiv 的登录会话有时效，重新登录一遍复制新的 Cookie 就好。

### 返回的不是 JSON，而是 HTML（被 Cloudflare 拦截）

```python
resp.headers.get("content-type")  # → "text/html"
```

**原因：** 短时间内请求太多，触发了 Cloudflare 的防护。响应体里通常有 "Just a moment" 字样。

```python
def ensure_json(resp: httpx.Response) -> dict:
    if "text/html" in resp.headers.get("content-type", "").lower():
        if "Just a moment" in resp.text:
            raise RuntimeError("被 Cloudflare 拦截了，稍后再试或换个 IP")
        raise RuntimeError("期望 JSON 但收到了 HTML")
    return resp.json()
```

**对策：** 降低请求频率、换代理 IP，或者等一段时间再试。

### 图片下载 403

**原因：** 没带 `Referer` 头，或者 Referer 指向了错误的页面。检查下载请求的 `referer` 是否设为了作品页 URL。

### 作品不存在或已删除

API 返回 `{"error": true}`，说明作品 ID 无效、被删除或被作者隐藏了。

---

## 总结

回顾一下从零到一的完整流程：

```
Pixiv URL  →  提取 illust ID  →  /ajax/illust/{id} 获取元信息
                                  /ajax/illust/{id}/pages 获取图片 URL
                                               ↓
                                  下载（原图优先 → 超限降级 regular）
                                               ↓
                                     本地图片文件 ✅
```

整个方案的核心就三个要点：

1. **两个 Ajax 接口**就够了，不需要 OAuth、不需要复杂的认证流程
2. **Referer 头必须带**，这是 Pixiv CDN 的访问凭证
3. **原图优先、超限降级**——既要质量又要稳定

掌握了这些，你不仅能下载 Pixiv 的作品，还能基于同样的思路去解析 Bilibili、Twitter 等其他平台的资源——找到网页的内置 API，模拟请求，解析 JSON，下载资源。模式是一样的。

---

Happy coding.

