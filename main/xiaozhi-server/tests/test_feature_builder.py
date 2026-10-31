import json
import subprocess
import sys
import time
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
from plugins_func.functions import feature_builder as fb  # noqa: E402


class FakeProc:
    pid = 99999

    def __init__(self, out="Built it. Tests pass.", code=0, hang=False):
        self.out, self.returncode, self.hang = out, code, hang

    def communicate(self, timeout=None):
        if self.hang:
            raise subprocess.TimeoutExpired("claude", timeout)
        return self.out, ""

    def kill(self):
        pass


def git_ok(*a, **k):
    return subprocess.CompletedProcess([], 0, stdout="", stderr="")


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / "repo").mkdir()
        self.patches = [
            mock.patch.object(fb, "BUILDS_DIR", d / "builds"),
            mock.patch.object(fb, "JOBS", d / "builds" / "jobs.json"),
            mock.patch.object(fb, "VIBI", str(d / "no-vibi")),
            mock.patch.dict(mc.PROJECTS, {"server": str(d / "repo"), "voicebox": str(d / "repo")}),
        ]
        for p in self.patches:
            p.start()
        mc._pending.clear()
        fb._procs.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def start(self, proc, request="add a timer tool", **kw):
        fb.build_feature(None, request, "server", **kw)
        with mock.patch.object(fb.mc, "_run", side_effect=git_ok) as run, \
                mock.patch.object(fb.subprocess, "Popen", return_value=proc) as popen, \
                mock.patch.object(fb.threading, "Thread") as th:
            r = mc.confirm_action(None)
        return r, run, popen, th

    def test_nothing_starts_without_confirmation(self):
        with mock.patch.object(fb.subprocess, "Popen") as popen:
            r = fb.build_feature(None, "add a timer tool", "server")
        self.assertFalse(popen.called)
        self.assertIn("add a timer tool", r.response)  # details read back
        self.assertIn("no pushing", r.response)

    def test_rejects_trading_and_empty(self):
        self.assertIn("Which one", fb.build_feature(None, "x", "trading").response)
        self.assertIn("What should I build", fb.build_feature(None, " ", "server").response)
        self.assertEqual(mc._pending, {})

    def test_confirm_starts_worktree_and_sandboxed_claude(self):
        r, run, popen, th = self.start(FakeProc())
        self.assertIn("Started", r.response)
        wt = run.call_args[0][0]
        self.assertEqual(wt[:4], ["git", "-C", mc.PROJECTS["server"], "worktree"])
        self.assertTrue(wt[wt.index("-b") + 1].startswith("voice/"))
        cmd = popen.call_args[0][0]
        denied = cmd[cmd.index("--disallowedTools") + 1:cmd.index("--permission-mode")]
        for rule in ("Bash(git push:*)", "Bash(rm:*)", "Bash(git merge:*)"):
            self.assertIn(rule, denied)
        self.assertFalse(popen.call_args[1]["shell"])
        self.assertTrue(popen.call_args[1]["cwd"].startswith(str(fb.BUILDS_DIR)))
        self.assertTrue(th.return_value.start.called)
        self.assertEqual(fb._latest()["status"], "running")

    def test_one_build_at_a_time(self):
        self.start(FakeProc())
        fb.build_feature(None, "another", "server")
        r = mc.confirm_action(None)
        self.assertIn("already running", r.response)

    def test_watch_records_result_and_status_followups(self):
        self.start(FakeProc())
        job = fb._latest()
        fb._watch(job["id"], FakeProc())
        self.assertEqual(fb._latest()["status"], "done")
        r = fb.build_status(None, "result")
        self.assertIn("Built it", r.response)
        self.assertIn(job["branch"], r.response)

    def test_timeout_and_failure(self):
        self.start(FakeProc())
        jid = fb._latest()["id"]
        with mock.patch.object(fb, "_kill") as kill:
            fb._watch(jid, FakeProc(hang=True))
        self.assertTrue(kill.called)
        self.assertEqual(fb._latest()["status"], "timed_out")
        self.assertIn("timed out", fb.build_status(None).response)

    def test_cancel(self):
        self.start(FakeProc())
        jid = fb._latest()["id"]
        fb._procs[jid] = FakeProc()
        with mock.patch.object(fb, "_kill") as kill:
            r = fb.build_status(None, "cancel")
        self.assertTrue(kill.called)
        self.assertEqual(fb._latest()["status"], "cancelled")
        self.assertIn("stopped", r.response)

    def test_followup_reuses_branch(self):
        self.start(FakeProc())
        first = fb._latest()
        fb._watch(first["id"], FakeProc())
        Path(first["path"]).mkdir(parents=True)  # git is mocked, so create the worktree dir
        time.sleep(1.1)  # job ids are timestamps
        r, run, popen, th = self.start(FakeProc(), "also add a cancel command", followup=True)
        self.assertIn("Started", r.response)
        self.assertFalse(run.called)  # no new worktree
        self.assertEqual(popen.call_args[1]["cwd"], first["path"])
        self.assertEqual(fb._latest()["branch"], first["branch"])

    def test_followup_without_previous(self):
        fb.build_feature(None, "more", "server", followup=True)
        self.assertIn("no earlier", mc.confirm_action(None).response)

    def test_status_when_server_restarted(self):
        self.start(FakeProc())
        fb._procs.clear()
        self.assertIn("restarted", fb.build_status(None).response)


if __name__ == "__main__":
    unittest.main()
