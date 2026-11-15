"""R2D2-style feedback tones for the Mac app: short synthesized beeps played instead of a full spoken
reply or dead silence. See core/utils/tones.py for the board-side equivalent (same tone design, played
through the websocket/Opus pipeline there instead of the local speaker here) and for why each kind fires
where it does.

Kinds: confused (wake-phrase near-miss), action (carrying out a confirmed action - one tone, not a
separate "accepted" chirp first), pending (a new outbox message exists).
"""
from typing import Callable

import numpy as np

RATE = 16000  # independent of the speaker's own output rate; play_tone() resamples for the device


def _segment(f_start: float, f_end: float, dur_s: float, wave_shape: str = "square",
             vibrato_hz: float = 0.0, vibrato_depth: float = 0.0, amp: float = 0.6) -> np.ndarray:
    n = max(1, int(RATE * dur_s))
    t = np.arange(n) / RATE
    freq = np.linspace(f_start, f_end, n)
    if vibrato_hz:
        freq = freq + vibrato_depth * np.sin(2 * np.pi * vibrato_hz * t)
    phase = 2 * np.pi * np.cumsum(freq) / RATE
    samples = np.sign(np.sin(phase)) if wave_shape == "square" else np.sin(phase)
    attack = min(n // 4, int(RATE * 0.005))
    envelope = np.ones(n)
    if attack:
        envelope[:attack] = np.linspace(0, 1, attack)
        envelope[-attack:] = np.linspace(1, 0, attack)
    return samples * envelope * amp


def _silence(dur_s: float) -> np.ndarray:
    return np.zeros(int(RATE * dur_s))


def _confused() -> np.ndarray:
    return np.concatenate([
        _segment(700, 450, 0.14, vibrato_hz=35, vibrato_depth=40),
        _segment(450, 650, 0.10, vibrato_hz=35, vibrato_depth=40),
    ])


def _action() -> np.ndarray:
    beep, gap = _segment(550, 550, 0.06), _silence(0.04)
    return np.concatenate([beep, gap, beep, gap, beep])


def _pending() -> np.ndarray:
    return np.concatenate([
        _segment(400, 600, 0.15, wave_shape="sine", amp=0.4),
        _silence(0.06),
        _segment(600, 480, 0.15, wave_shape="sine", amp=0.4),
    ])


_BUILDERS = {"confused": _confused, "action": _action, "pending": _pending}
_pcm_cache: dict[str, bytes] = {}


def synth_pcm(kind: str) -> bytes:
    """int16 mono PCM at RATE for the given tone kind, built once and cached."""
    if kind not in _pcm_cache:
        samples = np.clip(_BUILDERS[kind](), -1.0, 1.0)
        _pcm_cache[kind] = (samples * 32767).astype(np.int16).tobytes()
    return _pcm_cache[kind]


def play_tone(kind: str, player: Callable[[np.ndarray, int], None] = None) -> None:
    """Play a tone kind through `player(samples, rate)` (default: sounddevice.play, fire-and-forget).

    A tone is generated locally, never the server's TTS audio, so it plays the same way regardless of
    which speaker backend (`Speaker` or `SaySpeaker`) the app is using for spoken replies.
    """
    if player is None:
        import sounddevice as sd
        player = sd.play
    pcm = synth_pcm(kind)
    samples = np.frombuffer(pcm, dtype=np.int16)
    player(samples, RATE)
