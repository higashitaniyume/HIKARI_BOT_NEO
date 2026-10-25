"""基于标准库 urllib 的 HTTP 会话封装（零第三方依赖）。"""
from __future__ import annotations

import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from typing import Optional

from ..constants import UA
from .jsonp import parse_jsonp

# 默认请求头：缺失时腾讯登录接口会返回 403。
# 注意不设 Accept-Encoding，urllib 不会自动解压 gzip。
_DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}


class HttpSession:
    """
    轻量 HTTP 会话。

    可选携带 CookieJar 以支持登录态（如扫码登录流程）。
    请求均为同步阻塞调用，由调用方在异步上下文执行（与 SDK 其他部分保持一致）。
    """

    def __init__(self, cookie_jar: Optional[CookieJar] = None):
        self._jar = cookie_jar or CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar)
        )

    @property
    def cookie_jar(self) -> CookieJar:
        return self._jar

    def _request(self, url: str, headers: dict, method: str, data: bytes | None, timeout: float) -> bytes:
        merged = {**_DEFAULT_HEADERS, **headers}
        req = urllib.request.Request(url, data=data, method=method)
        for k, v in merged.items():
            req.add_header(k, v)
        return self._opener.open(req, timeout=timeout).read()

    def post(self, url: str, data: dict, headers: dict, timeout: float) -> dict | None:
        """POST 表单数据并解析 JSONP 响应。"""
        body = urllib.parse.urlencode(data).encode("utf-8")
        raw = self._request(url, headers, "POST", body, timeout)
        return parse_jsonp(raw.decode("utf-8", errors="replace"))

    def get(self, url: str, params: dict, headers: dict, timeout: float) -> str:
        """GET 并返回文本响应（自动拼接查询串）。"""
        return self.raw_get(
            f"{url}?{urllib.parse.urlencode(params)}", headers, timeout
        ).decode("utf-8", errors="replace")

    def raw_get(self, url: str, headers: dict, timeout: float) -> bytes:
        """GET 指定完整 URL，返回原始字节（用于已带查询串的地址，如扫码跳转）。"""
        return self._request(url, headers, "GET", None, timeout)
