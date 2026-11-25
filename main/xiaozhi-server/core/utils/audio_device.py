"""Which device answers the wake word, shared with the Mac menu-bar app through files in the voice-box data dir.

data/audio_device  "auto" | "mac" | "board" (written by the Mac app's vb menu)
data/tones_disabled  exists = wake and thinking tones are off (Mac app Dock menu)
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


def muted() -> bool:
    """True while the Mac app's Mute or Stealth mode is on (data/muted or data/stealth exists): the board neither listens nor answers."""
    return (_dir() / "muted").exists() or (_dir() / "stealth").exists()


def tones_disabled() -> bool:
    """True while "Disable tones" is on in the Mac app's Dock / menu-bar menu (data/tones_disabled exists): no wake or thinking tones."""
    return (_dir() / "tones_disabled").exists()


def board_muted(conn) -> bool:
    """True when the board's audio must be dropped: Mute is on, or the user picked "This Mac"."""
    return is_board(conn) and (muted() or mode() == "mac")


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
