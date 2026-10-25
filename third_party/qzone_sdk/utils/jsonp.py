"""JSONP 响应解析。"""
from __future__ import annotations

import json
import re


def parse_jsonp(raw: str) -> dict | None:
    """解析 JSONP 响应，兼容多种回调格式；失败返回 None。"""
    for pattern in [
        r"_preloadCallback\((.*)\);?$",
        r"frameElement\.callback\((.*)\)",
        r"_Callback\((.*)\)",
        r"callback\((.*)\)",
    ]:
        m = re.search(pattern, raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None
