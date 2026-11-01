import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not (Path(__file__).resolve().parents[1] / "data" / ".config.yaml").exists():
    _stub = types.ModuleType("config.logger")
    _log = mock.MagicMock()
    _log.bind.return_value = _log
    _stub.setup_logging = lambda: _log
    sys.modules["config.logger"] = _stub

from plugins_func.functions import web as w  # noqa: E402


class WebTests(unittest.TestCase):
    def route(self, mode, **kw):
        calls = {}

        def fake(module, name):
            return lambda conn, *a: calls.update(target=(module, name), args=a) or "result"

        with mock.patch.object(w, "_fn", side_effect=fake):
            out = w.web(None, mode, **kw)
        return out, calls

    def test_each_mode_routes_to_its_tool(self):
        cases = [
            ("answer", {"query": "weather today"}, ("search_web", "search_web"), ("weather today",)),
            ("read", {"url": "https://a.com/x", "query": "price"}, ("browser", "read_page"), ("https://a.com/x", "price")),
            ("open", {"query": "esp32 docs"}, ("browser", "open_in_browser"), ("esp32 docs",)),
            ("github", {"query": "esp32", "kind": "issues", "owner": "me", "open_top": True}, ("github_search", "github_search"), ("esp32", "issues", "me", True)),
            ("browser", {"url": "https://en.wikipedia.org", "query": "open Godel"}, ("browser_task", "browser_task"), ("https://en.wikipedia.org", "open Godel")),
        ]
        for mode, kw, target, args in cases:
            out, calls = self.route(mode, **kw)
            self.assertEqual(out, "result", mode)
            self.assertEqual(calls["target"], target, mode)
            self.assertEqual(calls["args"], args, mode)

    def test_bad_mode_empty_and_missing_module(self):
        self.assertIn("Which one", w.web(None, "shell", query="ls").response)
        out, calls = self.route("answer")
        self.assertFalse(calls)  # empty request never reaches a tool
        with mock.patch.object(w, "_fn", return_value=None):
            self.assertIn("isn't installed", w.web(None, "answer", query="x").response)

    def test_single_description_covers_all_modes(self):
        d = w.web_desc["function"]
        self.assertEqual(d["parameters"]["properties"]["mode"]["enum"], list(w.MODES))
        for m in w.MODES:
            self.assertIn(m, d["description"])


if __name__ == "__main__":
    unittest.main()
