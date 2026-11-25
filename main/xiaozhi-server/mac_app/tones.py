"""R2D2-style feedback tones for the Mac app.

Synthesis is shared with the board (`core.utils.tones`); this module only plays the PCM on the local
speaker. A tone is generated locally, never the server's TTS audio, so it plays the same way regardless
of which speaker backend (`Speaker` or `SaySpeaker`) the app is using for spoken replies. `stop()` cuts
off a tone started here so it cannot keep sounding over the start of a spoken reply.

Wake-ack and thinking tones are not played here. The server sends them as opus on the session
websocket, and the session plays that once. Playing them again locally would double them whenever
the Mac speaker plays server audio. The confused and pending chirps stay local because the server
never sends those.
"""
import os
import subprocess
import tempfile
import threading
import wave
from typing import Callable

import numpy as np

from core.utils.tones import RATE, synth_pcm

_default_playing = False


_proc = None


def _play_default(samples, rate) -> None:
    """Play through macOS `afplay`, which follows the CURRENT system output (Bluetooth earphones included);
    PortAudio's default output is fixed when it initialises and kept sending tones to the Mac speakers."""
    global _proc
    _stop_default()
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="vibi-tone-")
    with os.fdopen(fd, "wb") as f, wave.open(f, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.asarray(samples, dtype=np.int16).tobytes())
    _proc = subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    threading.Thread(target=_cleanup, args=(_proc, path), daemon=True).start()


def _cleanup(proc, path) -> None:
    proc.wait()
    try:
        os.unlink(path)
    except OSError:
        pass


def _stop_default() -> None:
    global _proc
    proc, _proc = _proc, None
    if proc is not None and proc.poll() is None:
        proc.terminate()


def play_tone(kind: str, player: Callable[[np.ndarray, int], None] = None) -> None:
    """Play a tone kind through `player(samples, rate)` (default: sounddevice.play, fire-and-forget)."""
    global _default_playing
    if player is None:
        from core.utils.audio_device import tones_disabled

        if tones_disabled():
            return
        player = _play_default
        _default_playing = True
    else:
        _default_playing = False
    samples = np.frombuffer(synth_pcm(kind), dtype=np.int16)
    player(samples, RATE)


def stop() -> None:
    """Stop a tone this module started on the default output. A custom `player` is left alone."""
    global _default_playing
    if not _default_playing:
        return
    _default_playing = False
    _stop_default()
