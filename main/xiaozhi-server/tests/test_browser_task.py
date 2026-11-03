import subprocess
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not (Path(__file__).resolve().parents[1] / "data" / ".config.yaml").exists():
    _stub = types.ModuleType("config.logger")
    _log = mock.MagicMock()
    _log.bind.return_value = _log
    _stub.setup_logging = lambda: _log
    sys.modules["config.logger"] = _stub

from plugins_func.functions import mac_control as mc  # noqa: E402
from plugins_func.functions import browser_task as bt  # noqa: E402


class FakeProc:
    pid = 4242

    def __init__(self, out="  2798 ms  4 actions  done\nhttps://en.wikipedia.org/wiki/Foo", err="", code=0, hang=False):
        self.out, self.err, self.returncode, self.hang = out, err, code, hang

    def communicate(self, timeout=None):
        if self.hang and timeout:
            raise subprocess.TimeoutExpired("uv", timeout)
        return self.out, self.err


class BrowserTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        (Path(self.tmp.name) / ".env").write_text("x=1")
        self.p = mock.patch.object(bt, "JEV_DIR", Path(self.tmp.name))
        self.p.start()
        mc._pending.clear()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_runs_immediately_without_confirmation(self):
        with mock.patch.object(bt.subprocess, "Popen", return_value=FakeProc()) as popen:
            r = bt.browser_task(None, "https://en.wikipedia.org", "open the article about Godel")
        self.assertEqual(mc._pending, {})
        cmd = popen.call_args[0][0]
        self.assertIn("jev_openrouter_run.py", cmd[5])
        self.assertEqual(cmd[-4:], ["--url", "https://en.wikipedia.org", "--goal", "open the article about Godel"])
        self.assertFalse(popen.call_args[1]["shell"])
        self.assertEqual(popen.call_args[1]["env"]["BH_TELEMETRY"], "0")
        self.assertIn("Done", r.response)
        self.assertIn("en.wikipedia.org", r.response)

    def test_done_without_acting_is_not_success(self):
        out = "  900 ms  0 actions  done\nhttps://gemini.google.com/app"
        with mock.patch.object(bt.subprocess, "Popen", return_value=FakeProc(out=out)):
            r = bt.browser_task(None, "https://gemini.google.com", "ask for food")
        self.assertIn("couldn't finish", r.response)
        self.assertNotIn("Done", r.response)

    def test_reports_what_it_did(self):
        out = "  900 ms  2 actions  done\ndid: Enter a prompt = pizza; Send message\nhttps://gemini.google.com/app/1"
        with mock.patch.object(bt.subprocess, "Popen", return_value=FakeProc(out=out)):
            r = bt.browser_task(None, "https://gemini.google.com", "ask for pizza")
        self.assertIn("Send message", r.response)

    def test_rejects_bad_addresses(self):
        for url in ("http://example.com", "https://127.0.0.1", "https://localhost/x", "https://nas.local", "file:///etc/passwd", "https://192.168.1.1"):
            bt.browser_task(None, url, "open it")
            self.assertEqual(mc._pending, {}, url)

    def test_blocks_risky_goals(self):
        for goal in ("log in to my account", "buy the first result", "send a message", "submit the form", "post a comment"):
            r = bt.browser_task(None, "https://example.com", goal)
            self.assertIn("won't", r.response)
        self.assertEqual(mc._pending, {})

    def test_timeout_and_failure(self):
        with mock.patch.object(bt.subprocess, "Popen", return_value=FakeProc(hang=True)), mock.patch.object(bt.os, "killpg"):
            self.assertIn("ran out of time", bt.browser_task(None, "https://example.com", "open the about page").response)
        with mock.patch.object(bt.subprocess, "Popen", return_value=FakeProc(out="", err="boom", code=1)):
            self.assertIn("didn't work", bt.browser_task(None, "https://example.com", "open the about page").response)

    def test_not_configured(self):
        (Path(self.tmp.name) / ".env").unlink()
        self.assertIn("isn't set up", bt.browser_task(None, "https://example.com", "open it").response)


if __name__ == "__main__":
    unittest.main()
