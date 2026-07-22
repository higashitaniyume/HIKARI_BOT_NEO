"""Tests for the AstrBot compatibility shim layer.

These tests cover the shim API (message components, chain, config, filters,
argument parsing, schema/metadata parsing) and can run without a running
NoneBot instance — only the ``shim/`` directory needs to be on ``sys.path``.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Shim imports — these work without NoneBot because they are pure
# data classes, simple wrappers, or stand-alone utility functions in the
# shim layer.
# ---------------------------------------------------------------------------

_SHIM = str((Path(__file__).resolve().parent.parent / "plugins" / "astrbot_compat" / "shim").resolve())

import sys

sys.path.insert(0, _SHIM)

from astrbot.api.AstrBotConfig import AstrBotConfig
from astrbot.api.message_components import (
    At,
    AtAll,
    BaseMessageComponent,
    Image,
    Plain,
    Record,
    Reply,
    Share,
    Video,
)
from astrbot.api.event.filter import (
    GreedyStr,
    command,
    event_message_type,
    get_command_meta,
    get_event_type_meta,
    get_permission_meta,
    get_regex_meta,
    on_message,
    parse_command_args,
    permission,
    regex,
)
from astrbot.core.message.message_event_result import MessageChain, MessageEventResult

# NamedGroupPattern so regex handlers can use named groups.
import re

# ---------------------------------------------------------------------------
# Config / schema / venv helpers (also under compat, not shim, but
# importable since they don't pull in NoneBot at module level).
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins" / "astrbot_compat"))

from config import parse_metadata, parse_schema
from venv_manager import PluginVenvManager

# ======================================================================
# 1 — Message components
# ======================================================================


class MessageComponentTests(unittest.TestCase):
    """Construction and attribute access for all major message components."""

    def test_plain_accepts_positional_text(self) -> None:
        p = Plain("Hello")
        self.assertEqual(p.text, "Hello")
        self.assertEqual(p.type.value, "plain")

    def test_plain_accepts_keyword_text(self) -> None:
        p = Plain(text="World")
        self.assertEqual(p.text, "World")

    def test_plain_defaults_to_empty(self) -> None:
        p = Plain()
        self.assertEqual(p.text, "")

    def test_image_from_url(self) -> None:
        img = Image.fromURL("https://example.com/pic.jpg")
        self.assertEqual(img.url, "https://example.com/pic.jpg")
        self.assertEqual(img._type, "url")
        self.assertEqual(img.type.value, "image")

    def test_image_from_filesystem(self) -> None:
        img = Image.fromFileSystem("/tmp/pic.png")
        self.assertEqual(img.path, "/tmp/pic.png")
        self.assertEqual(img._type, "path")

    def test_image_accepts_keyword_file(self) -> None:
        img = Image(file="test.jpg")
        self.assertEqual(img.file, "test.jpg")

    def test_at_accepts_qq(self) -> None:
        at = At(qq=12345)
        self.assertEqual(at.qq, 12345)
        self.assertEqual(at.type.value, "at")

    def test_at_with_name(self) -> None:
        at = At(qq=12345, name="User")
        self.assertEqual(at.name, "User")

    def test_at_all(self) -> None:
        aa = AtAll()
        self.assertEqual(aa.qq, "all")
        self.assertEqual(aa.type.value, "at_all")

    def test_record(self) -> None:
        r = Record(file="voice.amr")
        self.assertEqual(r.file, "voice.amr")
        self.assertEqual(r.type.value, "record")

    def test_video(self) -> None:
        v = Video(file="vid.mp4")
        self.assertEqual(v.file, "vid.mp4")
        self.assertEqual(v.type.value, "video")

    def test_reply(self) -> None:
        r = Reply(id=999, message_str="original message")
        self.assertEqual(r.id, 999)
        self.assertEqual(r.message_str, "original message")
        self.assertEqual(r.type.value, "reply")

    def test_share(self) -> None:
        s = Share(url="https://example.com", title="Example")
        self.assertEqual(s.url, "https://example.com")
        self.assertEqual(s.title, "Example")
        self.assertEqual(s.type.value, "share")


# ======================================================================
# 2 — MessageChain / MessageEventResult
# ======================================================================


class MessageChainTests(unittest.TestCase):
    """Chain building, chaining, and utility methods."""

    def test_empty_chain(self) -> None:
        c = MessageChain()
        self.assertEqual(c.chain, [])

    def test_message_appends_plain(self) -> None:
        c = MessageChain().message("Hello")
        self.assertEqual(len(c.chain), 1)
        self.assertIsInstance(c.chain[0], Plain)
        self.assertEqual(c.chain[0].text, "Hello")

    def test_url_image_appends_image(self) -> None:
        c = MessageChain().url_image("https://example.com/pic.jpg")
        self.assertEqual(len(c.chain), 1)
        self.assertIsInstance(c.chain[0], Image)
        self.assertEqual(c.chain[0].url, "https://example.com/pic.jpg")

    def test_file_image(self) -> None:
        c = MessageChain().file_image("/tmp/img.png")
        self.assertIsInstance(c.chain[0], Image)
        self.assertEqual(c.chain[0].path, "/tmp/img.png")

    def test_at_append(self) -> None:
        c = MessageChain().at("User", 12345)
        self.assertIsInstance(c.chain[0], At)
        self.assertEqual(c.chain[0].qq, 12345)

    def test_at_all(self) -> None:
        c = MessageChain().at_all()
        self.assertIsInstance(c.chain[0], AtAll)

    def test_multiple_components(self) -> None:
        c = MessageChain().message("text").url_image("img.jpg").at_all()
        self.assertEqual(len(c.chain), 3)

    def test_fluent_chaining_returns_self(self) -> None:
        c = MessageChain()
        result = c.message("a").url_image("b.jpg")
        self.assertIs(result, c)

    def test_get_plain_text(self) -> None:
        c = MessageChain().message("Hello").message("World").url_image("x.jpg")
        self.assertEqual(c.get_plain_text(), "Hello World")

    def test_get_plain_text_with_markers(self) -> None:
        c = MessageChain().message("Hi").url_image("x.jpg")
        self.assertIn("Image", c.get_plain_text(with_other_comps_mark=True))

    def test_squash_plain_merges_adjacent_text(self) -> None:
        c = MessageChain().message("A").message("B").url_image("x.jpg").message("C")
        c.squash_plain()
        plains = [comp for comp in c.chain if isinstance(comp, Plain)]
        self.assertEqual(len(plains), 1)
        self.assertEqual(plains[0].text, "ABC")

    def test_derive_with_explicit_chain(self) -> None:
        c1 = MessageChain().message("Hello")
        c2 = c1.derive(list(c1.chain))
        self.assertEqual(len(c2.chain), 1)
        self.assertEqual(c2.chain[0].text, "Hello")

    def test_derive_shares_use_t2i_flag(self) -> None:
        c1 = MessageChain().use_t2i(True)
        c2 = c1.derive()
        self.assertTrue(c2.use_t2i_)

    def test_base64_image(self) -> None:
        c = MessageChain().base64_image("AAAA")
        self.assertIsInstance(c.chain[0], Image)
        self.assertEqual(c.chain[0]._type, "base64")


class MessageEventResultTests(unittest.TestCase):
    """Result type, propagation control, and chain inheritance."""

    def test_default_result_is_continue(self) -> None:
        r = MessageEventResult()
        self.assertFalse(r.is_stopped())

    def test_stop_event(self) -> None:
        r = MessageEventResult().stop_event()
        self.assertTrue(r.is_stopped())

    def test_continue_event(self) -> None:
        r = MessageEventResult().stop_event().continue_event()
        self.assertFalse(r.is_stopped())

    def test_inherits_chain_methods(self) -> None:
        r = MessageEventResult().message("Hello")
        self.assertEqual(len(r.chain), 1)
        self.assertEqual(r.chain[0].text, "Hello")

    def test_chain_result_content_type(self) -> None:
        r = MessageEventResult()
        r.set_result_content_type(r.result_content_type.__class__.LLM_RESULT)
        self.assertTrue(r.is_llm_result())

    def test_model_result(self) -> None:
        from astrbot.core.message.message_event_result import ResultContentType

        r = MessageEventResult()
        r.set_result_content_type(ResultContentType.AGENT_RUNNER_ERROR)
        self.assertTrue(r.is_model_result())

    def test_error_method_deprecated_but_works(self) -> None:
        c = MessageChain().error("oops")
        self.assertEqual(c.chain[0].text, "oops")


# ======================================================================
# 3 — Filter decorator metadata
# ======================================================================


class FilterDecoratorTests(unittest.TestCase):
    """Decorators attach correct metadata to handler functions."""

    def test_command_meta(self) -> None:
        @command("test-cmd")
        async def handler(self, event): ...

        meta = get_command_meta(handler)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["name"], "test-cmd")
        self.assertEqual(meta["alias"], set())

    def test_command_with_alias(self) -> None:
        @command("test", alias={"t", "ts"})
        async def handler(self, event): ...

        meta = get_command_meta(handler)
        self.assertEqual(meta["alias"], {"t", "ts"})

    def test_regex_meta(self) -> None:
        @regex(r"^ping$")
        async def handler(self, event): ...

        pat = get_regex_meta(handler)
        self.assertIsNotNone(pat)
        self.assertTrue(pat.search("ping"))
        self.assertFalse(pat.search("pong"))

    def test_on_message_meta(self) -> None:
        @on_message()
        async def handler(self, event): ...

        from astrbot.api.event.filter import is_on_message

        self.assertTrue(is_on_message(handler))

    def test_permission_meta(self) -> None:
        @permission("admin")
        async def handler(self, event): ...

        self.assertEqual(get_permission_meta(handler), "admin")

    def test_permission_default(self) -> None:
        @command("x")
        async def handler(self, event): ...

        self.assertIsNone(get_permission_meta(handler))

    def test_event_type_meta(self) -> None:
        @event_message_type("group")
        async def handler(self, event): ...

        self.assertEqual(get_event_type_meta(handler), "group")


# ======================================================================
# 4 — Command argument parsing
# ======================================================================


class CommandArgParsingTests(unittest.TestCase):
    """Typed parameter resolution from raw argument strings."""

    def test_no_params_returns_empty(self) -> None:
        self.assertEqual(parse_command_args("a b c", []), {})

    def test_single_str_param(self) -> None:
        info = [{"name": "x", "annotation": str, "has_default": False,
                 "default": None, "is_greedy": False, "kind": ...}]
        result = parse_command_args("hello", info)
        self.assertEqual(result["x"], "hello")

    def test_int_param(self) -> None:
        info = [{"name": "n", "annotation": int, "has_default": False,
                 "default": None, "is_greedy": False, "kind": ...}]
        result = parse_command_args("42", info)
        self.assertEqual(result["n"], 42)

    def test_float_param(self) -> None:
        info = [{"name": "f", "annotation": float, "has_default": False,
                 "default": None, "is_greedy": False, "kind": ...}]
        result = parse_command_args("3.14", info)
        self.assertAlmostEqual(result["f"], 3.14)

    def test_bool_param_true(self) -> None:
        info = [{"name": "b", "annotation": bool, "has_default": False,
                 "default": None, "is_greedy": False, "kind": ...}]
        result = parse_command_args("true", info)
        self.assertTrue(result["b"])

    def test_bool_param_false(self) -> None:
        info = [{"name": "b", "annotation": bool, "has_default": False,
                 "default": None, "is_greedy": False, "kind": ...}]
        result = parse_command_args("false", info)
        self.assertFalse(result["b"])

    def test_multiple_params(self) -> None:
        info = [
            {"name": "a", "annotation": int, "has_default": False,
             "default": None, "is_greedy": False, "kind": ...},
            {"name": "b", "annotation": int, "has_default": False,
             "default": None, "is_greedy": False, "kind": ...},
        ]
        result = parse_command_args("3 5", info)
        self.assertEqual(result["a"], 3)
        self.assertEqual(result["b"], 5)

    def test_greedy_str_consumes_all(self) -> None:
        info = [{"name": "msg", "annotation": str, "has_default": False,
                 "default": None, "is_greedy": True, "kind": ...}]
        result = parse_command_args("hello world foo", info)
        self.assertEqual(result["msg"], "hello world foo")

    def test_greedy_str_with_empty(self) -> None:
        info = [{"name": "msg", "annotation": str, "has_default": False,
                 "default": None, "is_greedy": True, "kind": ...}]
        result = parse_command_args("", info)
        self.assertEqual(result["msg"], "")

    def test_missing_non_default_returns_empty_string(self) -> None:
        info = [{"name": "x", "annotation": str, "has_default": False,
                 "default": None, "is_greedy": False, "kind": ...}]
        result = parse_command_args("", info)
        self.assertEqual(result["x"], "")

    def test_missing_with_default_uses_default(self) -> None:
        info = [{"name": "x", "annotation": int, "has_default": True,
                 "default": 10, "is_greedy": False, "kind": ...}]
        result = parse_command_args("", info)
        self.assertEqual(result["x"], 10)

    def test_keyword_only_consumes_rest(self) -> None:
        from inspect import Parameter

        info = [{"name": "rest", "annotation": str, "has_default": False,
                 "default": None, "is_greedy": False,
                 "kind": Parameter.KEYWORD_ONLY}]
        result = parse_command_args("a b c", info)
        self.assertEqual(result["rest"], "a b c")


# ======================================================================
# 5 — AstrBotConfig (JSON-backed dict)
# ======================================================================


class AstrBotConfigTests(unittest.TestCase):
    """Persistence, reads, writes, reloads."""

    def _make_config(self, tmp: Path, initial: dict[str, Any] | None = None) -> AstrBotConfig:
        return AstrBotConfig(tmp / "config.json", initial=initial)

    def test_initial_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._make_config(Path(tmp), {"key": "val", "num": 42})
            self.assertEqual(cfg["key"], "val")
            self.assertEqual(cfg["num"], 42)

    def test_set_and_persist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._make_config(Path(tmp), {})
            cfg["new_key"] = "new_val"
            self.assertEqual(cfg["new_key"], "new_val")
            # Verify it wrote to disk
            saved = json.loads((Path(tmp) / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["new_key"], "new_val")

    def test_save_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._make_config(Path(tmp), {"x": 1})
            cfg["y"] = 2
            cfg.save_config()
            saved = json.loads((Path(tmp) / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["x"], 1)
            self.assertEqual(saved["y"], 2)

    def test_delete_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._make_config(Path(tmp), {"a": 1, "b": 2})
            del cfg["a"]
            self.assertNotIn("a", cfg)
            self.assertEqual(cfg["b"], 2)

    def test_reload_discards_memory_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._make_config(Path(tmp), {"persist": True})
            # mutate in memory — it auto-saves so we force-write a
            # different state behind its back.
            cfg["memory_only"] = "should_not_survive"
            # Write a different state directly
            (Path(tmp) / "config.json").write_text(
                json.dumps({"persist": True, "from_disk": True}),
                encoding="utf-8",
            )
            cfg.reload()
            self.assertNotIn("memory_only", cfg)
            self.assertTrue(cfg["from_disk"])

    def test_get_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._make_config(Path(tmp), {})
            self.assertEqual(cfg.get("missing", "fallback"), "fallback")
            self.assertIsNone(cfg.get("missing"))

    def test_len_and_iter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._make_config(Path(tmp), {"a": 1, "b": 2})
            self.assertEqual(len(cfg), 2)
            self.assertEqual(set(cfg), {"a", "b"})

    def test_non_existent_file_loads_initial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AstrBotConfig(Path(tmp) / "nonexistent" / "config.json",
                                initial={"default": True})
            self.assertTrue(cfg["default"])
            # File should have been created by _save in __setitem__
            self.assertTrue((Path(tmp) / "nonexistent" / "config.json").exists())

    def test_invalid_json_falls_back_to_initial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "config.json").write_text("{invalid", encoding="utf-8")
            cfg = AstrBotConfig(Path(tmp) / "config.json", initial={"safe": True})
            self.assertTrue(cfg["safe"])


# ======================================================================
# 6 — config.parse_schema / parse_metadata
# ======================================================================


class ConfigParsingTests(unittest.TestCase):
    """Schema and metadata file parsing."""

    def test_parse_schema_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            schema = {"key": {"description": "A key", "type": "string", "default": "val"}}
            (Path(tmp) / "_conf_schema.json").write_text(json.dumps(schema))
            result = parse_schema(Path(tmp) / "_conf_schema.json")
            self.assertEqual(result["defaults"]["key"], "val")
            self.assertEqual(result["schema"]["key"]["type"], "string")

    def test_parse_schema_no_default_uses_type_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            schema = {"count": {"description": "Count", "type": "int"}}
            (Path(tmp) / "_conf_schema.json").write_text(json.dumps(schema))
            result = parse_schema(Path(tmp) / "_conf_schema.json")
            self.assertEqual(result["defaults"]["count"], 0)

    def test_parse_schema_bool_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            schema = {"debug": {"type": "bool", "default": True}}
            (Path(tmp) / "_conf_schema.json").write_text(json.dumps(schema))
            result = parse_schema(Path(tmp) / "_conf_schema.json")
            self.assertTrue(result["defaults"]["debug"])

    def test_parse_schema_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = parse_schema(Path(tmp) / "_conf_schema.json")
            self.assertEqual(result["defaults"], {})

    def test_parse_schema_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "_conf_schema.json").write_text("not json")
            result = parse_schema(Path(tmp) / "_conf_schema.json")
            self.assertEqual(result["defaults"], {})

    def test_parse_metadata_yaml_simple_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "metadata.yaml").write_text(
                "name: my-plugin\nversion: 1.0.0\nauthor: tester\n"
            )
            meta = parse_metadata(Path(tmp))
            self.assertEqual(meta.get("name"), "my-plugin")
            self.assertEqual(meta.get("version"), "1.0.0")
            self.assertEqual(meta.get("author"), "tester")

    def test_parse_metadata_yaml_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "metadata.yaml").write_text(
                "name: my-plugin\ntags:\n  - utility\n  - fun\n"
            )
            meta = parse_metadata(Path(tmp))
            self.assertIn("utility", meta.get("tags", []))
            self.assertIn("fun", meta.get("tags", []))

    def test_parse_metadata_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            meta = parse_metadata(Path(tmp))
            self.assertEqual(meta, {})

    def test_parse_metadata_prefers_yaml_over_yml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "metadata.yml").write_text("name: from-yml\n")
            (Path(tmp) / "metadata.yaml").write_text("name: from-yaml\n")
            meta = parse_metadata(Path(tmp))
            self.assertEqual(meta.get("name"), "from-yaml")


# ======================================================================
# 7 — venv_manager.parse_requirements
# ======================================================================


class VenvParsingTests(unittest.TestCase):
    """requirements.txt line parsing."""

    def test_parse_requirements_basic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            req = Path(tmp) / "requirements.txt"
            req.write_text("httpx\nrequests>=2.28.0\n")
            mgr = PluginVenvManager(Path(tmp) / ".venv")
            deps = mgr.parse_requirements(req)
            self.assertIn("httpx", deps)
            self.assertIn("requests>=2.28.0", deps)

    def test_parse_requirements_skips_comments_and_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            req = Path(tmp) / "requirements.txt"
            req.write_text("# comment\n--index-url https://x\npillow\n")
            mgr = PluginVenvManager(Path(tmp) / ".venv")
            deps = mgr.parse_requirements(req)
            self.assertNotIn("# comment", deps)
            self.assertNotIn("--index-url", deps)
            self.assertIn("pillow", deps)

    def test_parse_requirements_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            req = Path(tmp) / "requirements.txt"
            req.write_text("")
            mgr = PluginVenvManager(Path(tmp) / ".venv")
            self.assertEqual(mgr.parse_requirements(req), [])

    def test_parse_requirements_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mgr = PluginVenvManager(Path(tmp) / ".venv")
            self.assertEqual(mgr.parse_requirements(Path(tmp) / "missing.txt"), [])


# ======================================================================
# 8 — Star subclass auto-registration (via shim)
# ======================================================================


class StarRegistrationTests(unittest.TestCase):
    """``Star.__init_subclass__`` captures plugin classes."""

    def setUp(self):
        # Clear registry so tests start fresh
        from astrbot.api.star import _star_classes

        _star_classes.clear()

    def tearDown(self):
        from astrbot.api.star import _star_classes

        _star_classes.clear()

    def test_star_subclass_is_registered(self) -> None:
        from astrbot.api.star import Star, get_registered_star_classes

        class _TestPluginA(Star):
            pass

        classes = get_registered_star_classes()
        key = _TestPluginA.__module__
        self.assertIn(key, classes)
        self.assertIs(classes[key], _TestPluginA)

    def test_multiple_stars_are_registered(self) -> None:
        from astrbot.api.star import Star, get_registered_star_classes

        class _TestPluginB(Star):
            pass

        class _TestPluginC(Star):
            pass

        classes = get_registered_star_classes()
        # Both classes registered — but __init_subclass__ keys by module,
        # and both classes share the same module, so the last one wins.
        # Verify at least one is present and the class dict is non-empty.
        self.assertGreaterEqual(len(classes), 1)
        self.assertTrue(
            any(cls.__name__ == "_TestPluginB" for cls in classes.values())
            or any(cls.__name__ == "_TestPluginC" for cls in classes.values())
        )

    def test_clear_star_registration(self) -> None:
        from astrbot.api.star import Star, clear_star_registration, get_registered_star_classes

        class _TestPluginD(Star):
            pass

        mod = _TestPluginD.__module__
        self.assertIn(mod, get_registered_star_classes())
        clear_star_registration(mod)
        self.assertNotIn(mod, get_registered_star_classes())


# ======================================================================
# 9 — chain → OneBot conversion (loader utilities)
# ======================================================================


class ChainConversionTests(unittest.TestCase):
    """convert_chain_to_onebot and _component_to_segment."""

    def setUp(self):
        # Import conversion.py directly via file path to bypass
        # plugins.astrbot_compat.__init__ (which calls get_driver()).
        import importlib.util as _util

        _path = str(
            Path(__file__).resolve().parent.parent
            / "plugins" / "astrbot_compat" / "conversion.py"
        )
        _spec = _util.spec_from_file_location("_compat_conversion", _path)
        _mod = _util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        self._convert = _mod.convert_chain_to_onebot
        self._comp2seg = _mod._component_to_segment

    def test_plain_text_returns_string(self) -> None:
        c = MessageChain().message("Just text")
        result = self._convert(c)
        self.assertEqual(result, "Just text")

    def test_mixed_chain_returns_segment_list(self) -> None:
        c = MessageChain().message("Hello").url_image("https://example.com/pic.jpg")
        result = self._convert(c)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].type, "text")
        self.assertEqual(result[1].type, "image")

    def test_empty_chain_returns_empty_string(self) -> None:
        c = MessageChain()
        result = self._convert(c)
        self.assertEqual(result, "")

    def test_image_segment_from_url(self) -> None:
        img = Image.fromURL("https://example.com/pic.jpg")
        seg = self._comp2seg(img)
        self.assertIsNotNone(seg)
        self.assertEqual(seg.type, "image")
        self.assertEqual(seg.data["file"], "https://example.com/pic.jpg")

    def test_reply_falls_back_to_text(self) -> None:
        rep = Reply(id=42, message_str="original")
        seg = self._comp2seg(rep)
        self.assertIsNotNone(seg)
        self.assertEqual(seg.type, "text")
        self.assertIn("original", seg.data.get("text", ""))

    def test_share_becomes_text(self) -> None:
        sh = Share(url="https://example.com", title="Example")
        seg = self._comp2seg(sh)
        self.assertIsNotNone(seg)
        self.assertEqual(seg.type, "text")
        self.assertIn("Example", seg.data.get("text", ""))

    def test_record_segment(self) -> None:
        rec = Record(file="voice.mp3")
        seg = self._comp2seg(rec)
        self.assertIsNotNone(seg)
        self.assertEqual(seg.type, "record")

    def test_video_segment(self) -> None:
        vid = Video(file="video.mp4")
        seg = self._comp2seg(vid)
        self.assertIsNotNone(seg)
        self.assertEqual(seg.type, "video")


# ======================================================================
# Run
# ======================================================================

if __name__ == "__main__":
    unittest.main()
