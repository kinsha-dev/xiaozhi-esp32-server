import wave

import numpy as np

import core.handle.sendAudioHandle as send_audio_handle
import core.utils.util as util_mod
from core.providers.tts.dto.dto import SentenceType
from core.utils import tones


def test_synth_pcm_is_int16_mono_and_cached():
    pcm = tones.synth_pcm("action")
    assert isinstance(pcm, bytes) and len(pcm) % 2 == 0
    samples = np.frombuffer(pcm, dtype=np.int16)
    assert samples.size > 0
    assert tones.synth_pcm("action") is pcm  # cached, not rebuilt


def test_every_kind_synthesizes_without_clipping_errors():
    for kind in ("confused", "confirm", "action", "pending"):
        samples = np.frombuffer(tones.synth_pcm(kind), dtype=np.int16)
        assert samples.size > 0
        assert np.abs(samples).max() <= 32767


def test_kinds_are_acoustically_distinct():
    pcms = {k: tones.synth_pcm(k) for k in ("confused", "confirm", "action", "pending")}
    assert len({v for v in pcms.values()}) == len(pcms)


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

    for kind in ("confused", "confirm", "action", "pending"):
        await tones.play_tone(FakeConn(), kind)
