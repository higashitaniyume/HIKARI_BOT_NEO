"""全局常量：UA、接口地址、动态类型映射、可见范围。"""

# 浏览器 UA
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_BASE = "https://user.qzone.qq.com/proxy/domain"

# QQ 空间 CGI 接口地址
URLS = {
    "publish": f"{_BASE}/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6",
    "delete": f"{_BASE}/taotao.qzone.qq.com/cgi-bin/emotion_cgi_delete_v6",
    "comment": f"{_BASE}/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds",
    "forward": f"{_BASE}/taotao.qzone.qq.com/cgi-bin/emotion_cgi_forward_v6",
    "like": f"{_BASE}/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app",
    "feed": f"{_BASE}/ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more",
    "mood_list": f"{_BASE}/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6",
    "upload_image": "https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image",
}

# appid → 动态类型名
APP_TYPES = {
    "311": "说说",
    "202": "日志",
    "1": "照片",
    "2": "相册",
    "10": "分享",
    "6600": "广告",
}

# 说说可见范围
VISIBLE = {
    "public": 1,    # 公开
    "friends": 3,   # QQ 好友可见
    "private": 4,   # 仅自己可见
}
