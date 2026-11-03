#!/usr/bin/python3
"""Copies WhatsApp Desktop notifications from the macOS notification database into a small JSON inbox.

This is the only process that needs Full Disk Access; the XiaoZhi server then reads just the inbox file
(whatsapp_reader.py). Stdlib only, read-only on the database, writes one file (mode 600) under the data dir,
keeps the last 24 h / 50 messages, never touches the network. Run it as /usr/bin/python3 (see MAC_CONTROL.md).

  whatsapp_collector.py            # loop forever, refresh every 20 s
  whatsapp_collector.py --once     # one refresh (used by tests / launchd StartInterval)
"""
import json
import os
import plistlib
import sqlite3
import sys
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("MAC_CONTROL_DATA_DIR", Path.home() / "esp/xiaozhi-local/data"))
INBOX = DATA_DIR / "whatsapp_inbox.json"
ALL_INBOX = DATA_DIR / "notifications_inbox.json"  # every other app, for mac_notifications.py
NOTIF_DB = Path(os.environ.get(
    "WHATSAPP_NOTIF_DB", Path.home() / "Library/Group Containers/group.com.apple.usernoted/db2/db"))
APP_MATCH = "whatsapp"
APPLE_EPOCH = 978307200  # CFAbsoluteTime 0 = 2001-01-01 UTC
MAX_AGE = 24 * 3600
MAX_MESSAGES = 50


def fetch(db=None):
    """[{id, sender, group, body, ts}] for WhatsApp notifications, newest first. Raises OSError."""
    try:
        con = sqlite3.connect(f"file:{db or NOTIF_DB}?mode=ro", uri=True, timeout=2)
        try:
            rows = con.execute(
                "SELECT r.rec_id, r.data, r.delivered_date FROM record r JOIN app a ON a.app_id = r.app_id "
                "WHERE lower(a.identifier) LIKE ? ORDER BY r.delivered_date DESC LIMIT 200",
                (f"%{APP_MATCH}%",),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.DatabaseError as e:
        raise OSError(str(e))
    out = []
    for rec_id, blob, delivered in rows:
        try:
            req = plistlib.loads(blob).get("req", {})
        except Exception:
            continue
        body = str(req.get("body") or "").strip()
        if body:
            out.append({"id": str(rec_id), "sender": str(req.get("titl") or "Someone").strip(),
                        "group": str(req.get("subt") or "").strip(), "body": body,
                        "ts": float(delivered) + APPLE_EPOCH if delivered else time.time()})
    return out


def fetch_all(db=None):
    """[{id, app, title, sub, body, ts}] for non-WhatsApp notifications, newest first. Raises OSError."""
    try:
        con = sqlite3.connect(f"file:{db or NOTIF_DB}?mode=ro", uri=True, timeout=2)
        try:
            rows = con.execute(
                "SELECT r.rec_id, a.identifier, r.data, r.delivered_date FROM record r JOIN app a ON a.app_id = r.app_id "
                "WHERE lower(a.identifier) NOT LIKE ? ORDER BY r.delivered_date DESC LIMIT 200",
                (f"%{APP_MATCH}%",),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.DatabaseError as e:
        raise OSError(str(e))
    out = []
    for rec_id, ident, blob, delivered in rows:
        try:
            req = plistlib.loads(blob).get("req", {})
        except Exception:
            continue
        body = str(req.get("body") or "").strip()
        title = str(req.get("titl") or "").strip()
        if "scripteditor" in str(ident).lower():  # vibi's own "Voice box" banners; they are spoken already
            continue
        if body or title:
            out.append({"id": f"n{rec_id}", "app": str(ident).rsplit(".", 1)[-1], "title": title,
                        "sub": str(req.get("subt") or "").strip(), "body": body,
                        "ts": float(delivered) + APPLE_EPOCH if delivered else time.time()})
    return out


def _write(path, msgs):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"updated": time.time(), "messages": msgs}))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def refresh_once():
    cutoff = time.time() - MAX_AGE
    msgs = [m for m in fetch() if m["ts"] >= cutoff][:MAX_MESSAGES]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _write(INBOX, msgs)
    _write(ALL_INBOX, [m for m in fetch_all() if m["ts"] >= cutoff][:MAX_MESSAGES])
    return len(msgs)


def main(argv):
    once = "--once" in argv
    while True:
        try:
            refresh_once()
        except OSError as e:
            print(f"whatsapp_collector: {e}", file=sys.stderr)
            if once:
                return 1
        if once:
            return 0
        time.sleep(20)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
