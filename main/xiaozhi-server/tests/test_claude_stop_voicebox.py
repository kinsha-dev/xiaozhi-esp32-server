import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import claude_stop_voicebox as h  # noqa: E402


def transcript(d, *texts):
    p = Path(d) / "t.jsonl"
    p.write_text("\n".join(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": t}]}}) for t in texts))
    return str(p)


class HookTests(unittest.TestCase):
    def run_hook(self, d, payload, vibi_exists=True):
        with mock.patch.object(h.sys, "stdin", io.StringIO(json.dumps(payload))), \
                mock.patch.object(h.os.path, "exists", return_value=vibi_exists), \
                mock.patch.object(h.subprocess, "run") as run, \
                mock.patch.dict(h.os.environ, {"VIBI_HOOK_DIRS": d}):
            rc = h.main()
        return rc, run

    def test_posts_last_message_as_plain_speech(self):
        with TemporaryDirectory() as d:
            t = transcript(d, "Old.", "Done in `app.py` at src/x.py. Tests pass. Third sentence.")
            rc, run = self.run_hook(d, {"cwd": d, "transcript_path": t})
        self.assertEqual(rc, 0)
        say = run.call_args[0][0]
        self.assertEqual(say[1], "say")
        self.assertIn("Tests pass", say[2])
        self.assertNotIn("Third", say[2])
        self.assertNotIn("src/", say[2])
        self.assertNotIn("`", say[2])
        self.assertFalse(run.call_args[1]["shell"])

    def test_out_of_scope_or_loop_or_missing_does_nothing(self):
        with TemporaryDirectory() as d, TemporaryDirectory() as other:
            t = transcript(d, "Hello there.")
            self.assertFalse(self.run_hook(d, {"cwd": other, "transcript_path": t})[1].called)
            self.assertFalse(self.run_hook(d, {"cwd": d, "transcript_path": t, "stop_hook_active": True})[1].called)
            self.assertFalse(self.run_hook(d, {"cwd": d, "transcript_path": t}, vibi_exists=False)[1].called)
            self.assertFalse(self.run_hook(d, {"cwd": d, "transcript_path": "/nope"})[1].called)

    def test_never_raises(self):
        with mock.patch.object(h.sys, "stdin", io.StringIO("not json")):
            self.assertEqual(h.main(), 0)


if __name__ == "__main__":
    unittest.main()
