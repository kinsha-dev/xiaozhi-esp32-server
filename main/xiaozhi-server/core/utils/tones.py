"""R2D2-style feedback tones: short synthesized beeps played instead of a full spoken reply.

Six kinds, each a few pitched square/sine-wave sweeps (classic R2D2 sound design, not realistic audio):
  confused - heard something, but it wasn't a reply worth speaking (IgnoreGate's "[ignore]" decision)
  confirm  - the whole reply was a bare acknowledgment not worth speaking (IgnoreGate's "[ack]" decision)
  action   - a confirmed action is now being carried out (the spoken result still follows)
  pending  - a new pending outbox message exists (Mac app only; this module still synthesizes it)
  wake     - a short "I heard you" the moment a turn is accepted, before the LLM
  thinking - one quiet, low dial-up-modem cycle, looped while the reply is prepared

Synthesized once with numpy. The board caches a tiny WAV under config/assets/tones/ whose name includes a
hash of the PCM, so a design change cannot keep playing the previous file. Playback of a tone that replaces
the reply follows helloHandle.checkWakeupWords: a new sentence id (so the rate controller resets and the
chat turn's own stop is not the one that ends this audio), then FIRST, then a single LAST. A tone played
in the middle of a turn (the action chirp, the wake ack, each thinking cycle) sends only the audio — the
reply's own LAST is the stop. The thinking loop never sends LAST: that stop would end the turn and the
board would drop the real reply.
"""
import asyncio
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


def _wake() -> np.ndarray:
    """A short, soft "I heard you": two rising sine notes, quieter than speech."""
    return np.concatenate([
        _segment(620, 780, 0.07, wave_shape="sine", amp=0.26),
        _silence(0.025),
        _segment(780, 960, 0.09, wave_shape="sine", amp=0.26),
    ])


def _thinking() -> np.ndarray:
    """One cycle of a dial-up handshake, several octaves down and very quiet.

    A low answer tone, a slow warble, a low two-tone shift, then a pause. The pause
    is part of the cached cycle so a loop breathes instead of droning. Sine only:
    a square wave here is the screech the handshake is known for.
    """
    soft = 0.10
    return np.concatenate([
        _segment(210, 210, 0.16, wave_shape="sine", amp=soft),
        _segment(220, 320, 0.22, wave_shape="sine", vibrato_hz=6, vibrato_depth=12, amp=soft),
        _segment(320, 180, 0.16, wave_shape="sine", amp=soft),
        _silence(0.04),
        _segment(260, 240, 0.09, wave_shape="sine", amp=soft),
        _segment(170, 190, 0.09, wave_shape="sine", amp=soft),
        _segment(200, 280, 0.18, wave_shape="sine", vibrato_hz=8, vibrato_depth=18, amp=soft),
        _silence(0.42),
    ])


_BUILDERS = {
    "confused": _confused,
    "confirm": _confirm,
    "action": _action,
    "pending": _pending,
    "wake": _wake,
    "thinking": _thinking,
}
WAKE_ACK_FLAG = "enable_wake_ack_tone"
THINKING_FLAG = "enable_thinking_tone"
# The board enters speaking on tts "start" and resets its decoder as it does.
# Opus that arrives in that same burst is dropped. This pause lets that transition finish.
SPEAKING_SETTLE_S = 0.12
TAG = __name__
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


def tone_duration(kind: str) -> float:
    """Seconds of PCM for one cached cycle."""
    return len(synth_pcm(kind)) / (2 * RATE)


def _enabled(conn, key: str) -> bool:
    """Missing config means on. Only an explicit false turns a tone off."""
    from core.utils import audio_device

    if audio_device.tones_disabled():  # Dock menu "Disable tones"; read per call so a toggle applies at once
        return False
    cfg = getattr(conn, "config", None) or {}
    return cfg.get(key, True) is not False


def _log(conn, message: str) -> None:
    logger = getattr(conn, "logger", None)
    if logger is None:
        return
    try:
        logger.bind(tag=TAG).info(message)
    except Exception:
        pass


def _lock(conn) -> asyncio.Lock:
    lock = getattr(conn, "_feedback_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        conn._feedback_lock = lock
    return lock


def _stop_requested(conn) -> bool:
    ev = getattr(conn, "_feedback_stop", None)
    return ev is not None and ev.is_set()


def _drop_unsent_feedback(conn) -> None:
    """Drop tone packets the rate controller has not sent yet.

    reset() also cancels the background sender so a packet it already popped cannot
    go out after speech is queued. Packets already on the wire are in the board's
    buffer; that tail is one or two frames.
    """
    rc = getattr(conn, "audio_rate_controller", None)
    reset = getattr(rc, "reset", None)
    if reset is not None:
        reset()


async def play_tone(conn, kind: str, *, close_turn: bool = False) -> None:
    """Play a tone on the board/client `conn` is connected to.

    close_turn: the tone is the whole reply. Reset like the cached wakeup reply and send
    the single stop afterwards. Leave it false when a spoken result follows (the action
    chirp, the wake ack, a thinking cycle); a stop here would end that reply.

    Anything except a thinking cycle stops the thinking loop first, so the modem sound
    cannot overlap a closing tone or an action chirp. The loop's own cycles do not.
    """
    if getattr(conn, "_feedback_turn_open", False):
        return
    conn._feedback_sentence_ready = False
    if not (_enabled(conn, WAKE_ACK_FLAG) or _enabled(conn, THINKING_FLAG)):
        return
    if getattr(conn, "client_abort", False):
        return
    if getattr(conn, "tts", None) is None:
        # TTS is still initializing (lazy init); sendAudioMessage needs conn.tts.
        return
    if kind != "thinking":
        await stop_turn_feedback(conn)
        if getattr(conn, "client_abort", False):
            return
    async with _lock(conn):
        if getattr(conn, "client_abort", False):
            return
        if kind == "thinking" and _stop_requested(conn):
            return
        from core.handle.sendAudioHandle import sendAudioMessage
        from core.providers.tts.dto.dto import SentenceType
        from core.utils.util import audio_to_data

        opus_packets = await audio_to_data(str(_ensure_wav(kind)), use_cache=True)
        if getattr(conn, "client_abort", False) or (kind == "thinking" and _stop_requested(conn)):
            return
        if not close_turn:
            await sendAudioMessage(conn, SentenceType.MIDDLE, opus_packets, None)
            return
        begin_closing_tone(conn)
        await sendAudioMessage(conn, SentenceType.FIRST, opus_packets, None)
        await sendAudioMessage(conn, SentenceType.LAST, [], None)


def take_prepared_sentence_id(conn):
    """Sentence id open_feedback_turn claimed for this turn, once.

    chat() must keep it. Minting a new one resets the rate controller and cuts off
    the wake ack or the thinking loop. None when this turn did not open feedback.
    """
    if getattr(conn, "_feedback_sentence_ready", False) and getattr(conn, "sentence_id", None):
        conn._feedback_sentence_ready = False
        return conn.sentence_id
    return None


async def _flush_send_queue(conn) -> None:
    """Wait until queued opus has been handed to the socket, so a later reset cannot eat it."""
    rc = getattr(conn, "audio_rate_controller", None)
    ev = getattr(rc, "queue_empty_event", None) if rc is not None else None
    if ev is None or ev.is_set():
        return
    try:
        await asyncio.wait_for(ev.wait(), timeout=2)
    except asyncio.TimeoutError:
        _log(conn, "wake ack flush timed out")


async def play_wake_ack(conn, *, flush: bool = True) -> None:
    """One wake ack. No LAST, no sentence-id change, no second play this turn."""
    if getattr(conn, "_wake_ack_played", False) or not _enabled(conn, WAKE_ACK_FLAG):
        return
    if getattr(conn, "client_abort", False):
        return
    await play_tone(conn, "wake")
    if getattr(conn, "client_abort", False):
        return
    conn._wake_ack_played = True
    _log(conn, "wake ack")
    if flush:
        await _flush_send_queue(conn)


def _loop_done(conn, ev, gen) -> bool:
    return (
        ev.is_set()
        or getattr(conn, "client_abort", False)
        or getattr(conn, "_feedback_generation", 0) != gen
    )


async def _thinking_loop(conn, ev, gen) -> None:
    try:
        while not _loop_done(conn, ev, gen):
            await play_tone(conn, "thinking")
            if _loop_done(conn, ev, gen):
                return
            try:
                await asyncio.wait_for(ev.wait(), max(tone_duration("thinking"), 0.001))
            except asyncio.TimeoutError:
                continue
    except asyncio.CancelledError:
        raise


async def _start_thinking_loop(conn) -> None:
    if not _enabled(conn, THINKING_FLAG) or getattr(conn, "client_abort", False):
        return
    ev = asyncio.Event()
    conn._feedback_stop = ev
    gen = getattr(conn, "_feedback_generation", 0)
    conn._feedback_task = asyncio.create_task(_thinking_loop(conn, ev, gen))
    _log(conn, "thinking tone started")


async def begin_turn_feedback(conn) -> None:
    """Wake ack, then the thinking loop. Neither sends LAST or rotates sentence_id.

    A barge-in skips playback and leaves client_abort set. The ack is flushed onto
    the wire before the loop starts, so a later reset (cached wakeup greeting, or
    the first spoken packet) cannot discard it.
    """
    if getattr(conn, "client_abort", False):
        return
    if _enabled(conn, WAKE_ACK_FLAG):
        await play_wake_ack(conn, flush=True)
    if getattr(conn, "client_abort", False):
        return
    await _start_thinking_loop(conn)


async def open_feedback_turn(conn) -> None:
    """Put the board in speaking and start feedback before the LLM call.

    The board plays binary opus only while it is speaking, and entering speaking
    resets the decoder. "start" goes out first, then a short settle, then the ack.
    Claims sentence_id so chat() will not rotate it out from under the loop.
    """
    if getattr(conn, "_feedback_turn_open", False):
        return
    conn._feedback_sentence_ready = False
    if not (_enabled(conn, WAKE_ACK_FLAG) or _enabled(conn, THINKING_FLAG)):
        return
    if getattr(conn, "client_abort", False):
        return
    conn._feedback_turn_open = True
    conn.sentence_id = uuid.uuid4().hex
    conn._feedback_sentence_ready = True
    from core.handle.sendAudioHandle import send_tts_message

    await send_tts_message(conn, "start")
    conn.client_is_speaking = True
    if SPEAKING_SETTLE_S:
        await asyncio.sleep(SPEAKING_SETTLE_S)
    if getattr(conn, "client_abort", False):
        return
    await begin_turn_feedback(conn)


async def stop_turn_feedback(conn) -> None:
    """Stop the thinking loop and drop tone packets that have not been sent.

    Idempotent. Does not send tts stop, does not rotate sentence_id, and does not
    clear client_abort. A second call leaves audio queued by the real reply alone:
    the queue is cleared only while the loop is still the thing filling it.
    """
    task = getattr(conn, "_feedback_task", None)
    ev = getattr(conn, "_feedback_stop", None)
    running = task is not None and not task.done()
    pending = ev is not None and not ev.is_set()
    if not running and not pending:
        return
    conn._feedback_generation = getattr(conn, "_feedback_generation", 0) + 1
    if ev is not None and not ev.is_set():
        ev.set()
    if running and task is not asyncio.current_task():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        if getattr(conn, "_feedback_task", None) is task:
            conn._feedback_task = None
    _log(conn, "thinking tone stopped")
    async with _lock(conn):
        _drop_unsent_feedback(conn)


async def release_for_cached_wakeup(conn) -> bool:
    """Stop thinking before a cached wakeup greeting.

    False when this turn already started feedback and the user barged in: the
    greeting must not clear client_abort and talk over them. True otherwise,
    including a stale abort from before this turn (the greeting clears that).
    """
    barged = bool(getattr(conn, "_feedback_turn_open", False) and getattr(conn, "client_abort", False))
    await stop_turn_feedback(conn)
    return not barged


def stop_turn_feedback_threadsafe(conn) -> None:
    """stop_turn_feedback from the chat thread, when the LLM fails and no audio will follow."""
    loop = getattr(conn, "loop", None)
    if loop is None or not loop.is_running():
        return
    try:
        asyncio.run_coroutine_threadsafe(stop_turn_feedback(conn), loop).result(timeout=2)
    except Exception:
        pass
