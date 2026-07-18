# 从零开始用 Python 解析 B 站视频

B 站（bilibili.com）是国内最大的弹幕视频网站。它的内容形态比 Pixiv 复杂得多——普通 UGC 视频（BV/AV 号）、番剧（ep/season）、动态/opus（图文或转发视频），各有不同的数据链路。

本文从零开始，逐步讲解如何用 Python 从链接里解析出 B 站的视频信息和播放地址。

> 读完你会发现：B 站看似接口多，但链路非常清晰，核心就是 **BV/AV → 元信息 → cid → 播放地址** 这条线。

---

## 准备工作

```bash
pip install httpx
```

用 [`httpx`](https://www.python-httpx.org/) 作为 HTTP 客户端，支持异步和流式下载。

---

## 第一步：理解 B 站的链接体系

B 站的链接格式比想象中多样：

```
# 普通视频
https://www.bilibili.com/video/BV1xx411c7mD
https://www.bilibili.com/video/av170001
BV1xx411c7mD           # 纯 BV 号也能识别
av170001                # 纯 AV 号也能识别

# 番剧
https://www.bilibili.com/bangumi/play/ep123456
https://www.bilibili.com/bangumi/play/ss12345

# 动态/图文
https://www.bilibili.com/opus/1234567890
https://t.bilibili.com/1234567890

# 短链
https://b23.tv/xxxxxx
```

**BV 和 AV 的关系：** AV 号是早期的数字编号（如 `av170001`），BV 号是后来引入的字符串编号（如 `BV1xx411c7mD`）。两者一一对应，内部存的是同一套数据。

先写一个工具函数实现 AV 号和 BV 号的互转：

```python
# BV 号编码表（来自 B 站开源算法）
BV_TABLE = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"
XOR_CODE = 23442827791579
MAX_AID = 1 << 51
BASE = 58

def av2bv(av: int) -> str:
    """将 AV 号整数转换为 BV 号字符串。"""
    bytes_arr = ['B', 'V', '1', '0', '0', '0', '0', '0', '0', '0', '0', '0']
    bv_idx = len(bytes_arr) - 1
    tmp = (MAX_AID | av) ^ XOR_CODE
    while tmp > 0:
        bytes_arr[bv_idx] = BV_TABLE[tmp % BASE]
        tmp //= BASE
        bv_idx -= 1
    # 交换固定位置
    bytes_arr[3], bytes_arr[9] = bytes_arr[9], bytes_arr[3]
    bytes_arr[4], bytes_arr[7] = bytes_arr[7], bytes_arr[4]
    return ''.join(bytes_arr)


# 验证：av2bv(170001) 应该返回 "BV17x411w7KC"
```

---

## 第二步：从链接中提取关键信息

把各种格式的链接归一化为标准形式，判断是普通视频（UGC）还是番剧（PGC）：

```python
import re

URL_RE = {
    "b23": re.compile(r"b23\.tv/\S+", re.IGNORECASE),
    "bv": re.compile(r"[Bb][Vv][0-9A-Za-z]{10,}"),
    "av": re.compile(r"[Aa][Vv](\d+)"),
    "ep": re.compile(r"ep(\d+)", re.IGNORECASE),
    "ss": re.compile(r"ss(\d+)", re.IGNORECASE),
}

def detect_target(url: str):
    """
    检测链接类型，返回 (类型, 标识符字典)。
    - "ugc": {"bvid": "BV1xx..."}
    - "pgc": {"ep_id": "123456"} 或 {"season_id": "12345"}
    """
    m = re.search(r"/ep(\d+)|[?&]ep_id=(\d+)", url, re.IGNORECASE)
    if m:
        ep_id = m.group(1) or m.group(2)
        return "pgc", {"ep_id": ep_id}

    m = re.search(r"/ss(\d+)|[?&]season_id=(\d+)", url, re.IGNORECASE)
    if m:
        ss_id = m.group(1) or m.group(2)
        return "pgc", {"season_id": ss_id}

    m = URL_RE["bv"].search(url)
    if m:
        bvid = m.group(0)
        if bvid[:2].upper() != "BV":
            bvid = "BV" + bvid[2:]
        return "ugc", {"bvid": bvid}

    m = URL_RE["av"].search(url)
    if m:
        aid = int(m.group(1))
        return "ugc", {"bvid": av2bv(aid)}

    return None, {}
```

对于短链 `b23.tv`，需要先展开：

```python
async def expand_b23(url: str) -> str:
    """展开 b23.tv 短链，获取真实 URL。"""
    if "b23.tv" not in url.lower():
        return url

    headers = {"User-Agent": "Mozilla/5.0 ..."}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        resp = await client.get(url)
        return str(resp.url)
```

---

## 第三步：调用 API 获取视频元信息

B 站的核心 API 是 `x/web-interface/view`，参数只需要 BV 号或 AV 号：

```python
import httpx

async def get_ugc_info(bvid: str) -> dict:
    """获取 UGC 视频的元信息。"""
    url = "https://api.bilibili.com/x/web-interface/view"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com",
    }

    async with httpx.AsyncClient(headers=headers) as client:
        resp = await client.get(url, params={"bvid": bvid})
        data = resp.json()

    if data.get("code") != 0:
        raise RuntimeError(f"API 错误: {data.get('message')}")

    return data["data"]
```

返回的结构（简化版）：

```json
{
  "bvid": "BV1xx411c7mD",
  "title": "视频标题",
  "desc": "视频简介...",
  "pubdate": 1234567890,
  "owner": {
    "name": "UP主名",
    "mid": 123456
  },
  "cid": 12345,
  "pages": [
    {"page": 1, "cid": 12345, "part": "P1 标题"},
    {"page": 2, "cid": 67890, "part": "P2 标题"}
  ],
  "aid": 170001,
  "rights": {"pay": 0, "ugc_pay": 0}
}
```

关键字段：

| 字段 | 含义 |
| --- | --- |
| `title` | 视频标题 |
| `desc` | 简介 |
| `owner.name` | UP 主昵称 |
| `owner.mid` | UP 主 UID |
| `pubdate` | 发布时间（Unix 时间戳） |
| `cid` | **当前分 P 的 cid**（播放视频的关键 ID） |
| `pages` | **分 P 列表**，多 P 视频靠这个拿每 P 的 cid |
| `aid` | AV 号数字形式 |
| `rights` | 版权/付费标志 |

对于番剧，需要用不同的接口：

```python
async def get_pgc_info(ep_id: str) -> dict:
    """获取番剧/动画的元信息。"""
    url = "https://api.bilibili.com/pgc/view/web/season"
    headers = {"User-Agent": "Mozilla/5.0 ...", "Referer": "https://www.bilibili.com"}

    async with httpx.AsyncClient(headers=headers) as client:
        resp = await client.get(url, params={"ep_id": ep_id})
        data = resp.json()

    if data.get("code") != 0:
        raise RuntimeError(f"API 错误: {data.get('message')}")

    result = data.get("result") or {}
    # 在 episodes 中找到匹配 ep_id 的那一集
    for ep in result.get("episodes", []):
        if str(ep.get("ep_id")) == str(ep_id):
            return {
                "title": ep.get("long_title") or ep.get("title", ""),
                "desc": result.get("evaluate", ""),
                "author": result.get("up_info", {}).get("name", ""),
                "aid": ep.get("aid"),
            }

    return None
```

> **番剧 vs 普通视频：** 番剧的播放接口、元数据接口和普通视频完全不同。不能拿 UGC 的接口去套番剧。简单识别方法：链接里有 `ep` 或 `ss` 就按番剧走。

---

## 第四步：分 P 处理与 cid

刚才的 `view` 接口只返回了第一个分 P 的 `cid`。如果视频有多个分 P（视频下方可以切换的段落），需要单独调用分 P 列表接口：

```python
async def get_pagelist(bvid: str) -> list[dict]:
    """获取视频的分 P 列表。"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.bilibili.com/x/player/pagelist",
            params={"bvid": bvid, "jsonp": "json"},
        )
        data = resp.json()

    if data.get("code") != 0:
        raise RuntimeError(f"分 P API 错误: {data.get('message')}")

    return data["data"]  # 返回 [{page, cid, part}, ...]
```

然后根据用户想要的 P（通过 URL 中的 `?p=2` 参数指定）选择对应的 cid：

```python
from urllib.parse import urlparse, parse_qs

def extract_p(url: str) -> int:
    """从 URL 提取分 P 序号，默认为 1。"""
    try:
        return int(parse_qs(urlparse(url).query).get("p", ["1"])[0])
    except Exception:
        return 1
```

**所以元信息获取的完整链路是：**

```
BV/AV 号
  ↓
x/web-interface/view  →  元数据（含第一 P 的 cid）
  ↓
x/player/pagelist     →  所有分 P 的 cid
  ↓
根据 ?p=N 选择目标 cid
  ↓
x/player/playurl      →  真正的视频播放地址
```

---

## 第五步：获取视频播放地址

有了 BV 号和 cid，就可以调播放接口了：

```python
async def get_playurl(bvid: str, cid: int, qn: int = 80) -> dict:
    """
    获取视频播放地址。
    
    qn 参数控制画质：
      16  = 144P
      32  = 360P
      64  = 480P
      80  = 720P
      120 = 1080P
      127 = 4K (需要登录/大会员)
    """
    async with httpx.AsyncClient(headers={
        "User-Agent": "Mozilla/5.0 ...",
        "Referer": f"https://www.bilibili.com/video/{bvid}",
    }) as client:
        resp = await client.get(
            "https://api.bilibili.com/x/player/playurl",
            params={
                "bvid": bvid,
                "cid": cid,
                "qn": qn,
                "fnver": 0,
                "fnval": 4048,  # 请求 DASH 格式
                "fourk": 1,
            },
        )
        data = resp.json()

    if data.get("code") != 0:
        raise RuntimeError(f"播放地址获取失败: {data.get('message')}")

    return data["data"]
```

播放接口返回的结构有两种情况：

### 情况 A：普通 MP4 直链（durl）

```json
{
  "durl": [
    {"url": "https://.../xxx.mp4", "length": 123456, "size": 12345678}
  ],
  "quality": 80,
  "accept_quality": [120, 80, 64]
}
```

### 情况 B：DASH 格式（音视频分离）

```json
{
  "dash": {
    "video": [
      {"id": 80, "baseUrl": "https://.../video.m4s", "bandwidth": 800000},
      {"id": 120, "baseUrl": "https://.../video.m4s", "bandwidth": 1500000}
    ],
    "audio": [
      {"id": 30280, "baseUrl": "https://.../audio.m4s", "bandwidth": 120000}
    ]
  },
  "quality": 80
}
```

DASH 格式需要音视频分别下载后用 ffmpeg 合并。如果想省事，可以先用带 `fnval=0` 的请求获取 MP4 直链：

```python
async def get_direct_url(bvid: str, cid: int) -> str | None:
    """获取 MP4 直链（优先）。"""
    # 第一次请求，探测最高可用画质
    probe = await get_playurl(bvid, cid, qn=120)
    best_qn = max(probe.get("accept_quality", [80]))

    # 用 fnval=0 获取 MP4 直链
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.bilibili.com/x/player/playurl",
            params={"bvid": bvid, "cid": cid, "qn": best_qn, "fnval": 0},
        )
        data = resp.json()

    payload = data.get("data", {})
    durl = payload.get("durl")
    if durl:
        return durl[0].get("url")

    return None  # 没有 MP4 直链，需要用 DASH
```

也可以选择 DASH 中画质最高的视频流和音频流：

```python
def build_dash_url(dash: dict) -> str | None:
    """从 DASH 数据构建下载 URL（标记为特殊格式，供下载器处理）。"""
    if not dash:
        return None
    videos = dash.get("video", [])
    audios = dash.get("audio", [])

    # 选最高画质的视频流
    best_video = sorted(videos, key=lambda x: (x.get("id", 0), x.get("bandwidth", 0)), reverse=True)[0]
    video_url = best_video.get("baseUrl") or best_video.get("base_url", "")

    # 选最高码率的音频流
    if audios:
        best_audio = sorted(audios, key=lambda x: x.get("bandwidth", 0), reverse=True)[0]
        audio_url = best_audio.get("baseUrl") or best_audio.get("base_url", "")
        return f"dash:{video_url}||{audio_url}"  # 格式：dash:video_url||audio_url

    return video_url
```

---

## 第六步：番剧的播放地址

番剧的播放接口和普通视频不同：

```python
async def get_pgc_playurl(ep_id: str, qn: int = 120) -> dict:
    """获取番剧的播放地址。"""
    async with httpx.AsyncClient(headers={
        "Referer": f"https://www.bilibili.com/bangumi/play/ep{ep_id}",
    }) as client:
        resp = await client.get(
            "https://api.bilibili.com/pgc/player/web/v2/playurl",
            params={"ep_id": ep_id, "qn": qn, "fnval": 4048, "fourk": 1},
        )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"番剧播放地址获取失败: {data.get('message')}")

    return data.get("result") or data.get("data") or data
```

番剧更容易遇到访问限制（大会员专享、地区限制、试看等）。接口返回的 `support_formats` 里会标注 `need_vip`：

```python
def check_access(playurl_data: dict) -> dict:
    """分析播放接口返回，判断视频是否可访问。"""
    info = {
        "can_access": False,
        "is_preview": False,
        "need_vip": False,
        "message": "",
    }

    payload = playurl_data.get("video_info", playurl_data)
    support_formats = payload.get("support_formats", [])

    info["need_vip"] = any(
        fmt.get("need_vip") for fmt in support_formats
    )
    info["is_preview"] = bool(payload.get("is_preview"))

    has_dash = bool(payload.get("dash", {}).get("video"))
    has_durl = bool(payload.get("durl"))

    if has_dash or has_durl:
        info["can_access"] = True
    elif info["need_vip"]:
        info["message"] = "大会员专享"
    elif info["is_preview"]:
        info["message"] = "仅试看"

    return info
```

---

## 第七步：番剧 season → ep 转换

有时候拿到的是 `ss`（season）链接，而不是具体的某一集。需要通过 season 找到第一集的 ep_id：

```python
async def get_first_ep_by_season(season_id: str) -> str:
    """根据 season_id 找到对应番剧的第一个 ep_id。"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.bilibili.com/pgc/view/web/season",
            params={"season_id": season_id},
        )
        data = resp.json()

    result = data.get("result") or {}
    episodes = result.get("episodes", [])
    if not episodes:
        raise RuntimeError("未找到番剧分集")

    for ep in episodes:
        ep_id = ep.get("ep_id")
        if ep_id is not None:
            return str(ep_id)

    raise RuntimeError("season 数据缺少 ep_id")
```

---

## 第八步：完整解析流程串联

把前面的步骤拼起来，就是一个完整的 B 站视频解析函数：

```python
async def parse_bilibili(url: str) -> dict:
    """
    解析 B 站链接，返回标准化的元数据。
    
    Returns:
        {
            "url": str,
            "title": str,
            "author": str,
            "desc": str,
            "timestamp": str,
            "video_urls": [[str]],   # 二维列表，每个元素是一个视频的候选 URL 列表
        }
    """
    # 1. 展开短链
    url = await expand_b23(url)

    # 2. 检测视频类型
    vtype, ident = detect_target(url)
    if not vtype:
        raise RuntimeError(f"无法识别的链接: {url}")

    if vtype == "pgc":
        # 番剧链路
        ep_id = ident.get("ep_id")
        if not ep_id:
            ep_id = await get_first_ep_by_season(ident["season_id"])

        info = await get_pgc_info(ep_id)
        playurl_data = await get_pgc_playurl(ep_id)

        # 获取 MP4 直链
        payload = playurl_data.get("video_info", playurl_data)
        durl = payload.get("durl")
        if durl:
            direct_url = durl[0]["url"]
        else:
            dash = payload.get("dash", {})
            direct_url = build_dash_url(dash)
    else:
        # UGC 视频链路
        bvid = ident["bvid"]
        info = await get_ugc_info(bvid)
        p = extract_p(url)
        pages = await get_pagelist(bvid)
        cid = pages[p - 1]["cid"]

        access_info = check_access(await get_playurl(bvid, cid))
        direct_url = await get_direct_url(bvid, cid)

    # 3. 组装结果
    from datetime import datetime
    author_name = info.get("author", info.get("owner", {}).get("name", ""))
    author_id = info.get("mid", info.get("owner", {}).get("mid", ""))
    author = f"{author_name}(uid:{author_id})" if author_name and author_id else author_name

    ts = info.get("pubdate")
    timestamp = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d") if ts else ""

    result = {
        "url": url,
        "title": info.get("title", ""),
        "author": author,
        "desc": info.get("desc", info.get("evaluate", "")),
        "timestamp": timestamp,
        "video_urls": [[direct_url]] if direct_url else [],
        "image_urls": [],
    }

    return result
```

---

## 进阶：B 站 Cookie 的作用

B 站的公开 API 大多不需要 Cookie 也能调用，但带上 Cookie 后会有这些差异：

| 场景 | 无 Cookie | 有 Cookie |
|------|----------|----------|
| 画质 | 最高 720P（qn=80） | 可达 1080P+（qn=120+） |
| 番剧权限 | 只能试看 | 大会员可看完整版 |
| 4K 视频 | 不可访问 | 可访问 |
| 热门评论 | 不可获取 | 可获取（需额外签名） |

Cookie 从浏览器登录后复制即可，和 Pixiv 一样。

---

## 进阶：WBI 签名（获取热门评论）

B 站的部分接口（如评论 API）需要 WBI 签名。签名的流程是：

1. 调用导航接口 `/x/web-interface/nav` 拿到 `wbi_img`
2. 从 `wbi_img` 的 URL 中提取两个 key，混排后得到 `mixin_key`
3. 用 `mixin_key` 对请求参数做 MD5 签名

```python
import time
import hashlib
from urllib.parse import urlencode
from pathlib import Path

# WBI 混排表（固定的 64 位索引）
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

async def get_wbi_mixin_key() -> str:
    """从导航接口获取 WBI mixin_key。"""
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://api.bilibili.com/x/web-interface/nav")
        data = resp.json()

    wbi_img = data.get("data", {}).get("wbi_img", {})
    img_url = wbi_img.get("img_url", "")
    sub_url = wbi_img.get("sub_url", "")

    img_key = Path(img_url).stem
    sub_key = Path(sub_url).stem

    # 混排
    raw = img_key + sub_key
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def sign_wbi_params(params: dict, mixin_key: str) -> dict:
    """为请求参数添加 WBI 签名。"""
    params = dict(params)
    params["wts"] = int(time.time())
    params = dict(sorted(params.items()))

    # 移除特殊字符
    cleaned = {}
    for k, v in params.items():
        text = str(v)
        for ch in "!'()*":
            text = text.replace(ch, "")
        cleaned[k] = text

    query = urlencode(cleaned)
    w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
    cleaned["w_rid"] = w_rid
    return cleaned


# 使用示例
async def fetch_hot_comments(aid: int):
    """获取视频的热门评论（需要 WBI 签名）。"""
    mixin_key = await get_wbi_mixin_key()
    params = sign_wbi_params({
        "oid": aid,
        "type": 1,
        "mode": 3,
        "next": 0,
        "plat": 1,
    }, mixin_key)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.bilibili.com/x/v2/reply/wbi/main",
            params=params,
        )
        data = resp.json()

    replies = data.get("data", {}).get("replies", [])
    return [
        {
            "username": r.get("member", {}).get("uname", ""),
            "message": r.get("content", {}).get("message", ""),
            "likes": r.get("like", 0),
        }
        for r in replies
    ]
```

---

## 总结

回顾 B 站视频解析的完整链路：

```
任意 B 站链接
  ↓
展开短链 (b23.tv → 真实 URL)
  ↓
判断类型: UGC (BV/AV) / PGC (ep/ss) / Opus (动态)
  ↓
UGC:                          PGC:
  x/web-interface/view          pgc/view/web/season
  x/player/pagelist             pgc/player/web/v2/playurl
  x/player/playurl
  ↓
获取播放地址（MP4 直链 或 DASH 音视频分离）
  ↓
组装元数据（标题、作者、时间、封面……）
```

**核心要点：**

1. **cid** 是播放视频的关键参数，必须从 `view` 或 `pagelist` 接口拿到
2. **BV 号和 AV 号**可以互转，但播放接口推荐用 BV 号
3. **DASH 格式**音视频分离，需要额外合并；`fnval=0` 可请求 MP4 直链
4. **番剧和 UGC 是两套接口**，不能混用
5. **WBI 签名**用于评论等需要登录态的接口

掌握了这些，你不仅能解析 B 站视频，还能基于同样的思路去获取弹幕、评论、字幕等更多数据。
