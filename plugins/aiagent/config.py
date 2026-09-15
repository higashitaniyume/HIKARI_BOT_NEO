"""AI Agent 配置模块（多配置文件版）。

磁盘上是一份「多配置文档」：

    {
      "enabled": false,                  // 全局主开关
      "active_profile": "default",       // 全局默认配置文件
      "profiles": {                      // 每个配置文件一套模型/人格/工具设置
        "default": {"name": "默认配置", "api": {}, "model": {}, ...}
      },
      "bindings": {                      // 群号 / QQ 号 -> 配置文件 ID
        "group": {"123456": "default"},
        "private": {}
      },
      "quota": {},                       // 全局配额（配额页管理）
      "permissions": {}                  // 全局黑白名单（配额页管理）
    }

对运行时来说，`get_config()` 仍然返回和以前一样的**扁平**配置
（profile 的段 + 全局段），所以 client / memory / quota / tools 等模块
完全不用感知多配置的存在。按会话取配置用 `get_config_for_event()`。

老版本的扁平配置文件会在 `ensure_config()` 里自动迁移为
`profiles.default`，迁移是幂等的。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

from core.access_control import DEFAULT_ACCESS_RULES
from .persona import PERSONA_ROOT

logger = logging.getLogger("HikariBot.AIAgent.Config")

CONFIG_PATH = Path("BotData/plugin_configs/aiagent.json")

# 属于单个配置文件的段（后台「AI」页编辑）。
PROFILE_KEYS: tuple[str, ...] = ("api", "model", "thinking", "vision", "persona", "chat", "memory", "tools")
# 所有配置文件共用的全局段（后台「AI 配额」页 / 「AI Agent」页编辑）。
GLOBAL_KEYS: tuple[str, ...] = ("enabled", "quota", "permissions", "chatlog")

DEFAULT_PROFILE_ID = "default"
DEFAULT_PROFILE_NAME = "默认配置"
MAX_PROFILES = 20
MAX_PROFILE_NAME_CHARS = 40

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    # API 协议：
    #   "responses" — DeepSeek Responses API（无状态，兼容 OpenAI Responses 格式，
    #                 支持服务端内置 web_search 工具）
    #   "chat_completions" — OpenAI Chat Completions 兼容协议（旧接口）
    "api": {
        "protocol": "responses",
    },
    "model": {
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "model": "deepseek-v4-flash",
        "temperature": 0.7,
        "top_p": 1.0,
        "max_tokens": 8192,
        "timeout_seconds": 120,
        "proxy": "",
        # null（不传 tool_choice）以兼容 DeepSeek V4 思考模式。
        # 可设为 "auto" / "none" / "required"。
        "tool_choice": None,
    },
    "thinking": {
        "enabled": True,
        "reasoning_effort": "high",
    },
    # 图片输入。需要视觉模型（如 deepseek-v4-flash-vision-exp）；模型不支持时
    # 会自动去掉图片重试一次，所以开错了不会让机器人失声，只是白下载一遍。
    "vision": {
        "enabled": False,
        "max_images": 2,
        # low 会把图片缩到 512×512，每张最多按 384 token 计费，最省钱。
        "detail": "low",
        "include_quoted": True,
        "max_bytes": 5242880,
        "download_timeout_seconds": 20,
    },
    "persona": {
        "skill_path": "BotData/agent_personas/default",
        "max_chars": 12000,
        "include_references": True,
        "reference_max_depth": 1,
        "reference_max_files": 8,
        "reference_max_chars_per_file": 8000,
        "reference_max_total_chars": 24000,
        "fallback_prompt": "你是 {bot_name} 的聊天 AI Agent。请自然、简洁地回复用户。",
    },
    "chat": {
        "max_user_chars": 2000,
        "max_reply_chars": 3500,
        "short_reply_chars": 200,
        "max_history_messages": 10,
        # 短期上下文总字符预算：超出时从最旧的对话开始丢弃（防止长消息撑爆上下文与费用）
        "max_context_chars": 12000,
        "cooldown_seconds": 3,
        "system_prompt_extra": "",
        # 群聊公共上下文（默认关闭）：开启后把本群最近几轮「成员 ↔ 机器人」的对话
        # 作为背景注入，供多人接话/跨用户话题使用；关闭时每个用户只看自己的上下文。
        "group_shared_context": {
            "enabled": False,
            "max_messages": 10,
        },
        "blocked_url_domains": [
            "douyin.com",
            "iesdouyin.com",
            "bilibili.com",
            "b23.tv",
            "xiaohongshu.com",
            "xhslink.com",
            "xhslink.cn",
            "xiaoheihe.cn",
            "heybox.cn",
            "twitter.com",
            "x.com",
            "t.co",
            "toutiao.com",
            "ixigua.com",
            "kuaishou.com",
            "gifshow.com",
            "weibo.com",
            "weibo.cn",
            "tiktok.com",
            "vm.tiktok.com",
        ],
    },
    "memory": {
        "enabled": True,
        "root": "UserData/aiagent_memory",
        "max_read_chars_per_file": 8000,
        "max_file_chars": 60000,
    },
    # 本地群消息记录（chatlog）：NapCat 不存历史（消息走 LRU，约 5000 条即过期），
    # 「总结某人之前说了什么」只能靠机器人自己记。只记纯文本，按 群/日期 存 JSONL。
    # 默认记录所有群；可用 groups 白名单收窄，按 retention_days / max_total_mb 自动清理。
    "chatlog": {
        "enabled": True,
        "groups": [],
        "retention_days": 7,
        "max_total_mb": 200,
        # 是否连机器人自己的发言也记
        "record_bot": False,
    },
    "tools": {
        "help": {
            "enabled": True,
        },
        "search": {
            "enabled": True,
            # "builtin": 用 DeepSeek Responses API 服务端内置 web_search（无需自建搜索）；
            # "searxng": 用自建 SearXNG 的函数工具（兼容任意 OpenAI 兼容端点）。
            # Responses 协议下默认内置搜索；Chat Completions 协议下自动退回 SearXNG。
            "mode": "builtin",
            "base_url": "http://searxng-core:8080",
            "timeout_seconds": 30,
            "max_results": 5,
            "safesearch": 1,
            "language": "auto",
            "categories": "general",
        },
        "files": {
            "enabled": True,
            "allow_writes": False,
            "max_read_chars": 20000,
            "max_write_chars": 20000,
        },
        # 群聊工具（只读，且只在当前群生效）：枚举本群成员、查成员名片资料、查成员发言
        "group_members": {
            "enabled": True,
            # 一次最多返回多少位成员，避免大群人名单把上下文撑爆
            "max_members": 100,
        },
        "member_profile": {
            "enabled": True,
        },
        "user_messages": {
            "enabled": True,
            "max_messages": 50,
            # 返回发言的总字符预算（从最旧的开始丢）
            "max_chars": 4000,
            # 本地记录不足时，是否用 NapCat 的实时群历史窗口补齐（只覆盖最近一小段）
            "allow_live_history": True,
        },
        "plugin_tools": {
            "enabled": True,
            "allow_side_effects": False,
            "enabled_names": [],
            "disabled_names": [],
        },
        "max_tool_rounds": 4,
        # wiki 优先预取：命中 wiki 别名时先替模型跑一次 wiki（可选再跑一次 web_search）
        "wiki_prefetch": {
            "enabled": True,
            "web_search": True,
        },
        # 单个工具调用的超时（秒）：挂住的工具会拖住整轮回复与当前会话的锁。
        "tool_timeout_seconds": 30,
    },
    # 配额：替代原 permissions 黑白名单。群聊扣群额度，私聊扣用户额度。
    # 额度单位为「对话次数」（一条用户消息 = 1 次），每日 / 每小时各一窗。
    # 所有限额 0 = 不限额；user/group_overrides 可给个别用户/群定制；
    # exempt_* 完全跳过检查与扣费。enabled 默认关闭，在后台「AI 配额」页启用。
    "quota": {
        "enabled": False,
        "default_user": {"daily": 100, "hourly": 10},
        "default_group": {"daily": 300, "hourly": 30},
        "user_overrides": {},
        "group_overrides": {},
        "exempt_user_ids": [],
        "exempt_group_ids": [],
        "count_background": True,
    },
    # 黑白名单：配额页「访问控制」板块管理。命中黑名单/不在白名单时直接拒绝，
    # 优先于配额检查。
    "permissions": copy.deepcopy(DEFAULT_ACCESS_RULES),
}

# 单个配置文件的默认内容。
DEFAULT_PROFILE: dict[str, Any] = {
    "name": DEFAULT_PROFILE_NAME,
    **{key: copy.deepcopy(DEFAULT_CONFIG[key]) for key in PROFILE_KEYS},
}

# 磁盘上的多配置文档默认内容。
DEFAULT_DOCUMENT: dict[str, Any] = {
    "enabled": DEFAULT_CONFIG["enabled"],
    "active_profile": DEFAULT_PROFILE_ID,
    "profiles": {DEFAULT_PROFILE_ID: copy.deepcopy(DEFAULT_PROFILE)},
    "bindings": {"group": {}, "private": {}},
    "quota": copy.deepcopy(DEFAULT_CONFIG["quota"]),
    "permissions": copy.deepcopy(DEFAULT_CONFIG["permissions"]),
    # 全局段：本地聊天记录是整机一份（一个保留策略），不随配置文件切换
    "chatlog": copy.deepcopy(DEFAULT_CONFIG["chatlog"]),
}

BINDING_KINDS: tuple[str, ...] = ("group", "private")

_PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
# 配置读写跨线程：NoneBot 事件循环（聊天命令改绑定）+ admin HTTP 线程（保存配置）。
_lock = threading.RLock()


# ── 读写 ──────────────────────────────────────────────────────────────────


def _write_config(data: dict[str, Any]) -> None:
    """原子写入配置文件（先写临时文件再 replace，避免半截 JSON）。"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = CONFIG_PATH.with_name(
        f"{CONFIG_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, CONFIG_PATH)


def _read_raw() -> dict[str, Any] | None:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ── 规范化 ────────────────────────────────────────────────────────────────


def _safe_profile_name(value: Any) -> str:
    name = _CONTROL_CHARS_RE.sub(" ", str(value or "")).strip()
    name = re.sub(r"\s+", " ", name)[:MAX_PROFILE_NAME_CHARS].strip()
    return name or "未命名配置"


def _slug(name: Any) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", str(name or "").strip().lower())
    return slug.strip("-")[:32].strip("-")


def _unique_profile_id(name: Any, existing: set[str]) -> str:
    base = _slug(name) or "profile"
    if base not in existing:
        return base
    for index in range(2, MAX_PROFILES + 1000):
        candidate = f"{base[:28]}-{index}"
        if candidate not in existing:
            return candidate
    raise ValueError("无法生成唯一的配置文件 ID。")


def _normalize_profile(raw: Any) -> dict[str, Any]:
    """把任意输入收敛成一份完整的配置文件（只保留 name + PROFILE_KEYS）。"""
    src = raw if isinstance(raw, dict) else {}
    patch = {key: src[key] for key in PROFILE_KEYS if isinstance(src.get(key), dict)}
    profile = _deep_merge({key: DEFAULT_PROFILE[key] for key in PROFILE_KEYS}, patch)
    return {
        "name": _safe_profile_name(src.get("name")),
        **{key: profile[key] for key in PROFILE_KEYS},
    }


def _normalize_profiles(raw: Any) -> dict[str, dict[str, Any]]:
    src = raw if isinstance(raw, dict) else {}
    profiles: dict[str, dict[str, Any]] = {}
    for raw_id, raw_profile in src.items():
        profile = _normalize_profile(raw_profile)
        profile_id = str(raw_id).strip()
        if not _PROFILE_ID_RE.fullmatch(profile_id) or profile_id in profiles:
            profile_id = _unique_profile_id(profile_id or profile["name"], set(profiles))
        profiles[profile_id] = profile
        if len(profiles) >= MAX_PROFILES:
            break
    if not profiles:
        profiles[DEFAULT_PROFILE_ID] = _normalize_profile({"name": DEFAULT_PROFILE_NAME})
    return profiles


def _normalize_bindings(raw: Any, profile_ids: set[str]) -> dict[str, dict[str, str]]:
    """只保留指向真实存在的配置文件的绑定。"""
    src = raw if isinstance(raw, dict) else {}
    bindings: dict[str, dict[str, str]] = {}
    for kind in BINDING_KINDS:
        table = src.get(kind) if isinstance(src.get(kind), dict) else {}
        clean: dict[str, str] = {}
        for ident, profile_id in table.items():
            key = str(ident).strip()
            value = str(profile_id or "").strip()
            if key and value in profile_ids:
                clean[key] = value
        bindings[kind] = clean
    return bindings


def _normalize_document(raw: Any) -> dict[str, Any]:
    """把磁盘内容（新旧格式都行）收敛成规范的多配置文档。

    幂等：对已规范的文档返回内容相等的结果。`ensure_config()` 靠这个判断
    是否需要回写磁盘——每条聊天消息都会走一次，不能每次都改文件。
    """
    src = dict(raw) if isinstance(raw, dict) else {}

    raw_profiles = src.get("profiles")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        # 旧的扁平配置：顶层直接放着 model / persona / tools 等段。
        legacy = {key: src[key] for key in PROFILE_KEYS if isinstance(src.get(key), dict)}
        raw_profiles = {
            DEFAULT_PROFILE_ID: {**legacy, "name": src.get("name") or DEFAULT_PROFILE_NAME}
        }
        if legacy:
            logger.info("检测到旧版 AI Agent 扁平配置，已迁移为配置文件 %r", DEFAULT_PROFILE_ID)

    profiles = _normalize_profiles(raw_profiles)
    profile_ids = set(profiles)

    active = str(src.get("active_profile") or "").strip()
    if active not in profile_ids:
        active = DEFAULT_PROFILE_ID if DEFAULT_PROFILE_ID in profile_ids else next(iter(profiles))

    if isinstance(src.get("quota"), dict):
        quota = _deep_merge(DEFAULT_DOCUMENT["quota"], src["quota"])
    else:
        quota = copy.deepcopy(DEFAULT_DOCUMENT["quota"])

    # permissions 由「访问控制」板块整体管理，原样保留，缺失时补默认值。
    permissions = src.get("permissions")
    if permissions is None:
        permissions = DEFAULT_DOCUMENT["permissions"]

    # chatlog 是全局段：缺失时补默认值，存在时按默认值深合并（新增字段自动补齐）。
    if isinstance(src.get("chatlog"), dict):
        chatlog = _deep_merge(DEFAULT_DOCUMENT["chatlog"], src["chatlog"])
    else:
        chatlog = copy.deepcopy(DEFAULT_DOCUMENT["chatlog"])

    return {
        "enabled": bool(src.get("enabled", DEFAULT_DOCUMENT["enabled"])),
        "active_profile": active,
        "profiles": profiles,
        "bindings": _normalize_bindings(src.get("bindings"), profile_ids),
        "quota": quota,
        "permissions": copy.deepcopy(permissions),
        "chatlog": chatlog,
    }


# ── 文档级读写 ────────────────────────────────────────────────────────────


def ensure_config() -> None:
    """确保配置文件存在且是规范的多配置结构（含旧格式自动迁移）。"""
    with _lock:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        PERSONA_ROOT.mkdir(parents=True, exist_ok=True)
        if not CONFIG_PATH.exists():
            _write_config(copy.deepcopy(DEFAULT_DOCUMENT))
            logger.info("已创建 AI Agent 配置文件: %s", CONFIG_PATH)
            return

        data = _read_raw()
        if data is None:
            return

        normalized = _normalize_document(data)
        if normalized != data:
            _write_config(normalized)
            logger.info("已补全 AI Agent 配置文件: %s", CONFIG_PATH)


def get_raw_config() -> dict[str, Any]:
    """读取完整的多配置文档（后台管理用）。"""
    ensure_config()
    with _lock:
        data = _read_raw()
    if data is None:
        logger.warning("读取 AI Agent 配置失败，使用默认配置")
        return copy.deepcopy(DEFAULT_DOCUMENT)
    return _normalize_document(data)


def save_raw_config(doc: dict[str, Any]) -> dict[str, Any]:
    """规范化并写入完整的多配置文档。"""
    normalized = _normalize_document(doc)
    with _lock:
        _write_config(normalized)
    return copy.deepcopy(normalized)


# ── 有效配置（扁平，运行时用） ─────────────────────────────────────────────


def _effective_config(doc: dict[str, Any], profile_id: str | None = None) -> dict[str, Any]:
    profiles = doc["profiles"]
    pid = profile_id if profile_id in profiles else doc["active_profile"]
    profile = profiles[pid]

    cfg = _deep_merge(DEFAULT_CONFIG, {key: profile[key] for key in PROFILE_KEYS})
    for key in GLOBAL_KEYS:
        if key in doc:
            cfg[key] = copy.deepcopy(doc[key])
    cfg["_profile_id"] = pid
    cfg["_profile_name"] = profile.get("name") or pid
    return cfg


def get_config(profile_id: str | None = None) -> dict[str, Any]:
    """返回扁平的有效配置（默认取全局默认配置文件）。

    形状与多配置改造前一致，额外带 `_profile_id` / `_profile_name`。
    """
    return _effective_config(get_raw_config(), profile_id)


def binding_scope(event: Any) -> tuple[str, str]:
    """事件 -> (绑定类型, 标识)：群聊 ("group", 群号)，私聊 ("private", QQ 号)。"""
    group_id = getattr(event, "group_id", None)
    if group_id is not None:
        return "group", str(group_id)
    try:
        user_id = str(event.get_user_id())
    except Exception:
        user_id = ""
    return "private", user_id


def resolve_profile_id(kind: str, ident: Any, doc: dict[str, Any] | None = None) -> str:
    """查会话绑定的配置文件 ID；未绑定或指向已删除配置时回落全局默认。"""
    doc = doc if doc is not None else get_raw_config()
    if kind in BINDING_KINDS:
        bound = str(doc.get("bindings", {}).get(kind, {}).get(str(ident)) or "").strip()
        if bound and bound in doc["profiles"]:
            return bound
    return doc["active_profile"]


def get_config_for_event(event: Any) -> dict[str, Any]:
    """按会话绑定取有效配置；未绑定的会话走全局默认配置文件。"""
    doc = get_raw_config()
    kind, ident = binding_scope(event)
    return _effective_config(doc, resolve_profile_id(kind, ident, doc=doc))


def save_config(data: dict[str, Any], profile_id: str | None = None) -> dict[str, Any]:
    """把扁平配置写回指定配置文件（默认全局默认配置）+ 全局段。

    只更新 `data` 里出现的段，别的配置文件不受影响；`permissions` 等全局段
    缺失时保留磁盘上的原值。返回写入后的有效配置。
    """
    with _lock:
        doc = get_raw_config()
        pid = profile_id if profile_id in doc["profiles"] else doc["active_profile"]

        patch = {key: data[key] for key in PROFILE_KEYS if isinstance(data.get(key), dict)}
        merged = _deep_merge(doc["profiles"][pid], patch)
        if isinstance(data.get("name"), str) and data["name"].strip():
            merged["name"] = data["name"]
        doc["profiles"][pid] = _normalize_profile(merged)

        for key in GLOBAL_KEYS:
            if key in data:
                doc[key] = copy.deepcopy(data[key])

        saved = save_raw_config(doc)
    return _effective_config(saved, pid)


# ── 配置文件管理 ──────────────────────────────────────────────────────────


def _binding_counts(doc: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for kind in BINDING_KINDS:
        for profile_id in doc.get("bindings", {}).get(kind, {}).values():
            counts[profile_id] = counts.get(profile_id, 0) + 1
    return counts


def _profile_summary(doc: dict[str, Any], profile_id: str, counts: dict[str, int]) -> dict[str, Any]:
    profile = doc["profiles"][profile_id]
    model_cfg = profile.get("model", {})
    persona_cfg = profile.get("persona", {})
    return {
        "id": profile_id,
        "name": profile.get("name") or profile_id,
        "model": str(model_cfg.get("model") or ""),
        "base_url": str(model_cfg.get("base_url") or ""),
        "protocol": api_protocol(profile),
        "persona_path": str(persona_cfg.get("skill_path") or ""),
        # 只暴露「有没有配 key」，绝不把 key 本身发给后台。
        "api_key_set": bool(str(model_cfg.get("api_key") or "").strip()),
        "is_active": profile_id == doc["active_profile"],
        "bound_count": counts.get(profile_id, 0),
    }


def list_profiles(doc: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """配置文件摘要列表（默认配置排最前，其余按名称）。"""
    doc = doc if doc is not None else get_raw_config()
    counts = _binding_counts(doc)
    summaries = [_profile_summary(doc, pid, counts) for pid in doc["profiles"]]
    summaries.sort(key=lambda item: (not item["is_active"], item["name"].casefold()))
    return summaries


def list_bindings(doc: dict[str, Any] | None = None) -> dict[str, dict[str, str]]:
    doc = doc if doc is not None else get_raw_config()
    return copy.deepcopy(doc["bindings"])


def create_profile(name: Any, *, copy_from: str | None = None) -> dict[str, Any]:
    """新建配置文件；`copy_from` 给定时复制该配置的内容，否则用默认值。"""
    clean_name = _safe_profile_name(name)
    with _lock:
        doc = get_raw_config()
        if len(doc["profiles"]) >= MAX_PROFILES:
            raise ValueError(f"配置文件数量已达上限（{MAX_PROFILES} 个）。")
        if any((item.get("name") or "") == clean_name for item in doc["profiles"].values()):
            raise ValueError(f"已存在同名配置文件：{clean_name}")
        source = doc["profiles"].get(copy_from) if copy_from else None
        base = copy.deepcopy(source) if isinstance(source, dict) else {}
        base["name"] = clean_name
        profile_id = _unique_profile_id(clean_name, set(doc["profiles"]))
        doc["profiles"][profile_id] = _normalize_profile(base)
        saved = save_raw_config(doc)
    logger.info("[AIAgent] 新建配置文件 -> %s (%s)", clean_name, profile_id)
    return _profile_summary(saved, profile_id, _binding_counts(saved))


def rename_profile(profile_id: str, name: Any) -> dict[str, Any]:
    clean_name = _safe_profile_name(name)
    with _lock:
        doc = get_raw_config()
        if profile_id not in doc["profiles"]:
            raise ValueError(f"配置文件不存在：{profile_id}")
        for other_id, item in doc["profiles"].items():
            if other_id != profile_id and (item.get("name") or "") == clean_name:
                raise ValueError(f"已存在同名配置文件：{clean_name}")
        doc["profiles"][profile_id]["name"] = clean_name
        saved = save_raw_config(doc)
    logger.info("[AIAgent] 配置文件改名 -> %s (%s)", clean_name, profile_id)
    return _profile_summary(saved, profile_id, _binding_counts(saved))


def delete_profile(profile_id: str) -> None:
    """删除配置文件，连带清掉它的会话绑定。"""
    with _lock:
        doc = get_raw_config()
        if profile_id not in doc["profiles"]:
            raise ValueError(f"配置文件不存在：{profile_id}")
        if len(doc["profiles"]) <= 1:
            raise ValueError("至少要保留一个配置文件。")
        if profile_id == doc["active_profile"]:
            raise ValueError("默认配置不能删除，请先把别的配置设为默认。")
        doc["profiles"].pop(profile_id, None)
        # _normalize_bindings 会自动丢掉指向已删除配置的绑定。
        save_raw_config(doc)
    logger.info("[AIAgent] 已删除配置文件 -> %s", profile_id)


def set_active_profile(profile_id: str) -> dict[str, Any]:
    """把某个配置文件设为全局默认。"""
    with _lock:
        doc = get_raw_config()
        if profile_id not in doc["profiles"]:
            raise ValueError(f"配置文件不存在：{profile_id}")
        doc["active_profile"] = profile_id
        saved = save_raw_config(doc)
    logger.info("[AIAgent] 全局默认配置 -> %s", profile_id)
    return _profile_summary(saved, profile_id, _binding_counts(saved))


def set_binding(kind: str, ident: Any, profile_id: str) -> dict[str, dict[str, str]]:
    """把某个群 / 私聊绑定到指定配置文件；`profile_id` 传空串表示解绑。"""
    if kind not in BINDING_KINDS:
        raise ValueError(f"绑定类型无效：{kind}")
    key = str(ident or "").strip()
    if not key:
        raise ValueError("群号 / QQ 号不能为空。")
    if not key.isdigit():
        raise ValueError(f"群号 / QQ 号必须是数字：{key}")
    target = str(profile_id or "").strip()
    with _lock:
        doc = get_raw_config()
        if target and target not in doc["profiles"]:
            raise ValueError(f"配置文件不存在：{target}")
        table = doc["bindings"].setdefault(kind, {})
        if target:
            table[key] = target
        else:
            table.pop(key, None)
        saved = save_raw_config(doc)
    logger.info("[AIAgent] 会话绑定 -> %s:%s = %s", kind, key, target or "(解绑)")
    return saved["bindings"]


def clear_binding(kind: str, ident: Any) -> dict[str, dict[str, str]]:
    return set_binding(kind, ident, "")


def find_profile_id(keyword: Any, doc: dict[str, Any] | None = None) -> str | None:
    """按 ID 或名称（忽略大小写）查配置文件，找不到返回 None。"""
    doc = doc if doc is not None else get_raw_config()
    text = str(keyword or "").strip()
    if not text:
        return None
    if text in doc["profiles"]:
        return text
    folded = text.casefold()
    for profile_id, profile in doc["profiles"].items():
        if profile_id.casefold() == folded:
            return profile_id
        if str(profile.get("name") or "").casefold() == folded:
            return profile_id
    return None


# ── 杂项 ──────────────────────────────────────────────────────────────────


def api_protocol(cfg: dict[str, Any]) -> str:
    """当前配置使用的 API 协议：`responses`（默认）或 `chat_completions`。"""
    api_cfg = cfg.get("api") if isinstance(cfg.get("api"), dict) else {}
    protocol = str(api_cfg.get("protocol") or "").strip().lower()
    if protocol not in {"responses", "chat_completions"}:
        return "responses"
    return protocol


def _safe_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


def safe_persona_max_chars(value: Any) -> int:
    return _safe_int(value, 12000, minimum=1000, maximum=80000)


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default
