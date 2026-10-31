"""Read-only voice access to incoming WhatsApp messages for the local XiaoZhi server.

Tool: whatsapp_messages. Source: the notifications WhatsApp Desktop already shows on this Mac, read from the
macOS notification database (needs Full Disk Access for the server's Python, no WhatsApp login, no network).
Safety model: nothing is sent, replied to or deleted; message text never leaves this machine except in the
spoken reply; a small local buffer (last 24 h, 50 messages) is kept in DATA_DIR; and the owner-voice gate
limits who can ask. No spoken confirmation: a request reads up to MAX_READ messages straight away.
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
BUFFER = DATA_DIR / "whatsapp_buffer.json"  # read flags + recent messages
INBOX = DATA_DIR / "whatsapp_inbox.json"    # written by scripts/whatsapp_collector.py
STALE_AFTER = 300  # seconds without a collector refresh before we say it is not running
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
    """Messages from the inbox file written by scripts/whatsapp_collector.py (the only process with Full Disk
    Access). Raises OSError when the collector has not run or its data is stale."""
    try:
        data = json.loads(INBOX.read_text())
        msgs, updated = data["messages"], float(data["updated"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise OSError(f"inbox unreadable: {e}")
    if _now() - updated > STALE_AFTER:
        raise OSError("inbox is stale; is the collector running?")
    return msgs


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
        "I can't read WhatsApp notifications. The WhatsApp collector isn't running or lacks Full Disk Access.")


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
    n = len(unread)
    spoken = f"You have {n} new WhatsApp message{'s' if n != 1 else ''}. " + " ".join(_line(m) + "." for m in batch)
    if rest:
        spoken += f" And {rest} more. Ask again to hear the rest."
    return ActionResponse(Action.RESPONSE, f"read {len(batch)} WhatsApp messages aloud", spoken)


whatsapp_messages_desc = {
    "type": "function",
    "function": {
        "name": "whatsapp_messages",
        "description": (
            "Check for new incoming WhatsApp messages and read them aloud. Use when the user asks 'any new "
            "WhatsApp messages?', 'read my WhatsApp' or similar. Read-only: it cannot send, reply or delete. "
            "It says how many are waiting, then reads up to five with sender and a short preview. "
            "Never invent message content."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


@register_function("whatsapp_messages", whatsapp_messages_desc, ToolType.SYSTEM_CTL)
def whatsapp_messages(conn):
    return _speak_unread()
