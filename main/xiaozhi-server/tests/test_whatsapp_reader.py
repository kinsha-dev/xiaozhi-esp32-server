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


def make_db(path, rows, app="net.whatsapp.WhatsApp"):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE app (app_id INTEGER PRIMARY KEY, identifier TEXT)")
    con.execute("CREATE TABLE record (rec_id INTEGER PRIMARY KEY, app_id INTEGER, data BLOB, delivered_date REAL)")
    con.execute("INSERT INTO app VALUES (1, ?)", (app,))
    con.execute("INSERT INTO app VALUES (2, 'com.other.App')")
    for rec_id, app_id, titl, subt, body, age in rows:
        data = plistlib.dumps({"req": {"titl": titl, "subt": subt, "body": body}})
        con.execute("INSERT INTO record VALUES (?,?,?,?)",
                    (rec_id, app_id, data, time.time() - wr.APPLE_EPOCH - age))
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
            mock.patch.object(wr, "NOTIF_DB", self.db),
        ]
        for p in self.patches:
            p.start()
        mc._pending.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def token(self):
        return next(iter(mc._pending))

    def test_none_waiting(self):
        make_db(self.db, [(1, 2, "Other", "", "not whatsapp", 5)])
        r = wr.whatsapp_messages(None)
        self.assertIn("No new WhatsApp", r.response)
        self.assertEqual(mc._pending, {})

    def test_count_first_then_confirm_reads(self):
        make_db(self.db, [(1, 1, "Ann", "", "Dinner at eight?", 60), (2, 1, "Bob", "Team", "Running late", 30)])
        r = wr.whatsapp_messages(None)
        self.assertIn("2 new WhatsApp messages", r.response)
        self.assertNotIn("Ann", r.response)
        self.assertNotIn("Dinner", r.response)
        self.assertNotIn(self.token(), r.result)  # token only in spoken text
        r2 = mc.confirm_action(None, self.token())
        self.assertIn("Ann: Dinner at eight?", r2.response)
        self.assertIn("Bob in Team: Running late", r2.response)
        # now read: nothing new
        self.assertIn("No new", wr.whatsapp_messages(None).response)

    def test_wrong_token_does_not_read(self):
        make_db(self.db, [(1, 1, "Ann", "", "secret", 5)])
        wr.whatsapp_messages(None)
        r = mc.confirm_action(None, "0000")
        self.assertNotIn("secret", r.response)

    def test_cap_and_preview(self):
        rows = [(i, 1, f"P{i}", "", "x" * 300, 100 - i) for i in range(1, 9)]
        make_db(self.db, rows)
        wr.whatsapp_messages(None)
        r = mc.confirm_action(None, self.token())
        self.assertEqual(r.response.count("P"), 5)
        self.assertIn("And 3 more", r.response)
        self.assertLess(len(r.response), 5 * (wr.PREVIEW_CHARS + 20) + 60)

    def test_old_messages_dropped(self):
        make_db(self.db, [(1, 1, "Old", "", "ancient", wr.MAX_AGE + 100)])
        self.assertIn("No new", wr.whatsapp_messages(None).response)

    def test_unreadable_db(self):
        r = wr.whatsapp_messages(None)  # file missing
        self.assertIn("Full Disk Access", r.response)
        self.assertEqual(mc._pending, {})

    def test_no_message_content_in_llm_result(self):
        make_db(self.db, [(1, 1, "Ann", "", "private text", 5)])
        r = wr.whatsapp_messages(None)
        self.assertNotIn("private", r.result)
        r2 = mc.confirm_action(None, self.token())
        self.assertNotIn("private", r2.result)


if __name__ == "__main__":
    unittest.main()
