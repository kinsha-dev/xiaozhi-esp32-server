from mac_app.config import Config
from mac_app.controller import Controller


def _ctl(tmp_path):
    cfg = Config("ws://x", "hi bibi", tmp_path, 24000)
    return Controller(cfg, None, None, lambda cb: None, None, lambda t: None)


def test_toggle_persists_via_flag_file(tmp_path):
    c = _ctl(tmp_path)
    assert not c.tones_disabled()
    c.set_tones_disabled(True)
    assert (tmp_path / "tones_disabled").exists() and _ctl(tmp_path).tones_disabled()
    c.set_tones_disabled(False)
    assert not (tmp_path / "tones_disabled").exists()


def test_local_tone_skipped_when_disabled(tmp_path, monkeypatch):
    import mac_app.tones as t
    monkeypatch.setenv("VOICE_BOX_DATA", str(tmp_path))
    (tmp_path / "tones_disabled").write_text("1")
    monkeypatch.setattr(t, "_play_default", lambda *a: (_ for _ in ()).throw(AssertionError("played")))
    t.play_tone("confused")
