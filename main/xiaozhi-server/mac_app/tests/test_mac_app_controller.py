import asyncio
import time

from mac_app.app import Controller
from mac_app.audio import AudioError
from mac_app.config import Config


class FakeSpeaker:
    def is_busy(self):
        return False


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
    def feed(self, frame):
        return frame == b"WAKE"


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
