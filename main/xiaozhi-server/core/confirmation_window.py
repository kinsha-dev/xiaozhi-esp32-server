"""Marks that Vibi just asked a question, so the next short answer ("yes", "read them") is accepted more easily.

Written to data/awaiting_reply_until (epoch seconds) when a spoken sentence ends in "?"; the whisper service lowers the
owner-voice threshold for short clips only while that time is in the future.
"""
import os
import time
from pathlib import Path

WINDOW_S = 40  # long enough for the question to be spoken and answered


def _file():
    return Path(os.environ.get("VOICE_BOX_DATA", Path.home() / "esp/voice-note-box/data")) / "awaiting_reply_until"


def note_spoken(text):
    if not text or not text.strip().endswith("?"):
        return
    try:
        f = _file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"{time.time() + WINDOW_S:.0f}")
    except OSError:
        pass
