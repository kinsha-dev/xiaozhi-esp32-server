"""R2D2-style feedback tones: short synthesized beeps played instead of a full spoken reply.

Four kinds, each a few pitched square/sine-wave sweeps (classic R2D2 sound design, not realistic audio):
  confused - heard something, but it wasn't a reply worth speaking (IgnoreGate's "[ignore]" decision)
  confirm  - the whole reply was a bare acknowledgment not worth speaking (IgnoreGate's "[ack]" decision)
  action   - a confirmed action is now being carried out (the spoken result still follows)
  pending  - a new pending outbox message exists (Mac app only; this module still synthesizes it)

Synthesized once with numpy. The board caches a tiny WAV under config/assets/tones/ whose name includes a
hash of the PCM, so a design change cannot keep playing the previous file. Playback of a tone that replaces
the reply follows helloHandle.checkWakeupWords: a new sentence id (so the rate controller resets and the
chat turn's own stop is not the one that ends this audio), then FIRST, then a single LAST. A tone played
in the middle of a turn (the action chirp) sends only the audio — the reply's own LAST is the stop.
"""
import hashlib
import uuid
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
    """A soft, smooth sine "hm?": a gentle dip then rise, no harsh square wave or vibrato."""
    return np.concatenate([
        _segment(520, 420, 0.12, wave_shape="sine", amp=0.3),
        _segment(420, 540, 0.14, wave_shape="sine", amp=0.3),
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
    """Write the tone if this PCM has not been cached. The hash is the cache key, so a changed
    design writes a new file and the previous one is removed. audio_to_data keys its opus cache
    on path plus mtime, so the new file is a cache miss there too."""
    pcm = synth_pcm(kind)
    digest = hashlib.sha256(pcm).hexdigest()[:12]
    path = TONES_DIR / f"{kind}-{digest}.wav"
    TONES_DIR.mkdir(parents=True, exist_ok=True)
    for old in list(TONES_DIR.glob(f"{kind}.wav")) + list(TONES_DIR.glob(f"{kind}-*.wav")):
        if old != path:
            old.unlink(missing_ok=True)
    if not path.exists():
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm)
    return path


_CLOSING = {"ignore": "confused", "ack": "confirm"}


def begin_closing_tone(conn) -> None:
    """Reset the turn the way helloHandle.checkWakeupWords does before a cached wakeup reply.

    A new sentence_id forces the opus rate controller to restart, and a stop already queued
    for the previous id is dropped by the TTS thread instead of ending this tone early.
    client_abort is cleared so a leftover flag from the previous turn cannot swallow the
    packets. Callers must not use this after a barge-in on the current turn: clearing that
    flag would talk over the user.
    """
    conn.client_abort = False
    conn.sentence_id = uuid.uuid4().hex


def take_closing_tone(conn, matched, aborted: bool):
    """Kind of tone that replaces a bare [ignore] or [ack], after begin_closing_tone.

    None when the reply should be spoken, or the user already barged in. The caller must not
    queue its own SentenceType.LAST after a non-None return: play_tone(..., close_turn=True)
    sends that stop, and a second one either cuts the tone off or ends the turn twice.
    """
    if aborted or matched not in _CLOSING:
        return None
    begin_closing_tone(conn)
    return _CLOSING[matched]


async def play_tone(conn, kind: str, *, close_turn: bool = False) -> None:
    """Play a tone on the board/client `conn` is connected to.

    close_turn: the tone is the whole reply. Reset like the cached wakeup reply and send
    the single stop afterwards. Leave it false when a spoken result follows (the action
    chirp); a stop here would end that reply.
    """
    if getattr(conn, "client_abort", False):
        return
    from core.handle.sendAudioHandle import sendAudioMessage
    from core.providers.tts.dto.dto import SentenceType
    from core.utils.util import audio_to_data

    opus_packets = await audio_to_data(str(_ensure_wav(kind)), use_cache=True)
    if getattr(conn, "client_abort", False):
        return
    if not close_turn:
        await sendAudioMessage(conn, SentenceType.MIDDLE, opus_packets, None)
        return
    begin_closing_tone(conn)
    await sendAudioMessage(conn, SentenceType.FIRST, opus_packets, None)
    await sendAudioMessage(conn, SentenceType.LAST, [], None)
