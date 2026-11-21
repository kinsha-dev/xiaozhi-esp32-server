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
            self.assertEqual(col.fetch_mac_mail(), [])
        self.assertEqual(run.call_count, 1)  # never ran the reading script
        bad = [types.SimpleNamespace(stdout="true\n", stderr="", returncode=0),
               types.SimpleNamespace(stdout="", stderr="not allowed (-1743)", returncode=1)]
        with mock.patch.object(col.subprocess, "run", side_effect=bad):
            self.assertEqual(col.fetch_mac_mail(), [])

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

    def test_gmail_fetch_uses_metadata_only_and_maps_fields(self):
        calls = []

        def getter(path, params):
            calls.append((path, params))
            if path == "messages":
                return {"messages": [{"id": "a1"}]}
            return {"snippet": "Your invoice is due Friday.", "internalDate": "1762900000000",
                    "payload": {"headers": [{"name": "From", "value": "Acme <b@acme.com>"}, {"name": "Subject", "value": "Invoice 42"}]}}

        mails = col.fetch_gmail(getter=getter, access="x")
        self.assertEqual(mails, [{"sender": "Acme <b@acme.com>", "subject": "Invoice 42", "date": "a1",
                                  "preview": "Your invoice is due Friday.", "ts": 1762900000.0}])
        self.assertEqual(calls[0][1]["q"], "in:inbox newer_than:1d")
        self.assertEqual(calls[1][1]["format"], "metadata")  # never full

    def test_gmail_errors_are_wrapped_without_details(self):
        def boom(path, params):
            raise RuntimeError("secret-token-abc")
        with self.assertRaises(col.GmailError) as cm:
            col.fetch_gmail(getter=boom, access="x")
        self.assertNotIn("secret", str(cm.exception))

    def test_not_linked_when_token_missing_and_inbox_says_so(self):
        with TemporaryDirectory() as d:
            d = Path(d)
            col._gmail_token.update(access="", exp=0.0)
            with mock.patch.object(col, "GMAIL_TOKEN", d / "none.json"), mock.patch.object(col, "mail_config", return_value=dict(CFG, source="gmail")):
                self.assertEqual(col.fetch_mail(), [])
                self.assertEqual(col.mail_status["error"], "Gmail not linked yet")
                col._mail_cache.update(at=0.0, rows=[])
                with mock.patch.object(col, "DATA_DIR", d), mock.patch.object(col, "INBOX", d / "w.json"), \
                        mock.patch.object(col, "ALL_INBOX", d / "all.json"), mock.patch.object(col, "fetch", return_value=[]), \
                        mock.patch.object(col, "fetch_all", return_value=[]):
                    col.refresh_once()
                self.assertEqual(json.loads((d / "all.json").read_text())["mail_error"], "Gmail not linked yet")

    def test_token_refresh_is_stdlib_and_cached(self):
        with TemporaryDirectory() as d:
            tok = Path(d) / "t.json"
            tok.write_text(json.dumps({"client_id": "c", "client_secret": "s", "refresh_token": "r"}))
            col._gmail_token.update(access="", exp=0.0)
            resp = mock.MagicMock()
            resp.__enter__.return_value = resp
            resp.read.return_value = json.dumps({"access_token": "AT", "expires_in": 3600}).encode()
            with mock.patch.object(col, "GMAIL_TOKEN", tok), mock.patch.object(col.urllib.request, "urlopen", return_value=resp) as u:
                self.assertEqual(col._gmail_access_token(now=1000.0), "AT")
                self.assertEqual(col._gmail_access_token(now=1100.0), "AT")
                self.assertEqual(u.call_count, 1)

    def test_default_source_is_gmail_and_mac_mail_is_opt_in(self):
        with TemporaryDirectory() as d:
            cfgp = Path(d) / "c.json"
            with mock.patch.object(col, "MAIL_CONFIG", cfgp):
                self.assertEqual(col.mail_config()["source"], "gmail")
                cfgp.write_text(json.dumps({"source": "mac_mail"}))
                self.assertEqual(col.mail_config()["source"], "mac_mail")
                with mock.patch.object(col, "fetch_mac_mail", return_value=["m"]) as f, mock.patch.object(col, "fetch_gmail") as g:
                    self.assertEqual(col.fetch_mail(), ["m"])
                    g.assert_not_called()

    def test_mail_failure_never_triggers_self_exit(self):
        with mock.patch.object(col, "refresh_once", return_value=0), mock.patch.object(col, "time") as t:
            self.assertEqual(col.main(["--once"]), 0)


if __name__ == "__main__":
    unittest.main()
