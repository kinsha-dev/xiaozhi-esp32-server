from core.utils import name_filter as nf


def test_name_variants_are_heard():
    for t in ("hi vibi what time is it", "Hey Bibi.", "vb, play music", "what's up vivi", "Hi V.B."):
        assert nf.says_name(t), t


def test_background_speech_has_no_name():
    for t in ("We are on the sacred land in Austin, Texas", "That is a nice vibe, baby", "see you at the bobby show"):
        assert not nf.says_name(t), t


def test_followup_window_lets_a_reply_through(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_BOX_DATA", str(tmp_path))
    assert not nf.should_answer("and tomorrow", 0.0, now=100.0)
    assert nf.should_answer("and tomorrow", 120.0, now=100.0)
    (tmp_path / "awaiting_reply_until").write_text("200")
    assert nf.should_answer("yes", 0.0, now=150.0)
    assert not nf.should_answer("yes", 0.0, now=250.0)
