"""
QQ 音乐解析插件的异常类型。

所有异常的 ``str(exc)`` 都是可直接展示给用户的中文描述；调用方按类型映射成
不同的提示语（尤其是 VIP 付费、缺 cookie、ID 解析失败三种要分开说）。
"""

from __future__ import annotations


class QQMusicError(RuntimeError):
    """QQ 音乐解析/下载失败基类。"""


class QQMusicResolveError(QQMusicError):
    """无法把链接里的 ID 解析成有效歌曲。

    典型原因：songid 被当成 songmid 使用（接口会返回字段全空的 track_info 而不报错）、
    歌曲已下架、或链接里的 ID 被截断。
    """


class QQMusicNoFormatError(QQMusicError):
    """yt-dlp 报告该曲目没有任何可用格式。

    需要结合接口返回的付费标志进一步区分：VIP/付费曲目、缺 cookie、或确实不可下载。
    """


class QQMusicVipRequiredError(QQMusicError):
    """曲目属于「播放也需付费/会员」的 VIP 档，匿名与普通账号都拿不到。"""


class QQMusicCookieRequiredError(QQMusicError):
    """需要登录 cookie（可能只是没配，也可能是登录后才有权限）。"""


class QQMusicSizeError(QQMusicError):
    """音频文件超过配置的大小上限。"""


class QQMusicDownloadError(QQMusicError):
    """其余下载阶段失败（网络、超时、文件缺失等）。"""
