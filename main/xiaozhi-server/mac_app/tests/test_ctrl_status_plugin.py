"""Component status is a pluggable floating panel; the Components tab is gone."""
from mac_app import ctrl_backend as be
from mac_app.ctrl import UI, CtrlPanel, page

ST = lambda label, *a: {"running": True, "pid": 7, "detail": ""}


def test_plugins_are_inlined_into_the_served_page(tmp_path):
    panel = CtrlPanel(tmp_path, tmp_path, lambda t: True)
    html = panel.handle("GET", "/", {"t": panel.token})[2].decode()
    assert 'VibiPlugins.add({id:"components"' in html and "<!--PLUGINS-->" not in html
    assert html.index("const VibiPlugins=") < html.index('VibiPlugins.add({id:"components"')
    assert '["components","Components"]' not in UI.read_text() and b"</script>" in page()


def test_error_time_comes_from_the_log(tmp_path):
    log = tmp_path / "x.log"
    log.write_text("261128 02:21:05[v]-ERROR-send failed\n261128 03:00:00 fine\n")
    assert be.last_error_at(log) == "Nov 28 02:21"
    log.write_text("2026-11-27 23:05:09 starting\nTraceback (most recent call last):\n  File x\nTypeError: no\n")
    assert be.last_error_at(log) == "Nov 27 23:05"
    log.write_text("TypeError: no time anywhere\n")
    assert be.last_error_at(log).endswith(" or earlier")
    log.write_text("all fine\n")
    assert be.last_error_at(log) == "" and be.last_error_at(tmp_path / "missing") == ""


def test_every_component_has_the_same_fields(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "server.log").write_text("261128 02:21:05 ERROR boom\n")
    found = be.components(tmp_path, tmp_path, status=ST)
    assert len({tuple(sorted(k for k in c if k != "muted")) for c in found}) == 1
    server = next(c for c in found if c["key"] == "server")
    assert server["error_at"] == "Nov 28 02:21" and server["restart"]
