"""QQ 空间操作客户端。"""
from __future__ import annotations

import base64
import logging
import re
import time

from .auth.base import AuthInfo, AuthProvider
from .auth.resolve import resolve_provider
from .config import QZoneConfig
from .constants import URLS, UA
from .utils.feed import parse_feed_item, parse_mood
from .utils.http import HttpSession
from .utils.jsonp import parse_jsonp
from .utils.response import success_or_error

logger = logging.getLogger("qzone_sdk")


class QZoneClient:
    """
    QQ 空间操作客户端。

    用法:
        client = QZoneClient(QZoneConfig(auth_provider=NapCatAuthProvider()))
        result = await client.publish("Hello World!")

        # 带图片
        result = await client.publish("看这张图", image_paths=["photo.jpg"])

    所有方法返回 dict，success=True 表示成功。
    """

    def __init__(
        self,
        config: QZoneConfig | None = None,
        *,
        auth_provider: AuthProvider | None = None,
        timeout: int | None = None,
    ):
        """
        创建客户端。

        Args:
            config: 完整配置（二选一）。传入后忽略 auth_provider/timeout。
            auth_provider: 认证提供者。不传则自动解析：环境变量 Cookie → 凭证文件 → NapCat。
            timeout: HTTP 超时秒数，默认 30。

        最简单的用法：``client = QZoneClient()``，配合 ``qzone login --save``
        保存过的凭证即可直接使用。
        """
        if config is not None:
            self.config = config
        else:
            self.config = QZoneConfig(
                auth_provider=(
                    auth_provider if auth_provider is not None else resolve_provider()
                ),
                timeout=timeout if timeout is not None else 30,
            )
        self._http = HttpSession()

    # ── 内部工具 ────────────────────────────────────────────────

    async def _auth(self) -> AuthInfo:
        return await self.config.auth_provider.get_auth()

    def _headers(self, auth: AuthInfo, post: bool = False) -> dict:
        h = {
            "Cookie": auth.cookies,
            "User-Agent": UA,
            "Referer": f"https://user.qzone.qq.com/{auth.uin}",
        }
        if post:
            h["Content-Type"] = "application/x-www-form-urlencoded"
            h["Origin"] = "https://user.qzone.qq.com"
        return h

    def _post(self, url: str, data: dict, headers: dict) -> dict | None:
        return self._http.post(url, data, headers, self.config.timeout)

    def _get(self, url: str, params: dict, headers: dict) -> str:
        return self._http.get(url, params, headers, self.config.timeout)

    # ── 发布说说 ────────────────────────────────────────────────

    async def publish(
        self, content: str, *, visible: int = 1, image_paths: list[str] | None = None
    ) -> dict:
        """
        发布说说。

        Args:
            content: 说说文本
            visible: 1=公开, 3=QQ好友可见, 4=仅自己
            image_paths: 图片本地路径列表（可选，最多9张）

        Returns:
            {"success": True, "tid": "...", "time": "..."}
            或 {"success": False, "code": ..., "message": "..."}
        """
        auth = await self._auth()
        headers = self._headers(auth, post=True)
        referer = f"https://user.qzone.qq.com/{auth.uin}"

        data = {
            "syn_tweet_verson": "1",
            "paramstr": "1",
            "pic_template": "",
            "richtype": "",
            "richval": "",
            "special_url": "",
            "subrichtype": "",
            "pic_bo": "",
            "who": "1",
            "con": content,
            "feedversion": "1",
            "ver": "1",
            "ugc_right": str(visible),
            "to_sign": "0",
            "hostuin": auth.uin,
            "code_version": "1",
            "format": "fs",
            "qzreferrer": referer,
        }

        # 图片上传
        if image_paths:
            llocs, bos = [], []
            for path in image_paths:
                try:
                    img = await self._upload_image(path, auth)
                    llocs.append(img["lloc"])
                    if img["bo"]:
                        bos.append(img["bo"])
                    logger.info("图片上传成功: %s", path)
                except Exception as e:
                    return {"success": False, "code": -1, "message": f"图片上传失败: {e}"}

            if llocs:
                parts = [""]
                for lloc in llocs:
                    parts.extend([lloc, lloc, lloc])
                parts.extend(["", "0", "0", "", "0", "0"])
                data["richtype"] = "1"
                data["subrichtype"] = "1"
                data["richval"] = ",".join(parts)
                if bos:
                    data["pic_bo"] = "\t".join(bos)

        result = self._post(f"{URLS['publish']}?g_tk={auth.g_tk}", data, headers)
        return success_or_error(result)

    async def _upload_image(self, image_path: str, auth: AuthInfo) -> dict:
        """上传图片到 QZone，返回 {lloc, bo, url}。"""
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        data = {
            "filename": "image.jpg",
            "uin": auth.uin,
            "skey": auth.skey,
            "zzpaneluin": auth.uin,
            "zzpanelkey": "",
            "p_uin": auth.uin,
            "p_skey": auth.p_skey,
            "qzonetoken": "",
            "uploadtype": "1",
            "albumtype": "7",
            "exttype": "0",
            "refer": "shuoshuo",
            "output_type": "jsonhtml",
            "charset": "utf-8",
            "output_charset": "utf-8",
            "upload_hd": "1",
            "hd_width": "2048",
            "hd_height": "10000",
            "hd_quality": "96",
            "backUrls": "http://upbak.photo.qzone.qq.com/cgi-bin/upload/cgi_upload_image",
            "url": f"{URLS['upload_image']}?g_tk={auth.g_tk}",
            "base64": "1",
            "jsonhtml_callback": "callback",
            "picfile": b64,
            "qzreferrer": f"https://user.qzone.qq.com/{auth.uin}",
        }

        headers = self._headers(auth, post=True)
        raw = self._post(f"{URLS['upload_image']}?g_tk={auth.g_tk}", data, headers)

        if not raw or "data" not in raw:
            raise RuntimeError(f"图片上传响应解析失败: {str(raw)[:200]}")

        d = raw["data"]
        lloc = d.get("lloc", "")
        if not lloc:
            raise RuntimeError(f"图片上传未返回 lloc: {d}")

        bo = d.get("bo", "")
        if not bo:
            url = d.get("url", "")
            bo_m = re.search(r"&bo=([^&]+)", url)
            bo = bo_m.group(1) if bo_m else ""

        return {"lloc": lloc, "bo": bo, "url": d.get("url", "")}

    # ── 删除说说 ────────────────────────────────────────────────

    async def delete(self, tid: str, curkey: str = "", timestamp: int = 0) -> dict:
        """
        删除说说。

        Args:
            tid: 说说 ID
            curkey: 说说 key（可选）。留空时自动使用 tid 兜底，
                新发布的说说 curkey 与 tid 相同；删除旧说说时建议
                用 publish() 返回的 curkey 或从说说列表中获取。
            timestamp: 发布时间戳（可选）

        Returns:
            {"success": True/False, ...}
        """
        auth = await self._auth()
        headers = self._headers(auth, post=True)
        data = {
            "uin": auth.uin,
            "topicId": tid,
            "feedsType": "0",
            "feedsFlag": "0",
            "feedsKey": curkey or tid,
            "feedsAppid": "311",
            "feedsTime": str(timestamp),
            "fupdate": "1",
            "ref": "feeds",
            "qzreferrer": f"https://user.qzone.qq.com/{auth.uin}",
        }
        result = self._post(f"{URLS['delete']}?g_tk={auth.g_tk}", data, headers)
        return success_or_error(result)

    # ── 点赞 ────────────────────────────────────────────────────

    async def like(
        self, target_qq: str, fid: str, cur_key: str, uni_key: str
    ) -> dict:
        """
        点赞一条说说。

        Args:
            target_qq: 对方 QQ 号
            fid: 说说 fid
            cur_key: 当前 key
            uni_key: 唯一 key

        Returns:
            {"success": True/False, ...}
        """
        auth = await self._auth()
        headers = self._headers(auth, post=True)
        data = {
            "qzreferrer": f"https://user.qzone.qq.com/{target_qq}",
            "opuin": auth.uin,
            "unikey": uni_key,
            "curkey": cur_key,
            "appid": "311",
            "from": "1",
            "typeid": "0",
            "abstime": str(int(time.time())),
            "fid": fid,
            "active": "0",
            "format": "json",
            "fupdate": "1",
        }
        result = self._post(f"{URLS['like']}?g_tk={auth.g_tk}", data, headers)
        return success_or_error(result)

    # ── 评论 ────────────────────────────────────────────────────

    async def comment(self, target_qq: str, fid: str, content: str) -> dict:
        """
        评论一条说说。

        Args:
            target_qq: 对方 QQ 号
            fid: 说说的 topicId，格式如 "{uin}_{tid}__1"
            content: 评论内容

        Returns:
            {"success": True/False, ...}
        """
        auth = await self._auth()
        headers = self._headers(auth, post=True)
        data = {
            "topicId": fid,
            "feedsType": "100",
            "inCharset": "utf-8",
            "outCharset": "utf-8",
            "plat": "qzone",
            "source": "ic",
            "hostUin": target_qq,
            "isSignIn": "",
            "platformid": "52",
            "uin": auth.uin,
            "format": "fs",
            "ref": "feeds",
            "content": content,
            "richval": "",
            "richtype": "",
            "private": "0",
            "paramstr": "1",
            "qzreferrer": f"https://user.qzone.qq.com/{target_qq}",
        }
        result = self._post(f"{URLS['comment']}?g_tk={auth.g_tk}", data, headers)
        return success_or_error(result)

    # ── 转发 ────────────────────────────────────────────────────

    async def forward(self, target_qq: str, tid: str, content: str = "") -> dict:
        """
        转发一条说说。

        Args:
            target_qq: 原作者 QQ 号
            tid: 说说 ID
            content: 转发附言

        Returns:
            {"success": True/False, ...}
        """
        auth = await self._auth()
        headers = self._headers(auth, post=True)
        data = {
            "t1_uin": target_qq,
            "t1_source": "1",
            "tid": tid,
            "signin": "0",
            "con": content,
            "with_cmt": "0",
            "fwdToWeibo": "0",
            "forward_source": "2",
            "code_version": "1",
            "format": "fs",
            "hostuin": auth.uin,
            "qzreferrer": f"https://user.qzone.qq.com/{auth.uin}",
        }
        result = self._post(f"{URLS['forward']}?g_tk={auth.g_tk}", data, headers)
        return success_or_error(result)

    # ── 获取好友动态 ────────────────────────────────────────────

    async def get_feed_list(self, page: int = 1, count: int = 10) -> list[dict]:
        """
        获取好友动态列表（信息流）。

        Args:
            page: 页码
            count: 每页数量

        Returns:
            list[dict]，每个 dict 包含 key, appid, nickname, content, images 等
        """
        auth = await self._auth()
        headers = self._headers(auth)
        params = {
            "uin": auth.uin, "scope": "0", "view": "1", "filter": "all",
            "flag": "1", "applist": "all", "pagenum": str(page),
            "count": str(count), "format": "json", "g_tk": str(auth.g_tk),
            "useutf8": "1", "outputhtmlfeed": "1",
        }
        raw = self._get(URLS["feed"], params, headers)
        items = raw.split("{ver:")
        return [parse_feed_item(item) for item in items[1:] if item.strip()]

    # ── 获取指定好友说说 ────────────────────────────────────────

    async def get_friend_moods(self, qq: str, num: int = 10) -> list[dict]:
        """
        获取指定好友的说说列表。

        Args:
            qq: 好友 QQ 号
            num: 获取数量

        Returns:
            list[dict]，每个 dict 包含 tid, content, created_time, cmtnum 等
        """
        auth = await self._auth()
        headers = self._headers(auth)
        params = {
            "uin": qq, "ftype": "0", "sort": "0", "pos": "0",
            "num": str(num), "replynum": "100", "g_tk": str(auth.g_tk),
            "code_version": "1", "format": "jsonp",
            "need_private_comment": "1", "callback": "_preloadCallback",
        }
        raw = self._get(URLS["mood_list"], params, headers)
        data = parse_jsonp(raw)

        if not data or data.get("code") != 0:
            return []

        return [parse_mood(msg) for msg in data.get("msglist", [])]
