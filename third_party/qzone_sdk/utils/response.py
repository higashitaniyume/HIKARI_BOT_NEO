"""接口返回值统一格式化。"""
from __future__ import annotations


def success_or_error(result: dict | None) -> dict:
    """将 CGI 原始响应统一为 {"success": bool, ...} 格式。"""
    if not result:
        return {"success": False, "code": -1, "message": "响应解析失败"}
    code = result.get("code", result.get("ret", -1))
    if code == 0:
        tid = result.get("t1_tid") or result.get("tid", "")
        out = {
            "success": True,
            "tid": tid,
            "time": result.get("t1_time", ""),
            "content": result.get("content", ""),
        }
        # 发布接口会返回 curkey（删除说说时使用，新说说与 tid 相同）
        curkey = result.get("curkey") or result.get("t1_curkey") or ""
        if curkey:
            out["curkey"] = curkey
        return out
    return {
        "success": False,
        "code": code,
        "message": result.get("message") or result.get("msg", "未知错误"),
    }
