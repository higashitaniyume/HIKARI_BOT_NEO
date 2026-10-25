"""异常体系：所有库内异常均继承自 QZoneError。"""


class QZoneError(Exception):
    """qzone-sdk 基础异常。"""


class QZoneAuthError(QZoneError):
    """认证/登录相关错误（Cookie 缺失、扫码失败、NapCat 未登录等）。"""


class QZoneApiError(QZoneError):
    """接口调用失败（服务端返回 code != 0）。"""


class QZoneNetworkError(QZoneError):
    """网络层错误（连接失败、超时、响应解析失败等）。"""
