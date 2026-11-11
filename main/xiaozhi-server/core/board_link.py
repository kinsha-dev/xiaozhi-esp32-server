"""Tell the board which link to use. data/board_link holds "auto" or "bluetooth" (set from the Mac menu bar).

While a board is connected over the websocket, this sends it {"type":"system","command":"link_bluetooth"|"link_auto"}
on connect and whenever the file changes. Over Bluetooth the Mac relay sends the same message (mac_app/ble_central.py).
"""
import asyncio
import json
import os
import time

from pathlib import Path

from core.utils import audio_device

POLL_S = 2.0
COMMANDS = {"auto": "link_auto", "bluetooth": "link_bluetooth"}


def _file() -> Path:
    return Path(os.environ.get("VOICE_BOX_DATA", Path.home() / "esp/voice-note-box/data")) / "board_link"


def read_mode() -> str:
    try:
        m = _file().read_text().strip()
    except OSError:
        return "auto"
    return m if m in COMMANDS else "auto"


def silent() -> bool:
    """True while silent mode is on (data/silent_until holds an epoch time in the future)."""
    try:
        return float((_file().parent / "silent_until").read_text().strip()) > time.time()
    except (OSError, ValueError):
        return False


def probe_body() -> str:
    """Body of GET /xiaozhi/link: the link mode, plus "silent" while silent mode is on and "muted" while Mute is on (the board shows both)."""
    return read_mode() + (" silent" if silent() else "") + (" muted" if audio_device.muted() else "")


def message(mode: str) -> str:
    return json.dumps({"type": "system", "command": COMMANDS[mode]})


async def watch(conn):
    """Per-connection task: push the link mode to a board whenever it changes. Ends with the connection."""
    if not audio_device.is_board(conn):
        return
    sent = None
    while not conn.stop_event.is_set():
        mode = read_mode()
        if mode != sent:
            try:
                await conn.websocket.send(message(mode))
                sent = mode
            except Exception:
                return
        await asyncio.sleep(POLL_S)
