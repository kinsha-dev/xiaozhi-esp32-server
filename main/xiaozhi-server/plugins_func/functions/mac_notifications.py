"""Read-only voice access to the Mac's notifications (Mail, Calendar, Slack, ...), WhatsApp excluded.

Tool: mac_notifications. Source: data/notifications_inbox.json, written by scripts/whatsapp_collector.py (the only
process with Full Disk Access). Nothing is sent, replied to or dismissed; text is only spoken locally. Read
notifications are remembered in DATA_DIR/notifications_buffer.json so each is read once. No spoken confirmation.
"""
import json
import os
import threading
import time
from pathlib import Path

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from plugins_func.functions import mac_control
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

DATA_DIR = Path(os.environ.get("MAC_CONTROL_DATA_DIR", Path.home() / "esp/xiaozhi-local/data"))
BUFFER = DATA_DIR / "notifications_buffer.json"
INBOX = DATA_DIR / "notifications_inbox.json"
STALE_AFTER = 300
MAX_AGE = 24 * 3600
MAX_BUFFER = 100
MAX_READ = 5
PREVIEW_CHARS = 80

ASK_WINDOW = 120  # seconds a spoken "yes" stays valid after the question

_lock = threading.Lock()
_asked_at = 0.0


def _load():
    try:
        data = json.loads(BUFFER.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(buf):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = BUFFER.with_suffix(".tmp")
    tmp.write_text(json.dumps(buf))
    os.chmod(tmp, 0o600)
    tmp.replace(BUFFER)


def _fetch():
    try:
        data = json.loads(INBOX.read_text())
        msgs, updated = data["messages"], float(data["updated"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise OSError(f"inbox unreadable: {e}")
    if time.time() - updated > STALE_AFTER:
        raise OSError("inbox is stale; is the collector running?")
    return msgs


def _unread():
    with _lock:
        buf = _load()
        cutoff = time.time() - MAX_AGE
        for n in _fetch():
            if n["ts"] >= cutoff and n["id"] not in buf:
                buf[n["id"]] = {**n, "read": False}
        buf = dict(sorted(((k, v) for k, v in buf.items() if v["ts"] >= cutoff),
                          key=lambda kv: kv[1]["ts"], reverse=True)[:MAX_BUFFER])
        _save(buf)
        return sorted((v for v in buf.values() if not v["read"]), key=lambda v: v["ts"])


def _mark_read(ids):
    with _lock:
        buf = _load()
        for i in ids:
            if i in buf:
                buf[i]["read"] = True
        _save(buf)


def _line(n):
    head = n["app"].replace("-", " ")
    who = ", ".join(x for x in (n["title"], n["sub"]) if x)
    text = mac_control._shorten(n["body"], PREVIEW_CHARS) if n["body"] else ""
    return f"{head}: " + ": ".join(x for x in (who, text) if x)


def clear_unread():
    """Mark every unread notification as read without speaking it; returns how many. 0 if the inbox is unreadable."""
    try:
        _unread()
    except OSError:
        pass  # collector down: still clear what is buffered
    with _lock:
        buf = _load()
        n = sum(1 for v in buf.values() if not v["read"])
        for v in buf.values():
            v["read"] = True
        _save(buf)
    return n


mac_notifications_desc = {
    "type": "function",
    "function": {
        "name": "mac_notifications",
        "description": (
            "Read out the Mac's new notifications (Mail, Calendar, Slack and other apps, not WhatsApp). Use for "
            "'read out notifications', 'any notifications?', 'what did I miss on my Mac'. Read-only: it cannot "
            "reply or dismiss. FIRST call it without confirmed: it only says how many are waiting and asks the user "
            "yes or no. Call it again with confirmed true ONLY after the user says yes; then it reads up to five. "
            "On no, do not call it again. Never invent content."
        ),
        "parameters": {"type": "object", "properties": {"confirmed": {"type": "boolean", "description": "True only after the user answered yes to the question."}}, "required": []},
    },
}


@register_function("mac_notifications", mac_notifications_desc, ToolType.SYSTEM_CTL)
def mac_notifications(conn, confirmed=False):
    global _asked_at
    try:
        unread = _unread()
    except OSError as e:
        logger.bind(tag=TAG).warning(f"Mac notifications unreadable: {e}")
        return ActionResponse(Action.RESPONSE, f"cannot read notifications: {e}",
                              "I can't read Mac notifications. The collector isn't running or lacks Full Disk Access.")
    if not unread:
        return ActionResponse(Action.RESPONSE, "no new notifications", "No new notifications.")
    n = len(unread)
    if not (confirmed and time.time() - _asked_at <= ASK_WINDOW):
        _asked_at = time.time()
        return ActionResponse(Action.RESPONSE, f"asked to read {n} notifications",
                              f"You have {n} new notification{'s' if n != 1 else ''}. Want me to read {'them' if n != 1 else 'it'}? Yes or no.")
    _asked_at = 0.0
    batch = unread[:MAX_READ]
    _mark_read([n["id"] for n in batch])
    rest = n - len(batch)
    spoken = " ".join(_line(m) + "." for m in batch)
    if rest:
        spoken += f" And {rest} more. Ask again to hear the rest."
    return ActionResponse(Action.RESPONSE, f"read {len(batch)} notifications aloud", spoken)
