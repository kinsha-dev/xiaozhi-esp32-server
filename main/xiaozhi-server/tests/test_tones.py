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
    with wave.open(str(path)) as w:
        assert w.getframerate() == tones.RATE
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getnframes() > 0
    mtime = path.stat().st_mtime
    assert tones._ensure_wav("action") == path
    assert path.stat().st_mtime == mtime  # not rewritten once cached on disk


class FakeTTS:
    tts_audio_first_sentence = False


class FakeConn:
    def __init__(self):
        self.tts = FakeTTS()
        self.sentence_id = "sid"
        self.calling = False
        self.close_after_chat = False
        self.sent = []
        self.logger = _NullLogger()


class _NullLogger:
    def bind(self, **_kw):
        return self

    def info(self, *_a, **_kw):
        pass


async def test_play_tone_sends_first_then_last(tmp_path, monkeypatch):
    monkeypatch.setattr(tones, "TONES_DIR", tmp_path)
    sent = []

    async def fake_send(conn, sentence_type, audios, text, sentence_id=None):
        sent.append((sentence_type, audios, text))

    async def fake_audio_to_data(path, use_cache=True):
        return [b"opus1", b"opus2"]

    monkeypatch.setattr(send_audio_handle, "sendAudioMessage", fake_send)
    monkeypatch.setattr(util_mod, "audio_to_data", fake_audio_to_data)

    await tones.play_tone(FakeConn(), "confused")

    assert [s[0] for s in sent] == [SentenceType.FIRST, SentenceType.LAST]
    assert sent[0][1] == [b"opus1", b"opus2"]
    assert sent[1][1] == []
    assert all(s[2] is None for s in sent)  # no text: not a conversational reply


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
