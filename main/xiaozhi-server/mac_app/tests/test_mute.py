import os
import time

from mac_app.app import Controller
from mac_app.config import Config
from mac_app.tests.test_mac_app_controller import FakeDetector, FakeMic, FakeSession, FakeSpeaker


def make(tmp_path):
    cfg = Config("ws://x", "hi bibi", tmp_path, 24000)
    mics = []
    ctl = Controller(cfg, FakeSpeaker(), FakeDetector(), lambda cb: mics.append(FakeMic(cb)) or mics[-1], FakeSession, lambda t: None)
    return ctl, mics


def test_mute_writes_and_clears_flag(tmp_path):
    ctl, mics = make(tmp_path)
    ctl.set_muted(True)
    assert (tmp_path / "muted").exists() and not mics[0].running
    ctl.set_muted(False)
    assert not (tmp_path / "muted").exists() and mics[0].running


def test_external_change_is_picked_up(tmp_path):
    ctl, _ = make(tmp_path)
    (tmp_path / "muted").write_text("1")
    ctl.sync_mute(every=0)
    assert ctl.muted
    (tmp_path / "muted").unlink()
    ctl.sync_mute(every=0)
    assert not ctl.muted


def test_mute_survives_restart_and_blocks_sessions(tmp_path):
    (tmp_path / "muted").write_text("1")
    ctl, mics = make(tmp_path)
    ctl.start()
    assert ctl.muted and not mics[0].running
    assert ctl.start_session("hi bibi") is False
    assert ctl.start_session("x", force=True) is False  # force never overrides mute
    ctl.stop()
