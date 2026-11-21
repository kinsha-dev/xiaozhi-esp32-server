import asyncio
import wave
from collections import deque

import numpy as np

import core.handle.receiveAudioHandle as receive_audio
import core.handle.sendAudioHandle as send_audio_handle
import core.utils.util as util_mod
from core.handle import helloHandle
from core.providers.tts.dto.dto import SentenceType
from core.utils import tones


async def _real_sleep(seconds: float) -> None:
    """conftest replaces asyncio.sleep with a zero-yield. Feedback timing needs the real clock."""
    await asyncio._real_sleep(seconds)


def test_synth_pcm_is_int16_mono_and_cached():
    pcm = tones.synth_pcm("action")
    assert isinstance(pcm, bytes) and len(pcm) % 2 == 0
    samples = np.frombuffer(pcm, dtype=np.int16)
    assert samples.size > 0
    assert tones.synth_pcm("action") is pcm  # cached, not rebuilt


_KINDS = ("confused", "confirm", "action", "pending", "wake", "thinking")


def test_every_kind_synthesizes_without_clipping_errors():
    for kind in _KINDS:
        samples = np.frombuffer(tones.synth_pcm(kind), dtype=np.int16)
        assert samples.size > 0
        assert np.abs(samples).max() <= 32767


def test_kinds_are_acoustically_distinct():
    pcms = {k: tones.synth_pcm(k) for k in _KINDS}
    assert len({v for v in pcms.values()}) == len(pcms)


def test_wake_ack_is_short_and_pleasant():
    samples = np.frombuffer(tones.synth_pcm("wake"), dtype=np.int16)
    seconds = samples.size / tones.RATE
    peak = int(np.abs(samples).max())
    assert 0.1 < seconds < 0.4
    assert int(0.15 * 32767) <= peak <= int(0.40 * 32767)


def test_thinking_is_a_low_quiet_modem_cycle_with_a_pause():
    """A dial-up handshake dropped in pitch and level: quiet, low, then a gap so the loop can breathe."""
    samples = np.frombuffer(tones.synth_pcm("thinking"), dtype=np.int16).astype(np.float32)
    seconds = samples.size / tones.RATE
    peak = int(np.max(np.abs(samples)))
    wake_peak = int(np.max(np.abs(np.frombuffer(tones.synth_pcm("wake"), dtype=np.int16))))
    assert 0.7 < seconds < 1.6
    assert peak <= int(0.16 * 32767)
    assert peak < wake_peak
    tail = samples[-int(tones.RATE * 0.3):]
    assert np.max(np.abs(tail)) == 0  # the pause is part of the cached cycle

    audible = samples[: samples.size - int(tones.RATE * 0.3)]
    audible = audible - np.mean(audible)
    spec = np.abs(np.fft.rfft(audible))
    freqs = np.fft.rfftfreq(audible.size, 1 / tones.RATE)
    power = spec ** 2
    power[0] = 0  # DC is not pitch
    centroid = float(np.sum(freqs * power) / np.sum(power))
    assert centroid < 500  # low, not a screechy high handshake


def test_ensure_wav_writes_a_valid_playable_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tones, "TONES_DIR", tmp_path)
    path = tones._ensure_wav("action")
    assert path.exists()
    assert path.name.startswith("action-")  # hash of the PCM, so a design change cannot reuse a stale file
    with wave.open(str(path)) as w:
        assert w.getframerate() == tones.RATE
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getnframes() > 0
    mtime = path.stat().st_mtime
    assert tones._ensure_wav("action") == path
    assert path.stat().st_mtime == mtime  # not rewritten once cached on disk


def test_ensure_wav_rebuilds_and_drops_stale_files_when_the_tone_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(tones, "TONES_DIR", tmp_path)
    tones._pcm_cache.pop("action", None)
    first = tones._ensure_wav("action")
    stale = tmp_path / "action.wav"
    stale.write_bytes(b"stale")
    original = tones._BUILDERS["action"]
    tones._pcm_cache.pop("action", None)
    tones._BUILDERS["action"] = lambda: np.ones(8) * 0.2
    try:
        second = tones._ensure_wav("action")
    finally:
        tones._BUILDERS["action"] = original
        tones._pcm_cache.pop("action", None)
    assert second != first
    assert second.exists() and second.name.startswith("action-")
    assert not first.exists()
    assert not stale.exists()


class FakeTTS:
    tts_audio_first_sentence = False


class FakeConn:
    def __init__(self):
        self.tts = FakeTTS()
        self.sentence_id = "sid"
        self.client_abort = False
        self.calling = False
        self.close_after_chat = False
        self.sent = []
        self.logger = _NullLogger()


class _NullLogger:
    def bind(self, **_kw):
        return self

    def info(self, *_a, **_kw):
        pass


async def test_closing_tone_resets_the_turn_and_sends_one_stop(tmp_path, monkeypatch):
    """A tone that replaces the reply follows helloHandle: new sentence id, then FIRST, then the only stop."""
    monkeypatch.setattr(tones, "TONES_DIR", tmp_path)
    sent = []

    async def fake_send(conn, sentence_type, audios, text, sentence_id=None):
        sent.append((sentence_type, audios, text))

    async def fake_audio_to_data(path, use_cache=True):
        return [b"opus1", b"opus2"]

    monkeypatch.setattr(send_audio_handle, "sendAudioMessage", fake_send)
    monkeypatch.setattr(util_mod, "audio_to_data", fake_audio_to_data)

    conn = FakeConn()
    await tones.play_tone(conn, "confused", close_turn=True)

    assert conn.sentence_id != "sid"  # flow controller resets, same as the cached wakeup reply
    assert conn.client_abort is False
    assert [s[0] for s in sent] == [SentenceType.FIRST, SentenceType.LAST]
    assert sent[0][1] == [b"opus1", b"opus2"]
    assert sent[1][1] == []
    assert all(s[2] is None for s in sent)  # no text: not a conversational reply


async def test_in_turn_tone_does_not_send_the_reply_stop(tmp_path, monkeypatch):
    """The action chirp is followed by a spoken result. Its own stop would end that reply."""
    monkeypatch.setattr(tones, "TONES_DIR", tmp_path)
    sent = []

    async def fake_send(conn, sentence_type, audios, text, sentence_id=None):
        sent.append((sentence_type, audios, text))

    async def fake_audio_to_data(path, use_cache=True):
        return [b"opus1", b"opus2"]

    monkeypatch.setattr(send_audio_handle, "sendAudioMessage", fake_send)
    monkeypatch.setattr(util_mod, "audio_to_data", fake_audio_to_data)

    conn = FakeConn()
    await tones.play_tone(conn, "action")

    assert conn.sentence_id == "sid"
    assert [s[0] for s in sent] == [SentenceType.MIDDLE]
    assert sent[0][1] == [b"opus1", b"opus2"]


async def test_closing_tone_does_not_play_over_a_barge_in(tmp_path, monkeypatch):
    monkeypatch.setattr(tones, "TONES_DIR", tmp_path)
    sent = []

    async def fake_send(conn, sentence_type, audios, text, sentence_id=None):
        sent.append(sentence_type)

    async def fake_audio_to_data(path, use_cache=True):
        return [b"opus"]

    monkeypatch.setattr(send_audio_handle, "sendAudioMessage", fake_send)
    monkeypatch.setattr(util_mod, "audio_to_data", fake_audio_to_data)

    conn = FakeConn()
    conn.client_abort = True
    await tones.play_tone(conn, "confused", close_turn=True)

    assert sent == []
    assert conn.sentence_id == "sid"
    assert conn.client_abort is True


def test_take_closing_tone_resets_before_the_caller_queues_its_own_stop():
    conn = FakeConn()
    assert tones.take_closing_tone(conn, "ignore", aborted=False) == "confused"
    assert conn.sentence_id != "sid"
    rotated = conn.sentence_id
    assert tones.take_closing_tone(conn, "ack", aborted=False) == "confirm"
    assert conn.sentence_id != rotated


def test_take_closing_tone_leaves_speech_and_aborts_alone():
    conn = FakeConn()
    assert tones.take_closing_tone(conn, None, aborted=False) is None
    conn.client_abort = True
    assert tones.take_closing_tone(conn, "ignore", aborted=True) is None
    assert conn.sentence_id == "sid"
    assert conn.client_abort is True  # a barge-in is not cleared; the wakeup-reply reset is for a fresh playback


async def test_play_tone_works_for_every_kind(tmp_path, monkeypatch):
    monkeypatch.setattr(tones, "TONES_DIR", tmp_path)

    async def fake_send(conn, sentence_type, audios, text, sentence_id=None):
        pass

    async def fake_audio_to_data(path, use_cache=True):
        return [b"opus"]

    monkeypatch.setattr(send_audio_handle, "sendAudioMessage", fake_send)
    monkeypatch.setattr(util_mod, "audio_to_data", fake_audio_to_data)

    for kind in _KINDS:
        await tones.play_tone(FakeConn(), kind)


def _patch_playback(monkeypatch, tmp_path, sent):
    monkeypatch.setattr(tones, "TONES_DIR", tmp_path)
    monkeypatch.setattr(tones, "SPEAKING_SETTLE_S", 0)

    async def fake_send(conn, sentence_type, audios, text, sentence_id=None):
        sent.append((sentence_type, audios, text))

    async def fake_audio_to_data(path, use_cache=True):
        kind = path.rsplit("/", 1)[-1].split("-", 1)[0]
        sent.append(("kind", kind, None))
        return [b"opus"]

    monkeypatch.setattr(send_audio_handle, "sendAudioMessage", fake_send)
    monkeypatch.setattr(util_mod, "audio_to_data", fake_audio_to_data)


def _short_thinking():
    """A tiny cycle so the loop can repeat inside a test without a second of modem audio."""
    original = tones._BUILDERS["thinking"]
    tones._pcm_cache.pop("thinking", None)
    tones._BUILDERS["thinking"] = lambda: np.concatenate([np.ones(16) * 0.05, np.zeros(16)])

    def restore():
        tones._BUILDERS["thinking"] = original
        tones._pcm_cache.pop("thinking", None)

    return restore


class _Queue:
    def __init__(self):
        self.queue = deque()
        self.resets = 0
        self.pending_send_task = None
        self.queue_empty_event = asyncio.Event()
        self.queue_empty_event.set()

    def reset(self):
        self.resets += 1
        self.queue.clear()


async def test_begin_plays_wake_ack_then_loops_thinking_without_closing_the_turn(tmp_path, monkeypatch):
    sent = []
    _patch_playback(monkeypatch, tmp_path, sent)
    restore = _short_thinking()
    conn = FakeConn()
    conn.config = {}
    try:
        await tones.begin_turn_feedback(conn)
        await _real_sleep(0.03)
        kinds = [item[1] for item in sent if item[0] == "kind"]
        assert kinds[0] == "wake"
        assert "thinking" in kinds
        assert kinds.count("thinking") >= 2  # it repeats while the reply is still being prepared
        assert SentenceType.LAST not in [item[0] for item in sent]
        assert SentenceType.FIRST not in [item[0] for item in sent]
        assert conn.sentence_id == "sid"  # chat owns the id; a tone must not rotate it
        assert conn.client_abort is False
    finally:
        await tones.stop_turn_feedback(conn)
        restore()


async def test_thinking_stops_and_does_not_keep_looping_or_end_the_turn(tmp_path, monkeypatch):
    sent = []
    _patch_playback(monkeypatch, tmp_path, sent)
    restore = _short_thinking()
    conn = FakeConn()
    conn.config = {"enable_wake_ack_tone": False}
    conn.audio_rate_controller = _Queue()
    conn.audio_rate_controller.queue.append(("audio", b"think"))
    try:
        await tones.begin_turn_feedback(conn)
        await _real_sleep(0.01)
        queued_before = len(sent)
        await tones.stop_turn_feedback(conn)
        assert conn.audio_rate_controller.resets == 1
        assert list(conn.audio_rate_controller.queue) == []
        await _real_sleep(0.03)
        assert len(sent) - queued_before <= 1  # one cycle already inside send may finish; the loop does not
        assert SentenceType.LAST not in [item[0] for item in sent]
        conn.audio_rate_controller.queue.append(("audio", b"speech"))
        await tones.stop_turn_feedback(conn)  # idempotent: speech already queued must stay
        assert conn.audio_rate_controller.resets == 1
        assert list(conn.audio_rate_controller.queue) == [("audio", b"speech")]
        assert conn.sentence_id == "sid"
        assert conn.client_abort is False
    finally:
        await tones.stop_turn_feedback(conn)
        restore()


async def test_stop_without_a_thinking_loop_does_not_drop_queued_audio():
    conn = FakeConn()
    conn.audio_rate_controller = _Queue()
    conn.audio_rate_controller.queue.append(("audio", b"wake"))
    await tones.stop_turn_feedback(conn)
    assert conn.audio_rate_controller.resets == 0
    assert list(conn.audio_rate_controller.queue) == [("audio", b"wake")]


async def test_barge_in_skips_feedback_and_is_not_cleared(tmp_path, monkeypatch):
    sent = []
    _patch_playback(monkeypatch, tmp_path, sent)
    conn = FakeConn()
    conn.client_abort = True
    await tones.begin_turn_feedback(conn)
    await tones.open_feedback_turn(conn)
    assert sent == []
    assert conn.client_abort is True
    assert conn.sentence_id == "sid"


async def test_thinking_loop_stops_itself_when_the_turn_is_aborted(tmp_path, monkeypatch):
    sent = []
    _patch_playback(monkeypatch, tmp_path, sent)
    restore = _short_thinking()
    conn = FakeConn()
    conn.config = {"enable_wake_ack_tone": False}
    try:
        await tones.begin_turn_feedback(conn)
        await _real_sleep(0.01)
        conn.client_abort = True
        n = len(sent)
        await _real_sleep(0.03)
        assert len(sent) - n <= 1
        assert conn.client_abort is True
    finally:
        await tones.stop_turn_feedback(conn)
        restore()


async def test_flags_toggle_each_tone(tmp_path, monkeypatch):
    sent = []
    _patch_playback(monkeypatch, tmp_path, sent)
    restore = _short_thinking()
    try:
        muted = FakeConn()
        muted.config = {"enable_wake_ack_tone": False, "enable_thinking_tone": False}
        await tones.begin_turn_feedback(muted)
        await _real_sleep(0.01)
        assert sent == []

        wake_only = FakeConn()
        wake_only.config = {"enable_thinking_tone": False}
        await tones.begin_turn_feedback(wake_only)
        kinds = [item[1] for item in sent if item[0] == "kind"]
        assert kinds == ["wake"]

        sent.clear()
        think_only = FakeConn()
        think_only.config = {"enable_wake_ack_tone": False}
        await tones.begin_turn_feedback(think_only)
        await _real_sleep(0.02)
        kinds = [item[1] for item in sent if item[0] == "kind"]
        assert kinds and set(kinds) == {"thinking"}
        await tones.stop_turn_feedback(think_only)
    finally:
        restore()


async def test_missing_config_leaves_both_tones_on(tmp_path, monkeypatch):
    sent = []
    _patch_playback(monkeypatch, tmp_path, sent)
    restore = _short_thinking()
    conn = FakeConn()  # no config attribute: same as a deployment that never set the flags
    try:
        await tones.begin_turn_feedback(conn)
        await _real_sleep(0.02)
        kinds = [item[1] for item in sent if item[0] == "kind"]
        assert "wake" in kinds and "thinking" in kinds
    finally:
        await tones.stop_turn_feedback(conn)
        restore()


async def test_closing_tone_stops_the_loop_and_still_sends_exactly_one_stop(tmp_path, monkeypatch):
    sent = []
    _patch_playback(monkeypatch, tmp_path, sent)
    restore = _short_thinking()
    conn = FakeConn()
    try:
        await tones.begin_turn_feedback(conn)
        await _real_sleep(0.01)
        await tones.play_tone(conn, "confused", close_turn=True)
        await _real_sleep(0.03)
        types = [item[0] for item in sent]
        assert types.count(SentenceType.LAST) == 1
        assert types[-2] == SentenceType.FIRST
        assert types[-1] == SentenceType.LAST
        assert conn.client_abort is False
    finally:
        await tones.stop_turn_feedback(conn)
        restore()


async def test_real_audio_stops_thinking_before_speech_is_sent(tmp_path, monkeypatch):
    """The first real reply packet goes through sendAudioMessage, which stops the loop first."""
    monkeypatch.setattr(tones, "TONES_DIR", tmp_path)
    restore = _short_thinking()
    audio_calls = []

    async def fake_audio_to_data(path, use_cache=True):
        return [b"opus"]

    async def fake_send_audio(conn, audios, frame_duration=60):
        audio_calls.append(list(audios))

    monkeypatch.setattr(util_mod, "audio_to_data", fake_audio_to_data)
    monkeypatch.setattr(send_audio_handle, "sendAudio", fake_send_audio)

    conn = FakeConn()
    conn.config = {"enable_wake_ack_tone": False}
    try:
        await tones.begin_turn_feedback(conn)
        await _real_sleep(0.01)
        task = conn._feedback_task
        assert task is not None and not task.done()
        await send_audio_handle.sendAudioMessage(conn, SentenceType.MIDDLE, [b"speech"], None)
        end = len(audio_calls)
        assert audio_calls[-1] == [b"speech"]
        assert task.done()
        await _real_sleep(0.03)
        assert len(audio_calls) == end  # the loop did not queue another cycle after speech
    finally:
        await tones.stop_turn_feedback(conn)
        restore()


def test_prepared_sentence_id_is_used_once():
    conn = FakeConn()
    conn.sentence_id = "claimed"
    conn._feedback_sentence_ready = True
    assert tones.take_prepared_sentence_id(conn) == "claimed"
    assert conn._feedback_sentence_ready is False
    assert tones.take_prepared_sentence_id(conn) is None


async def test_open_feedback_sends_speaking_start_before_the_ack_and_claims_the_sentence(tmp_path, monkeypatch):
    sent = []
    _patch_playback(monkeypatch, tmp_path, sent)
    restore = _short_thinking()
    order = []

    async def fake_tts(conn, state, text=None):
        order.append(state)

    monkeypatch.setattr(send_audio_handle, "send_tts_message", fake_tts)
    conn = FakeConn()
    conn.config = {"enable_thinking_tone": False}
    try:
        await tones.open_feedback_turn(conn)
        assert order[0] == "start"
        assert conn.client_is_speaking is True
        assert conn.sentence_id != "sid"
        assert tones.take_prepared_sentence_id(conn) == conn.sentence_id
        kinds = [item[1] for item in sent if item[0] == "kind"]
        assert kinds == ["wake"]
        assert SentenceType.LAST not in [item[0] for item in sent]
    finally:
        restore()


async def test_open_feedback_does_nothing_when_both_tones_are_off(tmp_path, monkeypatch):
    sent = []
    _patch_playback(monkeypatch, tmp_path, sent)
    conn = FakeConn()
    conn.config = {"enable_wake_ack_tone": False, "enable_thinking_tone": False}
    await tones.open_feedback_turn(conn)
    assert sent == []
    assert conn.sentence_id == "sid"
    assert conn._feedback_sentence_ready is False


async def test_flush_waits_until_the_ack_has_been_sent():
    ev = asyncio.Event()

    class RC:
        queue_empty_event = ev

    conn = FakeConn()
    conn.audio_rate_controller = RC()
    task = asyncio.create_task(tones._flush_send_queue(conn))
    await asyncio.sleep(0.02)
    assert not task.done()
    ev.set()
    await task


async def test_cached_wakeup_does_not_talk_over_a_barge_in(tmp_path, monkeypatch):
    sent = []
    _patch_playback(monkeypatch, tmp_path, sent)

    async def fake_tts(conn, state, text=None):
        sent.append(("tts", state, text))

    monkeypatch.setattr(helloHandle, "send_tts_message", fake_tts)
    monkeypatch.setattr(helloHandle, "sendAudioMessage", send_audio_handle.sendAudioMessage)
    monkeypatch.setattr(helloHandle, "audio_to_data", util_mod.audio_to_data)

    conn = FakeConn()
    conn.config = {
        "enable_wakeup_words_response_cache": True,
        "wakeup_words": ["hivibi"],
        "enable_thinking_tone": False,
    }
    conn._feedback_turn_open = True
    conn.client_abort = True
    conn.dialogue = type("D", (), {"put": lambda *a, **k: None})()

    assert await helloHandle.checkWakeupWords(conn, "hivibi") is True
    assert conn.client_abort is True
    assert sent == []
    assert conn.sentence_id == "sid"


async def test_cached_wakeup_plays_the_ack_before_the_greeting(tmp_path, monkeypatch):
    sent = []
    _patch_playback(monkeypatch, tmp_path, sent)

    async def fake_tts(conn, state, text=None):
        sent.append(("tts", state, None))

    monkeypatch.setattr(helloHandle, "send_tts_message", fake_tts)
    monkeypatch.setattr(helloHandle, "sendAudioMessage", send_audio_handle.sendAudioMessage)
    monkeypatch.setattr(helloHandle, "audio_to_data", util_mod.audio_to_data)
    monkeypatch.setattr(
        helloHandle.wakeup_words_config,
        "get_wakeup_response",
        lambda voice: {"voice": voice, "file_path": "cached.wav", "time": 10**12, "text": "I am here"},
    )

    conn = FakeConn()
    conn.config = {
        "enable_wakeup_words_response_cache": True,
        "wakeup_words": ["hivibi"],
        "enable_thinking_tone": False,
    }
    conn.dialogue = type("D", (), {"put": lambda *a, **k: None})()

    assert await helloHandle.checkWakeupWords(conn, "hivibi") is True
    await asyncio.sleep(0)  # the greeting's single stop is queued as a task; conftest sleep still yields
    kinds = [item[1] for item in sent if item[0] == "kind"]
    assert kinds[0] == "wake"
    types = [item[0] for item in sent]
    assert types.count(SentenceType.LAST) == 1
    assert conn.client_abort is False


async def test_name_filter_pass_opens_feedback_before_the_llm(monkeypatch):
    rah = receive_audio
    order = []

    async def fake_open(conn):
        order.append("feedback")

    async def fake_intent(conn, text):
        order.append("intent")
        return True

    monkeypatch.setattr(rah, "handle_user_intent", fake_intent)
    monkeypatch.setattr("core.utils.tones.open_feedback_turn", fake_open)
    monkeypatch.setattr("core.utils.audio_device.muted", lambda: False)
    monkeypatch.setattr("core.utils.name_filter._awaiting", lambda now: False)

    def conn():
        c = FakeConn()
        c.headers = {"device-id": "board-1"}
        c.config = {}
        c.client_listen_mode = "auto"
        c.client_is_speaking = False
        c.need_bind = False
        c.max_output_size = 0
        c.introduced_speakers = set()
        c.close_after_chat = False
        return c

    await rah.startToChat(conn(), "hivibi")
    assert order == ["feedback", "intent"]

    order.clear()
    await rah.startToChat(conn(), "the television said hello")
    assert order == []

    manual = conn()
    manual.client_listen_mode = "manual"
    await rah.startToChat(manual, "hello there")
    assert order == ["intent"]  # push-to-talk is not the name-filter / wake-ack path
