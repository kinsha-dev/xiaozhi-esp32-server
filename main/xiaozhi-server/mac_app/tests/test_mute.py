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


def test_stealth_flag_stops_mic_and_is_shared(tmp_path):
    ctl, mics = make(tmp_path)
    ctl.start()
    assert mics[0].running
    ctl.set_stealth(True)
    assert (tmp_path / "stealth").exists() and not mics[0].running and ctl.display_state() == "stealth"
    ctl.set_stealth(False)
    assert not (tmp_path / "stealth").exists() and mics[0].running
    (tmp_path / "stealth").write_text("1")  # set from Vibi Ctrl or the CLI
    ctl.sync_mute(every=0)
    assert ctl.stealth and not mics[0].running
    ctl.stop()


def test_stealth_survives_restart_blocks_wake_and_updates_but_allows_typed_commands(tmp_path):
    (tmp_path / "stealth").write_text("1")
    ctl, mics = make(tmp_path)
    notes = []
    ctl._notify = notes.append
    ctl.start()
    assert ctl.stealth and not mics[0].running  # no listening at all
    ctl.on_pending()
    assert notes == ["vibi has a message (stealth)"]  # no read-out, no chirp
    assert ctl.start_session("what time is it", force=True) is True  # a typed command from Vibi Ctrl still runs
    ctl.stop()


def test_stealth_session_is_quiet_and_reply_goes_to_the_board(tmp_path):
    import asyncio
    import json
    import sys

    vb = tmp_path / "vb"
    (vb / "server").mkdir(parents=True)
    (vb / "server" / "message_board.py").write_text(
        "import json\n"
        "def post_message(text, data_dir, **kw):\n"
        "    open(str(data_dir) + '/posted.json', 'w').write(json.dumps({'text': text, **kw}))\n")
    data = vb / "data"
    data.mkdir()
    (data / "stealth").write_text("1")
    cfg = Config("ws://x", "hi bibi", data, 24000)

    class Quiet:
        reply = ["It is noon."]

        def __init__(self, q, on_state):
            pass

        async def run(self, text, auto=False):
            assert self.quiet is True

    ctl = Controller(cfg, FakeSpeaker(), FakeDetector(), lambda cb: FakeMic(cb), Quiet, lambda t: None)
    ctl.start()
    assert ctl.start_session("what time is it", force=True)
    for _ in range(100):
        if (data / "posted.json").exists():
            break
        time.sleep(0.02)
    row = json.loads((data / "posted.json").read_text())
    assert "You: what time is it" in row["text"] and "Vibi: It is noon." in row["text"]
    assert (row["source"], row["channel"], row["status"]) == ("Vibi", "typed", "done")
    ctl.stop()
