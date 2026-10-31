import json
import plistlib
import sqlite3
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
from plugins_func.functions import whatsapp_reader as wr  # noqa: E402
from scripts import whatsapp_collector as col  # noqa: E402


def make_db(path, rows):
    """Write the collector's inbox file; rows are (id, app_id, sender, group, body, age_seconds), app_id 1 = WhatsApp."""
    msgs = [{"id": str(i), "sender": t, "group": sub, "body": b, "ts": time.time() - age}
            for i, app, t, sub, b, age in rows if app == 1]
    path.write_text(json.dumps({"updated": time.time(), "messages": msgs}))


def make_notif_db(path, rows, app="net.whatsapp.WhatsApp"):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE app (app_id INTEGER PRIMARY KEY, identifier TEXT)")
    con.execute("CREATE TABLE record (rec_id INTEGER PRIMARY KEY, app_id INTEGER, data BLOB, delivered_date REAL)")
    con.execute("INSERT INTO app VALUES (1, ?)", (app,))
    con.execute("INSERT INTO app VALUES (2, 'com.other.App')")
    for rec_id, app_id, titl, subt, body, age in rows:
        data = plistlib.dumps({"req": {"titl": titl, "subt": subt, "body": body}})
        con.execute("INSERT INTO record VALUES (?,?,?,?)", (rec_id, app_id, data, time.time() - col.APPLE_EPOCH - age))
    con.commit()
    con.close()


class WhatsAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        d = Path(self.tmp.name)
        self.db = d / "db"
        self.patches = [
            mock.patch.object(wr, "DATA_DIR", d),
            mock.patch.object(wr, "BUFFER", d / "whatsapp_buffer.json"),
            mock.patch.object(wr, "INBOX", self.db),
        ]
        for p in self.patches:
            p.start()
        mc._pending.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_none_waiting(self):
        make_db(self.db, [(1, 2, "Other", "", "not whatsapp", 5)])
        self.assertIn("No new WhatsApp", wr.whatsapp_messages(None).response)

    def test_reads_immediately_without_confirmation(self):
        make_db(self.db, [(1, 1, "Ann", "", "Dinner at eight?", 60), (2, 1, "Bob", "Team", "Running late", 30)])
        r = wr.whatsapp_messages(None)
        self.assertIn("2 new WhatsApp messages", r.response)
        self.assertIn("Ann: Dinner at eight?", r.response)
        self.assertIn("Bob in Team: Running late", r.response)
        self.assertEqual(mc._pending, {})
        self.assertIn("No new", wr.whatsapp_messages(None).response)  # marked read

    def test_cap_and_preview(self):
        make_db(self.db, [(i, 1, f"P{i}", "", "x" * 300, 100 - i) for i in range(1, 9)])
        r = wr.whatsapp_messages(None)
        self.assertEqual(r.response.count("P"), 5)
        self.assertIn("And 3 more", r.response)
        self.assertLess(len(r.response), 5 * (wr.PREVIEW_CHARS + 20) + 100)

    def test_old_messages_dropped(self):
        make_db(self.db, [(1, 1, "Old", "", "ancient", wr.MAX_AGE + 100)])
        self.assertIn("No new", wr.whatsapp_messages(None).response)

    def test_stale_inbox(self):
        make_db(self.db, [(1, 1, "Ann", "", "hi", 5)])
        with mock.patch.object(wr, "_now", return_value=time.time() + wr.STALE_AFTER + 5):
            r = wr.whatsapp_messages(None)
        self.assertIn("collector", r.response)

    def test_collector_filters_app_and_writes_private_inbox(self):
        src = Path(self.tmp.name) / "notif.db"
        make_notif_db(src, [(1, 1, "Ann", "", "hello", 10), (2, 2, "Other", "", "nope", 10), (3, 1, "Old", "", "x", col.MAX_AGE + 50)])
        with mock.patch.object(col, "NOTIF_DB", src), mock.patch.object(col, "DATA_DIR", Path(self.tmp.name)), \
                mock.patch.object(col, "INBOX", Path(self.tmp.name) / "inbox.json"):
            self.assertEqual(col.refresh_once(), 1)
            f = Path(self.tmp.name) / "inbox.json"
            self.assertEqual(json.loads(f.read_text())["messages"][0]["sender"], "Ann")
            self.assertEqual(f.stat().st_mode & 0o777, 0o600)
            with mock.patch.object(col, "NOTIF_DB", Path(self.tmp.name) / "missing"):
                self.assertEqual(col.main(["--once"]), 1)

    def test_unreadable_inbox(self):
        self.assertIn("collector", wr.whatsapp_messages(None).response)  # file missing

    def test_no_message_content_in_llm_result(self):
        make_db(self.db, [(1, 1, "Ann", "", "private text", 5)])
        r = wr.whatsapp_messages(None)
        self.assertIn("private text", r.response)
        self.assertNotIn("private", r.result)


if __name__ == "__main__":
    unittest.main()
