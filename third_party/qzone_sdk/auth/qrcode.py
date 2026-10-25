"""手机 QQ 扫码登录认证提供者（无需 NapCat）。

协议说明（与 QQ 网页版一致）：
1. 请求 ptqrshow 获取二维码图片，并记录 qrsig cookie；
2. 用 qrsig 计算 ptqrtoken；
3. 轮询 ptqrlogin 直到用户扫码确认；
4. 从成功响应中提取 ptsigx，请求 check_sig 换取最终 Cookie（p_skey/skey/uin 等）。
"""
from __future__ import annotations

import asyncio
import re
import time
import urllib.parse
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..errors import QZoneAuthError, QZoneNetworkError
from ..utils.gtk import compute_ptqrtoken
from ..utils.http import HttpSession
from .base import AuthInfo, AuthProvider, auth_from_cookies

# QQ 空间 web 端 appid / daid
_QR_APPID = "549000912"
_QR_DAID = "5"
# 登录成功后的落地页
_LOGIN_SUCC_URL = "https://qzs.qq.com/qzone/v5/loginsucc.html?para=izone"

_QR_SHOW_URL = (
    "https://ssl.ptlogin2.qq.com/ptqrshow"
    "?appid=549000912&e=2&l=M&s=3&d=72&v=4&t={t}&daid=5&pt_3rd_aid=0"
)
_POLL_URL = "https://ssl.ptlogin2.qq.com/ptqrlogin"
_CHECK_SIG_URL = (
    "https://ptlogin2.qzone.qq.com/check_sig"
    "?pttype=1&uin={uin}&service=ptqrlogin&nodirect=0&ptsigx={ptsigx}"
    "&s_url={s_url}&f_url=&ptlang=2052&ptredirect=100&aid=549000912&daid=5"
    "&j_later=0&low_login_hour=0&regmaster=0&pt_login_type=3&pt_aid=0"
    "&pt_aaid=16&pt_light=0&pt_3rd_aid=0"
)


class LoginStatus(Enum):
    """扫码登录状态码（对应 ptuiCB 回调首个参数）。"""

    WAITING = "66"   # 二维码未失效，等待扫码
    SCANNED = "67"   # 已扫码，等待手机确认
    SUCCESS = "0"    # 登录成功
    EXPIRED = "65"   # 二维码已失效，需重新获取


@dataclass
class QRState:
    """单次轮询结果。"""

    status: LoginStatus
    message: str = ""
    redirect_url: str = ""
    ptsigx: str = ""
    nick: str = ""   # ptuiCB 第 6 参数，实际是昵称而非 QQ 号


def _guess_uin(redirect_url: str, nick: str) -> str:
    """从登录成功响应中猜测 QQ 号：优先跳转地址的 clientuin，其次纯数字昵称。"""
    m = re.search(r"clientuin=(\d+)", redirect_url)
    if m:
        return m.group(1)
    if nick.isdigit():
        return nick
    return ""


class QRCodeLoginProvider(AuthProvider):
    """
    扫码登录认证提供者。

    首次使用命令行 ``qzone login --save`` 保存凭证后，后续命令无需 NapCat；
    也可在代码中直接使用：QZoneClient(QZoneConfig(auth_provider=QRCodeLoginProvider())).

    Args:
        qr_path: 二维码图片保存路径
        timeout: HTTP 请求超时秒数
        poll_interval: 状态轮询间隔秒数
    """

    def __init__(self, qr_path: str = "qrcode.png", timeout: float = 10, poll_interval: float = 3):
        self.qr_path = qr_path
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._session = HttpSession()
        self._ptqrtoken: str = ""

    # ── 二维码 ────────────────────────────────────────────────

    async def get_qr_image(self) -> bytes:
        """获取二维码图片字节，同时记录 qrsig 并计算 ptqrtoken。"""
        url = _QR_SHOW_URL.format(t=int(time.time() * 1000))
        raw = self._session.raw_get(url, {}, self.timeout)
        qrsig = next(
            (c.value for c in self._session.cookie_jar if c.name == "qrsig"), ""
        )
        if not qrsig:
            raise QZoneAuthError("获取二维码失败：未返回 qrsig")
        self._ptqrtoken = str(compute_ptqrtoken(qrsig))
        return raw

    def save_qr_image(self, image: bytes, path: str | None = None) -> str:
        """将二维码图片写入文件，返回实际路径（根据图片格式自动补扩展名）。"""
        target = Path(path or self.qr_path)
        if not target.suffix:
            target = target.with_suffix(self._guess_ext(image))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(image)
        return str(target)

    @staticmethod
    def _guess_ext(data: bytes) -> str:
        if data[:4] == b"GIF8":
            return ".gif"
        return ".png"

    # ── 轮询 ──────────────────────────────────────────────────

    async def check_login(self) -> QRState:
        """轮询一次登录状态。"""
        params = {
            # 直接放原始 URL，由 urlencode 编码一次即可（切勿先 quote 再 urlencode，会二次编码触发风控）
            "u1": _LOGIN_SUCC_URL,
            "ptqrtoken": self._ptqrtoken,
            "ptredirect": "0",
            "h": "1",
            "t": "1",
            "g": "1",
            "from_ui": "1",
            "ptlang": "2052",
            "action": f"0-0-{time.time()}",
            "js_ver": "20032614",
            "js_type": "1",
            "login_sig": "",
            "pt_uistyle": "40",
            "aid": _QR_APPID,
            "daid": _QR_DAID,
        }
        url = _POLL_URL + "?" + urllib.parse.urlencode(params)
        body = self._session.raw_get(url, {}, self.timeout).decode("utf-8", errors="replace")
        return self._parse_callback(body)

    @staticmethod
    def _parse_callback(body: str) -> QRState:
        """解析 ptuiCB('code','result','url','msg','nick','uin') 回调。"""
        vals = re.findall(r"['\"]([^'\"]*)['\"]", body)
        if len(vals) < 5:
            raise QZoneNetworkError(f"登录状态响应解析失败: {body[:200]}")
        code, redirect_url, message = vals[0], vals[2], vals[4]
        try:
            status = LoginStatus(code)
        except ValueError:
            raise QZoneNetworkError(f"未知登录状态码: {code}")
        ptsigx_m = re.search(r"ptsigx=([^&\s'\"]+)", body)
        return QRState(
            status=status,
            message=message,
            redirect_url=redirect_url,
            ptsigx=ptsigx_m.group(1) if ptsigx_m else "",
            nick=vals[5] if len(vals) > 5 else "",
        )

    # ── 完成登录 ──────────────────────────────────────────────

    async def finish_login(self, state: QRState | None = None) -> AuthInfo:
        """用成功响应的 ptsigx 换取最终 Cookie 并构建认证信息。"""
        if state is None:
            raise QZoneAuthError("缺少登录状态，请先完成扫码")
        if state.status is not LoginStatus.SUCCESS:
            raise QZoneAuthError(f"尚未登录成功（当前状态: {state.status.value}）")
        if not state.ptsigx:
            raise QZoneNetworkError("登录成功但响应中缺少 ptsigx，请重试")

        # uin 提取：优先 pt4_auth 跳转地址中的 clientuin（真实 QQ 号）；
        # 否则跟随跳转地址，从服务端下发的 uin/p_uin Cookie 中获取。
        uin = _guess_uin(state.redirect_url, state.nick)
        if not uin:
            if state.redirect_url:
                self._session.raw_get(state.redirect_url, {}, self.timeout)
            uin = next(
                (
                    c.value.lstrip("o")
                    for c in self._session.cookie_jar
                    if c.name in ("uin", "p_uin")
                ),
                "",
            )
        if not uin:
            raise QZoneAuthError("登录未完成：无法确定 QQ 号，请重试")

        check_url = _CHECK_SIG_URL.format(
            uin=uin or "0",
            ptsigx=state.ptsigx,
            s_url=urllib.parse.quote(_LOGIN_SUCC_URL, safe=""),
        )
        if not check_url.isascii():
            raise QZoneNetworkError("登录凭证解析异常：换票地址含非 ASCII 字符，请重试")
        self._session.raw_get(check_url, {}, self.timeout)

        # 从 CookieJar 汇总最终 Cookie（uin 以服务端下发的 p_uin/uin 为准）
        cookies = {c.name: c.value for c in self._session.cookie_jar}
        p_skey = cookies.get("p_skey", "")
        if not p_skey and state.redirect_url:
            # 兜底：跟随 pt4_auth 跳转再次收集 Cookie
            self._session.raw_get(state.redirect_url, {}, self.timeout)
            cookies = {c.name: c.value for c in self._session.cookie_jar}
            p_skey = cookies.get("p_skey", "")
        if not p_skey:
            raise QZoneAuthError("登录未完成：未获取到 p_skey")
        uin = (cookies.get("p_uin") or cookies.get("uin") or uin or "").lstrip("o")
        if not uin:
            raise QZoneAuthError("登录未完成：未获取到 uin")

        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        return auth_from_cookies(cookie_str, uin)

    # ── 完整流程 ──────────────────────────────────────────────

    async def get_auth(self, on_status=None) -> AuthInfo:
        """
        完整登录流程：获取二维码 → 轮询 → 完成登录。

        Args:
            on_status: 可选回调，接收状态消息字符串（如扫码进度提示）。
        """
        image = await self.get_qr_image()
        path = self.save_qr_image(image)
        if on_status:
            on_status(f"二维码已保存到: {path}")

        while True:
            state = await self.check_login()
            if state.status is LoginStatus.SUCCESS:
                if on_status:
                    on_status(state.message or "登录成功")
                return await self.finish_login(state)
            if state.status is LoginStatus.EXPIRED:
                image = await self.get_qr_image()
                path = self.save_qr_image(image)
                if on_status:
                    on_status(f"二维码已失效，已重新生成: {path}")
            elif state.status is LoginStatus.SCANNED and on_status:
                on_status("已扫码，请在手机上确认登录")
            elif state.status is LoginStatus.WAITING and on_status:
                on_status(state.message or "等待扫码...")
            await asyncio.sleep(self.poll_interval)
