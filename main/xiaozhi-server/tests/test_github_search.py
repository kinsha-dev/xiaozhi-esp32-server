import json
import subprocess
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

from plugins_func.functions import github_search as gs  # noqa: E402


def gh(data, code=0, err=""):
    return subprocess.CompletedProcess([], code, stdout=json.dumps(data), stderr=err)


REPOS = [{"fullName": f"a/r{i}", "description": f"repo number {i}", "url": f"https://github.com/a/r{i}"} for i in range(5)]


class GithubSearchTests(unittest.TestCase):
    def test_repo_search_speaks_top_three(self):
        with mock.patch.object(gs.mc, "_run", return_value=gh(REPOS)) as run:
            r = gs.github_search(None, "voice assistant")
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[1:3], ["search", "repos"])
        self.assertEqual(cmd[-2:], ["--", "voice assistant"])
        self.assertIn("a/r0", r.response)
        self.assertIn("a/r2", r.response)
        self.assertNotIn("a/r3", r.response)
        self.assertIn("I found 5", r.response)

    def test_query_cannot_become_a_flag(self):
        with mock.patch.object(gs.mc, "_run", return_value=gh([])) as run:
            gs.github_search(None, "--web x")
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[-2], "--")
        self.assertEqual(cmd[-1], "--web x")

    def test_kinds_and_owner(self):
        issue = [{"title": "Crash on start", "repository": {"nameWithOwner": "a/b"}, "state": "open", "url": "https://github.com/a/b/issues/1"}]
        with mock.patch.object(gs.mc, "_run", return_value=gh(issue)) as run:
            r = gs.github_search(None, "crash", kind="issues", owner="kinsha-dev")
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[2], "issues")
        self.assertEqual(cmd[cmd.index("--owner") + 1], "kinsha-dev")
        self.assertIn("Crash on start in a/b", r.response)
        with mock.patch.object(gs.mc, "_run") as run:
            r = gs.github_search(None, "x", owner="bad name; rm")
        self.assertFalse(run.called)

    def test_open_top_only_github_https(self):
        with mock.patch.object(gs.mc, "_run", return_value=gh(REPOS)) as run:
            run.side_effect = [gh(REPOS), subprocess.CompletedProcess([], 0, "", "")]
            r = gs.github_search(None, "x", open_top=True)
        self.assertEqual(run.call_args[0][0], ["open", "-a", "Google Chrome", "https://github.com/a/r0"])
        self.assertIn("Opening", r.response)
        bad = [{"fullName": "x/y", "url": "https://evil.example/x"}]
        with mock.patch.object(gs.mc, "_run", side_effect=[gh(bad)]) as run:
            r = gs.github_search(None, "x", open_top=True)
        self.assertEqual(run.call_count, 1)
        self.assertIn("couldn't open", r.response)

    def test_errors_and_empty(self):
        with mock.patch.object(gs.mc, "_run", return_value=gh([])):
            self.assertIn("no GitHub repos", gs.github_search(None, "zzz").response)
        with mock.patch.object(gs.mc, "_run", return_value=gh([], 1, "auth required")):
            self.assertIn("signed in", gs.github_search(None, "x").response)
        with mock.patch.object(gs.mc, "_run", side_effect=subprocess.TimeoutExpired("gh", 30)):
            self.assertIn("didn't work", gs.github_search(None, "x").response)
        self.assertIn("What should I search", gs.github_search(None, " ").response)


if __name__ == "__main__":
    unittest.main()
