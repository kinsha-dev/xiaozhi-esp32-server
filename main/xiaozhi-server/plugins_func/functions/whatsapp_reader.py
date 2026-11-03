"""Read-only voice access to incoming WhatsApp messages for the local XiaoZhi server.

Tool: whatsapp_messages. Source: the notifications WhatsApp Desktop already shows on this Mac, read from the
macOS notification database (needs Full Disk Access for the server's Python, no WhatsApp login, no network).
Safety model: nothing is sent, replied to or deleted; message text never leaves this machine except in the
spoken reply; a small local buffer (last 24 h, 50 messages) is kept in DATA_DIR; and the owner-voice gate
limits who can ask. No spoken confirmation: a request reads up to MAX_READ messages straight away.
"""
import json
import os
import subprocess
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

ASK_WINDOW = 120  # seconds a spoken "yes" stays valid after the question

_lock = threading.Lock()
_asked_at = 0.0


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


def _speak_unread(confirmed=False):
    global _asked_at
    try:
        unread = _refresh()
    except (OSError, PermissionError) as e:
        return _unavailable(e)
    if not unread:
        return ActionResponse(Action.RESPONSE, "no new messages", "No new WhatsApp messages.")
    n = len(unread)
    if not (confirmed and _now() - _asked_at <= ASK_WINDOW):
        _asked_at = _now()
        return ActionResponse(Action.RESPONSE, f"asked to read {n} WhatsApp messages",
                              f"You have {n} new WhatsApp message{'s' if n != 1 else ''}. Want me to read {'them' if n != 1 else 'it'}? Yes or no.")
    _asked_at = 0.0
    batch = unread[:MAX_READ]
    _mark_read([m["id"] for m in batch])
    rest = n - len(batch)
    spoken = " ".join(_line(m) + "." for m in batch)
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
            "FIRST call it without confirmed: it only says how many are waiting and asks yes or no. Call it again "
            "with confirmed true ONLY after the user says yes; then it reads up to five with sender and a short "
            "preview. On no, do not call it again. Never invent message content."
        ),
        "parameters": {"type": "object", "properties": {"confirmed": {"type": "boolean", "description": "True only after the user answered yes."}}, "required": []},
    },
}


@register_function("whatsapp_messages", whatsapp_messages_desc, ToolType.SYSTEM_CTL)
def whatsapp_messages(conn, confirmed=False):
    return _speak_unread(confirmed)


VIBI = os.environ.get("VIBI_BIN", str(Path.home() / "esp/voice-note-box/bin/vibi"))


def _clear_whatsapp():
    """Mark every unread message as read, including notifications the collector has not delivered yet."""
    try:
        _refresh()
    except OSError as e:  # collector down: still clear what is buffered
        logger.bind(tag=TAG).warning(f"clear: inbox not refreshed: {e}")
    with _lock:
        buf = _load_buffer()
        n = sum(1 for v in buf.values() if not v["read"])
        for v in buf.values():
            v["read"] = True
        _save_buffer(buf)
    return n


def _clear_claude():
    """Drop Claude's queued voice-box messages (`vibi clear`) so they are not read out. 0 if vibi is unavailable."""
    if not os.path.exists(VIBI):
        return 0
    try:
        out = subprocess.run([VIBI, "clear"], timeout=15, capture_output=True, text=True, shell=False).stdout
        return int(json.loads(out).get("cleared", 0))
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError) as e:
        logger.bind(tag=TAG).warning(f"clear: vibi clear failed: {e}")
        return 0


def _clear_notifications():
    from plugins_func.functions import mac_notifications
    return mac_notifications.clear_unread()


def _clear_unread():
    wa, cl, nt = _clear_whatsapp(), _clear_claude(), _clear_notifications()
    if not wa and not cl and not nt:
        return ActionResponse(Action.RESPONSE, "nothing to clear", "No unread messages to clear.")
    parts = []
    if wa:
        parts.append(f"{wa} WhatsApp message{'s' if wa != 1 else ''}")
    if cl:
        parts.append(f"{cl} Claude message{'s' if cl != 1 else ''}")
    if nt:
        parts.append(f"{nt} notification{'s' if nt != 1 else ''}")
    return ActionResponse(Action.RESPONSE, f"cleared {wa} WhatsApp, {cl} Claude, {nt} notifications",
                          f"Cleared {' and '.join(parts)}.")


clear_whatsapp_messages_desc = {
    "type": "function",
    "function": {
        "name": "clear_whatsapp_messages",
        "description": (
            "Clear all waiting messages, WhatsApp, Claude and Mac notifications, without reading them aloud, so they are not read "
            "later. Use when the user says 'vibi clear messages', 'clear my messages', 'clear Claude messages' "
            "or 'clear WhatsApp messages'. It only drops them from the reading queue; nothing is deleted from "
            "WhatsApp."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


@register_function("clear_whatsapp_messages", clear_whatsapp_messages_desc, ToolType.SYSTEM_CTL)
def clear_whatsapp_messages(conn):
    return _clear_unread()
