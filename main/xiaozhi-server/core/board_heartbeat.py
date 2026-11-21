"""Board heartbeat: the time the board last proved it is alive (a message from it, or a reply to our websocket ping).

Written to data/board_heartbeat as epoch seconds; the Mac app shows X in the menu bar when it is more than 5 minutes old.
The Mac app itself (device-id "mac-app") never counts.
"""
import os
import time
from pathlib import Path

MIN_INTERVAL = 5  # seconds between file writes
_last_write = 0.0


def _file():
    return Path(os.environ.get("VOICE_BOX_DATA", Path.home() / "esp/voice-note-box/data")) / "board_heartbeat"


def beat(device_id=None):
    global _last_write
    now = time.time()
    if device_id == "mac-app" or now - _last_write < MIN_INTERVAL:
        return
    _last_write = now
    try:
        _file().write_text(f"{now:.0f}")
    except OSError:
        pass
