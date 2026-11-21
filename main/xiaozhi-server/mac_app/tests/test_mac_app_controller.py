import asyncio
import time

from mac_app.app import Controller
from mac_app.audio import AudioError
from mac_app.config import Config


class FakeSpeaker:
    def __init__(self):
        self.busy = False

    def is_busy(self):
        return self.busy


class FakeMic:
    def __init__(self, on_frame, fail=False):
        self.on_frame, self.fail, self.running = on_frame, fail, False

    def start(self):
        if self.fail:
            raise AudioError("no mic")
        self.running = True

    def stop(self):
        self.running = False


class FakeDetector:
    def __init__(self):
        self.last_result = None

    def feed(self, frame):
        if frame == b"WAKE":
            self.last_result = "matched"
            return True
        if frame == b"NEARMISS":  # even length: must still parse as int16 PCM in _on_mic
            self.last_result = "no_match"
        return False


class FakeSession:
    runs = []
    autos = []

    def __init__(self, q, on_state):
        self.on_state = on_state

    async def run(self, text, auto=False):
        FakeSession.runs.append(text)
        FakeSession.autos.append(auto)
        self.on_state("listening")
        await asyncio.sleep(0.2)
        self.on_state("idle")


def make(fail=False):
    FakeSession.runs = []
    notes = []
    cfg = Config("ws://x", "hi bibi", None, 24000)
    ctl = Controller(cfg, FakeSpeaker(), FakeDetector(), lambda cb: FakeMic(cb, fail), FakeSession, notes.append)
    ctl.start()
    return ctl, notes


def wait(pred, t=2.0):
    end = time.monotonic() + t
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_wake_starts_one_session():
    ctl, _ = make()
    ctl._on_mic(b"WAKE")
    assert wait(lambda: FakeSession.runs == ["hi bibi"])
    ctl._on_mic(b"WAKE")  # while active: ignored
    assert wait(lambda: ctl.state == "idle")
    assert FakeSession.runs == ["hi bibi"]
    ctl.stop()


def test_muted_ignores_wake_and_notifies_on_pending():
    ctl, notes = make()
    ctl.set_muted(True)
    assert not ctl._mic.running
    ctl._on_mic(b"WAKE")
    ctl.on_pending()
    time.sleep(0.3)
    assert FakeSession.runs == [] and len(notes) == 1
    ctl.set_muted(False)
    assert ctl._mic.running
    ctl.stop()


def test_pending_does_nothing_until_hi_vibi_by_default():
    ctl, _ = make()
    ctl.on_pending()
    time.sleep(0.4)
    assert FakeSession.runs == []
    ctl.stop()


def test_pending_opens_session_when_not_muted():
    import tempfile
    from pathlib import Path
    ctl, _ = make()
    ctl.cfg.data_dir = Path(tempfile.mkdtemp())
    ctl.set_auto_speak(True)
    ctl.on_pending()
    assert wait(lambda: FakeSession.runs == ["hi bibi"])
    assert FakeSession.autos[-1] is True  # started by itself: it must end after speaking
    ctl.stop()


def test_mic_failure_sets_error_state():
    ctl, _ = make(fail=True)
    assert ctl.error and ctl.display_state() == "error"
    ctl.stop()


def test_wake_near_miss_plays_confused_tone(monkeypatch):
    played = []
    monkeypatch.setattr("mac_app.controller.tones.play_tone", lambda kind: played.append(kind))
    ctl, _ = make()
    ctl._on_mic(b"NEARMISS")
    assert wait(lambda: played == ["confused"])
    assert FakeSession.runs == []
    ctl.stop()


def test_pending_plays_tone_when_waiting_for_hi_vibi(monkeypatch):
    played = []
    monkeypatch.setattr("mac_app.controller.tones.play_tone", lambda kind: played.append(kind))
    ctl, _ = make()
    ctl.on_pending()
    assert played == ["pending"]
    assert FakeSession.runs == []
    ctl.stop()


def test_pending_stays_silent_when_muted(monkeypatch):
    played = []
    monkeypatch.setattr("mac_app.controller.tones.play_tone", lambda kind: played.append(kind))
    ctl, notes = make()
    ctl.set_muted(True)
    ctl.on_pending()
    assert played == [] and len(notes) == 1
    ctl.stop()


def test_pending_does_not_play_over_a_spoken_reply(monkeypatch):
    played = []
    monkeypatch.setattr("mac_app.controller.tones.play_tone", lambda kind: played.append(kind))
    ctl, _ = make()
    ctl._speaker.busy = True
    ctl.on_pending()
    assert played == []
    ctl.stop()


def test_confused_chirp_fires_once_per_segment_not_every_frame(monkeypatch):
    played = []
    monkeypatch.setattr("mac_app.controller.tones.play_tone", lambda kind: played.append(kind))
    ctl, _ = make()
    ctl._on_mic(b"NEARMISS")
    assert wait(lambda: played == ["confused"])
    for _ in range(5):
        ctl._on_mic(b"\x00\x00")  # later frames must not replay the same near-miss
    time.sleep(0.3)
    assert played == ["confused"]
    ctl.stop()


def test_confused_chirp_is_rate_limited(monkeypatch):
    played = []
    monkeypatch.setattr("mac_app.controller.tones.play_tone", lambda kind: played.append(kind))
    ctl, _ = make()
    ctl._on_mic(b"NEARMISS")
    assert wait(lambda: played == ["confused"])
    ctl._on_mic(b"NEARMISS")
    time.sleep(0.3)
    assert played == ["confused"]
    ctl._confused_until = 0.0
    ctl._on_mic(b"NEARMISS")
    assert wait(lambda: played == ["confused", "confused"])
    ctl.stop()


def test_session_stops_a_tone_so_it_cannot_cut_off_the_reply(monkeypatch):
    stopped = []
    monkeypatch.setattr("mac_app.controller.tones.play_tone", lambda kind: None)
    monkeypatch.setattr("mac_app.controller.tones.stop", lambda: stopped.append("stop"))
    ctl, _ = make()
    ctl._on_mic(b"WAKE")
    assert wait(lambda: FakeSession.runs == ["hi bibi"] and stopped)
    ctl.stop()


def test_voice_animation_flag_is_not_reread_every_redraw(tmp_path):
    ctl, _ = make()
    ctl.cfg.data_dir = tmp_path
    (tmp_path / "voice_animation").write_text("0")
    assert ctl.voice_animation_enabled() is False
    (tmp_path / "voice_animation").write_text("1")  # a redraw must not hit the file again
    assert ctl.voice_animation_enabled() is False
    ctl.set_voice_animation(True)
    assert ctl.voice_animation_enabled() is True
    assert (tmp_path / "voice_animation").read_text() == "1"
    ctl.stop()


def test_voice_animation_enabled_by_default():
    ctl, _ = make()
    assert ctl.voice_animation_enabled() is True
    ctl.stop()


def test_voice_animation_toggle_persists(tmp_path):
    ctl, _ = make()
    ctl.cfg.data_dir = tmp_path
    ctl.set_voice_animation(False)
    assert ctl.voice_animation_enabled() is False
    ctl.set_voice_animation(True)
    assert ctl.voice_animation_enabled() is True
    ctl.stop()


def test_pending_does_not_play_tone_when_starting_a_session(monkeypatch):
    import tempfile
    from pathlib import Path
    played = []
    monkeypatch.setattr("mac_app.controller.tones.play_tone", lambda kind: played.append(kind))
    ctl, _ = make()
    ctl.cfg.data_dir = Path(tempfile.mkdtemp())
    ctl.set_auto_speak(True)
    ctl.on_pending()
    assert wait(lambda: FakeSession.runs == ["hi bibi"])
    assert played == []
    ctl.stop()
