import numpy as np
import pytest

from mac_app.wake import WakeDetector, matches_wake

FRAME = 960  # 30 ms at 16 kHz int16
SPEECH = (np.sin(np.linspace(0, 2 * np.pi * 200, FRAME // 2)) * 12000).astype(np.int16).tobytes()
SILENCE = bytes(FRAME)


@pytest.mark.parametrize("text", ["Hi Bibi.", "hey vibi", "Hi, bee bee!", "hi vivi", "Hi, baby.", "hi bibi what time is it"])
def test_matches(text):
    assert matches_wake(text)


@pytest.mark.parametrize("text", ["Thank you.", "", "hi there", "bibi", "say hi bibi", "you"])
def test_no_match(text):
    assert not matches_wake(text)


class FakeVad:
    """Treats a frame as speech iff it is non-zero."""

    def is_speech(self, frame, rate):
        return any(frame)


def make(text="hi bibi"):
    calls = []

    def transcribe(audio):
        calls.append(audio)
        return text

    return WakeDetector(transcribe, "hi bibi", vad=FakeVad()), calls


def feed_all(det, frames):
    return [det.feed(f) for f in frames]


def test_speech_then_silence_matches_once():
    det, calls = make()
    out = feed_all(det, [SPEECH] * 15 + [SILENCE] * 25)
    assert out.count(True) == 1 and len(calls) == 1
    assert calls[0].dtype == np.float32


def test_silence_never_transcribes():
    det, calls = make()
    assert not any(feed_all(det, [SILENCE] * 100))
    assert calls == []


def test_non_matching_segment_returns_false():
    det, calls = make("thank you")
    assert not any(feed_all(det, [SPEECH] * 15 + [SILENCE] * 25))
    assert len(calls) == 1


def test_long_segment_truncated_to_last_3s():
    det, calls = make()
    feed_all(det, [SPEECH] * 200 + [SILENCE] * 25)  # 6 s of speech
    assert len(calls[0]) <= 3 * 16000


def test_reset_between_segments():
    det, calls = make()
    out = feed_all(det, ([SPEECH] * 15 + [SILENCE] * 25) * 2)
    assert out.count(True) == 2


def test_preroll_keeps_audio_before_speech_onset():
    det, calls = make()
    feed_all(det, [SILENCE] * 30 + [SPEECH] * 15 + [SILENCE] * 25)
    assert len(calls[0]) >= (15 + 10) * 480  # speech + >=10 frames (300 ms) of pre-roll


def test_quiet_audio_is_normalised_for_whisper():
    quiet = (np.sin(np.linspace(0, 2 * np.pi * 200, FRAME // 2)) * 2000).astype(np.int16).tobytes()
    det, calls = make()
    feed_all(det, [quiet] * 15 + [SILENCE] * 25)
    assert np.abs(calls[0]).max() > 0.3


def test_room_noise_is_not_transcribed():
    noise = (np.sin(np.linspace(0, 2 * np.pi * 200, FRAME // 2)) * 300).astype(np.int16).tobytes()
    det, calls = make()
    feed_all(det, [noise] * 15 + [SILENCE] * 25)
    assert calls == []
