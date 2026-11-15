"""R2D2-style feedback tones for the Mac app.

Synthesis is shared with the board (`core.utils.tones`); this module only plays the PCM on the local
speaker. A tone is generated locally, never the server's TTS audio, so it plays the same way regardless
of which speaker backend (`Speaker` or `SaySpeaker`) the app is using for spoken replies. `stop()` cuts
off a tone started here so it cannot keep sounding over the start of a spoken reply.
"""
from typing import Callable

import numpy as np

from core.utils.tones import RATE, synth_pcm

_default_playing = False


def _play_default(samples, rate) -> None:
    import sounddevice as sd

    sd.play(samples, rate)


def _stop_default() -> None:
    import sounddevice as sd

    sd.stop()


def play_tone(kind: str, player: Callable[[np.ndarray, int], None] = None) -> None:
    """Play a tone kind through `player(samples, rate)` (default: sounddevice.play, fire-and-forget)."""
    global _default_playing
    if player is None:
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
