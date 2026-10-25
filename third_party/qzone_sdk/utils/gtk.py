"""g_tk / ptqrtoken 哈希计算（QQ 空间固定算法）。"""


def compute_g_tk(p_skey: str) -> int:
    """由 p_skey（或 skey）计算 g_tk 令牌。初始哈希值为 5381。"""
    h = 5381
    for ch in p_skey:
        h += (h << 5) + ord(ch)
    return h & 0x7FFFFFFF


def compute_ptqrtoken(qrsig: str) -> int:
    """由 qrsig 计算扫码登录所需的 ptqrtoken。初始哈希值为 0。"""
    e = 0
    for ch in qrsig:
        e += (e << 5) + ord(ch)
    return 2147483647 & e
