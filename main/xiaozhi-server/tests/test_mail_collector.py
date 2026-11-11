import json
import sys
import time
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import whatsapp_collector as col  # noqa: E402

CFG = {"keywords": ["invoice", "security"], "senders": ["mum@home.com"]}
NOW = time.time()
MAILS = [
    {"sender": "Acme Billing <bill@acme.com>", "subject": "Invoice 42 due", "date": "d1", "ts": NOW - 60,
     "preview": "Your invoice of $40 is due Friday. See https://x.co/abc for details."},
    {"sender": "Bank <no-reply@bank.com>", "subject": "Security code", "date": "d2", "ts": NOW - 50,
     "preview": "Your verification code is 482913."},
    {"sender": "News <n@news.com>", "subject": "Weekly deals", "date": "d3", "ts": NOW - 40, "preview": "Great stuff"},
    {"sender": "Mum <mum@home.com>", "subject": "hi", "date": "d4", "ts": NOW - 30, "preview": "Call me when free, love you"},
    {"sender": "X", "subject": "Invoice odd date", "date": "d5", "ts": None, "preview": "x"},
]


class MailCollectorTests(unittest.TestCase):
    def test_rows_filter_gist_and_codes(self):
        rows = col.mail_rows(MAILS, CFG)
        self.assertEqual([r["title"] for r in rows][:1], ["Acme Billing"])
        self.assertEqual(len(rows), 3)  # newsletter filtered, undated skipped
        self.assertTrue(all(r["app"] == "mail" for r in rows))
        self.assertEqual(rows[0]["body"], "Your invoice of $40 is due Friday")
        code = rows[1]
        self.assertTrue(code["code"])
        self.assertNotIn("482913", json.dumps(code))
        self.assertNotIn("Bank", json.dumps(code))
        self.assertEqual(rows[2]["title"], "Mum")

    def test_ids_stable(self):
        self.assertEqual(col.mail_rows(MAILS, CFG)[0]["id"], col.mail_rows(MAILS, CFG)[0]["id"])

    def test_parse_stamp(self):
        self.assertIsNotNone(col.parse_stamp("2026-11-11-78485"))
        self.assertIsNone(col.parse_stamp("garbage"))

    def test_fetch_mail_skips_when_mail_not_running_or_blocked(self):
        run = mock.Mock(return_value=types.SimpleNamespace(stdout="false\n", stderr="", returncode=0))
        with mock.patch.object(col.subprocess, "run", run):
            self.assertEqual(col.fetch_mail(), [])
        self.assertEqual(run.call_count, 1)  # never ran the reading script
        bad = [types.SimpleNamespace(stdout="true\n", stderr="", returncode=0),
               types.SimpleNamespace(stdout="", stderr="not allowed (-1743)", returncode=1)]
        with mock.patch.object(col.subprocess, "run", side_effect=bad):
            self.assertEqual(col.fetch_mail(), [])

    def test_refresh_merges_mail_into_all_inbox_without_breaking_others(self):
        with TemporaryDirectory() as d:
            d = Path(d)
            col._mail_cache.update(at=0.0, rows=[])
            with mock.patch.object(col, "DATA_DIR", d), mock.patch.object(col, "INBOX", d / "w.json"), \
                    mock.patch.object(col, "ALL_INBOX", d / "all.json"), \
                    mock.patch.object(col, "fetch", return_value=[]), \
                    mock.patch.object(col, "fetch_all", return_value=[{"id": "n1", "app": "slack", "title": "t", "sub": "", "body": "b", "ts": NOW - 5}]), \
                    mock.patch.object(col, "fetch_mail", return_value=MAILS), \
                    mock.patch.object(col, "mail_config", return_value=CFG):
                col.refresh_once()
                msgs = json.loads((d / "all.json").read_text())["messages"]
        self.assertEqual({m["app"] for m in msgs}, {"slack", "mail"})
        self.assertEqual(msgs[0]["id"], "n1")  # newest first

    def test_mail_failure_never_triggers_self_exit(self):
        with mock.patch.object(col, "refresh_once", return_value=0), mock.patch.object(col, "time") as t:
            self.assertEqual(col.main(["--once"]), 0)


if __name__ == "__main__":
    unittest.main()
