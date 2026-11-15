import numpy as np

from mac_app import tones


def test_synth_pcm_is_int16_mono_and_cached():
    pcm = tones.synth_pcm("action")
    assert isinstance(pcm, bytes) and len(pcm) % 2 == 0
    assert tones.synth_pcm("action") is pcm


def test_every_kind_synthesizes():
    for kind in ("confused", "action", "pending"):
        samples = np.frombuffer(tones.synth_pcm(kind), dtype=np.int16)
        assert samples.size > 0
        assert np.abs(samples).max() <= 32767


def test_play_tone_calls_player_with_rate():
    calls = []
    tones.play_tone("pending", player=lambda samples, rate: calls.append((len(samples), rate)))
    assert calls and calls[0][0] > 0 and calls[0][1] == tones.RATE
