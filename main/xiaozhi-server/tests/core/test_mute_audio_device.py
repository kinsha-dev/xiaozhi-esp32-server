from types import SimpleNamespace

from core import board_link
from core.utils import audio_device


def conn(device="board-1"):
    return SimpleNamespace(headers={"device-id": device})


def test_mute_drops_board_audio_and_marks_probe(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_BOX_DATA", str(tmp_path))
    assert not audio_device.board_muted(conn()) and board_link.probe_body() == "auto"
    (tmp_path / "muted").write_text("1")
    assert audio_device.board_muted(conn())
    assert not audio_device.board_muted(conn("mac-app"))  # the Mac app handles its own mute
    assert board_link.probe_body().endswith(" muted")
    (tmp_path / "muted").unlink()
    assert not audio_device.board_muted(conn()) and board_link.probe_body() == "auto"
