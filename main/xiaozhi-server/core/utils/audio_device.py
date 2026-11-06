"""Which device answers the wake word, shared with the Mac menu-bar app through files in the voice-box data dir.

data/audio_device  "auto" | "mac" | "board" (written by the Mac app's vb menu)
data/board_state   "<unix ts> listening|speaking|idle" (written here, mirrored by the Mac app's overlay)
"""
import os
import time
from pathlib import Path

MAC_APP_ID = "mac-app"


def _dir() -> Path:
    return Path(os.environ.get("VOICE_BOX_DATA", Path.home() / "esp/voice-note-box/data"))


def is_board(conn) -> bool:
    try:
        return conn.headers.get("device-id") != MAC_APP_ID
    except AttributeError:
        return True


def mode() -> str:
    try:
        m = (_dir() / "audio_device").read_text().strip()
    except OSError:
        return "auto"
    return m if m in ("auto", "mac", "board") else "auto"


def board_muted(conn) -> bool:
    """True when the user picked "This Mac": the board's audio is dropped."""
    return is_board(conn) and mode() == "mac"


_last = {}


def set_board_state(conn, state: str):
    if not is_board(conn):
        return
    now = time.time()
    if _last.get("state") == state and now - _last.get("ts", 0) < 1.0:
        return
    _last.update(state=state, ts=now)
    try:
        (_dir() / "board_state").write_text(f"{now:.2f} {state}")
    except OSError:
        pass
