"""Vibi Ctrl serves a chat photo or video to the signed-in page only."""
from mac_app import ctrl_backend as be
from mac_app.ctrl import CtrlPanel, byte_range

BOARD = '''from pathlib import Path
def media_file(data_dir, media_id):
    p = Path(data_dir) / (media_id + ".mp4")
    return (p, "video/mp4") if p.is_file() else None
'''


def panel(tmp_path, monkeypatch):
    (tmp_path / "board.py").write_text(BOARD)
    monkeypatch.setenv("VIBI_MESSAGE_BOARD", str(tmp_path / "board.py"))
    (tmp_path / "abcdef012345.mp4").write_bytes(b"0123456789")
    return CtrlPanel(tmp_path, tmp_path, lambda t: True)


def test_media_needs_the_page_token(tmp_path, monkeypatch):
    p = panel(tmp_path, monkeypatch)
    assert p.handle("GET", "/media/abcdef012345", {"t": "wrong"})[0] == 403
    assert p.handle("GET", "/media/abcdef012345", {"t": p.token}) == (200, "video/mp4", b"0123456789")
    assert p.handle("GET", "/media/ffffffffffff", {"t": p.token})[0] == 404
    assert p.handle("GET", "/media/../board", {"t": p.token})[0] == 404


def test_missing_board_module_is_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBI_MESSAGE_BOARD", str(tmp_path / "none.py"))
    assert be.media(tmp_path, "abcdef012345") is None


def test_byte_range():
    assert byte_range(b"0123456789", "bytes=2-4") == (206, b"234", "bytes 2-4/10")
    assert byte_range(b"0123456789", "bytes=-3") == (206, b"789", "bytes 7-9/10")
    assert byte_range(b"0123456789", "bytes=20-")[0] == 416
    assert byte_range(b"0123456789", None) == (200, b"0123456789", "")
