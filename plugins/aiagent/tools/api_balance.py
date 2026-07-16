"""AI Agent tool: 查询 API 余额（DeepSeek / Fish Audio）。"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from core.ai_tool_registry import AIToolContext, register_ai_tool

logger = logging.getLogger("HikariBot.AIAgent.Tools.ApiBalance")

_FISH_AUDIO_CREDIT_URL = "https://api.fish.audio/wallet/self/api-credit"
_DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"
_TIMEOUT_SECONDS = 15


def _build_result(service: str, ok: bool, data: Any, error: str = "") -> dict[str, Any]:
    entry: dict[str, Any] = {"service": service}
    if ok:
        entry["ok"] = True
        if isinstance(data, dict):
            entry.update(data)
        else:
            entry["raw"] = str(data)
    else:
        entry["ok"] = False
        entry["error"] = error or "未知错误"
    return entry


async def _check_fish_audio(api_key: str, proxy: str | None = None) -> dict[str, Any]:
    """查询 Fish Audio API 额度。"""
    if not api_key:
        return _build_result("Fish Audio", False, None, "未配置 API Key")
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, proxy=proxy or None) as client:
            resp = await client.get(
                _FISH_AUDIO_CREDIT_URL,
                headers=headers,
                params={"check_free_credit": "true"},
            )
        if resp.status_code == 200:
            data = resp.json()
            return _build_result("Fish Audio", True, {
                "credit_remaining": data.get("credit", "?"),
                "has_free_credit": data.get("has_free_credit", "?"),
            })
        error_detail = resp.text[:200]
        logger.warning("[ApiBalance] Fish Audio API 返回 %s: %s", resp.status_code, error_detail)
        return _build_result("Fish Audio", False, None, f"HTTP {resp.status_code}: {error_detail}")
    except Exception as e:
        logger.warning("[ApiBalance] Fish Audio 查询失败: %s", e)
        return _build_result("Fish Audio", False, None, str(e)[:200])


async def _check_deepseek(api_key: str, proxy: str | None = None) -> dict[str, Any]:
    """查询 DeepSeek 账户余额信息。"""
    if not api_key:
        return _build_result("DeepSeek", False, None, "未配置 API Key")
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, proxy=proxy or None) as client:
            resp = await client.get(_DEEPSEEK_BALANCE_URL, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            balance_infos = data.get("balance_infos") or []
            entries = []
            for bi in balance_infos:
                entries.append({
                    "currency": bi.get("currency", "?"),
                    "total_balance": bi.get("total_balance", "?"),
                    "granted_balance": bi.get("granted_balance", "?"),
                    "topped_up_balance": bi.get("topped_up_balance", "?"),
                })
            return _build_result("DeepSeek", True, {
                "is_available": data.get("is_available", False),
                "balance_infos": entries,
            })
        if resp.status_code == 404:
            return _build_result("DeepSeek", True, {
                "note": "余额查询接口不可用，请前往 platform.deepseek.com 查看",
            })
        error_detail = resp.text[:200]
        logger.warning("[ApiBalance] DeepSeek API 返回 %s: %s", resp.status_code, error_detail)
        return _build_result("DeepSeek", False, None, f"HTTP {resp.status_code}: {error_detail}")
    except Exception as e:
        logger.warning("[ApiBalance] DeepSeek 查询失败: %s", e)
        return _build_result("DeepSeek", False, None, str(e)[:200])


@register_ai_tool(
    "api_balance",
    plugin_name="aiagent",
    description="查询各 API 服务的余额和用量信息，包括 DeepSeek 和 Fish Audio。当用户询问'还剩多少额度''余额''还能用多久'时使用。",
    parameters={
        "type": "object",
        "properties": {
            "service": {
                "type": "string",
                "description": "要查询的服务，留空则查询全部。可选值：deepseek, fish_audio",
                "enum": ["", "deepseek", "fish_audio"],
            },
        },
        "additionalProperties": False,
    },
    readonly=True,
    requires_superuser=False,
    enabled_by_default=False,
)
async def handle_api_balance(context: AIToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """查询 API 余额。"""
    service = str(arguments.get("service") or "").strip().lower()

    # 读取配置
    deepseek_key = ""
    fish_key = ""
    proxy = None

    try:
        from plugins.aiagent.config import get_config as get_aiagent_config
        ai_cfg = get_aiagent_config()
        model_cfg = ai_cfg.get("model") if isinstance(ai_cfg.get("model"), dict) else {}
        deepseek_key = str(model_cfg.get("api_key") or "")
        proxy = str(model_cfg.get("proxy") or "").strip() or None
    except Exception as e:
        logger.warning("[ApiBalance] 读取 AI Agent 配置失败: %s", e)

    try:
        from plugins.tts_speaker.config import get_config as get_tts_config
        tts_cfg = get_tts_config()
        fish_cfg = tts_cfg.get("fish_audio") if isinstance(tts_cfg.get("fish_audio"), dict) else {}
        fish_key = str(fish_cfg.get("api_key") or "")
        if not proxy:
            proxy = str(tts_cfg.get("proxy", "")).strip() or None
    except Exception as e:
        logger.warning("[ApiBalance] 读取 TTS 配置失败: %s", e)

    results: list[dict[str, Any]] = []

    if not service or service == "deepseek":
        results.append(await _check_deepseek(deepseek_key, proxy))
    if not service or service == "fish_audio":
        results.append(await _check_fish_audio(fish_key, proxy))

    if not results:
        results.append(_build_result("unknown", False, None, f"未知服务: {service}"))

    return {"services": results}
