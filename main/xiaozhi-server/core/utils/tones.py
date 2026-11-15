"""R2D2-style feedback tones: short synthesized beeps played instead of a full spoken reply.

Four kinds, each a few pitched square/sine-wave sweeps (classic R2D2 sound design, not realistic audio):
  confused - heard something, but it wasn't a reply worth speaking (IgnoreGate's "[ignore]" decision)
  confirm  - the whole reply was a bare acknowledgment not worth speaking (IgnoreGate's "[ack]" decision)
  action   - a confirmed action is now being carried out
  pending  - a new pending outbox message exists

Synthesized once with numpy, cached as a tiny WAV file under config/assets/tones/, then played on the board
through the exact pipeline helloHandle.py already uses for cached wakeup-word replies: audio_to_data() turns
the file into Opus packets and sendAudioMessage() sends them as a short, textless "reply".
"""
import wave
from pathlib import Path

import numpy as np

RATE = 16000
TONES_DIR = Path(__file__).resolve().parents[2] / "config" / "assets" / "tones"


def _segment(f_start: float, f_end: float, dur_s: float, wave_shape: str = "square",
             vibrato_hz: float = 0.0, vibrato_depth: float = 0.0, amp: float = 0.6) -> np.ndarray:
    n = max(1, int(RATE * dur_s))
    t = np.arange(n) / RATE
    freq = np.linspace(f_start, f_end, n)
    if vibrato_hz:
        freq = freq + vibrato_depth * np.sin(2 * np.pi * vibrato_hz * t)
    phase = 2 * np.pi * np.cumsum(freq) / RATE
    samples = np.sign(np.sin(phase)) if wave_shape == "square" else np.sin(phase)
    attack = min(n // 4, int(RATE * 0.005))  # 5 ms attack/release so segments don't click
    envelope = np.ones(n)
    if attack:
        envelope[:attack] = np.linspace(0, 1, attack)
        envelope[-attack:] = np.linspace(1, 0, attack)
    return samples * envelope * amp


def _silence(dur_s: float) -> np.ndarray:
    return np.zeros(int(RATE * dur_s))


def _confused() -> np.ndarray:
    """A wavering down-then-up chirp, like R2D2's confused "hmm?"."""
    return np.concatenate([
        _segment(700, 450, 0.14, vibrato_hz=35, vibrato_depth=40),
        _segment(450, 650, 0.10, vibrato_hz=35, vibrato_depth=40),
    ])


def _confirm() -> np.ndarray:
    """A crisp, bright double-blip: "got it"."""
    return np.concatenate([
        _segment(500, 750, 0.07),
        _silence(0.03),
        _segment(650, 950, 0.07),
    ])


def _action() -> np.ndarray:
    """Three steady mid-pitch beeps: "working on it"."""
    beep, gap = _segment(550, 550, 0.06), _silence(0.04)
    return np.concatenate([beep, gap, beep, gap, beep])


def _pending() -> np.ndarray:
    """A soft two-note chime, gentler than confirm: "you have a message"."""
    return np.concatenate([
        _segment(400, 600, 0.15, wave_shape="sine", amp=0.4),
        _silence(0.06),
        _segment(600, 480, 0.15, wave_shape="sine", amp=0.4),
    ])


_BUILDERS = {"confused": _confused, "confirm": _confirm, "action": _action, "pending": _pending}
_pcm_cache: dict[str, bytes] = {}


def synth_pcm(kind: str) -> bytes:
    """int16 mono PCM at RATE for the given tone kind, built once and cached."""
    if kind not in _pcm_cache:
        samples = np.clip(_BUILDERS[kind](), -1.0, 1.0)
        _pcm_cache[kind] = (samples * 32767).astype(np.int16).tobytes()
    return _pcm_cache[kind]


def _ensure_wav(kind: str) -> Path:
    path = TONES_DIR / f"{kind}.wav"
    if not path.exists():
        TONES_DIR.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(synth_pcm(kind))
    return path


async def play_tone(conn, kind: str) -> None:
    """Play a tone on the board/client `conn` is connected to, as a short textless "reply"."""
    from core.handle.sendAudioHandle import sendAudioMessage
    from core.providers.tts.dto.dto import SentenceType
    from core.utils.util import audio_to_data

    opus_packets = await audio_to_data(str(_ensure_wav(kind)), use_cache=True)
    await sendAudioMessage(conn, SentenceType.FIRST, opus_packets, None)
    await sendAudioMessage(conn, SentenceType.LAST, [], None)
