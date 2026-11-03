import numpy as np
import pytest
import sounddevice as sd

from mac_app import audio
from mac_app.audio import AudioError, Mic, OpusDecoder, OpusEncoder, Speaker


def test_opus_round_trip_keeps_tone():
    rate = 16000
    t = np.arange(960) / rate
    frames = [(np.sin(2 * np.pi * 440 * (t + i * 960 / rate)) * 10000).astype(np.int16) for i in range(10)]
    enc, dec = OpusEncoder(rate), OpusDecoder(rate, 60)
    out = np.concatenate([np.frombuffer(dec.decode(enc.encode(f.tobytes())), dtype=np.int16) for f in frames])
    ref = np.concatenate(frames)
    # Opus adds a few ms of delay, so compare at the best lag (skipping codec warm-up)
    a = ref[1920:-400].astype(float)
    best = max(np.corrcoef(a, out[1920 + lag:len(ref) - 400 + lag].astype(float))[0, 1] for lag in range(300))
    assert best > 0.9


def test_mic_start_without_input_device_raises(monkeypatch):
    def boom(*a, **k):
        raise ValueError("No input device matching")

    monkeypatch.setattr(audio.sd, "query_devices", boom)
    with pytest.raises(AudioError):
        Mic(lambda f: None).start()


class DummyStream:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


def test_speaker_busy_until_flush(monkeypatch):
    monkeypatch.setattr(audio.sd, "RawOutputStream", DummyStream)
    sp = Speaker(24000)
    assert not sp.is_busy()
    sp.play(bytes(4800))
    assert sp.is_busy()
    sp.flush()
    assert not sp.is_busy()
