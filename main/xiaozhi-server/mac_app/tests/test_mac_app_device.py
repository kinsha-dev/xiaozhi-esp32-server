import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from mac_app.controller import Controller


class _Mic:
    started = stopped = 0

    def start(self): self.started += 1
    def stop(self): self.stopped += 1


def make(d):
    mic = _Mic()
    c = Controller(SimpleNamespace(data_dir=Path(d)), None, None, lambda cb: mic, None, lambda t: None)
    return c, mic


class DeviceTests(unittest.TestCase):
    def test_modes_and_board_state(self):
        with tempfile.TemporaryDirectory() as d:
            c, mic = make(d)
            self.assertEqual(c.device_mode(), "auto")
            self.assertFalse(c.defer_to_board())
            (Path(d) / "board_connected").write_text(f"{os.getpid()} 1")
            self.assertTrue(c.defer_to_board())
            (Path(d) / "board_state").write_text(f"{time.time()} speaking")
            self.assertEqual(c.display_state(), "speaking")
            self.assertTrue(c.mirroring)
            (Path(d) / "board_state").write_text(f"{time.time() - 10} speaking")
            self.assertEqual(c.display_state(), "idle")
            c.set_device_mode("mac")
            self.assertFalse(c.defer_to_board())
            c.set_device_mode("board")
            self.assertTrue(c.defer_to_board())
            self.assertEqual(mic.stopped, 1)


if __name__ == "__main__":
    unittest.main()
