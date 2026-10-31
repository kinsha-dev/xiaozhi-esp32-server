"""Read-only voice access to incoming WhatsApp messages for the local XiaoZhi server.

Tool: whatsapp_messages. Source: the notifications WhatsApp Desktop already shows on this Mac, read from the
macOS notification database (needs Full Disk Access for the server's Python, no WhatsApp login, no network).
Safety model: nothing is sent, replied to or deleted; message text never leaves this machine except in the
spoken reply; a small local buffer (last 24 h, 50 messages) is kept in DATA_DIR; and reading the messages aloud
needs the same spoken yes/confirm step as mac_control (the first reply only says how many are waiting).
"""
import json
import os
import plistlib
import sqlite3
import threading
import time
from pathlib import Path

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from plugins_func.functions import mac_control
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

DATA_DIR = Path(os.environ.get("MAC_CONTROL_DATA_DIR", Path.home() / "esp/xiaozhi-local/data"))
BUFFER = DATA_DIR / "whatsapp_buffer.json"
NOTIF_DB = Path(os.environ.get(
    "WHATSAPP_NOTIF_DB", Path.home() / "Library/Group Containers/group.com.apple.usernoted/db2/db"))
APP_MATCH = "whatsapp"  # matches net.whatsapp.WhatsApp and WhatsApp Business bundle ids
APPLE_EPOCH = 978307200  # CFAbsoluteTime 0 = 2001-01-01 UTC
MAX_AGE = 24 * 3600
MAX_BUFFER = 50
MAX_READ = 5        # messages read aloud per request
PREVIEW_CHARS = 80  # per message

_lock = threading.Lock()


def _now():
    return time.time()


def _load_buffer():
    try:
        data = json.loads(BUFFER.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_buffer(buf):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = BUFFER.with_suffix(".tmp")
    tmp.write_text(json.dumps(buf))
    os.chmod(tmp, 0o600)
    tmp.replace(BUFFER)


def _fetch_notifications():
    """Return [{id, sender, group, body, ts}] for WhatsApp notifications. Raises PermissionError/OSError."""
    uri = f"file:{NOTIF_DB}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=2)
    except sqlite3.DatabaseError as e:
        raise OSError(str(e))
    try:
        rows = con.execute(
            "SELECT r.rec_id, r.data, r.delivered_date FROM record r JOIN app a ON a.app_id = r.app_id "
            "WHERE lower(a.identifier) LIKE ? ORDER BY r.delivered_date DESC LIMIT 200",
            (f"%{APP_MATCH}%",),
        ).fetchall()
    except sqlite3.DatabaseError as e:
        raise OSError(str(e))
    finally:
        con.close()
    out = []
    for rec_id, blob, delivered in rows:
        try:
            req = plistlib.loads(blob).get("req", {})
        except Exception:
            continue
        body = str(req.get("body") or "").strip()
        if not body:
            continue
        ts = float(delivered) + APPLE_EPOCH if delivered else _now()
        out.append({"id": str(rec_id), "sender": str(req.get("titl") or "Someone").strip(),
                    "group": str(req.get("subt") or "").strip(), "body": body, "ts": ts})
    return out


def _refresh():
    """Merge fresh notifications into the buffer (keeping read flags) and return the unread ones, oldest first."""
    with _lock:
        buf = _load_buffer()
        cutoff = _now() - MAX_AGE
        for n in _fetch_notifications():
            if n["ts"] >= cutoff and n["id"] not in buf:
                buf[n["id"]] = {**n, "read": False}
        buf = {k: v for k, v in buf.items() if v["ts"] >= cutoff}
        newest = sorted(buf.items(), key=lambda kv: kv[1]["ts"], reverse=True)[:MAX_BUFFER]
        buf = dict(newest)
        _save_buffer(buf)
        return sorted((v for v in buf.values() if not v["read"]), key=lambda v: v["ts"])


def _mark_read(ids):
    with _lock:
        buf = _load_buffer()
        for i in ids:
            if i in buf:
                buf[i]["read"] = True
        _save_buffer(buf)


def _line(msg):
    who = f"{msg['sender']} in {msg['group']}" if msg["group"] else msg["sender"]
    return f"{who}: {mac_control._shorten(msg['body'], PREVIEW_CHARS)}"


def _unavailable(e):
    logger.bind(tag=TAG).warning(f"WhatsApp notifications unreadable: {e}")
    return ActionResponse(
        Action.RESPONSE, f"cannot read notification database: {e}",
        "I can't read WhatsApp notifications. The server needs Full Disk Access and WhatsApp Desktop must be running.")


def _speak_unread(_args=None):
    try:
        unread = _refresh()
    except (OSError, PermissionError) as e:
        return _unavailable(e)
    if not unread:
        return ActionResponse(Action.RESPONSE, "no new messages", "No new WhatsApp messages.")
    batch = unread[:MAX_READ]
    _mark_read([m["id"] for m in batch])
    rest = len(unread) - len(batch)
    spoken = " ".join(_line(m) + "." for m in batch)
    if rest:
        spoken += f" And {rest} more. Ask again to hear the rest."
    return ActionResponse(Action.RESPONSE, f"read {len(batch)} WhatsApp messages aloud", spoken)


mac_control.register_pending_handler("whatsapp_read", _speak_unread)

whatsapp_messages_desc = {
    "type": "function",
    "function": {
        "name": "whatsapp_messages",
        "description": (
            "Check for new incoming WhatsApp messages and read them aloud. Use when the user asks 'any new "
            "WhatsApp messages?', 'read my WhatsApp' or similar. Read-only: it cannot send, reply or delete. "
            "It first says how many are waiting and asks for spoken confirmation; after that, call "
            "confirm_action once the user says yes. Never invent message content."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


@register_function("whatsapp_messages", whatsapp_messages_desc, ToolType.SYSTEM_CTL)
def whatsapp_messages(conn):
    try:
        unread = _refresh()
    except (OSError, PermissionError) as e:
        return _unavailable(e)
    if not unread:
        return ActionResponse(Action.RESPONSE, "no new messages", "No new WhatsApp messages.")
    n = len(unread)
    return mac_control._create_pending(
        "whatsapp_read", {}, f"You have {n} new WhatsApp message{'s' if n != 1 else ''}. I would read out up to {MAX_READ}, with sender and a short preview.")
