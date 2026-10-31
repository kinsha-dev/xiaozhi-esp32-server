import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os  # noqa: E402
import types  # noqa: E402

# The server's logger requires data/.config.yaml (absent in a fresh checkout and not something
# tests should read), so fall back to a stub logger when it is missing.
if not (Path(__file__).resolve().parents[1] / "data" / ".config.yaml").exists():
    _stub = types.ModuleType("config.logger")
    _log = mock.MagicMock()
    _log.bind.return_value = _log
    _stub.setup_logging = lambda: _log
    sys.modules["config.logger"] = _stub

from plugins_func.functions import mac_control as mc  # noqa: E402


def ok(out="Answer one. Answer two. Answer three."):
    return subprocess.CompletedProcess([], 0, stdout=out, stderr="")


class MacControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        d = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(mc, "DATA_DIR", d),
            mock.patch.object(mc, "ALLOWLIST", d / "control_allowlist.json"),
            mock.patch.object(mc, "TASKS_DIR", d / "pi_tasks"),
            mock.patch.object(mc, "PI_RUNS", d / "pi_runs.json"),
            mock.patch.object(mc, "CLAUDE_TASKS_DIR", d / "claude_tasks"),
        ]
        for p in self.patches:
            p.start()
        mc._pending.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_allowlist_created(self):
        data = mc._load_allowlist()
        self.assertIn("apps", data)
        self.assertEqual(json.loads(mc.ALLOWLIST.read_text())["shortcuts"], [])

    def test_ask_pi_read_mode(self):
        with mock.patch.object(mc.subprocess, "run", return_value=ok()) as run:
            r = mc.ask_pi(None, "what does the signal checker do?")
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[:3], [mc.PI_NODE, mc.PI_WRAPPER, "read"])
        self.assertTrue(cmd[3].endswith("Task: what does the signal checker do?"))
        self.assertIn("read aloud", cmd[3])  # spoken-style instruction is prepended
        self.assertFalse(run.call_args[1].get("shell"))
        self.assertEqual(run.call_args[1]["timeout"], mc.PI_TIMEOUT)
        self.assertEqual(run.call_args[1]["cwd"], mc.PI_CWD)
        self.assertEqual(len(list(mc.TASKS_DIR.glob("*.md"))), 1)
        self.assertIn("Answer one. Answer two.", r.response)
        self.assertNotIn("Answer three", r.response)  # spoken text limited to two sentences
        self.assertIn("Answer one. Answer two.", mc._two_sentences("Answer one. Answer two. Answer three."))

    def test_risky_task_needs_confirmation(self):
        with mock.patch.object(mc.subprocess, "run") as run:
            r = mc.ask_pi(None, "fix the bug in signals.py")
            self.assertFalse(run.called)
        self.assertIn("confirm", r.response)
        self.assertEqual(len(mc._pending), 1)
        token = next(iter(mc._pending))
        self.assertNotIn(token, r.result)  # token only spoken
        with mock.patch.object(mc.subprocess, "run", return_value=ok()) as run:
            mc.confirm_action(None, " ".join(token))
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[2], "edit")  # confirmed run uses pi's edit mode

    def test_daily_cap_blocks_pi(self):
        with mock.patch.object(mc, "PI_DAILY_CAP", 1), mock.patch.object(mc.subprocess, "run", return_value=ok()) as run:
            mc.ask_pi(None, "what is the status")
            r = mc.ask_pi(None, "and the status again")
        self.assertEqual(run.call_count, 1)
        self.assertIn("stop for now", r.response)

    def test_ask_claude_still_available(self):
        """'ask Claude' keeps using the Claude CLI (Bash allowed, deletion denied); 'ask pi' uses pi."""
        with mock.patch.object(mc.subprocess, "run", return_value=ok()) as run:
            r = mc.ask_claude(None, "what does the signal checker do?")
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[:3], ["claude", "-p", "what does the signal checker do?"])
        allowed = cmd[cmd.index("--allowedTools") + 1:cmd.index("--disallowedTools")]
        denied = cmd[cmd.index("--disallowedTools") + 1:cmd.index("--permission-mode")]
        self.assertEqual(allowed, ["Read", "Grep", "Glob", "Bash"])
        for rule in ("Bash(rm:*)", "Bash(sudo:*)", "Edit", "Write"):
            self.assertIn(rule, denied)
        self.assertFalse(run.call_args[1].get("shell"))
        self.assertEqual(run.call_args[1]["cwd"], mc.CLAUDE_CWD)
        self.assertEqual(len(list(mc.CLAUDE_TASKS_DIR.glob("*.md"))), 1)
        self.assertIn("Answer one. Answer two.", r.response)

    def test_ask_claude_risky_needs_confirmation_then_edits(self):
        with mock.patch.object(mc.subprocess, "run") as run:
            mc.ask_claude(None, "fix the bug in signals.py")
            self.assertFalse(run.called)
        token = next(iter(mc._pending))
        with mock.patch.object(mc.subprocess, "run", return_value=ok()) as run:
            mc.confirm_action(None, " ".join(token))
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[0], "claude")
        self.assertIn("Edit", cmd[cmd.index("--allowedTools") + 1:cmd.index("--disallowedTools")])

    def test_both_tools_registered(self):
        self.assertEqual(mc.ask_pi_desc["function"]["name"], "ask_pi")
        self.assertEqual(mc.ask_claude_desc["function"]["name"], "ask_claude")

    def test_token_single_use_and_expiry(self):
        mc.ask_pi(None, "x", allow_changes=True)
        token = next(iter(mc._pending))
        with mock.patch.object(mc.subprocess, "run", return_value=ok()):
            mc.confirm_action(None, token)
            again = mc.confirm_action(None, token)
        self.assertIn("wrong or has expired", again.response)
        mc.ask_pi(None, "y", allow_changes=True)
        token = next(iter(mc._pending))
        with mock.patch.object(mc, "_now", return_value=mc._now() + 61), mock.patch.object(mc.subprocess, "run") as run:
            r = mc.confirm_action(None, token)
            self.assertFalse(run.called)
        self.assertIn("expired", r.response)

    def test_volume(self):
        with mock.patch.object(mc.subprocess, "run", return_value=ok("")) as run:
            mc.mac_control(None, "set_volume", "40")
            self.assertEqual(run.call_args[0][0], ["osascript", "-e", "set volume output volume 40"])
            for bad in ("101", "-1", "loud", '1"; do shell script "x'):
                run.reset_mock()
                mc.mac_control(None, "set_volume", bad)
                self.assertFalse(run.called, bad)

    def test_open_app_allowlist(self):
        mc._load_allowlist()
        with mock.patch.object(mc.subprocess, "run", return_value=ok("")) as run:
            mc.mac_control(None, "open_app", "safari")
            self.assertEqual(run.call_args[0][0], ["open", "-a", "Safari"])
            run.reset_mock()
            r = mc.mac_control(None, "open_app", "Terminal; rm -rf /")
            self.assertFalse(run.called)
            self.assertIn("allowed", r.response)

    def test_lock_and_shortcut_need_confirm(self):
        mc.ALLOWLIST.parent.mkdir(parents=True, exist_ok=True)
        mc.ALLOWLIST.write_text(json.dumps({"apps": [], "shortcuts": ["Good Night"]}))
        with mock.patch.object(mc.subprocess, "run", return_value=ok("")) as run:
            mc.mac_control(None, "lock_screen")
            self.assertFalse(run.called)
            token = next(iter(mc._pending))
            mc.confirm_action(None, token)
            self.assertEqual(run.call_args[0][0], ["pmset", "displaysleepnow"])
            run.reset_mock()
            r = mc.mac_control(None, "run_shortcut", "Format Disk")
            self.assertFalse(run.called)
            self.assertFalse(mc._pending)
            mc.mac_control(None, "run_shortcut", "good night")
            self.assertFalse(run.called)
            mc.confirm_action(None, next(iter(mc._pending)))
            self.assertEqual(run.call_args[0][0], ["shortcuts", "run", "Good Night"])

    def test_unknown_action(self):
        with mock.patch.object(mc.subprocess, "run") as run:
            mc.mac_control(None, "shell", "ls")
            self.assertFalse(run.called)

    def test_no_shell_true_in_source(self):
        src = Path(mc.__file__).read_text()
        self.assertNotIn("shell=True", src)


if __name__ == "__main__":
    unittest.main()
