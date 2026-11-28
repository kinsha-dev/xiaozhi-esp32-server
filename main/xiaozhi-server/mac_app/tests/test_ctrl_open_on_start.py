"""Every start of the Mac app opens a fresh Vibi Ctrl tab."""
from pathlib import Path

from mac_app.ctrl import open_on_start


class Panel:
    opened = 0

    def open(self):
        self.opened += 1


def test_a_tab_opens_after_start():
    panel = Panel()
    open_on_start(panel, env={}, delay=0).join(2)
    assert panel.opened == 1


def test_it_can_be_turned_off():
    assert open_on_start(Panel(), env={"VIBI_CTRL_ON_START": "0"}) is None


def test_the_app_does_it():
    assert "open_on_start(panel)" in (Path(__file__).parents[1] / "app.py").read_text()
